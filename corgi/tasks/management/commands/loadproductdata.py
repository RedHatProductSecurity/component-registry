from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import ProductStream, ProductStreamTag
from corgi.tasks.errata_tool import link_stream_using_brew_tag, load_et_products
from corgi.tasks.prod_defs import update_products
from corgi.tasks.rhel_compose import load_composes


class Command(BaseCommand):

    help = "Fetch product data from Product Definitions."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "-s",
            "--stream",
            dest="stream",
            help="Assign to stream",
        )
        parser.add_argument(
            "-t",
            "--tag",
            dest="tag",
            help="Fetch variants for specific Brew tag",
        )
        parser.add_argument(
            "-i", "--inherit", dest="inherit", action="store_true", help="Fetch inherited Brew tags"
        )

    def handle(self, *args, **options):
        if options["stream"]:
            product_stream = ProductStream.objects.get(name=options["stream"])

            if not options["tag"]:
                self.stdout.write(
                    self.style.SUCCESS(
                        "No tag specified, getting tags from ProductStreamTags",
                    )
                )
                for tag in ProductStreamTag.objects.filter(product_stream=product_stream):
                    # ProductStreamTag values are strings
                    inherit = tag.value == "True"
                    self.link_stream_with_tag(product_stream.name, tag.name, inherit)
            else:
                self.link_stream_with_tag(product_stream.name, options["tag"], options["inherit"])

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
                "Loading RHEL Composes",
            )
        )
        load_composes()

    def link_stream_with_tag(self, stream: str, brew_tag: str, inherit: bool = False):
        link_stream_using_brew_tag(brew_tag, stream, inherit)
