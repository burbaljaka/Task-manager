from django import forms
from django.contrib.auth.models import User
from basic_app.models import UserProfileInfo, UserTask

class UserForm(forms.ModelForm):
    password = forms.CharField(widget=forms.PasswordInput())

    class Meta():
        model = User
        fields = ('username','email','password')


class UserProfileInfoForm(forms.ModelForm):
    class Meta():
        model = UserProfileInfo
        fields = ('portfolio_site','profile_pic')

class UserTaskForm(forms.ModelForm):
    id = forms.IntegerField()
    class Meta():
        model = UserTask
        fields = ('name', 'timer')
