from collections import Counter
import json
import logging
from urllib.parse import urlencode
import re
from django.contrib import messages
from django.core.exceptions import PermissionDenied, ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404, redirect, render
from basic_app.forms import UserForm, UserProfileInfoForm, UserTaskForm, StartTaskForm, StopTaskForm, ReturnTaskForm
from basic_app.schemas import KanbanColumnReorderBody, KanbanTaskReorderBody
from basic_app.board_scope import BoardScope
from basic_app.kanban_board import (
    build_kanban_columns_for_scope,
    expected_task_ids_by_status_for_scope,
    flat_tasks_for_timer_and_counter,
    gather_kanban_board_inputs as gather_kanban_board_inputs_for_scope,
    iter_all_tasks_in_tree,
    next_kanban_position_for_scope,
    resolve_board_scope,
    board_scope_from_request_query,
    update_overdue_priorities_for_scope,
    user_can_access_task,
    visible_team_tasks_queryset,
)
from basic_app.kanban_utils import (
    BUILTIN_KEYS,
    ensure_kanban_builtins,
    ensure_kanban_builtins_for_team,
    derive_key_from_label,
    derive_key_from_label_for_team,
    next_custom_sort_order,
    next_custom_sort_order_for_team,
)
from .models import (
    KanbanColumnDefinition,
    PartTask,
    TaskTeam,
    TaskTeamMembership,
    UserTask,
)
from django.utils.timezone import localtime, now
# Extra Imports for the Login and Logout Capabilities
from django.contrib.auth import authenticate, login, logout
from django.contrib.auth.models import User
from django.db import transaction
from django.db.models import Q
from django.http import Http404, HttpResponseRedirect, HttpResponse, JsonResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from pydantic import ValidationError
import time
from threading import Timer
import datetime
from django.utils import timezone
from dateutil import relativedelta as monthdelta
from calendar import monthrange
import requests

logger = logging.getLogger(__name__)


def gather_kanban_board_inputs(user_id: int) -> tuple[list, list[UserTask], list[UserTask]]:
    """Personal-board helper for tests; delegates to scoped gather."""
    scope = BoardScope(user_id=user_id, team_id=None)
    return gather_kanban_board_inputs_for_scope(scope)


def expected_task_ids_by_status_for_user(user_id: int) -> dict[str, list[int]]:
    return expected_task_ids_by_status_for_scope(BoardScope(user_id=user_id, team_id=None))


def task_snapshot_matches_expected(
    expected: dict[str, list[int]],
    received: dict[str, list[int]],
) -> bool:
    """True if received uses the same column keys and the same multiset of task ids as the current board.

    Per-column lists may differ from the DB when moving tasks between columns; only the union of ids
    (and column key set) must match so we reject stale/foreign ids without blocking valid moves.
    """
    if set(expected.keys()) != set(received.keys()):
        return False
    exp_union: Counter = Counter()
    rec_union: Counter = Counter()
    for k in expected:
        exp_union.update(expected[k])
        rec_union.update(received[k])
    return exp_union == rec_union


def _enrich_task_timer_and_due_helpers(
    task: UserTask, current_user_id: int, today: datetime.date
) -> None:
    """Set today_seconds, total_timer, due_date_* on task; recurse into subtasks_list."""
    today_parts = PartTask.objects.filter(
        usertask_id=task.id, user_id=current_user_id, date_start=today
    )
    task.today_seconds = sum(p.time_length for p in today_parts)
    task.total_timer = task.timer

    if task.is_counting and task.partnumber:
        try:
            running_part = PartTask.objects.get(pk=task.partnumber)
            if running_part.datetime_stop.year == 1 or running_part.datetime_stop.year < 2000:
                running_time = (
                    datetime.datetime.now() - running_part.datetime_start
                ).total_seconds()
                task.today_seconds += int(running_time)
                task.total_timer += int(running_time)
        except (PartTask.DoesNotExist, AttributeError):
            pass

    subtask_total_time = 0
    subtask_today_time = 0
    if hasattr(task, "subtasks_list") and task.subtasks_list:
        for subtask in task.subtasks_list:
            _enrich_task_timer_and_due_helpers(subtask, current_user_id, today)
            subtask_total_time += getattr(subtask, "total_timer", subtask.timer)
            subtask_today_time += getattr(subtask, "today_seconds", 0)

    task.total_timer += subtask_total_time
    task.today_seconds += subtask_today_time

    if task.due_date:
        days_diff = (task.due_date - today).days
        if days_diff < 0:
            task.due_date_class = "due-date-overdue"
            task.due_date_text = (
                f"{task.due_date.strftime('%d.%m.%Y')} ({abs(days_diff)} дн. назад)"
            )
        elif days_diff == 0:
            task.due_date_class = "due-date-today"
            task.due_date_text = "Сегодня"
        elif days_diff <= 3:
            task.due_date_class = "due-date-soon"
            task.due_date_text = f"осталось {days_diff} дн."
        else:
            task.due_date_class = "due-date-normal"
            task.due_date_text = task.due_date.strftime("%d.%m.%Y")
    else:
        task.due_date_class = "due-date-none"
        task.due_date_text = None


