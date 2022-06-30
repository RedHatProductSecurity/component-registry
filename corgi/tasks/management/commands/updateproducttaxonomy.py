from django.core.management.base import BaseCommand

from corgi.core.models import Product, ProductStream, ProductVariant, ProductVersion


class Command(BaseCommand):

    help = "Update product taxonomy."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "updating all product entity taxonomies",
            )
        )
        for product_variant in ProductVariant.objects.all():
            product_variant.save_product_taxonomy()
        for product_stream in ProductStream.objects.all():
            product_stream.save_product_taxonomy()
        for product_version in ProductVersion.objects.all():
            product_version.save_product_taxonomy()
        for product in Product.objects.all():
            product.save_product_taxonomy()
