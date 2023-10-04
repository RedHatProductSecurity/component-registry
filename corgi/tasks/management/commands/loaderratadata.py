import sys

from django.core.management.base import BaseCommand, CommandParser

from corgi.collectors.errata_tool import ErrataTool
from corgi.collectors.models import CollectorErrataProductVariant
from corgi.tasks.errata_tool import slow_load_errata
from corgi.tasks.pulp import update_cdn_repo_channels


class Command(BaseCommand):

    help = "Fetch various metadata from Errata Tool."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "errata_ids",
            nargs="*",
            type=str,
            help="Specific erratum ids to load",
        )
        parser.add_argument(
            "-r",
            "--repos",
            action="store_true",
            help="Fetch and update Variant-to-CDN-Repo mapping.",
        )
        parser.add_argument(
            "-i",
            "--inline",
            action="store_true",
            help="Call tasks inline, not in a celery task",
        )
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Force process task",
        )
        parser.add_argument(
            "-p", "--product_variants", nargs="+", help="A list of variants to load errata for"
        )

    def handle(self, *args, **options) -> None:
        if options["errata_ids"]:
            errata_ids = options["errata_ids"]
            for erratum_id in errata_ids:
                self.stdout.write(self.style.SUCCESS(f"Loading Errata {erratum_id}"))
                if options["inline"]:
                    slow_load_errata(erratum_id, force_process=options["force"])
                else:
                    slow_load_errata.delay(erratum_id, force_process=options["force"])
        elif options["repos"]:
            self.stdout.write(self.style.SUCCESS("Loading channels"))
            if options["inline"]:
                update_cdn_repo_channels()
            else:
                update_cdn_repo_channels.delay()
        elif options["product_variants"]:
            et = ErrataTool()
            product_ids = set()
            for et_variant in CollectorErrataProductVariant.objects.filter(
                name__in=options["product_variants"]
            ):
                product_ids.add(et_variant.product_version.product.et_id)
            # make sure there is only 1 product id for the names
            if len(product_ids) > 1:
                self.stderr.write(
                    self.style.ERROR(f"Variants had more than 1 product id. Found: {product_ids}")
                )
                sys.exit(1)

            product_id = product_ids.pop()
            self.stdout.write(
                self.style.SUCCESS(f"Searching shipped errata for product with id {product_id}")
            )
            # TODO switch this to use releases instead of Product
            product_errata = et.get_paged(
                f"api/v1/erratum/search?show_state_SHIPPED_LIVE=1&product[]={product_id}",
                page_data_attr="data",
            )
            all_errata = set()
            for erratum in product_errata:
                self.stdout.write(self.style.SUCCESS(f"Found shipped erratum {erratum['id']}"))
                builds = et.get(f"api/v1/erratum/{erratum['id']}/builds_list.json")
                for product_version in builds.values():
                    for build in product_version["builds"]:
                        for _, build_data in build.items():
                            for variant in build_data["variant_arch"].keys():
                                if variant in options["product_variants"]:
                                    self.stdout.write(
                                        self.style.SUCCESS(
                                            f"Found variant {variant} in {erratum['id']}"
                                        )
                                    )
                                    all_errata.add(str(erratum["id"]))

            for erratum in all_errata:
                self.stdout.write(self.style.SUCCESS(f"Loading erratum: {erratum}"))
                slow_load_errata.delay(erratum, force_process=options["force"])

        else:
            self.stderr.write(self.style.ERROR("No errata IDs or repo flag, or variants specified"))
            sys.exit(1)
