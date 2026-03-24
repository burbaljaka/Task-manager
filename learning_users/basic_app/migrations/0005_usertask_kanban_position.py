# Generated manually for Kanban task order

import datetime

from django.db import migrations, models


def backfill_kanban_position(apps, schema_editor):
    UserTask = apps.get_model("basic_app", "UserTask")
    min_date = datetime.date.min
    pairs = (
        UserTask.objects.filter(to_show=1)
        .values_list("user_id", "status")
        .distinct()
    )
    for user_id, status in pairs:
        tasks = list(
            UserTask.objects.filter(
                user_id=user_id,
                status=status,
                to_show=1,
            )
        )
        tasks.sort(
            key=lambda t: (
                t.due_date is None,
                t.due_date or min_date,
                t.id,
            )
        )
        for i, t in enumerate(tasks):
            if t.kanban_position != i:
                UserTask.objects.filter(pk=t.pk).update(kanban_position=i)


class Migration(migrations.Migration):

    dependencies = [
        ("basic_app", "0004_kanban_columns_and_status"),
    ]

    operations = [
        migrations.AddField(
            model_name="usertask",
            name="kanban_position",
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddIndex(
            model_name="usertask",
            index=models.Index(
                fields=["user", "status", "kanban_position"],
                name="usertask_user_status_kpos_idx",
            ),
        ),
        migrations.RunPython(backfill_kanban_position, migrations.RunPython.noop),
    ]
