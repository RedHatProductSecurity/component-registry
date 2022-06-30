from django.urls import path

from .views import home

urlpatterns = [
    # v1 API
    path("", home)
]
