# noinspection PyUnresolvedReferences
import os

from django.core.management.utils import get_random_secret_key

from .base import *  # noqa: F401, F403

DEBUG = True

SECRET_KEY = get_random_secret_key()

DATABASES = {
    "default": {
        "ENGINE": "django.db.backends.postgresql",
        "NAME": os.getenv("CORGI_DB_NAME", "corgi-db"),
        "USER": os.getenv("CORGI_DB_USER", "postgres"),
        "PASSWORD": os.getenv("CORGI_DB_PASSWORD", "secret"),
        "HOST": os.getenv("CORGI_DB_HOST", "localhost"),
        "PORT": os.getenv("CORGI_DB_PORT", "5432"),
    }
}

SESSION_COOKIE_SECURE = False

SCA_SCRATCH_DIR = "tests/data"
LOOKASIDE_DIR = SCA_SCRATCH_DIR
DISTGIT_DIR = SCA_SCRATCH_DIR