def _handle_timer_start(
    request,
    user: User,
    current_user_id: int,
    *,
    expected_task_id: int | None = None,
) -> None:
    form = StartTaskForm(request.POST)
    if not form.is_valid():
        logger.debug("Start task form invalid: %s", form.errors)
        return
    ident = form.cleaned_data["id"]
    if expected_task_id is not None and ident != expected_task_id:
        return
    try:
        usertask = UserTask.objects.get(pk=ident)
    except UserTask.DoesNotExist:
        usertask = None
    if not usertask or not user_can_access_task(user, usertask):
        return
    date_start = datetime.date.today()
    time_start = datetime.datetime.now().time()
    datetime_start = timezone.now()
    parttask = PartTask(
        usertask_id=ident,
        user_id=current_user_id,
        time_start=time_start,
        date_start=date_start,
        datetime_start=datetime_start,
    )
    parttask.save()

    team_ids = TaskTeamMembership.objects.filter(user_id=current_user_id).values_list(
        "team_id", flat=True
    )
    running_task = UserTask.objects.filter(is_counting=1).filter(
        Q(user_id=current_user_id, team__isnull=True) | Q(team_id__in=team_ids)
    )
    if len(running_task) > 0:
        running_parttask = PartTask.objects.get(pk=running_task[0].partnumber)
        running_parttask.date_stop = datetime.date.today()
        running_parttask.time_stop = datetime.datetime.now().time()
        running_parttask.datetime_stop = timezone.now()
        running_parttask.time_length = (
            running_parttask.datetime_stop - running_parttask.datetime_start
        ).total_seconds()
        running_parttask.save()

        running_task[0].timer += running_parttask.time_length
        running_task[0].is_counting = 0
        running_task[0].save()

    usertask.partnumber = parttask.pk
    usertask.is_counting = 1
    usertask.save()


def _handle_timer_stop(
    request,
    user: User,
    *,
    expected_task_id: int | None = None,
) -> None:
    form = StopTaskForm(request.POST)
    if not form.is_valid():
        logger.debug("Stop task form invalid: %s", form.errors)
        return
    partnumber = form.cleaned_data["partnumber"]
    try:
        parttask = PartTask.objects.get(pk=partnumber)
    except PartTask.DoesNotExist:
        logger.debug("Stop timer: PartTask id=%s not found", partnumber)
        return
    try:
        usertask = UserTask.objects.get(pk=parttask.usertask_id)
    except UserTask.DoesNotExist:
        logger.debug(
            "Stop timer: UserTask id=%s not found for PartTask id=%s",
            parttask.usertask_id,
            partnumber,
        )
        return
    if not user_can_access_task(user, usertask):
        return
    if expected_task_id is not None and usertask.id != expected_task_id:
        return
    parttask.date_stop = datetime.date.today()
    parttask.time_stop = datetime.datetime.now().time()
    parttask.datetime_stop = timezone.now()
    parttask.time_length = (
        parttask.datetime_stop - parttask.datetime_start
    ).total_seconds()
    parttask.save()

    usertask.timer += parttask.time_length
    usertask.is_counting = 0
    usertask.save()


