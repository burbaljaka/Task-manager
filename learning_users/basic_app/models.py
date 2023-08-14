from django.db import models
from django.contrib.auth.models import User


# Create your models here.
class UserProfileInfo(models.Model):
    # Create relationship (don't inherit from User!)
    user = models.OneToOneField(User, on_delete=models.DO_NOTHING)

    # Add any additional attributes you want
    portfolio_site = models.URLField(blank=True)
    # pip install pillow to use this!
    # Optional: pip install pillow --global-option=”build_ext” --global-option=”--disable-jpeg”
    profile_pic = models.ImageField(upload_to='profile_pics', blank=True)

    def __str__(self):
        # Built-in attribute of django.contrib.auth.models.User !
        return self.user.username


class UserTask(models.Model):
    user = models.ForeignKey(User, on_delete=models.DO_NOTHING)

    name = models.CharField(max_length=30, default='Задача')
    timer = models.IntegerField(default=0)
    color = models.CharField(max_length=30, default='0, 0, 0, 0')
    is_counting = models.IntegerField(default=0)
    partnumber = models.IntegerField(default=0)
    to_show = models.IntegerField(default=1)

    def __str__(self):
        return self.name


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
