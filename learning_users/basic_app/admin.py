from django import forms
from django.contrib import admin
from django.core.exceptions import PermissionDenied, ValidationError

from basic_app.kanban_utils import ensure_kanban_builtins_for_team
from basic_app.models import (
    KanbanColumnDefinition,
    PartTask,
    TaskTeam,
    TaskTeamMembership,
    UserProfileInfo,
    UserTask,
)


class SuperuserOnlyAdminMixin:
    def has_module_permission(self, request):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.is_superuser

    def has_add_permission(self, request):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.is_superuser

    def has_change_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.is_superuser

    def has_delete_permission(self, request, obj=None):
        if not request.user or not request.user.is_authenticated:
            return False
        return request.user.is_superuser

    def has_view_permission(self, request, obj=None):
        return self.has_change_permission(request, obj)


@admin.register(KanbanColumnDefinition)
class KanbanColumnDefinitionAdmin(admin.ModelAdmin):
    list_display = ("user", "team", "key", "label", "sort_order", "is_builtin")
    list_filter = ("is_builtin",)
    search_fields = ("key", "label", "user__username", "team__name")

    @staticmethod
    def _can_delete_column(obj: KanbanColumnDefinition) -> bool:
        if obj.is_builtin:
            return False
        if obj.user_id:
            return not UserTask.objects.filter(user=obj.user, status=obj.key, team__isnull=True).exists()
        if obj.team_id:
            return not UserTask.objects.filter(team=obj.team, status=obj.key).exists()
        return False

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
        tid = getattr(self.instance, "team_id", None)
        if tid:
            cols = list(
                KanbanColumnDefinition.objects.filter(team_id=tid).order_by(
                    "sort_order", "key"
                )
            )
        elif uid:
            cols = list(
                KanbanColumnDefinition.objects.filter(user_id=uid).order_by(
                    "sort_order", "key"
                )
            )
        else:
            cols = []
        if cols:
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
            if getattr(self.instance, "team_id", None):
                msg = "Недопустимый статус для колонок этой командной доски."
            else:
                msg = "Недопустимый статус для колонок личной доски пользователя."
            raise ValidationError(msg)
        return status


@admin.register(UserTask)
class UserTaskAdmin(admin.ModelAdmin):
    form = UserTaskAdminForm


class TaskTeamMembershipInline(admin.TabularInline):
    model = TaskTeamMembership
    extra = 0
    autocomplete_fields = ("user",)


@admin.register(TaskTeam)
class TaskTeamAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("name", "slug", "created_at")
    search_fields = ("name", "slug", "description")
    inlines = (TaskTeamMembershipInline,)

    def save_model(self, request, obj, form, change):
        super().save_model(request, obj, form, change)
        ensure_kanban_builtins_for_team(obj)


@admin.register(TaskTeamMembership)
class TaskTeamMembershipAdmin(SuperuserOnlyAdminMixin, admin.ModelAdmin):
    list_display = ("team", "user")
    autocomplete_fields = ("user",)
    search_fields = ("team__name", "user__username")


admin.site.register(UserProfileInfo)
admin.site.register(PartTask)
