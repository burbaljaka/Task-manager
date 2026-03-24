from collections import Counter, defaultdict
from collections.abc import Iterator
import json
import re
from django.contrib import messages
from django.core.exceptions import ValidationError as DjangoValidationError
from django.shortcuts import get_object_or_404, redirect, render
from basic_app.forms import UserForm, UserProfileInfoForm, UserTaskForm, StartTaskForm, StopTaskForm, ReturnTaskForm
from basic_app.schemas import KanbanColumnReorderBody, KanbanTaskReorderBody
from basic_app.kanban_utils import (
    BUILTIN_KEYS,
    ensure_kanban_builtins,
    derive_key_from_label,
    next_custom_sort_order,
)
from .models import KanbanColumnDefinition, UserTask, PartTask
from django.utils.timezone import localtime, now
# Extra Imports for the Login and Logout Capabilities
from django.contrib.auth import authenticate, login, logout
from django.db import transaction
from django.db.models import Max
from django.http import HttpResponseRedirect, HttpResponse, JsonResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
from django.views.decorators.http import require_POST
from pydantic import ValidationError
from django.db.models import Prefetch
import time
from threading import Timer
import datetime
from django.utils import timezone
from dateutil import relativedelta as monthdelta
from calendar import monthrange
import requests


def iter_all_tasks_in_tree(parent_tasks: list) -> Iterator[UserTask]:
    """Depth-first traversal of task hierarchy; yields each node once."""
    for task in parent_tasks:
        yield task
        subtasks = getattr(task, "subtasks_list", None) or []
        if subtasks:
            yield from iter_all_tasks_in_tree(subtasks)


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


def gather_kanban_board_inputs(user_id: int) -> tuple[list, list[UserTask], list[UserTask]]:
    parent_tasks = get_task_hierarchy(user_id)
    tree_ids = {t.id for t in iter_all_tasks_in_tree(parent_tasks)}
    non_completed_flat = list(
        UserTask.objects.filter(user_id=user_id, to_show=1)
        .exclude(status="COMPLETED")
        .select_related("parent_task")
    )
    orphans: list[UserTask] = []
    for task in non_completed_flat:
        if task.id not in tree_ids:
            task.subtasks_list = []
            orphans.append(task)
    completed_for_done = list(
        UserTask.objects.filter(
            user_id=user_id,
            to_show=1,
            status="COMPLETED",
        ).select_related("parent_task")
    )
    return parent_tasks, orphans, completed_for_done


def expected_task_ids_by_status_for_user(user_id: int) -> dict[str, list[int]]:
    parent_tasks, orphans, completed_for_done = gather_kanban_board_inputs(user_id)
    kanban_columns = build_kanban_columns_for_user(
        user_id,
        parent_tasks,
        orphans,
        completed_for_done,
    )
    out: dict[str, list[int]] = {}
    for col in kanban_columns:
        if col["status"] == "CANCELLED":
            continue
        out[col["status"]] = [t.id for t in col["tasks"]]
    return out


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


def next_kanban_position_for_user_status(user_id: int, status: str) -> int:
    agg = (
        UserTask.objects.filter(user_id=user_id, to_show=1, status=status).aggregate(
            m=Max("kanban_position")
        )
    ).get("m")
    return 0 if agg is None else int(agg) + 1


