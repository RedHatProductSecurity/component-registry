from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import ProductComponentRelation
from corgi.tasks.brew import fetch_unprocessed_relations


class Command(BaseCommand):
    help = "Fetch unprocessed builds from the relations table"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "-p",
            "--product_ref",
            dest="product_ref",
            help="Fetch unprocessed relations for product reference",
        )
        parser.add_argument(
            "-s",
            "--save_only",
            action="store_true",
            help="Call fetch anyway to save the product taxonomy if build already exists",
        )

    def handle(self, *args, **options):
        processed_builds = 0
        if options["product_ref"]:
            if (
                not ProductComponentRelation.objects.db_manager("read_only")
                .filter(product_ref=options["product_ref"])
                .exists()
            ):
                self.out.write(
                    self.style.ERROR(
                        f"Could not find relations with product ref: {options['product_ref']}"
                    )
                )
                exit(1)
            self.stdout.write(
                self.style.SUCCESS(
                    f"Loading unprocessed relations with product ref: {options['product_ref']}",
                )
            )
            processed_builds = fetch_unprocessed_relations(
                product_ref=options["product_ref"], save_only=options["save_only"]
            )
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "Loading unprocessed relations",
                )
            )
            processed_builds = fetch_unprocessed_relations(save_only=options["save_only"])
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {processed_builds} unprocessed relations",
            )
        )
