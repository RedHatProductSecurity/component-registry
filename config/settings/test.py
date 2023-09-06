# noinspection PyUnresolvedReferences
import os

from django.core.management.utils import get_random_secret_key

from .base import *  # noqa: F401, F403

DEBUG = True

SECRET_KEY = get_random_secret_key()

# Use PG's default admin user to allow creating the test database
_user = os.getenv("CORGI_DB_USER", "postgres")
DATABASES["default"]["USER"] = _user  # type: ignore # noqa: F405
DATABASES["read_only"]["USER"] = _user  # type: ignore  # noqa: F405

SESSION_COOKIE_SECURE = False

# Report test coverage in templates
TEMPLATES[0]["OPTIONS"]["debug"] = True  # noqa: F405