def _apply_user_task_form_post(
    request,
    *,
    user: User,
    team_id: int | None,
    fixed_task_id: int | None = None,
) -> tuple[bool, UserTaskForm]:
    form_kwargs: dict = {"user": user, "team_id": team_id}
    if fixed_task_id is not None:
        form_kwargs["task_id"] = fixed_task_id
    form = UserTaskForm(request.POST, **form_kwargs)
    if not form.instance.pk:
        form.instance.user = user
    if not form.is_valid():
        logger.debug("User task form invalid: %s", form.errors)
        return False, form

    ident = form.cleaned_data.get("id", 0)
    to_delete = form.cleaned_data.get("fordelete", "No")

    if fixed_task_id is not None:
        if not ident or ident != fixed_task_id:
            return False, form

    if ident and ident != 0:
        try:
            userform = UserTask.objects.get(pk=ident)
            if not user_can_access_task(user, userform):
                return False, form
            if to_delete == "Yes":
                userform.to_show = 0
                userform.save()
                return True, form
            userform.name = form.cleaned_data["name"]
            userform.timer = form.cleaned_data["timer"]
            userform.status = form.cleaned_data.get("status", "TODO")
            userform.due_date = form.cleaned_data.get("due_date")
            userform.priority = form.cleaned_data.get("priority", 2)
            userform.comment = form.cleaned_data.get("comment", "")
            userform.parent_task = form.cleaned_data.get("parent_task")
            if team_id:
                userform.assignee = form.cleaned_data.get("assignee")
            userform.save()
            return True, form
        except UserTask.DoesNotExist:
            return False, form
    else:
        task_name = form.cleaned_data.get("name", "").strip()
        parent_task = None
        if task_name:
            parent_task = form.cleaned_data.get("parent_task")
            if parent_task:
                try:
                    parent_task = UserTask.objects.get(pk=parent_task.pk)
                    if team_id:
                        if parent_task.team_id != team_id:
                            form.add_error(
                                "parent_task",
                                "Родительская задача должна относиться к этой команде.",
                            )
                            parent_task = None
                    elif parent_task.user != user or parent_task.team_id:
                        form.add_error(
                            "parent_task",
                            "Родительская задача должна принадлежать тому же пользователю.",
                        )
                        parent_task = None
                except UserTask.DoesNotExist:
                    form.add_error("parent_task", "Родительская задача не найдена.")
                    parent_task = None

        if not form.errors:
            new_status = form.cleaned_data.get("status", "TODO")
            userform = UserTask(
                name=form.cleaned_data["name"],
                user=user,
                team_id=team_id,
                assignee=form.cleaned_data.get("assignee") if team_id else None,
                timer=form.cleaned_data.get("timer", 0),
                status=new_status,
                due_date=form.cleaned_data.get("due_date"),
                priority=form.cleaned_data.get("priority", 2),
                comment=form.cleaned_data.get("comment", ""),
                parent_task=parent_task,
                kanban_position=next_kanban_position_for_scope(
                    BoardScope(user_id=user.id, team_id=team_id), new_status
                ),
            )
            userform.save()
            return True, form
        return False, form


def _task_detail_team_query_value(request, task: UserTask) -> str | None:
    if task.team_id is None:
        return None
    raw = (request.POST.get("team") or request.GET.get("team") or "").strip()
    if raw:
        try:
            tid = int(raw)
            if tid == task.team_id and TaskTeamMembership.objects.filter(
                team_id=task.team_id, user_id=request.user.id
            ).exists():
                return str(task.team_id)
        except ValueError:
            pass
    return str(task.team_id)


def _task_detail_redirect(request, task: UserTask):
    url = reverse("basic_app:task_detail", kwargs={"task_id": task.id})
    team_q = _task_detail_team_query_value(request, task)
    if team_q:
        url = f"{url}?{urlencode({'team': team_q})}"
    return redirect(url)


