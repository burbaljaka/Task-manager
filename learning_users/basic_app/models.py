from django.db import models
from django.contrib.auth.models import User

# Create your models here.
class UserProfileInfo(models.Model):

    # Create relationship (don't inherit from User!)
    user = models.OneToOneField(User, on_delete = 'Do_Nothing')

    # Add any additional attributes you want
    portfolio_site = models.URLField(blank=True)
    # pip install pillow to use this!
    # Optional: pip install pillow --global-option=”build_ext” --global-option=”--disable-jpeg”
    profile_pic = models.ImageField(upload_to='profile_pics',blank=True)

    def __str__(self):
        # Built-in attribute of django.contrib.auth.models.User !
        return self.user.username

class UserTask(models.Model):
    user = models.ForeignKey(User, on_delete = 'Do_Nothing')

    name = models.CharField(max_length = 30, default = 'Задача')
    timer = models.IntegerField(default = 0)
    color = models.CharField(max_length = 30, default = '0, 0, 0, 0')

    def __str__(self):
        return self.name
