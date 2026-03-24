"""Kanban board scope: personal vs team task queries and helpers."""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterator
import datetime

from django.contrib.auth.models import User
from django.db.models import Max, Q, QuerySet
from django.http import HttpRequest, HttpResponseRedirect

from basic_app.board_scope import BoardScope
from basic_app.models import KanbanColumnDefinition, TaskTeam, TaskTeamMembership, UserTask


def resolve_board_scope(request: HttpRequest) -> tuple[BoardScope, HttpResponseRedirect | None]:
    """Parse ?team= / POST board_team; invalid or inaccessible team → personal scope + redirect."""
    from django.contrib import messages
    from django.shortcuts import redirect
    from django.urls import reverse

    raw = ""
    if request.method == "POST":
        raw = (request.POST.get("board_team") or "").strip()
    if not raw:
        raw = (request.GET.get("team") or "").strip()

    if not raw:
        return BoardScope(user_id=request.user.id, team_id=None), None
    try:
        team_id = int(raw)
    except ValueError:
        messages.warning(request, "Некорректный параметр команды.")
        return BoardScope(user_id=request.user.id, team_id=None), redirect(
            reverse("basic_app:user_tasks_view")
        )
    if not TaskTeam.objects.filter(pk=team_id).exists():
        messages.warning(request, "Команда не найдена.")
        return BoardScope(user_id=request.user.id, team_id=None), redirect(
            reverse("basic_app:user_tasks_view")
        )
    if not TaskTeamMembership.objects.filter(team_id=team_id, user_id=request.user.id).exists():
        messages.warning(request, "У вас нет доступа к этой командной доске.")
        return BoardScope(user_id=request.user.id, team_id=None), redirect(
            reverse("basic_app:user_tasks_view")
        )
    return BoardScope(user_id=request.user.id, team_id=team_id), None


def board_scope_from_request_query(request: HttpRequest) -> BoardScope:
    """Scope from GET ?team= or POST board_team; invalid or inaccessible → personal."""
    raw = ""
    if request.method == "POST":
        raw = (request.POST.get("board_team") or "").strip()
    if not raw:
        raw = (request.GET.get("team") or "").strip()
    if not raw:
        return BoardScope(user_id=request.user.id, team_id=None)
    try:
        team_id = int(raw)
    except ValueError:
        return BoardScope(user_id=request.user.id, team_id=None)
    if not TaskTeam.objects.filter(pk=team_id).exists():
        return BoardScope(user_id=request.user.id, team_id=None)
    if not TaskTeamMembership.objects.filter(team_id=team_id, user_id=request.user.id).exists():
        return BoardScope(user_id=request.user.id, team_id=None)
    return BoardScope(user_id=request.user.id, team_id=team_id)


def team_member_user_ids(team_id: int) -> set[int]:
    return set(
        TaskTeamMembership.objects.filter(team_id=team_id).values_list("user_id", flat=True)
    )


def visible_team_tasks_queryset(team_id: int) -> QuerySet[UserTask]:
    member_ids = team_member_user_ids(team_id)
    return UserTask.objects.filter(team_id=team_id, to_show=1).filter(
        Q(assignee_id__isnull=True) | Q(assignee_id__in=member_ids)
    )


def user_can_access_task(user: User, task: UserTask) -> bool:
    if task.team_id is None:
        return task.user_id == user.id
    return TaskTeamMembership.objects.filter(team_id=task.team_id, user_id=user.id).exists()


def get_task_hierarchy_for_scope(
    scope: BoardScope, exclude_completed: bool = True
) -> list[UserTask]:
    if scope.team_id is None:
        query = UserTask.objects.filter(
            user_id=scope.user_id,
            team__isnull=True,
            to_show=1,
        )
    else:
        query = visible_team_tasks_queryset(scope.team_id)
    if exclude_completed:
        query = query.exclude(status="COMPLETED")

    all_tasks = list(query.select_related("parent_task", "assignee", "user", "team"))

    task_dict = {task.id: task for task in all_tasks}
    for task in all_tasks:
        task.subtasks_list = []

    parent_tasks: list[UserTask] = []
    for task in all_tasks:
        parent_id = getattr(task, "parent_task_id", None)
        if parent_id is None:
            parent_tasks.append(task)
        elif parent_id in task_dict:
            if exclude_completed and task.status == "COMPLETED":
                continue
            parent_task = task_dict[parent_id]
            parent_task.subtasks_list.append(task)
        else:
            # Parent not in this queryset (e.g. completed parent while excluding completed,
            # or parent invisible on a team board). Show as root so the task is not dropped.
            parent_tasks.append(task)

    return parent_tasks


def iter_all_tasks_in_tree(parent_tasks: list) -> Iterator[UserTask]:
    for task in parent_tasks:
        yield task
        subtasks = getattr(task, "subtasks_list", None) or []
        if subtasks:
            yield from iter_all_tasks_in_tree(subtasks)


