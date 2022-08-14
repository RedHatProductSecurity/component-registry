from django.core.management.base import BaseCommand

from corgi.tasks.brew import load_brew_tags
from corgi.tasks.errata_tool import load_et_products
from corgi.tasks.prod_defs import update_products
from corgi.tasks.rhel_compose import save_composes


class Command(BaseCommand):

    help = "Fetch product data from Product Definitions."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "Loading products from ET",
            )
        )
        load_et_products()

        self.stdout.write(
            self.style.SUCCESS(
                "Loading product-definitions",
            )
        )
        update_products()

        self.stdout.write(
            self.style.SUCCESS(
                "Loading Composes",
            )
        )
        save_composes()

        self.stdout.write(
            self.style.SUCCESS(
                "Loading Brew Tags",
            )
        )
        load_brew_tags()