def build_kanban_columns_for_user(
    user_id: int,
    parent_tasks: list,
    orphans: list[UserTask],
    completed_for_done: list[UserTask],
) -> list[dict]:
    """DB-driven column order + orphan columns before CANCELLED; COMPLETED column uses done bucket only."""
    defs = list(
        KanbanColumnDefinition.objects.filter(user_id=user_id).order_by("sort_order", "key")
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


def index(request):
    return render(request, 'basic_app/index.html')


@login_required
def special(request):
    # Remember to also set login url in settings.py!
    # LOGIN_URL = '/basic_app/user_login/'
    return HttpResponse("You are logged in. Nice!")


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
                return HttpResponse("Your account is not active.")
        else:
            print("Someone tried to login and failed.")
            print("They used username: {} and password: {}".format(username, password))
            return HttpResponse("Invalid login details supplied.")

    else:
        # Nothing has been provided for username or password.
        return render(request, 'basic_app/login.html', {})


def update_overdue_priorities(user_id):
    """Check and update priorities for overdue tasks"""
    overdue_tasks = UserTask.objects.filter(
        user_id=user_id,
        to_show=1,
        due_date__lt=datetime.date.today()
    )
    updated_count = 0
    for task in overdue_tasks:
        if task.update_priority_if_overdue():
            updated_count += 1
    return updated_count


def get_task_hierarchy(user_id, exclude_completed=True):
    """Organize tasks into hierarchy with parent tasks and subtasks"""
    # Get all tasks for user, excluding completed tasks by default
    query = UserTask.objects.filter(
        user_id=user_id,
        to_show=1
    )
    if exclude_completed:
        query = query.exclude(status='COMPLETED')
    
    all_tasks = list(query.select_related('parent_task'))
    
    # Create a dictionary to map task IDs to task objects for quick lookup
    task_dict = {task.id: task for task in all_tasks}
    
    # Initialize subtasks_list for all tasks
    for task in all_tasks:
        task.subtasks_list = []
    
    # Separate parent tasks (no parent_task) and organize subtasks
    parent_tasks = []
    for task in all_tasks:
        # Check if task has a parent using parent_task_id (more reliable than parent_task object)
        parent_id = getattr(task, 'parent_task_id', None)
        if parent_id is None:
            # No parent, this is a top-level task
            parent_tasks.append(task)
        else:
            # This is a subtask, add it to its parent's subtasks_list
            # Only add if parent is in task_dict AND exclude_completed filter allows it
            if parent_id in task_dict:
                # Double-check: if exclude_completed is True, make sure subtask is not completed
                if exclude_completed and task.status == 'COMPLETED':
                    continue  # Skip completed subtasks
                parent_task = task_dict[parent_id]
                parent_task.subtasks_list.append(task)
            else:
                # Parent not found in task_dict (shouldn't happen, but handle gracefully)
                # This might be a data inconsistency - log or handle as needed
                pass
    
    return parent_tasks


@login_required
def user_tasks_view(request):
    current_user_id = request.user.id
    ensure_kanban_builtins(request.user)

    # Update overdue priorities
    update_overdue_priorities(current_user_id)
    
    # Get tasks with subtasks organized in hierarchy
    parent_tasks = get_task_hierarchy(current_user_id)
    
    # Handle running timers
    parttasks = PartTask.objects.filter(datetime_stop='0001-01-01 00:00:00')
    all_tasks_flat = UserTask.objects.filter(user_id=current_user_id, to_show=1)
    if len(parttasks) > 0:
        for parttask in parttasks:
            for task in all_tasks_flat:
                if parttask.usertask_id == task.id:
                    task.timer = round((datetime.datetime.now() - parttask.datetime_start).total_seconds())

    if request.method == "POST" and 'start_button' in request.POST:
        form = StartTaskForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            ident = form.cleaned_data['id']
            date_start = datetime.date.today()
            time_start = datetime.datetime.now().time()
            datetime_start = timezone.now()
            parttask = PartTask(usertask_id=ident, user_id=current_user_id,
                                time_start=time_start, date_start=date_start, datetime_start=datetime_start)
            parttask.save()

            running_task = UserTask.objects.filter(user_id=current_user_id, is_counting=1)
            if len(running_task) > 0:
                running_parttask = PartTask.objects.get(pk=running_task[0].partnumber)
                running_parttask.date_stop = datetime.date.today()
                running_parttask.time_stop = datetime.datetime.now().time()
                running_parttask.datetime_stop = timezone.now()
                running_parttask.time_length = (
                        running_parttask.datetime_stop - running_parttask.datetime_start).total_seconds()
                running_parttask.save()

                running_task[0].timer += running_parttask.time_length
                running_task[0].is_counting = 0
                running_task[0].save()

            usertask = UserTask.objects.get(pk=ident)
            usertask.partnumber = parttask.pk
            usertask.is_counting = 1
            usertask.save()
        else:
            print(form)

    elif request.method == "POST" and 'stop_button' in request.POST:
        form = StopTaskForm(request.POST)
        if form.is_valid():
            partnumber = form.cleaned_data['partnumber']
            parttask = PartTask.objects.get(pk=partnumber)
            parttask.date_stop = datetime.date.today()
            parttask.time_stop = datetime.datetime.now().time()
            parttask.datetime_stop = timezone.now()
            parttask.time_length = (parttask.datetime_stop - parttask.datetime_start).total_seconds()
            parttask.save()

            usertask = UserTask.objects.get(pk=parttask.usertask_id)
            usertask.timer += parttask.time_length
            usertask.is_counting = 0
            usertask.save()
        else:
            print(form)

    elif request.method == "POST":
        form = UserTaskForm(request.POST, user=request.user)
        # Set user on form instance before validation to avoid RelatedObjectDoesNotExist error
        if not form.instance.pk:  # New task
            form.instance.user = request.user
        if form.is_valid():
            ident = form.cleaned_data.get('id', 0)
            to_delete = form.cleaned_data.get('fordelete', 'No')
            
            if ident and ident != 0:
                # Update existing task
                try:
                    userform = UserTask.objects.get(pk=ident, user=request.user)
                    if to_delete == "Yes":
                        userform.to_show = 0
                        userform.save()
                    else:
                        # Update all fields
                        userform.name = form.cleaned_data['name']
                        userform.timer = form.cleaned_data['timer']
                        userform.status = form.cleaned_data.get('status', 'TODO')
                        userform.due_date = form.cleaned_data.get('due_date')
                        userform.priority = form.cleaned_data.get('priority', 2)
                        userform.comment = form.cleaned_data.get('comment', '')
                        userform.parent_task = form.cleaned_data.get('parent_task')
                        userform.save()
                except UserTask.DoesNotExist:
                    pass  # Task doesn't exist, ignore
            else:
                # Create new task - only if name is provided
                task_name = form.cleaned_data.get('name', '').strip()
                if task_name:  # Only create if name is not empty
                    parent_task = form.cleaned_data.get('parent_task')
                
                # Validate parent_task belongs to same user if provided
                if parent_task:
                    # Refresh parent_task from database to ensure it has all attributes
                    try:
                        parent_task = UserTask.objects.get(pk=parent_task.pk)
                        if parent_task.user != request.user:
                            form.add_error('parent_task', 'Parent task must belong to the same user.')
                            parent_task = None  # Don't use invalid parent
                    except UserTask.DoesNotExist:
                        form.add_error('parent_task', 'Parent task does not exist.')
                        parent_task = None
                
                if not form.errors:
                    new_status = form.cleaned_data.get('status', 'TODO')
                    userform = UserTask(
                        name=form.cleaned_data['name'],
                        user=request.user,
                        timer=form.cleaned_data.get('timer', 0),
                        status=new_status,
                        due_date=form.cleaned_data.get('due_date'),
                        priority=form.cleaned_data.get('priority', 2),
                        comment=form.cleaned_data.get('comment', ''),
                        parent_task=parent_task,
                        kanban_position=next_kanban_position_for_user_status(
                            request.user.id, new_status
                        ),
                    )
                    # Save - model's save() will call full_clean() for validation
                    userform.save()
        else:
            print('Form errors:', form.errors)
            print('POST data:', request.POST)
            print('Form data:', form.data)

    # Rebuild hierarchy after potential changes (excluding completed tasks)
    parent_tasks = get_task_hierarchy(current_user_id, exclude_completed=True)
    
    # Get completed tasks from the last week
    today = datetime.date.today()
    week_ago = today - datetime.timedelta(days=7)
    # Get all completed tasks directly from database (not using hierarchy to avoid filtering issues)
    completed_tasks_query = UserTask.objects.filter(
        user_id=current_user_id,
        to_show=1,
        status='COMPLETED'
    ).select_related('parent_task')
    
    # Filter to only completed tasks from last week
    completed_tasks_last_week = []
    for task in completed_tasks_query:
        # Check if completion_date exists and is within last week
        if task.completion_date and task.completion_date >= week_ago:
            # Only include top-level tasks (no parent) or include all if needed
            if not task.parent_task_id:
                completed_tasks_last_week.append(task)
    
    # Also include completed subtasks if their parent is not completed (orphaned completed subtasks)
    # This ensures we show completed subtasks even if parent is not completed
    for task in completed_tasks_query:
        if task.completion_date and task.completion_date >= week_ago:
            if task.parent_task_id:
                # Check if parent is also completed - if not, include this subtask
                try:
                    parent = UserTask.objects.get(pk=task.parent_task_id)
                    if parent.status != 'COMPLETED':
                        completed_tasks_last_week.append(task)
                except UserTask.DoesNotExist:
                    pass
    
    # Calculate today's time for all tasks (including subtasks)
    all_tasks_flat = UserTask.objects.filter(user_id=current_user_id, to_show=1).exclude(status='COMPLETED')
    
    def add_today_time_and_helpers(task):
        """Add today's time and helper properties for template, including subtask aggregation"""
        # Calculate this task's own time (not including subtasks yet)
        today_parts = PartTask.objects.filter(usertask_id=task.id, user_id=current_user_id, date_start=today)
        task.today_seconds = sum([p.time_length for p in today_parts])
        task.total_timer = task.timer  # Start with task's own timer
        
        # Add running time if task is currently counting
        if task.is_counting and task.partnumber:
            try:
                # Use partnumber to get the specific running PartTask
                running_part = PartTask.objects.get(pk=task.partnumber)
                # Verify it's still running (datetime_stop is default value or null)
                if running_part.datetime_stop.year == 1 or running_part.datetime_stop.year < 2000:
                    running_time = (datetime.datetime.now() - running_part.datetime_start).total_seconds()
                    task.today_seconds += int(running_time)
                    # Add running time to total timer for display
                    task.total_timer += int(running_time)
            except (PartTask.DoesNotExist, AttributeError):
                pass
        
        # Process subtasks recursively (bottom-up approach)
        subtask_total_time = 0
        subtask_today_time = 0
        if hasattr(task, 'subtasks_list') and task.subtasks_list:
            for subtask in task.subtasks_list:
                add_today_time_and_helpers(subtask)
                # Accumulate subtask times into parent
                subtask_total_time += getattr(subtask, 'total_timer', subtask.timer)
                subtask_today_time += getattr(subtask, 'today_seconds', 0)
        
        # Add subtask times to parent task's totals
        task.total_timer += subtask_total_time
        task.today_seconds += subtask_today_time
        
        # Add due date helper properties
        if task.due_date:
            days_diff = (task.due_date - today).days
            if days_diff < 0:
                task.due_date_class = 'due-date-overdue'
                task.due_date_text = f"{task.due_date.strftime('%b %d')} ({abs(days_diff)}d ago)"
            elif days_diff == 0:
                task.due_date_class = 'due-date-today'
                task.due_date_text = "Today"
            elif days_diff <= 3:
                task.due_date_class = 'due-date-soon'
                task.due_date_text = f"{days_diff}d left"
            else:
                task.due_date_class = 'due-date-normal'
                task.due_date_text = task.due_date.strftime('%b %d')
        else:
            task.due_date_class = 'due-date-none'
            task.due_date_text = None
    
    for task in parent_tasks:
        add_today_time_and_helpers(task)

    tree_ids = {t.id for t in iter_all_tasks_in_tree(parent_tasks)}
    non_completed_flat = list(
        UserTask.objects.filter(user_id=current_user_id, to_show=1)
        .exclude(status="COMPLETED")
        .select_related("parent_task")
    )
    orphans: list[UserTask] = []
    for task in non_completed_flat:
        if task.id not in tree_ids:
            task.subtasks_list = []
            add_today_time_and_helpers(task)
            orphans.append(task)

    completed_for_done = list(
        UserTask.objects.filter(
            user_id=current_user_id,
            to_show=1,
            status="COMPLETED",
        ).select_related("parent_task")
    )
    for task in completed_for_done:
        task.subtasks_list = []
        add_today_time_and_helpers(task)

    kanban_columns = build_kanban_columns_for_user(
        current_user_id,
        parent_tasks,
        orphans,
        completed_for_done,
    )

    def_keys_non_cancel = set(
        KanbanColumnDefinition.objects.filter(user_id=current_user_id)
        .exclude(key="CANCELLED")
        .values_list("key", flat=True)
    )
    for col in kanban_columns:
        col["is_reorderable"] = col["status"] in def_keys_non_cancel

    initial_reorderable_column_keys = list(
        KanbanColumnDefinition.objects.filter(user_id=current_user_id)
        .exclude(key="CANCELLED")
        .order_by("sort_order", "key")
        .values_list("key", flat=True)
    )

    status_options = [
        {"key": d.key, "label": d.label}
        for d in KanbanColumnDefinition.objects.filter(user_id=current_user_id).order_by(
            "sort_order", "key"
        )
    ]

    # Process completed tasks for display (add time helpers)
    for task in completed_tasks_last_week:
        add_today_time_and_helpers(task)

    context = {
        "usertasks": [],
        "kanban_columns": kanban_columns,
        "completed_tasks": completed_tasks_last_week,
        "counter": len(all_tasks_flat),
        "today": today,
        "status_options": status_options,
        "builtin_status_keys": BUILTIN_KEYS,
        "initial_reorderable_column_keys": initial_reorderable_column_keys,
    }
    return render(request, "basic_app/tasks.html", context)


@login_required
def kanban_column_create(request):
    if request.method != "POST":
        return redirect("basic_app:user_tasks_view")
    ensure_kanban_builtins(request.user)
    label = (request.POST.get("label") or "").strip()
    if not label:
        messages.error(request, "Label is required.")
        return redirect("basic_app:user_tasks_view")
    raw_key = (request.POST.get("key") or "").strip()
    if raw_key:
        key = raw_key.upper().replace(" ", "_")
        key = re.sub(r"[^A-Z0-9_]", "_", key)
        key = re.sub(r"_+", "_", key).strip("_")[:64]
        if not key:
            messages.error(request, "Invalid key.")
            return redirect("basic_app:user_tasks_view")
    else:
        key = derive_key_from_label(label, request.user)
    if key in BUILTIN_KEYS:
        messages.error(request, "This key is reserved for a built-in column.")
        return redirect("basic_app:user_tasks_view")
    if KanbanColumnDefinition.objects.filter(user=request.user, key=key).exists():
        messages.error(request, "A column with this key already exists.")
        return redirect("basic_app:user_tasks_view")
    KanbanColumnDefinition.objects.create(
        user=request.user,
        key=key,
        label=label,
        sort_order=next_custom_sort_order(request.user),
        is_builtin=False,
    )
    messages.success(request, "Column added.")
    return redirect("basic_app:user_tasks_view")


@login_required
@require_POST
def kanban_column_reorder(request):
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return JsonResponse(
            {"ok": False, "error": "Content-Type must be application/json"},
            status=400,
        )
    try:
        body = KanbanColumnReorderBody.model_validate_json(request.body)
    except (ValidationError, json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "Invalid request body"},
            status=400,
        )

    user = request.user
    expected_keys = sorted(
        KanbanColumnDefinition.objects.filter(user=user)
        .exclude(key="CANCELLED")
        .values_list("key", flat=True)
    )
    received = list(body.column_keys)
    if len(received) != len(set(received)) or sorted(received) != expected_keys:
        return JsonResponse(
            {
                "ok": False,
                "error": "Column keys do not match the current board configuration.",
                "code": "columnKeysOutOfSync",
            },
            status=400,
        )

    with transaction.atomic():
        for index, key in enumerate(received):
            KanbanColumnDefinition.objects.filter(user=user, key=key).update(
                sort_order=index
            )

    return JsonResponse({"ok": True})


