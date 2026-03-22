"""Kanban column helpers: built-in seeds, key derivation, and ordering rules."""

from __future__ import annotations

import re
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from django.contrib.auth.models import User

# Built-in status keys (ordering for seeds / tests).
BUILTIN_ORDER_KEYS: tuple[str, ...] = (
    "TODO",
    "IN_PROGRESS",
    "COMPLETED",
    "CANCELLED",
)

# Default English labels for seeding and fallbacks.
DEFAULT_BUILTIN_LABELS: dict[str, str] = {
    "TODO": "To Do",
    "IN_PROGRESS": "In Progress",
    "COMPLETED": "Done",
    "CANCELLED": "Cancelled",
}

BUILTIN_KEYS = frozenset(BUILTIN_ORDER_KEYS)

# sort_order for built-ins: CANCELLED is sentinel last.
SORT_TODO = 0
SORT_IN_PROGRESS = 1
SORT_COMPLETED = 2
SORT_CANCELLED_SENTINEL = 9999


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


def ensure_kanban_builtins(user: User) -> None:
    """Idempotent: create four built-in column definitions for the user."""
    from basic_app.models import KanbanColumnDefinition

    seeds: tuple[tuple[str, str, int], ...] = (
        ("TODO", DEFAULT_BUILTIN_LABELS["TODO"], SORT_TODO),
        ("IN_PROGRESS", DEFAULT_BUILTIN_LABELS["IN_PROGRESS"], SORT_IN_PROGRESS),
        ("COMPLETED", DEFAULT_BUILTIN_LABELS["COMPLETED"], SORT_COMPLETED),
        ("CANCELLED", DEFAULT_BUILTIN_LABELS["CANCELLED"], SORT_CANCELLED_SENTINEL),
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


def next_custom_sort_order(user: User) -> int:
    """Insert custom columns in 3..9998 before CANCELLED (9999)."""
    from basic_app.models import KanbanColumnDefinition

    qs = KanbanColumnDefinition.objects.filter(user=user, sort_order__lt=SORT_CANCELLED_SENTINEL)
    current = qs.order_by("-sort_order").values_list("sort_order", flat=True).first()
    if current is None:
        return 3
    nxt = int(current) + 1
    if nxt >= SORT_CANCELLED_SENTINEL:
        return SORT_CANCELLED_SENTINEL - 1
    return max(nxt, 3)
