from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.db.models import Q
from basic_app.kanban_board import user_can_access_task
from basic_app.models import KanbanColumnDefinition, UserProfileInfo, UserTask, PartTask, TaskTeamMembership
from datetime import date

class UserForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control form-control-lg',
        'placeholder': 'Введите пароль',
        'style': 'border-radius: 0 12px 12px 0; border: 2px solid #e3f0ff; border-left: none; padding: 0.75rem 1rem; transition: all 0.3s ease;'
    }))
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': 'form-control form-control-lg',
        'placeholder': 'Введите имя пользователя',
        'autofocus': True,
        'style': 'border-radius: 0 12px 12px 0; border: 2px solid #e3f0ff; border-left: none; padding: 0.75rem 1rem; transition: all 0.3s ease;'
    }))
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'class': 'form-control form-control-lg',
        'placeholder': 'Введите email',
        'style': 'border-radius: 0 12px 12px 0; border: 2px solid #e3f0ff; border-left: none; padding: 0.75rem 1rem; transition: all 0.3s ease;'
    }))

    class Meta():
        model = User
        fields = ('username','email','password')


class UserProfileInfoForm(forms.ModelForm):
    portfolio_site = forms.URLField(required=False, widget=forms.URLInput(attrs={
        'class': 'form-control form-control-lg',
        'placeholder': 'https://example.com',
        'style': 'border-radius: 0 12px 12px 0; border: 2px solid #e3f0ff; border-left: none; padding: 0.75rem 1rem; transition: all 0.3s ease;'
    }))
    profile_pic = forms.ImageField(required=False, widget=forms.FileInput(attrs={
        'class': 'form-control form-control-lg',
        'accept': 'image/*',
        'style': 'border-radius: 0 12px 12px 0; border: 2px solid #e3f0ff; border-left: none; padding: 0.75rem 1rem; transition: all 0.3s ease;'
    }))

    class Meta():
        model = UserProfileInfo
        fields = ('portfolio_site','profile_pic')