def _task_detail_build_context(
    request,
    task: UserTask,
    *,
    task_form_override: UserTaskForm | None = None,
) -> dict:
    current_user_id = request.user.id
    if task.team_id:
        ensure_kanban_builtins_for_team(TaskTeam.objects.get(pk=task.team_id))
        col_qs = KanbanColumnDefinition.objects.filter(team_id=task.team_id).order_by(
            "sort_order", "key"
        )
    else:
        ensure_kanban_builtins(request.user)
        col_qs = KanbanColumnDefinition.objects.filter(user_id=current_user_id).order_by(
            "sort_order", "key"
        )

    status_options = [{"key": d.key, "label": d.label} for d in col_qs]

    team_member_options: list[dict] = []
    if task.team_id:
        team_member_options = [
            {"id": u.id, "username": u.get_username()}
            for u in User.objects.filter(
                id__in=TaskTeamMembership.objects.filter(team_id=task.team_id).values_list(
                    "user_id", flat=True
                )
            ).order_by("username")
        ]

    today = datetime.date.today()
    subtasks_qs = task.get_subtasks().select_related("parent_task", "assignee", "user")
    task.subtasks_list = list(subtasks_qs)
    for st in task.subtasks_list:
        st.subtasks_list = []
    _enrich_task_timer_and_due_helpers(task, current_user_id, today)

    task_form = task_form_override or UserTaskForm(
        instance=task,
        user=request.user,
        team_id=task.team_id,
        task_id=task.id,
    )

    user_teams = list(
        TaskTeam.objects.filter(
            memberships__user_id=current_user_id,
        ).order_by("name").distinct()
    )

    return {
        "task": task,
        "task_form": task_form,
        "status_options": status_options,
        "team_member_options": team_member_options,
        "is_team_board": task.team_id is not None,
        "board_team_id": task.team_id,
        "builtin_status_keys": BUILTIN_KEYS,
        "priority_choices": UserTask.PRIORITY_CHOICES,
        "team_query": _task_detail_team_query_value(request, task),
        "subtasks": task.subtasks_list,
        "user_teams": user_teams,
    }


def index(request):
    return render(request, 'basic_app/index.html')


@login_required
def special(request):
    # Remember to also set login url in settings.py!
    # LOGIN_URL = '/basic_app/user_login/'
    return HttpResponse("Вы вошли в систему.")


@login_required
def user_logout(request):
    # Log out the user.
    logout(request)
    # Return to homepage.
    return HttpResponseRedirect(reverse('index'))


def register(request):
    registered = False

    if request.method == 'POST':

        # Get info from "both" forms
        # It appears as one form to the user on the .html page
        user_form = UserForm(data=request.POST)
        profile_form = UserProfileInfoForm(data=request.POST)

        # Check to see both forms are valid
        if user_form.is_valid() and profile_form.is_valid():

            # Save User Form to Database
            user = user_form.save()

            # Hash the password
            user.set_password(user.password)

            # Update with Hashed password
            user.save()

            # Now we deal with the extra info!

            # Can't commit yet because we still need to manipulate
            profile = profile_form.save(commit=False)

            # Set One to One relationship between
            # UserForm and UserProfileInfoForm
            profile.user = user

            # Check if they provided a profile picture
            if 'profile_pic' in request.FILES:
                print('found it')
                # If yes, then grab it from the POST form reply
                profile.profile_pic = request.FILES['profile_pic']

            # Now save model
            profile.save()

            # Registration Successful!
            registered = True

        else:
            # One of the forms was invalid if this else gets called.
            print(user_form.errors, profile_form.errors)

    else:
        # Was not an HTTP post so we just render the forms as blank.
        user_form = UserForm()
        profile_form = UserProfileInfoForm()

    # This is the render and context dictionary to feed
    # back to the registration.html file page.
    return render(request, 'basic_app/registration.html',
                  {'user_form': user_form,
                   'profile_form': profile_form,
                   'registered': registered})


def user_login(request):
    if request.method == 'POST':
        # First get the username and password supplied
        username = request.POST.get('username')
        password = request.POST.get('password')

        # Django's built-in authentication function:
        user = authenticate(username=username, password=password)

        # If we have a user
        if user:
            # Check it the account is active
            if user.is_active:
                # Log the user in.
                login(request, user)
                # Send the user back to some page.
                # In this case their homepage.

                return HttpResponseRedirect(reverse('basic_app:user_tasks_view'))
            #                return render(request, 'basic_app/tasks.html', context)
            else:
                # If account is not active:
                return HttpResponse("Учётная запись не активирована.")
        else:
            logger.warning("Неудачная попытка входа для пользователя: %s", username)
            return HttpResponse("Неверные имя пользователя или пароль.")

    else:
        # Nothing has been provided for username or password.
        return render(request, 'basic_app/login.html', {})


