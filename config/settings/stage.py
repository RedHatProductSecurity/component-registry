import socket

from .base import *  # noqa: F401, F403

# SECURITY WARNING: keep the secret key used in stage secret!
SECRET_KEY = os.getenv("DJANGO_SECRET_KEY")  # noqa: F405

ALLOWED_HOSTS = [
    # Allow local host's IP address and hostname for health probes
    socket.gethostname(),
    socket.gethostbyname(socket.gethostname()),
    ".redhat.com",
]

# We trust OpenShift's HAProxy to strip the X-Forwarded-Proto header and to set it to "https" if
# the request came over HTTPS from the client to HAProxy.
USE_X_FORWARDED_HOST = True
SECURE_PROXY_SSL_HEADER = ("HTTP_X_FORWARDED_PROTO", "https")

# Temporary enable debug
DEBUG = True

# Django Debug Toolbar config; requires requirements/dev.txt deps
INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405
MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]  # noqa: F405

# Ensure debug toolbar is always shown in local env by overriding the check for specific IP
# addresses in INTERNAL_IPS (in a containerized environment, these change every time so adding
# localhost to INTERNAL_IPS is not sufficient).
# https://django-debug-toolbar.readthedocs.io/en/latest/configuration.html#toolbar-options
DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda _: True}
