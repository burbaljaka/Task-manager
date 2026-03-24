# Data migration: Russian labels for built-in Kanban columns (matches DEFAULT_BUILTIN_LABELS in kanban_utils).

from django.db import migrations


# Duplicated strings intentionally — avoid importing app code from migrations.
_RU_LABELS = {
    "TODO": "К выполнению",
    "IN_PROGRESS": "В работе",
    "COMPLETED": "Готово",
    "CANCELLED": "Отменено",
}


def forwards(apps, schema_editor):
    KanbanColumnDefinition = apps.get_model("basic_app", "KanbanColumnDefinition")
    for key, label in _RU_LABELS.items():
        KanbanColumnDefinition.objects.filter(key=key, is_builtin=True).update(label=label)


def backwards(apps, schema_editor):
    """Rollback not required for UI localization; no-op."""
    pass


class Migration(migrations.Migration):

    dependencies = [
        ("basic_app", "0005_usertask_kanban_position"),
    ]

    operations = [
        migrations.RunPython(forwards, backwards),
    ]