@login_required
def user_tasks_view(request):
    scope, redirect_resp = resolve_board_scope(request)
    if redirect_resp:
        return redirect_resp

    current_user_id = request.user.id

    if scope.team_id:
        ensure_kanban_builtins_for_team(TaskTeam.objects.get(pk=scope.team_id))
    else:
        ensure_kanban_builtins(request.user)

    update_overdue_priorities_for_scope(scope)

    parttasks = PartTask.objects.filter(datetime_stop='0001-01-01 00:00:00')
    all_tasks_flat = list(flat_tasks_for_timer_and_counter(scope))
    if len(parttasks) > 0:
        for parttask in parttasks:
            for task in all_tasks_flat:
                if parttask.usertask_id == task.id:
                    task.timer = round((datetime.datetime.now() - parttask.datetime_start).total_seconds())

    if request.method == "POST" and "start_button" in request.POST:
        _handle_timer_start(request, request.user, current_user_id)

    elif request.method == "POST" and "stop_button" in request.POST:
        _handle_timer_stop(request, request.user)

    elif request.method == "POST":
        _apply_user_task_form_post(
            request,
            user=request.user,
            team_id=scope.team_id,
        )

    parent_tasks, orphans, completed_for_done = gather_kanban_board_inputs_for_scope(scope)

    today = datetime.date.today()
    week_ago = today - datetime.timedelta(days=7)
    if scope.team_id is None:
        completed_tasks_query = UserTask.objects.filter(
            user_id=current_user_id,
            team__isnull=True,
            to_show=1,
            status="COMPLETED",
        ).select_related("parent_task")
    else:
        completed_tasks_query = visible_team_tasks_queryset(scope.team_id).filter(
            status="COMPLETED",
        ).select_related("parent_task")

    completed_tasks_last_week = []
    for task in completed_tasks_query:
        if task.completion_date and task.completion_date >= week_ago:
            if not task.parent_task_id:
                completed_tasks_last_week.append(task)

    for task in completed_tasks_query:
        if task.completion_date and task.completion_date >= week_ago:
            if task.parent_task_id:
                try:
                    parent = UserTask.objects.get(pk=task.parent_task_id)
                    if parent.status != "COMPLETED":
                        completed_tasks_last_week.append(task)
                except UserTask.DoesNotExist:
                    pass

    all_tasks_flat = flat_tasks_for_timer_and_counter(scope).exclude(status="COMPLETED")

    for task in parent_tasks:
        _enrich_task_timer_and_due_helpers(task, current_user_id, today)

    for task in orphans:
        _enrich_task_timer_and_due_helpers(task, current_user_id, today)

    for task in completed_for_done:
        _enrich_task_timer_and_due_helpers(task, current_user_id, today)

    kanban_columns = build_kanban_columns_for_scope(
        scope,
        parent_tasks,
        orphans,
        completed_for_done,
    )

    if scope.team_id is None:
        col_qs = KanbanColumnDefinition.objects.filter(user_id=current_user_id)
    else:
        col_qs = KanbanColumnDefinition.objects.filter(team_id=scope.team_id)

    def_keys = set(col_qs.values_list("key", flat=True))
    for col in kanban_columns:
        col["is_reorderable"] = col["status"] in def_keys

    initial_reorderable_column_keys = list(
        col_qs.order_by("sort_order", "key").values_list("key", flat=True)
    )

    status_options = [
        {"key": d.key, "label": d.label}
        for d in col_qs.order_by("sort_order", "key")
    ]

    for task in completed_tasks_last_week:
        _enrich_task_timer_and_due_helpers(task, current_user_id, today)

    user_teams = list(
        TaskTeam.objects.filter(
            memberships__user_id=current_user_id,
        ).order_by("name").distinct()
    )

    team_member_options: list[dict] = []
    if scope.team_id:
        team_member_options = [
            {"id": u.id, "username": u.get_username()}
            for u in User.objects.filter(
                id__in=TaskTeamMembership.objects.filter(team_id=scope.team_id).values_list(
                    "user_id", flat=True
                )
            ).order_by("username")
        ]

    context = {
        "usertasks": [],
        "kanban_columns": kanban_columns,
        "completed_tasks": completed_tasks_last_week,
        "counter": all_tasks_flat.count(),
        "today": today,
        "status_options": status_options,
        "builtin_status_keys": BUILTIN_KEYS,
        "initial_reorderable_column_keys": initial_reorderable_column_keys,
        "board_team_id": scope.team_id,
        "user_teams": user_teams,
        "is_team_board": scope.team_id is not None,
        "team_member_options": team_member_options,
    }
    return render(request, "basic_app/tasks.html", context)