def gather_kanban_board_inputs(
    scope: BoardScope,
) -> tuple[list, list[UserTask], list[UserTask]]:
    parent_tasks = get_task_hierarchy_for_scope(scope, exclude_completed=True)
    tree_ids = {t.id for t in iter_all_tasks_in_tree(parent_tasks)}

    if scope.team_id is None:
        non_completed_flat = list(
            UserTask.objects.filter(
                user_id=scope.user_id,
                team__isnull=True,
                to_show=1,
            )
            .exclude(status="COMPLETED")
            .select_related("parent_task")
        )
        completed_for_done = list(
            UserTask.objects.filter(
                user_id=scope.user_id,
                team__isnull=True,
                to_show=1,
                status="COMPLETED",
            ).select_related("parent_task")
        )
    else:
        non_completed_flat = list(
            visible_team_tasks_queryset(scope.team_id)
            .exclude(status="COMPLETED")
            .select_related("parent_task")
        )
        completed_for_done = list(
            visible_team_tasks_queryset(scope.team_id)
            .filter(status="COMPLETED")
            .select_related("parent_task")
        )

    orphans: list[UserTask] = []
    for task in non_completed_flat:
        if task.id not in tree_ids:
            task.subtasks_list = []
            orphans.append(task)

    return parent_tasks, orphans, completed_for_done


def build_kanban_columns_for_scope(
    scope: BoardScope,
    parent_tasks: list,
    orphans: list[UserTask],
    completed_for_done: list[UserTask],
) -> list[dict]:
    if scope.team_id is None:
        defs = list(
            KanbanColumnDefinition.objects.filter(user_id=scope.user_id).order_by(
                "sort_order", "key"
            )
        )
    else:
        defs = list(
            KanbanColumnDefinition.objects.filter(team_id=scope.team_id).order_by(
                "sort_order", "key"
            )
        )

    def_keys = {d.key for d in defs}
    non_completed_by_status: defaultdict[str, list[UserTask]] = defaultdict(list)
    for task in list(iter_all_tasks_in_tree(parent_tasks)) + orphans:
        if task.status == "COMPLETED":
            continue
        non_completed_by_status[task.status].append(task)

    statuses_in_tasks = set(non_completed_by_status.keys())
    orphan_keys = sorted(k for k in statuses_in_tasks if k not in def_keys)

    non_cancel = [d for d in defs if d.key != "CANCELLED"]
    cancel_row = next((d for d in defs if d.key == "CANCELLED"), None)

    merged: list[tuple[str, str]] = []
    for d in non_cancel:
        merged.append((d.key, d.label))
    for ok in orphan_keys:
        merged.append((ok, f"[{ok}]"))
    if cancel_row:
        merged.append((cancel_row.key, cancel_row.label))

    kanban_columns: list[dict] = []
    for key, label in merged:
        if key == "COMPLETED":
            column_tasks = sort_tasks_for_kanban_column(completed_for_done)
        else:
            column_tasks = sort_tasks_for_kanban_column(
                non_completed_by_status.get(key, [])
            )
        for t in column_tasks:
            t.subtasks_list = []
        kanban_columns.append({"status": key, "label": label, "tasks": column_tasks})
    return kanban_columns


def expected_task_ids_by_status_for_scope(scope: BoardScope) -> dict[str, list[int]]:
    parent_tasks, orphans, completed_for_done = gather_kanban_board_inputs(scope)
    kanban_columns = build_kanban_columns_for_scope(
        scope, parent_tasks, orphans, completed_for_done
    )
    out: dict[str, list[int]] = {}
    for col in kanban_columns:
        if col["status"] == "CANCELLED":
            continue
        out[col["status"]] = [t.id for t in col["tasks"]]
    return out


def sort_tasks_for_kanban_column(tasks: list[UserTask]) -> list[UserTask]:
    return sorted(
        tasks,
        key=lambda t: (
            t.kanban_position,
            t.due_date is None,
            t.due_date or datetime.date.min,
            t.id,
        ),
    )


def next_kanban_position_for_scope(scope: BoardScope, status: str) -> int:
    if scope.team_id is None:
        qs = UserTask.objects.filter(
            user_id=scope.user_id,
            team__isnull=True,
            to_show=1,
            status=status,
        )
    else:
        qs = visible_team_tasks_queryset(scope.team_id).filter(status=status)
    agg = qs.aggregate(m=Max("kanban_position")).get("m")
    return 0 if agg is None else int(agg) + 1


def update_overdue_priorities_for_scope(scope: BoardScope) -> int:
    today = datetime.date.today()
    if scope.team_id is None:
        overdue_tasks = UserTask.objects.filter(
            user_id=scope.user_id,
            team__isnull=True,
            to_show=1,
            due_date__lt=today,
        )
    else:
        overdue_tasks = visible_team_tasks_queryset(scope.team_id).filter(
            due_date__lt=today,
        )
    updated_count = 0
    for task in overdue_tasks:
        if task.update_priority_if_overdue():
            updated_count += 1
    return updated_count


def flat_tasks_for_timer_and_counter(scope: BoardScope) -> QuerySet[UserTask]:
    if scope.team_id is None:
        return UserTask.objects.filter(
            user_id=scope.user_id,
            team__isnull=True,
            to_show=1,
        )
    return visible_team_tasks_queryset(scope.team_id)
