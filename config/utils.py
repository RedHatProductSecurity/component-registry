import os


def get_env():
    # return "config.settings.env" -> ["config", "settings", "env"] -> "env"
    return os.getenv("DJANGO_SETTINGS_MODULE").split(".")[-1]


def running_dev():
    return get_env() == "dev"


def running_community():
    return get_env() == "community"


def running_prod():
    return get_env() == "prod"


def running_stage():
    return get_env() == "stage"