def _tasks_board_redirect(request):
    raw = (request.POST.get("board_team") or request.GET.get("team") or "").strip()
    if raw:
        return redirect(f"{reverse('basic_app:user_tasks_view')}?team={raw}")
    return redirect("basic_app:user_tasks_view")


@login_required
def kanban_column_create(request):
    if request.method != "POST":
        return redirect("basic_app:user_tasks_view")
    scope = board_scope_from_request_query(request)
    if scope.team_id:
        ensure_kanban_builtins_for_team(TaskTeam.objects.get(pk=scope.team_id))
    else:
        ensure_kanban_builtins(request.user)
    label = (request.POST.get("label") or "").strip()
    if not label:
        messages.error(request, "Укажите название колонки.")
        return _tasks_board_redirect(request)
    raw_key = (request.POST.get("key") or "").strip()
    if raw_key:
        key = raw_key.upper().replace(" ", "_")
        key = re.sub(r"[^A-Z0-9_]", "_", key)
        key = re.sub(r"_+", "_", key).strip("_")[:64]
        if not key:
            messages.error(request, "Недопустимый ключ колонки.")
            return _tasks_board_redirect(request)
    else:
        if scope.team_id:
            key = derive_key_from_label_for_team(label, TaskTeam.objects.get(pk=scope.team_id))
        else:
            key = derive_key_from_label(label, request.user)
    if key in BUILTIN_KEYS:
        messages.error(request, "Этот ключ зарезервирован для встроенной колонки.")
        return _tasks_board_redirect(request)
    if scope.team_id:
        team = TaskTeam.objects.get(pk=scope.team_id)
        if KanbanColumnDefinition.objects.filter(team=team, key=key).exists():
            messages.error(request, "Колонка с таким ключом уже существует.")
            return _tasks_board_redirect(request)
        KanbanColumnDefinition.objects.create(
            team=team,
            key=key,
            label=label,
            sort_order=next_custom_sort_order_for_team(team),
            is_builtin=False,
        )
    else:
        if KanbanColumnDefinition.objects.filter(user=request.user, key=key).exists():
            messages.error(request, "Колонка с таким ключом уже существует.")
            return _tasks_board_redirect(request)
        KanbanColumnDefinition.objects.create(
            user=request.user,
            key=key,
            label=label,
            sort_order=next_custom_sort_order(request.user),
            is_builtin=False,
        )
    messages.success(request, "Колонка добавлена.")
    return _tasks_board_redirect(request)


@login_required
@require_POST
def kanban_column_reorder(request):
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return JsonResponse(
            {"ok": False, "error": "Требуется Content-Type: application/json"},
            status=400,
        )
    try:
        body = KanbanColumnReorderBody.model_validate_json(request.body)
    except (ValidationError, json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "Некорректное тело запроса"},
            status=400,
        )

    scope = board_scope_from_request_query(request)
    if scope.team_id:
        ensure_kanban_builtins_for_team(TaskTeam.objects.get(pk=scope.team_id))
    else:
        ensure_kanban_builtins(request.user)

    if scope.team_id:
        col_filter = {"team_id": scope.team_id}
    else:
        col_filter = {"user_id": request.user.id}

    expected_keys = sorted(
        KanbanColumnDefinition.objects.filter(**col_filter).values_list("key", flat=True)
    )
    received = list(body.column_keys)
    if len(received) != len(set(received)) or sorted(received) != expected_keys:
        return JsonResponse(
            {
                "ok": False,
                "error": "Ключи колонок не совпадают с текущей конфигурацией доски.",
                "code": "columnKeysOutOfSync",
            },
            status=400,
        )

    with transaction.atomic():
        for index, key in enumerate(received):
            KanbanColumnDefinition.objects.filter(**col_filter, key=key).update(
                sort_order=index
            )

    return JsonResponse({"ok": True})


