from django.urls import path
from basic_app import views

# SET THE NAMESPACE!
app_name = 'basic_app'

# Be careful setting the name to just /login use userlogin instead!
urlpatterns=[
    path('register/',views.register,name='register'),
    path('user_login/',views.user_login,name='user_login'),
    path('tasks/', views.user_tasks_view, name = 'user_tasks_view'),
    path(
        'tasks/kanban-columns/create/',
        views.kanban_column_create,
        name='kanban_column_create',
    ),
    path(
        'tasks/kanban-columns/reorder/',
        views.kanban_column_reorder,
        name='kanban_column_reorder',
    ),
    path(
        'tasks/kanban-tasks/reorder/',
        views.kanban_task_reorder,
        name='kanban_task_reorder',
    ),
    path('tasks/<int:task_id>/', views.task_detail_view, name='task_detail'),
    path('reports/', views.reports, name = 'reports')

]
