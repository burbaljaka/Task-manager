import datetime
import json
from unittest.mock import patch

from django.conf import settings
from django.core.exceptions import ValidationError
from django.contrib.auth.models import User
from django.forms.models import model_to_dict
from django.contrib.messages import get_messages
from django.test import Client, TestCase
from django.urls import reverse

from basic_app.board_scope import BoardScope
from basic_app.kanban_board import (
    get_task_hierarchy_for_scope,
    iter_all_tasks_in_tree,
    sort_tasks_for_kanban_column,
)
from basic_app.kanban_utils import (
    BUILTIN_ORDER_KEYS,
    DEFAULT_BUILTIN_LABELS,
    ensure_kanban_builtins,
    ensure_kanban_builtins_for_team,
    status_css_slug,
)
from basic_app.admin import UserTaskAdminForm
from basic_app.models import (
    KanbanColumnDefinition,
    TaskTeam,
    TaskTeamMembership,
    UserTask,
)
from basic_app.views import expected_task_ids_by_status_for_user


class KanbanHelpersTests(TestCase):
    def test_builtin_order_keys(self):
        self.assertEqual(
            list(BUILTIN_ORDER_KEYS),
            ["TODO", "IN_PROGRESS", "COMPLETED", "CANCELLED"],
        )
        self.assertEqual(len(BUILTIN_ORDER_KEYS), 4)

    def test_status_css_slug_matches_slug_rules(self):
        self.assertEqual(status_css_slug("IN_PROGRESS"), "in-progress")
        self.assertEqual(status_css_slug("MY__CUSTOM"), "my-custom")

    def test_default_builtin_labels_are_russian(self):
        self.assertEqual(
            DEFAULT_BUILTIN_LABELS,
            {
                "TODO": "К выполнению",
                "IN_PROGRESS": "В работе",
                "COMPLETED": "Готово",
                "CANCELLED": "Отменено",
            },
        )

    def test_iter_all_tasks_in_tree_dfs_order(self):
        user = User.objects.create_user(username="u1", password="pass12345")
        p = UserTask.objects.create(user=user, name="P", status="TODO")
        c = UserTask.objects.create(
            user=user, name="C", status="IN_PROGRESS", parent_task=p
        )
        p.subtasks_list = [c]
        nodes = list(iter_all_tasks_in_tree([p]))
        self.assertEqual([n.id for n in nodes], [p.id, c.id])

    def test_sort_tasks_for_kanban_column_due_date_then_id(self):
        user = User.objects.create_user(username="u2", password="pass12345")
        d1 = datetime.date(2025, 1, 10)
        d2 = datetime.date(2025, 1, 5)
        t1 = UserTask.objects.create(user=user, name="a", status="TODO", due_date=d1)
        t2 = UserTask.objects.create(user=user, name="b", status="TODO", due_date=d2)
        t3 = UserTask.objects.create(user=user, name="c", status="TODO", due_date=None)
        sorted_tasks = sort_tasks_for_kanban_column([t1, t2, t3])
        self.assertEqual([t.id for t in sorted_tasks], [t2.id, t1.id, t3.id])

    def test_get_task_hierarchy_promotes_subtask_when_parent_excluded_completed(self):
        user = User.objects.create_user(username="hier", password="pass12345")
        parent = UserTask.objects.create(
            user=user,
            name="P",
            status="COMPLETED",
            completion_date=datetime.date.today(),
        )
        child = UserTask.objects.create(
            user=user, name="C", status="TODO", parent_task=parent
        )
        roots = get_task_hierarchy_for_scope(
            BoardScope(user_id=user.id, team_id=None), exclude_completed=True
        )
        root_ids = {t.id for t in roots}
        self.assertIn(child.id, root_ids)

    def test_sort_tasks_for_kanban_column_orders_by_kanban_position_first(self):
        user = User.objects.create_user(username="u3", password="pass12345")
        d_later = datetime.date(2025, 2, 1)
        d_earlier = datetime.date(2025, 1, 1)
        t_a = UserTask.objects.create(
            user=user,
            name="a",
            status="TODO",
            due_date=d_later,
            kanban_position=1,
        )
        t_b = UserTask.objects.create(
            user=user,
            name="b",
            status="TODO",
            due_date=d_earlier,
            kanban_position=0,
        )
        sorted_tasks = sort_tasks_for_kanban_column([t_a, t_b])
        self.assertEqual([t.id for t in sorted_tasks], [t_b.id, t_a.id])


class UserTasksKanbanViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="viewer", password="pass12345")
        self.client.login(username="viewer", password="pass12345")

    def test_anonymous_get_tasks_redirects_to_login(self):
        anon = Client()
        response = anon.get(reverse("basic_app:user_tasks_view"))
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_user_tasks_view_has_kanban_columns_shape(self):
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        self.assertEqual(response.status_code, 200)
        kanban_columns = response.context["kanban_columns"]
        self.assertEqual(len(kanban_columns), 4)
        expected_statuses = list(BUILTIN_ORDER_KEYS)
        for i, col in enumerate(kanban_columns):
            self.assertEqual(col["status"], expected_statuses[i])
            self.assertIn("label", col)
            self.assertIn("tasks", col)
            self.assertIsInstance(col["tasks"], list)
        self.assertEqual(len(response.context["status_options"]), 4)

    def test_usertasks_empty_for_board(self):
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        self.assertEqual(response.context["usertasks"], [])

    def test_kanban_cancelled_column_has_fixed_width_css_rule(self):
        """Cancelled column is outside reorderable wrapper; CSS must not let it flex-grow."""
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        self.assertEqual(response.status_code, 200)
        content = response.content.decode()
        self.assertIn('data-kanban-fixed="1"', content)
        self.assertIn(
            ".kanban-board > .kanban-column[data-kanban-fixed]",
            content,
        )

    def test_tasks_bucketed_by_status(self):
        UserTask.objects.create(user=self.user, name="t1", status="TODO")
        UserTask.objects.create(user=self.user, name="t2", status="IN_PROGRESS")
        UserTask.objects.create(user=self.user, name="t3", status="CANCELLED")
        UserTask.objects.create(
            user=self.user,
            name="t4",
            status="COMPLETED",
            completion_date=datetime.date.today(),
        )
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        cols = {c["status"]: c["tasks"] for c in response.context["kanban_columns"]}
        self.assertEqual(len(cols["TODO"]), 1)
        self.assertEqual(len(cols["IN_PROGRESS"]), 1)
        self.assertEqual(len(cols["CANCELLED"]), 1)
        self.assertEqual(len(cols["COMPLETED"]), 1)

    def test_custom_column_appears_before_cancelled_and_buckets_tasks(self):
        ensure_kanban_builtins(self.user)
        KanbanColumnDefinition.objects.create(
            user=self.user,
            key="REVIEW",
            label="Review",
            sort_order=10,
            is_builtin=False,
        )
        UserTask.objects.create(user=self.user, name="rev", status="REVIEW")
        UserTask.objects.create(user=self.user, name="can", status="CANCELLED")
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        statuses = [c["status"] for c in response.context["kanban_columns"]]
        self.assertIn("REVIEW", statuses)
        self.assertEqual(statuses[-1], "CANCELLED")
        rev_idx = statuses.index("REVIEW")
        cancel_idx = statuses.index("CANCELLED")
        self.assertLess(rev_idx, cancel_idx)
        cols = {c["status"]: c["tasks"] for c in response.context["kanban_columns"]}
        self.assertEqual(len(cols["REVIEW"]), 1)
        self.assertEqual(cols["REVIEW"][0].name, "rev")

    def test_orphan_status_column_without_definition(self):
        ensure_kanban_builtins(self.user)
        t = UserTask.objects.create(user=self.user, name="orph", status="TODO")
        UserTask.objects.filter(pk=t.pk).update(status="LEGACY_X")
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        statuses = [c["status"] for c in response.context["kanban_columns"]]
        self.assertIn("LEGACY_X", statuses)
        label = next(
            c["label"]
            for c in response.context["kanban_columns"]
            if c["status"] == "LEGACY_X"
        )
        self.assertEqual(label, "[LEGACY_X]")

    def test_kanban_column_create_adds_column(self):
        ensure_kanban_builtins(self.user)
        url = reverse("basic_app:kanban_column_create")
        response = self.client.post(
            url,
            {"label": "QA", "key": ""},
            follow=False,
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(
            KanbanColumnDefinition.objects.filter(user=self.user, key="QA").exists()
        )

    def _csrf_header(self) -> dict[str, str]:
        self.client.get(reverse("basic_app:user_tasks_view"))
        token = self.client.cookies.get("csrftoken")
        if not token:
            return {}
        return {"HTTP_X_CSRFTOKEN": token.value}

    def test_kanban_column_reorder_updates_board_order(self):
        ensure_kanban_builtins(self.user)
        url = reverse("basic_app:kanban_column_reorder")
        body = {"columnKeys": ["IN_PROGRESS", "TODO", "COMPLETED"]}
        response = self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content.decode()), {"ok": True})
        response_get = self.client.get(reverse("basic_app:user_tasks_view"))
        statuses = [c["status"] for c in response_get.context["kanban_columns"]]
        self.assertEqual(
            statuses,
            ["IN_PROGRESS", "TODO", "COMPLETED", "CANCELLED"],
        )
        orders = list(
            KanbanColumnDefinition.objects.filter(user=self.user)
            .exclude(key="CANCELLED")
            .order_by("sort_order", "key")
            .values_list("key", "sort_order")
        )
        self.assertEqual(
            orders,
            [("IN_PROGRESS", 0), ("TODO", 1), ("COMPLETED", 2)],
        )

    def test_kanban_column_reorder_out_of_sync_returns_code(self):
        ensure_kanban_builtins(self.user)
        KanbanColumnDefinition.objects.create(
            user=self.user,
            key="EXTRA",
            label="Extra",
            sort_order=5,
            is_builtin=False,
        )
        url = reverse("basic_app:kanban_column_reorder")
        body = {"columnKeys": ["IN_PROGRESS", "TODO", "COMPLETED"]}
        response = self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content.decode())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "columnKeysOutOfSync")

    def test_kanban_column_reorder_rejects_non_json_content_type(self):
        ensure_kanban_builtins(self.user)
        url = reverse("basic_app:kanban_column_reorder")
        response = self.client.post(
            url,
            data={"columnKeys": ["TODO"]},
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 400)

    def test_kanban_column_reorder_rejects_invalid_json(self):
        ensure_kanban_builtins(self.user)
        url = reverse("basic_app:kanban_column_reorder")
        response = self.client.post(
            url,
            data="{not json",
            content_type="application/json",
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 400)

    def test_kanban_column_reorder_anonymous_redirects_to_login(self):
        ensure_kanban_builtins(self.user)
        url = reverse("basic_app:kanban_column_reorder")
        anon = Client()
        response = anon.post(
            url,
            data=json.dumps({"columnKeys": ["TODO", "IN_PROGRESS", "COMPLETED"]}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_kanban_task_reorder_persists_order_within_column(self):
        ensure_kanban_builtins(self.user)
        t1 = UserTask.objects.create(user=self.user, name="a", status="TODO")
        t2 = UserTask.objects.create(user=self.user, name="b", status="TODO")
        expected = expected_task_ids_by_status_for_user(self.user.id)
        body = {"taskIdsByStatus": {}}
        for k, ids in expected.items():
            body["taskIdsByStatus"][k] = list(ids)
        body["taskIdsByStatus"]["TODO"] = [t2.id, t1.id]
        url = reverse("basic_app:kanban_task_reorder")
        response = self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content.decode()), {"ok": True})
        t1.refresh_from_db()
        t2.refresh_from_db()
        self.assertEqual(t1.kanban_position, 1)
        self.assertEqual(t2.kanban_position, 0)

    def test_kanban_task_reorder_moves_task_between_columns(self):
        ensure_kanban_builtins(self.user)
        t = UserTask.objects.create(user=self.user, name="move me", status="TODO")
        expected = expected_task_ids_by_status_for_user(self.user.id)
        body = {"taskIdsByStatus": {}}
        for k, ids in expected.items():
            body["taskIdsByStatus"][k] = [i for i in ids if i != t.id]
        if "IN_PROGRESS" not in body["taskIdsByStatus"]:
            self.fail("expected IN_PROGRESS column")
        body["taskIdsByStatus"]["IN_PROGRESS"] = list(
            body["taskIdsByStatus"]["IN_PROGRESS"]
        ) + [t.id]
        url = reverse("basic_app:kanban_task_reorder")
        response = self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(json.loads(response.content.decode()), {"ok": True})
        t.refresh_from_db()
        self.assertEqual(t.status, "IN_PROGRESS")

    def test_kanban_task_reorder_out_of_sync_returns_code(self):
        ensure_kanban_builtins(self.user)
        UserTask.objects.create(user=self.user, name="t", status="TODO")
        expected = expected_task_ids_by_status_for_user(self.user.id)
        body = {"taskIdsByStatus": {}}
        for k, ids in expected.items():
            body["taskIdsByStatus"][k] = list(ids)
        body["taskIdsByStatus"]["TODO"] = []
        url = reverse("basic_app:kanban_task_reorder")
        response = self.client.post(
            url,
            data=json.dumps(body),
            content_type="application/json",
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content.decode())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "taskIdsOutOfSync")

    def test_kanban_task_reorder_rejects_non_json_content_type(self):
        ensure_kanban_builtins(self.user)
        url = reverse("basic_app:kanban_task_reorder")
        response = self.client.post(
            url,
            data={"taskIdsByStatus": {}},
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 400)

    def test_kanban_task_reorder_rejects_invalid_json(self):
        ensure_kanban_builtins(self.user)
        url = reverse("basic_app:kanban_task_reorder")
        response = self.client.post(
            url,
            data="{not json",
            content_type="application/json",
            **self._csrf_header(),
        )
        self.assertEqual(response.status_code, 400)

    def test_kanban_task_reorder_anonymous_redirects_to_login(self):
        ensure_kanban_builtins(self.user)
        url = reverse("basic_app:kanban_task_reorder")
        anon = Client()
        response = anon.post(
            url,
            data=json.dumps({"taskIdsByStatus": {"TODO": [], "IN_PROGRESS": [], "COMPLETED": []}}),
            content_type="application/json",
        )
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_kanban_task_reorder_wrong_board_scope_returns_board_scope_mismatch(self):
        ensure_kanban_builtins(self.user)
        team = TaskTeam.objects.create(name="ReorderT")
        TaskTeamMembership.objects.create(team=team, user=self.user)
        ensure_kanban_builtins_for_team(team)
        team_task = UserTask.objects.create(
            user=self.user, name="team t", status="TODO", team=team
        )
        personal_expected = expected_task_ids_by_status_for_user(self.user.id)
        merged = {k: list(v) for k, v in personal_expected.items()}
        merged["TODO"] = list(merged["TODO"]) + [team_task.id]
        body = {"taskIdsByStatus": merged}
        url = reverse("basic_app:kanban_task_reorder")
        with patch(
            "basic_app.views.expected_task_ids_by_status_for_scope", return_value=merged
        ):
            response = self.client.post(
                url,
                data=json.dumps(body),
                content_type="application/json",
                **self._csrf_header(),
            )
        self.assertEqual(response.status_code, 400)
        payload = json.loads(response.content.decode())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "boardScopeMismatch")

    def test_kanban_task_reorder_access_denied_returns_access_denied(self):
        ensure_kanban_builtins(self.user)
        UserTask.objects.create(user=self.user, name="t", status="TODO")
        expected = expected_task_ids_by_status_for_user(self.user.id)
        body = {"taskIdsByStatus": {k: list(v) for k, v in expected.items()}}
        url = reverse("basic_app:kanban_task_reorder")
        with patch("basic_app.views.user_can_access_task", return_value=False):
            response = self.client.post(
                url,
                data=json.dumps(body),
                content_type="application/json",
                **self._csrf_header(),
            )
        self.assertEqual(response.status_code, 403)
        payload = json.loads(response.content.decode())
        self.assertFalse(payload["ok"])
        self.assertEqual(payload["code"], "accessDenied")

    def test_user_tasks_view_includes_initial_reorderable_column_keys(self):
        ensure_kanban_builtins(self.user)
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        keys = response.context["initial_reorderable_column_keys"]
        self.assertEqual(keys, ["TODO", "IN_PROGRESS", "COMPLETED"])

    def test_kanban_board_does_not_duplicate_subtask_dom_nodes(self):
        parent = UserTask.objects.create(
            user=self.user, name="Parent", status="TODO"
        )
        sub = UserTask.objects.create(
            user=self.user,
            name="Sub",
            status="IN_PROGRESS",
            parent_task=parent,
        )
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        needle = f'data-task-id="{sub.id}"'
        self.assertEqual(
            html.count(needle),
            1,
            "Subtask must appear exactly once (column card only; not nested under parent).",
        )

    def test_kanban_task_card_has_task_detail_link_with_target_blank(self):
        task = UserTask.objects.create(user=self.user, name="Linkable", status="TODO")
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        self.assertEqual(response.status_code, 200)
        html = response.content.decode()
        detail_url = reverse("basic_app:task_detail", kwargs={"task_id": task.id})
        self.assertIn(detail_url, html)
        self.assertIn(f'href="{detail_url}"', html)
        self.assertIn('target="_blank"', html)
        self.assertIn('rel="noopener noreferrer"', html)
        self.assertIn(f"#{task.id}", html)


class UserTaskAdminFormTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user(username="admform", password="pass12345")
        ensure_kanban_builtins(self.owner)

    def test_status_choices_include_orphan_key_with_bracket_label(self):
        t = UserTask.objects.create(user=self.owner, name="orph", status="TODO")
        UserTask.objects.filter(pk=t.pk).update(status="LEGACY_X")
        t.refresh_from_db()
        form = UserTaskAdminForm(instance=t)
        choices = list(form.fields["status"].widget.choices)
        legacy = [c for c in choices if c[0] == "LEGACY_X"]
        self.assertEqual(len(legacy), 1)
        self.assertEqual(legacy[0][1], "[LEGACY_X]")

    def test_clean_status_accepts_orphan_when_not_in_definitions(self):
        t = UserTask.objects.create(user=self.owner, name="orph2", status="TODO")
        UserTask.objects.filter(pk=t.pk).update(status="LEGACY_X")
        t.refresh_from_db()
        data = model_to_dict(t)
        data["status"] = "LEGACY_X"
        form = UserTaskAdminForm(data, instance=t)
        self.assertTrue(form.is_valid(), form.errors)


class KanbanColumnDefinitionAdminTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="colowner", password="pass12345")
        self.superuser = User.objects.create_superuser(
            username="admin", password="pass12345", email="a@example.com"
        )
        ensure_kanban_builtins(self.owner)
        self.custom = KanbanColumnDefinition.objects.create(
            user=self.owner,
            key="EMPTY_COL",
            label="Empty",
            sort_order=50,
            is_builtin=False,
        )

    def _delete_url(self, pk: int) -> str:
        return reverse("admin:basic_app_kanbancolumndefinition_delete", args=[pk])

    def test_admin_blocks_delete_builtin_column(self):
        builtin = KanbanColumnDefinition.objects.get(user=self.owner, key="TODO")
        self.client.login(username="admin", password="pass12345")
        response = self.client.post(self._delete_url(builtin.pk), {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            KanbanColumnDefinition.objects.filter(pk=builtin.pk).exists()
        )

    def test_admin_blocks_delete_column_referenced_by_task(self):
        UserTask.objects.create(user=self.owner, name="holds", status="EMPTY_COL")
        self.client.login(username="admin", password="pass12345")
        response = self.client.post(self._delete_url(self.custom.pk), {"post": "yes"})
        self.assertEqual(response.status_code, 403)
        self.assertTrue(
            KanbanColumnDefinition.objects.filter(pk=self.custom.pk).exists()
        )

    def test_admin_allows_delete_unused_custom_column(self):
        self.client.login(username="admin", password="pass12345")
        response = self.client.post(self._delete_url(self.custom.pk), {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(
            KanbanColumnDefinition.objects.filter(pk=self.custom.pk).exists()
        )


class LoginViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        User.objects.create_user(username="loguser", password="correctpass")

    def test_failed_login_does_not_log_password(self):
        url = reverse("basic_app:user_login")
        secret = "wrong_secret_abc"
        with self.assertLogs("basic_app.views", level="WARNING") as cm:
            self.client.post(
                url,
                {"username": "loguser", "password": secret},
            )
        logged = "\n".join(cm.output)
        self.assertNotIn(secret, logged)

    def test_failed_login_unknown_user_does_not_log_password(self):
        url = reverse("basic_app:user_login")
        secret = "any_secret_xyz"
        with self.assertLogs("basic_app.views", level="WARNING") as cm:
            self.client.post(
                url,
                {"username": "no_such_user", "password": secret},
            )
        logged = "\n".join(cm.output)
        self.assertNotIn(secret, logged)


class ReportsViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="reporter", password="pass12345")
        self.client.login(username="reporter", password="pass12345")

    def test_reports_anonymous_get_redirects_to_login(self):
        self.client.logout()
        url = reverse("basic_app:reports")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_reports_anonymous_post_redirects_to_login(self):
        self.client.logout()
        url = reverse("basic_app:reports")
        response = self.client.post(url, {"name": "x", "to_show": "1"})
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_reports_post_invalid_form_redirects_without_500(self):
        url = reverse("basic_app:reports")
        response = self.client.post(url, {}, follow=True)
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("форм" in m.lower() for m in msgs))

    def test_reports_post_valid_updates_task_and_renders(self):
        UserTask.objects.create(
            user=self.user, name="rtask", status="TODO", to_show=0
        )
        url = reverse("basic_app:reports")
        response = self.client.post(
            url, {"name": "rtask", "to_show": "1"}, follow=False
        )
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["period"], "Сегодня")
        self.assertFalse(response.context["show_all"])
        t = UserTask.objects.get(name="rtask", user=self.user)
        self.assertEqual(t.to_show, 1)

    def test_reports_post_unknown_task_name_redirects_without_500(self):
        url = reverse("basic_app:reports")
        response = self.client.post(
            url, {"name": "нет_такой_задачи", "to_show": "1"}, follow=True
        )
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("не найден" in m.lower() for m in msgs))
        self.assertFalse(
            UserTask.objects.filter(
                user=self.user, name="нет_такой_задачи"
            ).exists()
        )

    def test_reports_post_duplicate_task_names_updates_one_without_500(self):
        UserTask.objects.create(user=self.user, name="dup", status="TODO", to_show=0)
        UserTask.objects.create(user=self.user, name="dup", status="TODO", to_show=0)
        url = reverse("basic_app:reports")
        response = self.client.post(
            url, {"name": "dup", "to_show": "1"}, follow=False
        )
        self.assertEqual(response.status_code, 200)
        tasks = list(UserTask.objects.filter(user=self.user, name="dup").order_by("id"))
        self.assertEqual(len(tasks), 2)
        self.assertEqual(tasks[0].to_show, 1)
        self.assertEqual(tasks[1].to_show, 0)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("несколько" in m.lower() for m in msgs))
        self.assertTrue(any(str(tasks[0].id) in m for m in msgs))


class TaskDetailViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.owner = User.objects.create_user(username="owner", password="pass12345")
        self.other = User.objects.create_user(username="other", password="pass12345")
        self.task = UserTask.objects.create(
            user=self.owner,
            name="My task",
            status="IN_PROGRESS",
            comment="Note",
        )

    def test_owner_gets_200_and_sees_task_fields(self):
        self.client.login(username="owner", password="pass12345")
        url = reverse("basic_app:task_detail", kwargs={"task_id": self.task.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["task"].id, self.task.id)
        html = response.content.decode()
        self.assertIn(f"#{self.task.id}", html)
        self.assertIn("My task", html)
        self.assertIn("В работе", html)
        self.assertIn("Средний", html)
        self.assertIn("Note", html)

    def test_other_user_gets_404(self):
        self.client.login(username="other", password="pass12345")
        url = reverse("basic_app:task_detail", kwargs={"task_id": self.task.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 404)

    def test_anonymous_user_redirects_to_login(self):
        url = reverse("basic_app:task_detail", kwargs={"task_id": self.task.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 302)
        self.assertTrue(response.url.startswith(settings.LOGIN_URL))

    def test_team_member_can_view_team_task_detail(self):
        team = TaskTeam.objects.create(name="T1")
        member = User.objects.create_user(username="member", password="pass12345")
        TaskTeamMembership.objects.create(team=team, user=member)
        team_task = UserTask.objects.create(
            user=self.owner,
            name="Team t",
            status="TODO",
            team=team,
        )
        self.client.login(username="member", password="pass12345")
        url = reverse("basic_app:task_detail", kwargs={"task_id": team_task.id})
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertEqual(response.context["task"].id, team_task.id)


class TaskTeamModelTests(TestCase):
    def test_kanban_column_xor_requires_user_or_team(self):
        user = User.objects.create_user(username="xor", password="pass12345")
        team = TaskTeam.objects.create(name="TX")
        col = KanbanColumnDefinition.objects.create(
            user=user, team=None, key="K", label="L", sort_order=1, is_builtin=False
        )
        col.team = team
        with self.assertRaises(ValidationError):
            col.full_clean()

    def test_assignee_must_be_team_member(self):
        owner = User.objects.create_user(username="o", password="pass12345")
        outsider = User.objects.create_user(username="out", password="pass12345")
        team = TaskTeam.objects.create(name="T")
        TaskTeamMembership.objects.create(team=team, user=owner)
        t = UserTask(user=owner, team=team, name="x", assignee=outsider)
        with self.assertRaises(ValidationError):
            t.full_clean()


class TeamBoardViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="u", password="pass12345")
        self.client.login(username="u", password="pass12345")
        self.team = TaskTeam.objects.create(name="Alpha")
        TaskTeamMembership.objects.create(team=self.team, user=self.user)

    def test_invalid_team_param_redirects_with_message(self):
        response = self.client.get(
            reverse("basic_app:user_tasks_view") + "?team=99999",
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("не найден" in m.lower() for m in msgs))

    def test_non_member_redirects_from_team_board(self):
        other = User.objects.create_user(username="nm", password="pass12345")
        self.client.login(username="nm", password="pass12345")
        response = self.client.get(
            reverse("basic_app:user_tasks_view") + f"?team={self.team.id}",
            follow=True,
        )
        self.assertEqual(response.status_code, 200)
        msgs = [str(m) for m in get_messages(response.wsgi_request)]
        self.assertTrue(any("доступ" in m.lower() for m in msgs))


class TaskTeamAdminPermissionTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.staff = User.objects.create_user(username="staff", password="p", is_staff=True)
        self.superuser = User.objects.create_superuser(
            username="su", password="p", email="su@example.com"
        )

    def test_staff_cannot_see_task_team_in_admin(self):
        self.client.login(username="staff", password="p")
        url = reverse("admin:index")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)
        self.assertNotContains(response, "Task teams")

    def test_superuser_sees_task_team_admin(self):
        self.client.login(username="su", password="p")
        url = reverse("admin:basic_app_taskteam_changelist")
        response = self.client.get(url)
        self.assertEqual(response.status_code, 200)


class TaskTeamAdminDeleteTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.superuser = User.objects.create_superuser(
            username="admin", password="pass12345", email="admin@example.com"
        )
        self.member = User.objects.create_user(username="member", password="pass12345")

    def _login_superuser(self) -> None:
        self.client.force_login(self.superuser)

    def _task_team_delete_url(self, pk: int) -> str:
        return reverse("admin:basic_app_taskteam_delete", args=[pk])

    def test_admin_delete_team_with_builtin_columns_only_succeeds(self):
        team = TaskTeam.objects.create(name="TeamBuiltins")
        ensure_kanban_builtins_for_team(team)
        self.assertEqual(KanbanColumnDefinition.objects.filter(team=team).count(), 4)
        self._login_superuser()
        response = self.client.post(self._task_team_delete_url(team.pk), {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TaskTeam.objects.filter(pk=team.pk).exists())
        self.assertEqual(KanbanColumnDefinition.objects.filter(team_id=team.pk).count(), 0)

    def test_admin_delete_team_with_tasks_referencing_columns_succeeds_and_tasks_survive(self):
        team = TaskTeam.objects.create(name="TeamWithTasks")
        TaskTeamMembership.objects.create(team=team, user=self.member)
        ensure_kanban_builtins_for_team(team)
        task = UserTask.objects.create(
            user=self.member, name="team task", status="TODO", team=team
        )
        self._login_superuser()
        response = self.client.post(self._task_team_delete_url(team.pk), {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TaskTeam.objects.filter(pk=team.pk).exists())
        task.refresh_from_db()
        self.assertIsNone(task.team_id)
        self.assertEqual(task.status, "TODO")

    def test_admin_delete_team_leaves_other_team_kanban_columns_intact(self):
        team_a = TaskTeam.objects.create(name="TeamA")
        team_b = TaskTeam.objects.create(name="TeamB")
        ensure_kanban_builtins_for_team(team_a)
        ensure_kanban_builtins_for_team(team_b)
        b_keys = set(
            KanbanColumnDefinition.objects.filter(team=team_b).values_list("key", flat=True)
        )
        self._login_superuser()
        response = self.client.post(self._task_team_delete_url(team_a.pk), {"post": "yes"})
        self.assertEqual(response.status_code, 302)
        self.assertFalse(TaskTeam.objects.filter(pk=team_a.pk).exists())
        self.assertTrue(TaskTeam.objects.filter(pk=team_b.pk).exists())
        b_keys_after = set(
            KanbanColumnDefinition.objects.filter(team=team_b).values_list("key", flat=True)
        )
        self.assertEqual(b_keys_after, b_keys)

    def test_bulk_delete_selected_teams_succeeds(self):
        t1 = TaskTeam.objects.create(name="BulkOne")
        t2 = TaskTeam.objects.create(name="BulkTwo")
        ensure_kanban_builtins_for_team(t1)
        ensure_kanban_builtins_for_team(t2)
        changelist = reverse("admin:basic_app_taskteam_changelist")
        self._login_superuser()
        confirm = self.client.post(
            changelist,
            {
                "action": "delete_selected",
                "select_across": "0",
                "_selected_action": [str(t1.pk), str(t2.pk)],
                "index": "0",
            },
        )
        self.assertEqual(confirm.status_code, 200)
        deleted = self.client.post(
            changelist,
            {
                "action": "delete_selected",
                "select_across": "0",
                "_selected_action": [str(t1.pk), str(t2.pk)],
                "post": "yes",
                "index": "0",
            },
        )
        self.assertEqual(deleted.status_code, 302)
        self.assertFalse(TaskTeam.objects.filter(pk__in=[t1.pk, t2.pk]).exists())
