from django.core.management.base import BaseCommand

from corgi.core.models import (
    Channel,
    Product,
    ProductStream,
    ProductVariant,
    ProductVersion,
)


class Command(BaseCommand):

    help = "Update product taxonomy."

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "updating all product entity taxonomies",
            )
        )
        for product_variant in ProductVariant.objects.get_queryset():
            product_variant.save_product_taxonomy()
        for product_stream in ProductStream.objects.get_queryset():
            product_stream.save_product_taxonomy()
        for product_version in ProductVersion.objects.get_queryset():
            product_version.save_product_taxonomy()
        for product in Product.objects.get_queryset():
            product.save_product_taxonomy()
        for channel in Channel.objects.get_queryset():
            channel.save_product_taxonomy()
