from django.shortcuts import render
from basic_app.forms import UserForm,UserProfileInfoForm, UserTaskForm, StartTaskForm, StopTaskForm
from .models import UserTask, PartTask
from django.utils.timezone import localtime, now
# Extra Imports for the Login and Logout Capabilities
from django.contrib.auth import authenticate, login, logout
from django.http import HttpResponseRedirect, HttpResponse
from django.urls import reverse
from django.contrib.auth.decorators import login_required
import time
from threading import Timer
import datetime
from django.utils import timezone
import dateutil.relativedelta as monthdelta
from calendar import monthrange

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
    parttasks = PartTask.objects.filter(datetime_stop='0001-01-01 00:00:00')
    if len(parttasks) > 0:
        for parttask in parttasks:
            for task in q:
                if parttask.usertask_id == task.id:
                    task.timer = round((datetime.datetime.now() - parttask.datetime_start).total_seconds())
                    print(task.timer)

    if request.method == "POST" and 'start_button' in request.POST:
        print('start_button')
        form = StartTaskForm(request.POST)
        print(form)
        if form.is_valid():
            name = form.cleaned_data['name']
            ident = form.cleaned_data['id']
            date_start = datetime.date.today()
            time_start = datetime.datetime.now().time()
            datetime_start = timezone.now()
            parttask = PartTask(usertask_id=ident, user_id = current_user_id,
        	time_start = time_start, date_start = date_start, datetime_start = datetime_start)
            parttask.save()

            running_task = UserTask.objects.filter(user_id = current_user_id, is_counting = 1)
            if len(running_task) > 0:
                running_parttask = PartTask.objects.get(pk = running_task.partnumber)
                running_parttask.date_stop = datetime.date.today()
                running_parttask.time_stop = datetime.datetime.now().time()
                running_parttask.datetime_stop = timezone.now()
                running_parttask.time_length = (parttask.datetime_stop - parttask.datetime_start).total_seconds()
                running_parttask.save()

                running_task.timer += running_parttask.time_length
                running_task.is_counting = 0
                running_task.save()

            usertask = UserTask.objects.get(pk=ident)
            usertask.partnumber = parttask.pk
            usertask.is_counting = 1
            usertask.save()
        else:
        	print(form)

    elif request.method == "POST" and 'stop_button' in request.POST:
        print('stop_button')
        form = StopTaskForm(request.POST)
        print(form)
        if form.is_valid():
            partnumber = form.cleaned_data['partnumber']
            parttask = PartTask.objects.get(pk = partnumber)
            parttask.date_stop = datetime.date.today()
            parttask.time_stop = datetime.datetime.now().time()
            parttask.datetime_stop = timezone.now()
            parttask.time_length = (parttask.datetime_stop - parttask.datetime_start).total_seconds()
            parttask.save()

            usertask = UserTask.objects.get(pk = parttask.usertask_id)
            usertask.timer += parttask.time_length
            usertask.is_counting = 0
            usertask.save()
        else:
            print(form)

    elif request.method == "POST" and 'task_button' in request.POST:
        print('task_buttons')
        form = UserTaskForm(request.POST)
        if form.is_valid():
            name = form.cleaned_data['name']
            timer = form.cleaned_data['timer']
            ident = form.cleaned_data['id']
            to_delete = form.cleaned_data['fordelete']
            if ident != 0:
                userform = UserTask.objects.get(pk=ident)
                if to_delete == "No":
                    userform.name = name
                    userform.timer = timer
                    print('222', userform)
                    userform.save()
                else:
                    userform = UserTask.objects.get(pk=ident).delete()
            else:
                userform = UserTask(name = name, user_id = current_user_id)
                userform.save()
        else:
            print('333', form)

    else:
        print(request.POST  )

    context = {
        'usertasks': q,
        'counter': len(q)
    }
    return render(request, 'basic_app/tasks.html', context)

def reports(request):
    current_user_id = request.user.id

    if request.GET['period'] == 'this_day':
        parttasks = PartTask.objects.filter(user_id = current_user_id, date_start=datetime.date.today())
    elif request.GET['period'] == 'last_day':
    	parttasks = PartTask.objects.filter(user_id = current_user_id, date_start=datetime.date.today() - datetime.timedelta(days=1))
    elif request.GET['period'] == '15_days':
    	parttasks = PartTask.objects.filter(user_id = current_user_id,
    		date_start__range = (datetime.date.today() - datetime.timedelta(days=15), datetime.date.today()))
    elif request.GET['period'] == 'this_month':
    	parttasks = PartTask.objects.filter(user_id = current_user_id, date_start__range = (datetime.date.today() - datetime.timedelta(days = (datetime.date.today().day - 1)), datetime.date.today()))
    elif request.GET['period'] == 'last_month':
        date_minus_month = datetime.date.today() - monthdelta(months =+ 1)
        month_length = monthrange (date_minus_month.year, date_minus_month.month)[1]
        parttask = PartTask.objects.filter(user_id = current_user_id, date_start__range = (date_minus_month.replace(days = 1), date_minus_month.replace(days = month_length)))

    usertasks = UserTask.objects.filter(user_id = current_user_id)
    for usertask in usertasks:
    	usertask.timer = 0
    	for parttask in parttasks:
    		if parttask.usertask_id == usertask.id:
    			usertask.timer += parttask.time_length

    context = {
    	'usertasks': usertasks
        }

    return render(request, 'basic_app/report_page.html', context)

def base(request):
	link_to_site = 'https://community-open-weather-map.p.rapidapi.com/weather'
	headers={
            'X-RapidAPI-Host': 'community-open-weather-map.p.rapidapi.com',
            'X-RapidAPI-Key': '5aabc22e25msh45d6df4abdd28d0p1a4479jsn10ea7369318e'
                }
	parameters = {
                'q':'Ryazan,ru',
                'units':'metric',
                }
	weather = requests.get(link_to_site, headers=headers, params=parameters)
	current_weather = weather.json()
	context={
		'city_weather': current_weather['main']['temp']
	}
	return render(request, 'basic_app/base.html', context)
"""
def end_time_counting(user, task_name, result_time):7
    res = result_time.split(':')
    result_timer = res[0]*3600+res[1]*60+res[2]
    user=User.objects.get(username=user)
    q = UserTask.objects.get(user_id=user.id, name = task_name)
    q.timer = result_timer
    q.save()
"""
