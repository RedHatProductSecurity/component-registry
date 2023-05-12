from .base import *  # noqa: F401, F403

DEBUG = True

SECRET_KEY = "helloworld"

SESSION_COOKIE_SECURE = False

# When testing email functionality locally, you can start a debugging SMTP server that
# prints out the received emails with:
# python -m smtpd -n -c DebuggingServer localhost:1025
# with podman-compose add a service such as:
#  sdengine-mail:
#    container_name: sdengine-mail
#    image: sdengine
#    command: python3 -m smtpd -n -c DebuggingServer localhost:1025
#    ports:
#      - "1025:1025"

ALLOWED_HOSTS = ["*"]
STATIC_URL = "http://localhost:8080/"

# Django Debug Toolbar config; requires requirements/dev.txt deps
INSTALLED_APPS += ["debug_toolbar"]  # noqa: F405
MIDDLEWARE += ["debug_toolbar.middleware.DebugToolbarMiddleware"]  # noqa: F405

# Ensure debug toolbar is always shown in local env by overriding the check for specific IP
# addresses in INTERNAL_IPS (in a containerized environment, these change every time so adding
# localhost to INTERNAL_IPS is not sufficient).
# https://django-debug-toolbar.readthedocs.io/en/latest/configuration.html#toolbar-options
DEBUG_TOOLBAR_CONFIG = {"SHOW_TOOLBAR_CALLBACK": lambda _: True}
