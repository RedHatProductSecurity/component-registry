from django.conf import settings
from django.core.management.base import BaseCommand

from corgi.monitor.consumer import (
    BrewUMBTopicHandler,
    SbomerUMBTopicHandler,
    UMBDispatcher,
)


class Command(BaseCommand):
    """Run UMB monitor to listen on events on configured virtual topics."""

    help = __doc__

    def handle(self, *args: str, **options: dict[str, str]) -> None:
        if settings.UMB_BREW_MONITOR_ENABLED:
            try:
                dispatcher = UMBDispatcher()
                dispatcher.register_handler(BrewUMBTopicHandler)
                dispatcher.register_handler(SbomerUMBTopicHandler)
                dispatcher.consume()
            except KeyboardInterrupt:
                pass
