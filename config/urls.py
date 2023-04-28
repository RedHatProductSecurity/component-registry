from django.conf import settings
from django.urls import include, path

from config.utils import running_dev
from corgi.api.constants import CORGI_API_VERSION
from corgi.api.views import (
    ControlledAccessTestView,
    TokenAuthTestView,
    authentication_status,
    healthy,
)

urlpatterns = [
    # Generic health endpoint
    path("api/healthy", healthy),
    # Info about authentication
    path("api/authentication_status", authentication_status),
    # Test restricted access
    path("api/controlled_access_test", ControlledAccessTestView.as_view()),
    # Test token authentication
    path("api/token_auth_test", TokenAuthTestView.as_view()),
    # REST API views
    path(f"api/{CORGI_API_VERSION}/", include("corgi.api.urls")),
    # Web
    path("", include("corgi.web.urls")),
]

if running_dev():
    import debug_toolbar

    urlpatterns = [
        path("__debug__/", include(debug_toolbar.urls)),
    ] + urlpatterns


if settings.OIDC_AUTH_ENABLED:
    urlpatterns = [path("oidc/", include("mozilla_django_oidc.urls"))] + urlpatterns
