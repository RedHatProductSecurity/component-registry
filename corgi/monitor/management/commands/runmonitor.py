from django.conf import settings
from django.core.management.base import BaseCommand

from corgi.monitor.consumer import BrewUMBListener


class Command(BaseCommand):

    help = "Run Brew UMB monitor to listen on events on configured virtual topics."

    def handle(self, *args, **options):
        if settings.UMB_BREW_MONITOR_ENABLED:
            try:
                BrewUMBListener.consume()
            except KeyboardInterrupt:
                pass
