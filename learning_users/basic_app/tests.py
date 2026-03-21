import datetime

from django.conf import settings
from django.contrib.auth.models import User
from django.test import Client, TestCase
from django.urls import reverse

from basic_app.models import UserTask
from basic_app.views import (
    KANBAN_COLUMN_SPECS,
    iter_all_tasks_in_tree,
    sort_tasks_for_kanban_column,
)


class KanbanHelpersTests(TestCase):
    def test_kanban_column_specs_order_and_length(self):
        statuses = [s for s, _ in KANBAN_COLUMN_SPECS]
        self.assertEqual(
            statuses,
            ["TODO", "IN_PROGRESS", "COMPLETED", "CANCELLED"],
        )
        self.assertEqual(len(KANBAN_COLUMN_SPECS), 4)

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


class UserTasksKanbanViewTests(TestCase):
    def setUp(self):
        self.client = Client()
        self.user = User.objects.create_user(username="viewer", password="pass12345")
        self.client.login(username="viewer", password="pass12345")

    def test_user_tasks_view_has_kanban_columns_shape(self):
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        self.assertEqual(response.status_code, 200)
        kanban_columns = response.context["kanban_columns"]
        self.assertEqual(len(kanban_columns), 4)
        expected_statuses = [s for s, _ in KANBAN_COLUMN_SPECS]
        for i, col in enumerate(kanban_columns):
            self.assertEqual(col["status"], expected_statuses[i])
            self.assertIn("label", col)
            self.assertIn("tasks", col)
            self.assertIsInstance(col["tasks"], list)

    def test_usertasks_empty_for_board(self):
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        self.assertEqual(response.context["usertasks"], [])

    def test_tasks_bucketed_by_status(self):
        UserTask.objects.create(user=self.user, name="t1", status="TODO")
        UserTask.objects.create(user=self.user, name="t2", status="IN_PROGRESS")
        UserTask.objects.create(user=self.user, name="t3", status="CANCELLED")
        UserTask.objects.create(
            user=self.user, name="t4", status="COMPLETED", completion_date=datetime.date.today()
        )
        response = self.client.get(reverse("basic_app:user_tasks_view"))
        cols = {c["status"]: c["tasks"] for c in response.context["kanban_columns"]}
        self.assertEqual(len(cols["TODO"]), 1)
        self.assertEqual(len(cols["IN_PROGRESS"]), 1)
        self.assertEqual(len(cols["CANCELLED"]), 1)
        self.assertEqual(len(cols["COMPLETED"]), 1)

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
        self.assertIn("IN_PROGRESS", html)
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
