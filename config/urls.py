from django.urls import include, path

from config.utils import running_dev
from corgi.api.constants import CORGI_API_VERSION
from corgi.api.views import TokenAuthTestView, healthy

urlpatterns = [
    # Generic health endpoint
    path("api/healthy", healthy),
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
