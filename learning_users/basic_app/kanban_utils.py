"""Kanban column helpers: built-in seeds, key derivation, and ordering rules."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User

    from basic_app.models import TaskTeam

# Built-in status keys (ordering for seeds / tests).
BUILTIN_ORDER_KEYS: tuple[str, ...] = (
    "TODO",
    "IN_PROGRESS",
    "COMPLETED",
    "CANCELLED",
)

# Default Russian labels for seeding and fallbacks.
DEFAULT_BUILTIN_LABELS: dict[str, str] = {
    "TODO": "К выполнению",
    "IN_PROGRESS": "В работе",
    "COMPLETED": "Готово",
    "CANCELLED": "Отменено",
}

BUILTIN_KEYS = frozenset(BUILTIN_ORDER_KEYS)

# sort_order for built-in column seeds (sequential; CANCELLED is last by default, not a sentinel).
SORT_TODO = 0
SORT_IN_PROGRESS = 1
SORT_COMPLETED = 2
SORT_CANCELLED = 3


def status_css_slug(value: str) -> str:
    """Match JS statusCssSlug: lower, non-alphanumeric -> hyphen, collapse, trim."""
    s = (value or "").lower()
    s = re.sub(r"[^a-z0-9]+", "-", s)
    s = re.sub(r"-+", "-", s).strip("-")
    return s or "unknown"


def derive_key_from_label(label: str, user: User) -> str:
    """UPPER_SNAKE from label; non-alphanumeric -> _; collapse; unique per user."""
    from basic_app.models import KanbanColumnDefinition

    raw = (label or "").strip().upper()
    raw = re.sub(r"[^A-Z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw:
        raw = "COLUMN"
    base = raw[:64]
    key = base
    n = 2
    while KanbanColumnDefinition.objects.filter(user=user, key=key).exists():
        suffix = f"_{n}"
        key = (base[: 64 - len(suffix)] + suffix)[:64]
        n += 1
    return key


def derive_key_from_label_for_team(label: str, team: TaskTeam) -> str:
    """UPPER_SNAKE from label; unique per team board."""
    from basic_app.models import KanbanColumnDefinition

    raw = (label or "").strip().upper()
    raw = re.sub(r"[^A-Z0-9]+", "_", raw)
    raw = re.sub(r"_+", "_", raw).strip("_")
    if not raw:
        raw = "COLUMN"
    base = raw[:64]
    key = base
    n = 2
    while KanbanColumnDefinition.objects.filter(team=team, key=key).exists():
        suffix = f"_{n}"
        key = (base[: 64 - len(suffix)] + suffix)[:64]
        n += 1
    return key


def ensure_kanban_builtins(user: User) -> None:
    """Idempotent: create four built-in column definitions for the user."""
    from basic_app.models import KanbanColumnDefinition

    seeds: tuple[tuple[str, str, int], ...] = (
        ("TODO", DEFAULT_BUILTIN_LABELS["TODO"], SORT_TODO),
        ("IN_PROGRESS", DEFAULT_BUILTIN_LABELS["IN_PROGRESS"], SORT_IN_PROGRESS),
        ("COMPLETED", DEFAULT_BUILTIN_LABELS["COMPLETED"], SORT_COMPLETED),
        ("CANCELLED", DEFAULT_BUILTIN_LABELS["CANCELLED"], SORT_CANCELLED),
    )
    for key, label, sort_order in seeds:
        KanbanColumnDefinition.objects.get_or_create(
            user=user,
            key=key,
            defaults={
                "label": label,
                "sort_order": sort_order,
                "is_builtin": True,
            },
        )


def ensure_kanban_builtins_for_team(team: TaskTeam) -> None:
    """Idempotent: create four built-in column definitions for a team board."""
    from basic_app.models import KanbanColumnDefinition

    seeds: tuple[tuple[str, str, int], ...] = (
        ("TODO", DEFAULT_BUILTIN_LABELS["TODO"], SORT_TODO),
        ("IN_PROGRESS", DEFAULT_BUILTIN_LABELS["IN_PROGRESS"], SORT_IN_PROGRESS),
        ("COMPLETED", DEFAULT_BUILTIN_LABELS["COMPLETED"], SORT_COMPLETED),
        ("CANCELLED", DEFAULT_BUILTIN_LABELS["CANCELLED"], SORT_CANCELLED),
    )
    for key, label, sort_order in seeds:
        KanbanColumnDefinition.objects.get_or_create(
            team=team,
            key=key,
            defaults={
                "label": label,
                "sort_order": sort_order,
                "is_builtin": True,
            },
        )


def next_custom_sort_order(user: User) -> int:
    """Next sort_order after the maximum of all columns for this board."""
    from django.db.models import Max

    from basic_app.models import KanbanColumnDefinition

    agg = KanbanColumnDefinition.objects.filter(user=user).aggregate(m=Max("sort_order"))
    m = agg.get("m")
    return 0 if m is None else int(m) + 1


def next_custom_sort_order_for_team(team: TaskTeam) -> int:
    """Next sort_order after the maximum of all columns for this team board."""
    from django.db.models import Max

    from basic_app.models import KanbanColumnDefinition

    agg = KanbanColumnDefinition.objects.filter(team=team).aggregate(m=Max("sort_order"))
    m = agg.get("m")
    return 0 if m is None else int(m) + 1
