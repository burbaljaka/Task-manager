from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError

from basic_app.models import KanbanColumnDefinition, PartTask, UserProfileInfo, UserTask


@admin.register(KanbanColumnDefinition)
class KanbanColumnDefinitionAdmin(admin.ModelAdmin):
    list_display = ("user", "key", "label", "sort_order", "is_builtin")
    list_filter = ("is_builtin",)
    search_fields = ("key", "label", "user__username")

    @staticmethod
    def _can_delete_column(obj: KanbanColumnDefinition) -> bool:
        if obj.is_builtin:
            return False
        return not UserTask.objects.filter(user=obj.user, status=obj.key).exists()

    def has_delete_permission(self, request, obj=None):
        if not super().has_delete_permission(request, obj):
            return False
        if obj is None:
            return True
        return self._can_delete_column(obj)

    def delete_model(self, request, obj):
        if not self._can_delete_column(obj):
            raise PermissionDenied(
                "Built-in columns cannot be deleted, and columns still referenced "
                "by tasks cannot be removed."
            )
        super().delete_model(request, obj)

    def delete_queryset(self, request, queryset):
        blocked = [obj for obj in queryset if not self._can_delete_column(obj)]
        if blocked:
            raise PermissionDenied(
                "Built-in columns cannot be deleted, and columns still referenced "
                "by tasks cannot be removed."
            )
        super().delete_queryset(request, queryset)


class UserTaskAdminForm(forms.ModelForm):
    class Meta:
        model = UserTask
        fields = "__all__"

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        uid = getattr(self.instance, "user_id", None)
        if uid:
            cols = list(
                KanbanColumnDefinition.objects.filter(user_id=uid).order_by(
                    "sort_order", "key"
                )
            )
            def_keys = {c.key for c in cols}
            choices = [(c.key, c.label) for c in cols]
            inst = self.instance
            if inst.pk and inst.status and inst.status not in def_keys:
                choices.append((inst.status, f"[{inst.status}]"))
            self.fields["status"].widget = forms.Select(choices=choices)

    def clean_status(self):
        status = self.cleaned_data.get("status")
        if not status:
            return status
        if self.instance.pk and status not in self.instance.allowed_status_keys():
            raise ValidationError("Invalid status for this user.")
        return status


@admin.register(UserTask)
class UserTaskAdmin(admin.ModelAdmin):
    form = UserTaskAdminForm


admin.site.register(UserProfileInfo)
admin.site.register(PartTask)
