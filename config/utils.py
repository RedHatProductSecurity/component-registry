import os


def get_env() -> str:
    # return "config.settings.env" -> ["config", "settings", "env"] -> "env"
    return os.getenv("DJANGO_SETTINGS_MODULE", "").split(".")[-1]


def running_dev() -> bool:
    return get_env() == "dev"


def running_prod() -> bool:
    return get_env() == "prod"


def running_stage() -> bool:
    return get_env() == "stage"