@login_required
@require_POST
def kanban_task_reorder(request):
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return JsonResponse(
            {"ok": False, "error": "Требуется Content-Type: application/json"},
            status=400,
        )
    try:
        body = KanbanTaskReorderBody.model_validate_json(request.body)
    except (ValidationError, json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "Некорректное тело запроса"},
            status=400,
        )

    scope = board_scope_from_request_query(request)
    if scope.team_id:
        ensure_kanban_builtins_for_team(TaskTeam.objects.get(pk=scope.team_id))
    else:
        ensure_kanban_builtins(request.user)

    expected = expected_task_ids_by_status_for_scope(scope)
    received = body.task_ids_by_status
    if not task_snapshot_matches_expected(expected, received):
        return JsonResponse(
            {
                "ok": False,
                "error": "Идентификаторы задач не совпадают с текущим состоянием доски.",
                "code": "taskIdsOutOfSync",
            },
            status=400,
        )

    try:
        with transaction.atomic():
            for status_key, ids in received.items():
                for pos, tid in enumerate(ids):
                    task = UserTask.objects.select_for_update().get(pk=tid)
                    if not user_can_access_task(request.user, task):
                        raise PermissionDenied("Нет доступа к этой задаче.")
                    if scope.team_id:
                        if task.team_id != scope.team_id:
                            raise DjangoValidationError(
                                "Задача не относится к выбранной командной доске.",
                                code="boardScopeMismatch",
                            )
                    else:
                        if task.team_id is not None or task.user_id != request.user.id:
                            raise DjangoValidationError(
                                "Задача не относится к выбранной личной доске.",
                                code="boardScopeMismatch",
                            )
                    task.status = status_key
                    task.kanban_position = pos
                    task.save()
    except PermissionDenied as e:
        return JsonResponse(
            {
                "ok": False,
                "error": str(e) or "Нет доступа к этой задаче.",
                "code": "accessDenied",
            },
            status=403,
        )
    except DjangoValidationError as e:
        if getattr(e, "code", None) == "boardScopeMismatch":
            return JsonResponse(
                {
                    "ok": False,
                    "error": e.messages[0] if e.messages else "Задача не относится к выбранной доске.",
                    "code": "boardScopeMismatch",
                },
                status=400,
            )
        err_msg = "Недопустимый статус для одной или нескольких задач."
        if hasattr(e, "error_dict") and e.error_dict:
            for msgs in e.error_dict.values():
                if msgs:
                    err_msg = str(msgs[0])
                    break
        elif e.messages:
            err_msg = str(e.messages[0])
        return JsonResponse(
            {
                "ok": False,
                "error": err_msg,
                "code": "invalidStatusTransition",
            },
            status=400,
        )

    return JsonResponse({"ok": True})


