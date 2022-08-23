import requests
from django.conf import settings
from yaml import CSafeLoader as Loader
from yaml import load


class AppStreamLifeCycleCollector(object):
    """Interface to collect lifecycle-defs"""

    @classmethod
    def get_lifecycle_defs(cls) -> list:
        response = requests.get(url=settings.APP_STREAMS_LIFE_CYCLE_URL)
        response.raise_for_status()
        data = load(response.text, Loader=Loader)
        return data["lifecycles"]
