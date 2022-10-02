import os

from django.urls import include, path

from corgi.api.constants import CORGI_API_VERSION
from corgi.api.views import healthy

urlpatterns = [
    # Generic health endpoint
    path("api/healthy", healthy),
    # REST API views
    path(f"api/{CORGI_API_VERSION}/", include("corgi.api.urls")),
    # Web
    path("", include("corgi.web.urls")),
]

if os.getenv("DJANGO_SETTINGS_MODULE") == "config.settings.dev":
    import debug_toolbar

    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
    ] + urlpatterns

if os.getenv("DJANGO_SETTINGS_MODULE") == "config.settings.stage":
    import debug_toolbar

    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
    ] + urlpatterns