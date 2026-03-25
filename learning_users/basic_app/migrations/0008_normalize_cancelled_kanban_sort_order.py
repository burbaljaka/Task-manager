# Data migration: CANCELLED no longer uses sort_order sentinel 9999.

from django.db import migrations
from django.db.models import Max


def normalize_cancelled_sort_orders(apps, schema_editor):
    KanbanColumnDefinition = apps.get_model("basic_app", "KanbanColumnDefinition")

    cancelled_personal = KanbanColumnDefinition.objects.filter(
        key="CANCELLED",
        sort_order=9999,
        user__isnull=False,
        team__isnull=True,
    )
    for row in cancelled_personal:
        max_other = (
            KanbanColumnDefinition.objects.filter(user_id=row.user_id, team__isnull=True)
            .exclude(key="CANCELLED")
            .aggregate(m=Max("sort_order"))
            .get("m")
        )
        new_so = 0 if max_other is None else int(max_other) + 1
        KanbanColumnDefinition.objects.filter(pk=row.pk).update(sort_order=new_so)

    cancelled_team = KanbanColumnDefinition.objects.filter(
        key="CANCELLED",
        sort_order=9999,
        team__isnull=False,
    )
    for row in cancelled_team:
        max_other = (
            KanbanColumnDefinition.objects.filter(team_id=row.team_id)
            .exclude(key="CANCELLED")
            .aggregate(m=Max("sort_order"))
            .get("m")
        )
        new_so = 0 if max_other is None else int(max_other) + 1
        KanbanColumnDefinition.objects.filter(pk=row.pk).update(sort_order=new_so)


def noop_reverse(apps, schema_editor):
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("basic_app", "0007_teams_kanban_team_scope"),
    ]

    operations = [
        migrations.RunPython(normalize_cancelled_sort_orders, noop_reverse),
    ]