@login_required
def reports(request):
    current_user_id = request.user.id

    if request.method == "GET":
        if request.GET.get("period") == "this_day":
            parttasks = PartTask.objects.filter(
                user_id=current_user_id, date_start=datetime.date.today()
            )
            period = "Сегодня"

        elif request.GET.get("period") == "last_day":
            parttasks = PartTask.objects.filter(
                user_id=current_user_id,
                date_start=datetime.date.today() - datetime.timedelta(days=1),
            )
            period = "Вчера"

        elif request.GET.get("period") == "15_days":
            parttasks = PartTask.objects.filter(
                user_id=current_user_id,
                date_start__range=(
                    datetime.date.today() - datetime.timedelta(days=15),
                    datetime.date.today(),
                ),
            )
            period = "Последние 15 дней"

        elif request.GET.get("period") == "this_month":
            parttasks = PartTask.objects.filter(
                user_id=current_user_id,
                date_start__range=(
                    datetime.date.today()
                    - datetime.timedelta(days=(datetime.date.today().day - 1)),
                    datetime.date.today(),
                ),
            )
            period = "Текущий месяц"

        elif request.GET.get("period") == "last_month":
            date_minus_month = datetime.date.today() - monthdelta.relativedelta(months=+1)
            month_length = monthrange(date_minus_month.year, date_minus_month.month)[1]
            parttasks = PartTask.objects.filter(
                user_id=current_user_id,
                date_start__range=(
                    date_minus_month.replace(day=1),
                    date_minus_month.replace(day=month_length),
                ),
            )
            period = "Прошлый месяц"

        else:
            parttasks = PartTask.objects.none()
            period = "Период не выбран"

        show_all = request.GET.get("show", "active") == "all"
        usertasks = UserTask.objects.filter(user_id=current_user_id)
        if not show_all:
            usertasks = usertasks.filter(to_show=1)
        for usertask in usertasks:
            usertask.timer = 0
            for parttask in parttasks:
                if parttask.usertask_id == usertask.id:
                    usertask.timer += parttask.time_length

        context = {
            "usertasks": usertasks,
            "period": period,
            "counter": len(usertasks),
            "show_all": show_all,
        }

        return render(request, "basic_app/report_page.html", context)

    elif request.method == "POST":
        form = ReturnTaskForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data["name"]
            to_show = form.cleaned_data["to_show"]
            matching_tasks = list(
                UserTask.objects.filter(name=name, user_id=current_user_id).order_by(
                    "id"
                )
            )
            if not matching_tasks:
                messages.error(
                    request,
                    "Задача с таким названием не найдена.",
                )
                return redirect(reverse("basic_app:reports"))

            target_task = matching_tasks[0]
            if len(matching_tasks) > 1:
                messages.warning(
                    request,
                    "Найдено несколько задач с одинаковым названием. "
                    f"Обновлена задача №{target_task.id}.",
                )

            target_task.to_show = to_show
            target_task.save()

            parttasks = PartTask.objects.filter(
                user_id=current_user_id, date_start=datetime.date.today()
            )
            period = "Сегодня"

            usertasks = UserTask.objects.filter(user_id=current_user_id)
            for usertask in usertasks:
                usertask.timer = 0
                for parttask in parttasks:
                    if parttask.usertask_id == usertask.id:
                        usertask.timer += parttask.time_length

            context = {
                "usertasks": usertasks,
                "period": period,
                "counter": len(usertasks),
                "show_all": False,
            }

            return render(request, "basic_app/report_page.html", context)

        messages.error(
            request,
            "Не удалось обработать форму. Проверьте введённые данные.",
        )
        return redirect(reverse("basic_app:reports"))


@login_required
def task_detail_view(request, task_id: int):
    task = get_object_or_404(
        UserTask.objects.select_related("user", "team", "assignee", "parent_task"),
        pk=task_id,
    )
    if not user_can_access_task(request.user, task):
        raise Http404()

    if request.method == "POST" and "start_button" in request.POST:
        _handle_timer_start(
            request, request.user, request.user.id, expected_task_id=task_id
        )
        return _task_detail_redirect(request, task)

    if request.method == "POST" and "stop_button" in request.POST:
        _handle_timer_stop(request, request.user, expected_task_id=task_id)
        return _task_detail_redirect(request, task)

    if request.method == "POST":
        success, bound_form = _apply_user_task_form_post(
            request,
            user=request.user,
            team_id=task.team_id,
            fixed_task_id=task_id,
        )
        if success:
            return _task_detail_redirect(request, task)
        context = _task_detail_build_context(
            request, task, task_form_override=bound_form
        )
        return render(request, "basic_app/task_detail.html", context)

    context = _task_detail_build_context(request, task)
    return render(request, "basic_app/task_detail.html", context)


def base(request):
    link_to_site = 'https://community-open-weather-map.p.rapidapi.com/weather'
    headers = {
        'X-RapidAPI-Host': 'community-open-weather-map.p.rapidapi.com',
        'X-RapidAPI-Key': '5aabc22e25msh45d6df4abdd28d0p1a4479jsn10ea7369318e'
    }
    parameters = {
        'q': 'Ryazan,ru',
        'units': 'metric',
    }
    weather = requests.get(link_to_site, headers=headers, params=parameters)
    current_weather = weather.json()
    context = {
        'city_weather': current_weather['main']['temp']
    }
    return render(request, 'basic_app/base.html', context)
