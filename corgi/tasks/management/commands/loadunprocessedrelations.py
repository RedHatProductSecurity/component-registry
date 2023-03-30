from django.core.management.base import BaseCommand

from corgi.core.models import ProductComponentRelation
from corgi.tasks.brew import fetch_unprocessed_relations


class Command(BaseCommand):
    help = "Fetch unprocessed builds from the relations table"

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "Loading unprocessed relations",
            )
        )
        processed_builds = 0
        for relation_type in ProductComponentRelation.objects.values_list(
            "type", flat=True
        ).distinct():
            processed_builds += fetch_unprocessed_relations(relation_type=relation_type)
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {processed_builds} unprocessed relations",
            )
        )