@login_required
@require_POST
def kanban_task_reorder(request):
    content_type = (request.content_type or "").split(";")[0].strip().lower()
    if content_type != "application/json":
        return JsonResponse(
            {"ok": False, "error": "Content-Type must be application/json"},
            status=400,
        )
    try:
        body = KanbanTaskReorderBody.model_validate_json(request.body)
    except (ValidationError, json.JSONDecodeError, ValueError):
        return JsonResponse(
            {"ok": False, "error": "Invalid request body"},
            status=400,
        )

    ensure_kanban_builtins(request.user)
    user_id = request.user.id
    expected = expected_task_ids_by_status_for_user(user_id)
    received = body.task_ids_by_status
    if not task_snapshot_matches_expected(expected, received):
        return JsonResponse(
            {
                "ok": False,
                "error": "Task ids do not match the current board state.",
                "code": "taskIdsOutOfSync",
            },
            status=400,
        )

    try:
        with transaction.atomic():
            for status_key, ids in received.items():
                for pos, tid in enumerate(ids):
                    task = UserTask.objects.select_for_update().get(pk=tid, user_id=user_id)
                    task.status = status_key
                    task.kanban_position = pos
                    task.save()
    except DjangoValidationError:
        return JsonResponse(
            {
                "ok": False,
                "error": "Invalid status for one or more tasks.",
                "code": "invalidStatusTransition",
            },
            status=400,
        )

    return JsonResponse({"ok": True})


