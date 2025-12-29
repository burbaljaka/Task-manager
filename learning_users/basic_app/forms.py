from django import forms
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from basic_app.models import UserProfileInfo, UserTask, PartTask
from datetime import date

class UserForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput(attrs={
        'class': 'form-control form-control-lg',
        'placeholder': 'Enter your password',
        'style': 'border-radius: 0 12px 12px 0; border: 2px solid #e3f0ff; border-left: none; padding: 0.75rem 1rem; transition: all 0.3s ease;'
    }))
    username = forms.CharField(widget=forms.TextInput(attrs={
        'class': 'form-control form-control-lg',
        'placeholder': 'Enter your username',
        'autofocus': True,
        'style': 'border-radius: 0 12px 12px 0; border: 2px solid #e3f0ff; border-left: none; padding: 0.75rem 1rem; transition: all 0.3s ease;'
    }))
    email = forms.EmailField(widget=forms.EmailInput(attrs={
        'class': 'form-control form-control-lg',
        'placeholder': 'Enter your email',
        'style': 'border-radius: 0 12px 12px 0; border: 2px solid #e3f0ff; border-left: none; padding: 0.75rem 1rem; transition: all 0.3s ease;'
    }))

    class Meta():
        model = User
        fields = ('username','email','password')


class UserProfileInfoForm(forms.ModelForm):
    portfolio_site = forms.URLField(required=False, widget=forms.URLInput(attrs={
        'class': 'form-control form-control-lg',
        'placeholder': 'https://yourportfolio.com',
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
    parent_task = forms.ModelChoiceField(
        queryset=UserTask.objects.none(),
        required=False,
        empty_label="None (Top-level task)"
    )
    
    class Meta():
        model = UserTask
        fields = ('name', 'timer', 'status', 'due_date', 'priority', 'comment', 'parent_task')
        widgets = {
            'due_date': forms.DateInput(attrs={'type': 'date', 'class': 'form-control form-control-sm'}),
            'comment': forms.Textarea(attrs={'rows': 3, 'class': 'form-control form-control-sm'}),
            'status': forms.Select(attrs={'class': 'form-control form-control-sm'}),
            'priority': forms.NumberInput(attrs={'min': 1, 'max': 4, 'class': 'form-control form-control-sm'}),
        }
    
    def __init__(self, *args, **kwargs):
        user = kwargs.pop('user', None)
        task_id = kwargs.pop('task_id', None)
        super().__init__(*args, **kwargs)
        
        # Limit parent_task queryset to user's tasks, excluding self and subtasks
        if user:
            parent_queryset = UserTask.objects.filter(user=user, to_show=1)
            if task_id:
                # Exclude self and its subtasks to prevent circular references
                parent_queryset = parent_queryset.exclude(id=task_id)
                task = UserTask.objects.filter(id=task_id).first()
                if task:
                    # Exclude all subtasks of current task
                    subtask_ids = list(task.get_subtasks().values_list('id', flat=True))
                    if subtask_ids:
                        parent_queryset = parent_queryset.exclude(id__in=subtask_ids)
            self.fields['parent_task'].queryset = parent_queryset
    
    def clean(self):
        cleaned_data = super().clean()
        parent_task = cleaned_data.get('parent_task')
        task_id = cleaned_data.get('id')
        
        if parent_task and task_id:
            # Get the task instance if it exists
            try:
                current_task = UserTask.objects.get(id=task_id)
                
                # Check if parent_task has actually changed
                original_parent_id = getattr(current_task, 'parent_task_id', None)
                new_parent_id = parent_task.id if parent_task else None
                
                # Only validate if parent_task has changed
                if original_parent_id != new_parent_id:
                    # Ensure parent_task belongs to same user
                    if parent_task.user != current_task.user:
                        raise ValidationError('Parent task must belong to the same user.')
                    
                    # Prevent circular references (only if parent_task changed)
                    if not parent_task.can_be_parent_of(current_task):
                        raise ValidationError('Circular reference detected. A task cannot be its own ancestor.')
            except UserTask.DoesNotExist:
                pass  # New task, validation will happen in model clean()
        
        return cleaned_data
    
    def clean_priority(self):
        priority = self.cleaned_data.get('priority')
        if priority and (priority < 1 or priority > 4):
            raise ValidationError('Priority must be between 1 and 4.')
        return priority

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
