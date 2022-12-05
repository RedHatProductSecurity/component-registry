from django.core.management.base import BaseCommand

from corgi.tasks.brew import fetch_unprocessed_brew_tag_relations
from corgi.tasks.pulp import fetch_unprocessed_cdn_relations
from corgi.tasks.yum import fetch_unprocessed_yum_relations


class Command(BaseCommand):

    help = "Fetch unprocessed builds from the relations table"

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "Loading unprocessed YUM relations",
            )
        )
        fetch_unprocessed_yum_relations.delay()

        self.stdout.write(
            self.style.SUCCESS(
                "Loading unprocessed BREW_TAG relations",
            )
        )
        fetch_unprocessed_brew_tag_relations.delay()

        self.stdout.write(
            self.style.SUCCESS(
                "Loading unprocessed CDN relations",
            )
        )
        fetch_unprocessed_cdn_relations.delay()