@login_required
def task_detail_view(request, task_id: int):
    task = get_object_or_404(UserTask, pk=task_id, user=request.user)
    return render(request, "basic_app/task_detail.html", {"task": task})


def reports(request):
    current_user_id = request.user.id

    if request.method == 'GET':
        if request.GET.get('period') == 'this_day':
            parttasks = PartTask.objects.filter(user_id=current_user_id, date_start=datetime.date.today(), )
            period = 'This day'

        elif request.GET.get('period') == 'last_day':
            parttasks = PartTask.objects.filter(user_id=current_user_id,
                                                date_start=datetime.date.today() - datetime.timedelta(days=1))
            period = 'Last day'

        elif request.GET.get('period') == '15_days':
            parttasks = PartTask.objects.filter(user_id=current_user_id, date_start__range=(
                datetime.date.today() - datetime.timedelta(days=15), datetime.date.today()))
            period = 'Last 15 days'

        elif request.GET.get('period') == 'this_month':
            parttasks = PartTask.objects.filter(user_id=current_user_id,
                                                date_start__range=(datetime.date.today() - datetime.timedelta(
                                                    days=(datetime.date.today().day - 1)), datetime.date.today()))
            period = 'This month'

        elif request.GET.get('period') == 'last_month':
            date_minus_month = datetime.date.today() - monthdelta.relativedelta(months=+ 1)
            month_length = monthrange(date_minus_month.year, date_minus_month.month)[1]
            parttasks = PartTask.objects.filter(user_id=current_user_id, date_start__range=(
                date_minus_month.replace(day=1), date_minus_month.replace(day=month_length)))
            period = 'Last month'

        else:
            parttasks = PartTask.objects.none()
            period = 'No period selected'

        show_all = request.GET.get('show', 'active') == 'all'
        usertasks = UserTask.objects.filter(user_id=current_user_id)
        if not show_all:
            usertasks = usertasks.filter(to_show=1)
        for usertask in usertasks:
            usertask.timer = 0
            for parttask in parttasks:
                if parttask.usertask_id == usertask.id:
                    usertask.timer += parttask.time_length

        context = {
            'usertasks': usertasks,
            'period': period,
            'counter': len(usertasks),
            'show_all': show_all
        }

        return render(request, 'basic_app/report_page.html', context)

    elif request.method == 'POST':
        print(request)
        form = ReturnTaskForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            to_show = form.cleaned_data['to_show']
            usertask = UserTask.objects.get(name=name, user_id=current_user_id)
            usertask.to_show = to_show
            usertask.save()

            parttasks = PartTask.objects.filter(user_id=current_user_id, date_start=datetime.date.today())
            period = 'This day'

            usertasks = UserTask.objects.filter(user_id=current_user_id)
            for usertask in usertasks:
                usertask.timer = 0
                for parttask in parttasks:
                    if parttask.usertask_id == usertask.id:
                        usertask.timer += parttask.time_length

            context = {
                'usertasks': usertasks,
                'period': period,
                'counter': len(usertasks)
            }

        return render(request, 'basic_app/report_page.html', context)


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
