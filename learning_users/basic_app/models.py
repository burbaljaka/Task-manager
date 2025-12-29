from django.db import models
from django.contrib.auth.models import User
from django.core.exceptions import ValidationError
from django.utils import timezone
from datetime import date


# Create your models here.
class UserProfileInfo(models.Model):
    # Create relationship (don't inherit from User!)
    user = models.OneToOneField(User, on_delete=models.DO_NOTHING)

    # Add any additional attributes you want
    portfolio_site = models.URLField(blank=True)
    # pip install pillow to use this!
    # Optional: pip install pillow --global-option="build_ext" --global-option="--disable-jpeg"
    profile_pic = models.ImageField(upload_to='profile_pics', blank=True)

    def __str__(self):
        # Built-in attribute of django.contrib.auth.models.User !
        return self.user.username


class UserTask(models.Model):
    STATUS_CHOICES = [
        ('TODO', 'TODO'),
        ('IN_PROGRESS', 'IN_PROGRESS'),
        ('COMPLETED', 'COMPLETED'),
        ('CANCELLED', 'CANCELLED'),
    ]
    
    PRIORITY_CHOICES = [
        (1, 'LOW'),
        (2, 'MEDIUM'),
        (3, 'HIGH'),
        (4, 'URGENT'),
    ]
    
    user = models.ForeignKey(User, on_delete=models.DO_NOTHING)

    name = models.CharField(max_length=30, default='Задача')
    timer = models.IntegerField(default=0)
    color = models.CharField(max_length=30, default='0, 0, 0, 0')
    is_counting = models.IntegerField(default=0)
    partnumber = models.IntegerField(default=0)
    to_show = models.IntegerField(default=1)
    
    # New fields
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='TODO')
    due_date = models.DateField(null=True, blank=True)
    priority = models.IntegerField(choices=PRIORITY_CHOICES, default=2)
    comment = models.TextField(null=True, blank=True)
    parent_task = models.ForeignKey('self', on_delete=models.CASCADE, null=True, blank=True, related_name='subtasks')
    completion_date = models.DateField(null=True, blank=True)  # Track when task was completed

    def __str__(self):
        return self.name
    
    def clean(self):
        """Validate model to prevent circular references and ensure user ownership"""
        # Check if user is set safely (avoid RelatedObjectDoesNotExist)
        try:
            user_id = getattr(self, 'user_id', None)
            user_obj = None
            try:
                user_obj = self.user
            except UserTask.user.RelatedObjectDoesNotExist:
                pass
            
            # Skip validation if user is not set (will be set before save)
            if not user_id and not user_obj:
                return  # User will be set before save, skip validation for now
        except AttributeError:
            return  # User field doesn't exist yet, skip validation
        
        if self.parent_task:
            # For existing instances, check if parent_task has actually changed
            # Skip circular reference validation if parent_task hasn't changed
            parent_task_changed = True
            if self.pk:
                try:
                    # Get the original parent_task_id from database
                    original = UserTask.objects.get(pk=self.pk)
                    original_parent_id = getattr(original, 'parent_task_id', None)
                    # Get current parent_task_id (handle both ID and related object)
                    current_parent_id = getattr(self, 'parent_task_id', None)
                    if current_parent_id is None and self.parent_task:
                        try:
                            current_parent_id = self.parent_task.id if self.parent_task.id else None
                        except AttributeError:
                            pass
                    # Compare IDs (None means no parent)
                    if original_parent_id == current_parent_id:
                        # Parent task hasn't changed, skip circular reference check
                        parent_task_changed = False
                except UserTask.DoesNotExist:
                    pass  # New instance, continue with validation
            
            # Ensure parent_task belongs to same user
            try:
                parent_user_id = getattr(self.parent_task, 'user_id', None)
                if not parent_user_id:
                    try:
                        parent_user_id = self.parent_task.user.id if self.parent_task.user else None
                    except (AttributeError, UserTask.user.RelatedObjectDoesNotExist):
                        pass
                
                current_user_id = getattr(self, 'user_id', None)
                if not current_user_id:
                    try:
                        current_user_id = self.user.id if self.user else None
                    except (AttributeError, UserTask.user.RelatedObjectDoesNotExist):
                        pass
                
                if parent_user_id and current_user_id and parent_user_id != current_user_id:
                    raise ValidationError({'parent_task': 'Parent task must belong to the same user.'})
            except (AttributeError, UserTask.user.RelatedObjectDoesNotExist):
                # If we can't access user attributes, skip this check
                pass
            
            # Prevent circular references - only check if parent_task has changed
            if parent_task_changed:
                try:
                    if not self.parent_task.can_be_parent_of(self):
                        raise ValidationError({'parent_task': 'Circular reference detected. A task cannot be its own ancestor.'})
                except AttributeError:
                    pass
    
    def save(self, *args, **kwargs):
        """Override save to run validation and track completion date"""
        # Ensure user is set before validation
        if not getattr(self, 'user_id', None) and not hasattr(self, 'user'):
            raise ValueError('User must be set before saving.')
        
        # Track completion date when status changes to COMPLETED
        if self.status == 'COMPLETED' and not self.completion_date:
            self.completion_date = date.today()
        elif self.status != 'COMPLETED':
            # Clear completion date if status changes away from COMPLETED
            self.completion_date = None
        
        self.full_clean()
        super().save(*args, **kwargs)
    
    def is_overdue(self):
        """Check if task is overdue"""
        if self.due_date:
            return self.due_date < date.today()
        return False
    
    def get_subtasks(self):
        """Get all subtasks for this task"""
        return UserTask.objects.filter(parent_task=self, to_show=1)
    
    def can_be_parent_of(self, task):
        """Check if this task can be a parent of the given task (prevent circular references)"""
        if not task:
            return True
        
        # Cannot be parent of itself
        if self.id and self.id == task.id:
            return False
        
        # Check if task would create a cycle (task or its ancestors is self)
        current = task
        visited = set()
        while current and current.id:
            if current.id == self.id:
                return False
            if current.id in visited:
                break  # Prevent infinite loop
            visited.add(current.id)
            current = current.parent_task
        
        return True
    
    def update_priority_if_overdue(self):
        """Auto-increase priority if task is overdue"""
        if self.is_overdue() and self.priority < 4:
            self.priority = min(4, self.priority + 1)
            self.save(update_fields=['priority'])
            return True
        return False
    
    def get_priority_display_name(self):
        """Get human-readable priority name"""
        return dict(self.PRIORITY_CHOICES)[self.priority]


class PartTask(models.Model):
    usertask_id = models.IntegerField(default=0)
    date_start = models.DateField()
    time_start = models.TimeField()
    datetime_start = models.DateTimeField(default='0001-01-01 00:00:00')
    date_stop = models.DateField(default='0001-01-01')
    time_stop = models.TimeField(default='00:00:00')
    datetime_stop = models.DateTimeField(default='0001-01-01 00:00:00')
    comment = models.CharField(max_length=200)
    user = models.ForeignKey(User, on_delete=models.DO_NOTHING)
    time_length = models.IntegerField(default=0)

    def __str__(self):
        return self.date_start.strftime("%Y-%m-%d") + "T" + self.time_start.strftime("%H-%M-%S") + " " + str(self.usertask_id)
