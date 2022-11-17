from django.urls import path

from .views import data_list, home, running_tasks, tasks_list

urlpatterns = [
    # v1 API
    path("", home),
    path("data", data_list),
    path("tasks", tasks_list),
    path("tasks/running", running_tasks),
]