class UserTaskForm(forms.ModelForm):
    id = forms.IntegerField(required=False)
    fordelete = forms.CharField(required=False)
    name = forms.CharField(required=False, max_length=30)  # Make name optional, we'll validate in clean()
    parent_task = forms.ModelChoiceField(
        queryset=UserTask.objects.none(),
        required=False,
        empty_label="Нет (корневая задача)"
    )
    assignee = forms.ModelChoiceField(
        queryset=User.objects.none(),
        required=False,
        empty_label="Без исполнителя",
    )

    class Meta():
        model = UserTask
        fields = ('name', 'timer', 'status', 'due_date', 'priority', 'comment', 'parent_task', 'assignee')
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'comment': forms.Textarea(attrs={'rows': 3, 'class': 'form-control form-control-sm'}),
            'status': forms.Select(attrs={'class': 'form-control form-control-sm'}),
            'priority': forms.NumberInput(attrs={'min': 1, 'max': 4, 'class': 'form-control form-control-sm'}),
        }

    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        task_id = kwargs.pop('task_id', None)
        team_id = kwargs.pop('team_id', None)
        super().__init__(*args, **kwargs)
        self._user = user
        self._team_id = team_id

        if team_id:
            cols = KanbanColumnDefinition.objects.filter(team_id=team_id).order_by(
                "sort_order", "key"
            )
            member_ids = list(
                TaskTeamMembership.objects.filter(team_id=team_id).values_list("user_id", flat=True)
            )
            self.fields["assignee"].queryset = User.objects.filter(id__in=member_ids).order_by(
                "username"
            )
        else:
            self.fields["assignee"].widget = forms.HiddenInput()
            self.fields["assignee"].required = False
            self.fields["assignee"].queryset = User.objects.none()

        if user:
            if team_id:
                cols = KanbanColumnDefinition.objects.filter(team_id=team_id).order_by(
                    "sort_order", "key"
                )
            else:
                cols = KanbanColumnDefinition.objects.filter(user=user).order_by(
                    "sort_order", "key"
                )
            self.fields["status"].choices = [(c.key, c.label) for c in cols]

        if user:
            if team_id:
                parent_queryset = UserTask.objects.filter(
                    team_id=team_id,
                    to_show=1,
                ).filter(
                    Q(assignee_id__isnull=True)
                    | Q(assignee_id__in=TaskTeamMembership.objects.filter(team_id=team_id).values_list("user_id", flat=True))
                )
            else:
                parent_queryset = UserTask.objects.filter(user=user, team__isnull=True, to_show=1)
            if task_id:
                parent_queryset = parent_queryset.exclude(id=task_id)
                task = UserTask.objects.filter(id=task_id).first()
                if task:
                    subtask_ids = list(task.get_subtasks().values_list('id', flat=True))
                    if subtask_ids:
                        parent_queryset = parent_queryset.exclude(id__in=subtask_ids)
            self.fields['parent_task'].queryset = parent_queryset
            self.fields["parent_task"].widget.attrs.setdefault("class", "form-select")

    def clean(self):
        cleaned_data = super().clean()
        parent_task = cleaned_data.get('parent_task')
        task_id = cleaned_data.get('id')

        if self._team_id and self._user:
            assignee = cleaned_data.get("assignee")
            if assignee and not TaskTeamMembership.objects.filter(
                team_id=self._team_id, user_id=assignee.pk
            ).exists():
                raise ValidationError({'assignee': 'Исполнитель должен быть участником команды.'})

        name = cleaned_data.get('name', '').strip() if cleaned_data.get('name') else ''
        if (not task_id or task_id == 0) and not name:
            raise ValidationError({'name': 'Укажите название задачи.'})

        if parent_task and task_id:
            try:
                current_task = UserTask.objects.get(id=task_id)

                original_parent_id = getattr(current_task, 'parent_task_id', None)
                new_parent_id = parent_task.id if parent_task else None

                if original_parent_id != new_parent_id:
                    if self._team_id:
                        if parent_task.team_id != self._team_id:
                            raise ValidationError('Родительская задача должна относиться к той же команде.')
                    else:
                        if parent_task.user != current_task.user or parent_task.team_id:
                            raise ValidationError('Родительская задача должна принадлежать тому же пользователю.')

                    if not parent_task.can_be_parent_of(current_task):
                        raise ValidationError('Обнаружена циклическая ссылка. Задача не может быть своим предком.')
            except UserTask.DoesNotExist:
                pass

        return cleaned_data

    def clean_priority(self):
        priority = self.cleaned_data.get('priority')
        if priority and (priority < 1 or priority > 4):
            raise ValidationError('Приоритет должен быть от 1 до 4.')
        return priority

    def clean_status(self):
        status = self.cleaned_data.get("status")
        user = getattr(self, "_user", None)
        if not user or not status:
            return status
        task_id = self.cleaned_data.get("id")
        team_id = getattr(self, "_team_id", None)
        if task_id:
            try:
                inst = UserTask.objects.get(pk=task_id)
                if not user_can_access_task(user, inst):
                    return status
            except UserTask.DoesNotExist:
                return status
        else:
            inst = UserTask(user=user)
            if team_id:
                inst.team_id = team_id
        if status not in inst.allowed_status_keys():
            raise ValidationError("Недопустимый статус для этой доски.")
        return status


class StartTaskForm(forms.ModelForm):
    name = forms.CharField()
    id = forms.IntegerField()
    class Meta():
        model = UserTask
        fields = ('name', 'timer')


class StopTaskForm(forms.ModelForm):
    partnumber = forms.IntegerField()
    class Meta():
        model = UserTask
        fields = ('timer', )

# class ReportForm(forms.ModelForm):
#     period = forms.CharField()

class ReturnTaskForm(forms.ModelForm):

    class Meta():
        model = UserTask
        fields = ('name', 'to_show')
