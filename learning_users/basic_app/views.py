from django.shortcuts import render
from basic_app.forms import UserForm,UserProfileInfoForm, UserTaskForm, StartTaskForm, StopTaskForm
from .models import UserTask, PartTask

# Extra Imports for the Login and Logout Capabilities
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponseRedirect, HttpResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
import time
from threading import Timer
import datetime
from django.utils import timezone

def index(request):
    return render(request,'basic_app/index.html')

@login_required
def special(request):
    # Remember to also set login url in settings.py!
    # LOGIN_URL = '/basic_app/user_login/'
    return HttpResponse("You are logged in. Nice!")

@login_required
def user_logout(request):
    # Log out the user.
    logout(request)
    # Return to homepage.
    return HttpResponseRedirect(reverse('index'))

def register(request):

    registered = False

    if request.method == 'POST':

        # Get info from "both" forms
        # It appears as one form to the user on the .html page
        user_form = UserForm(data=request.POST)
        profile_form = UserProfileInfoForm(data=request.POST)

        # Check to see both forms are valid
        if user_form.is_valid() and profile_form.is_valid():

            # Save User Form to Database
            user = user_form.save()

            # Hash the password
            user.set_password(user.password)

            # Update with Hashed password
            user.save()

            # Now we deal with the extra info!

            # Can't commit yet because we still need to manipulate
            profile = profile_form.save(commit=False)

            # Set One to One relationship between
            # UserForm and UserProfileInfoForm
            profile.user = user

            # Check if they provided a profile picture
            if 'profile_pic' in request.FILES:
                print('found it')
                # If yes, then grab it from the POST form reply
                profile.profile_pic = request.FILES['profile_pic']

            # Now save model
            profile.save()

            # Registration Successful!
            registered = True

        else:
            # One of the forms was invalid if this else gets called.
            print(user_form.errors,profile_form.errors)

    else:
        # Was not an HTTP post so we just render the forms as blank.
        user_form = UserForm()
        profile_form = UserProfileInfoForm()

    # This is the render and context dictionary to feed
    # back to the registration.html file page.
    return render(request,'basic_app/registration.html',
                          {'user_form':user_form,
                           'profile_form':profile_form,
                           'registered':registered})

def user_login(request):

    if request.method == 'POST':
        # First get the username and password supplied
        username = request.POST.get('username')
        password = request.POST.get('password')

        # Django's built-in authentication function:
        user = authenticate(username=username, password=password)

        # If we have a user
        if user:
            #Check it the account is active
            if user.is_active:
                # Log the user in.
                login(request,user)
                # Send the user back to some page.
                # In this case their homepage.

                return HttpResponseRedirect(reverse('basic_app:user_tasks_view'))
#                return render(request, 'basic_app/tasks.html', context)
            else:
                # If account is not active:
                return HttpResponse("Your account is not active.")
        else:
            print("Someone tried to login and failed.")
            print("They used username: {} and password: {}".format(username,password))
            return HttpResponse("Invalid login details supplied.")

    else:
        #Nothing has been provided for username or password.
        return render(request, 'basic_app/login.html', {})

def user_tasks_view(request):
    current_user_id = request.user.id
    q = UserTask.objects.filter(user_id=current_user_id)
    parttasks = PartTask.objects.filter(time_stop='0001-01-01 00:00:00')
    for parttask in parttasks:
        for task in q:
            if parttask.UserTask_id == task.id:
                task.timer = (timezone.now() - parttask.time_start).total_seconds()

    if request.method == "POST" and 'start_button' in request.POST:
        form = StartTaskForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            ident = form.cleaned_data['id']
            time_start = timezone.now()
            parttask = PartTask(name=name, user_id = current_user_id, 
            	time_start = time_start)
        else:
        	print(form)
    elif request.method == "POST" and 'stop_button' in request.POST:
    	form = StopTaskForm(request.POST)
    	if form.is_valid:
    		
                if to_delete == "No":
                    userform.name = name
                    userform.timer = timer
                    print('222', userform)
                    userform.save()
                else:
                    userform = UserTask.objects.get(pk=ident).delete()
            else:
                userform = StartTaskForm(name = name, user_id = current_user_id)
                userform.save()
        else:
            print('333', form)

    context = {
        'usertasks': q,
        'counter': len(q)
    }
    return render(request, 'basic_app/tasks.html', context)

"""
def end_time_counting(user, task_name, result_time):
    res = result_time.split(':')
    result_timer = res[0]*3600+res[1]*60+res[2]
    user=User.objects.get(username=user)
    q = UserTask.objects.get(user_id=user.id, name = task_name)
    q.timer = result_timer
    q.save()
"""
