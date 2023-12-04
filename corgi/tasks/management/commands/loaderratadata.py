import sys

from django.core.management.base import BaseCommand, CommandParser

from corgi.tasks.errata_tool import slow_load_errata, slow_load_stream_errata
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
        parser.add_argument("-s", "--product_stream", help="Product Stream to load errata for.")

    def handle(self, *args, **options) -> None:
        if options["errata_ids"]:
            errata_ids = options["errata_ids"]
            for erratum_id in errata_ids:
                self.stdout.write(self.style.SUCCESS(f"Loading Errata {erratum_id}"))
                if options["inline"]:
                    slow_load_errata(erratum_id, force_process=options["force"])
                else:
                    # Tasks users run manually with management commands should finish ASAP
                    slow_load_errata.apply_async(args=(erratum_id, options["force"]), priority=0)
        elif options["repos"]:
            self.stdout.write(self.style.SUCCESS("Loading channels"))
            if options["inline"]:
                update_cdn_repo_channels()
            else:
                update_cdn_repo_channels.delay()
        elif options["product_stream"]:
            self.stdout.write(
                self.style.SUCCESS(f"Loading errata matching variants {options['product_stream']}")
            )
            slow_load_stream_errata(options["product_stream"], force_process=options["force"])

        else:
            self.stderr.write(self.style.ERROR("No errata IDs or repo flag, or variants specified"))
            sys.exit(1)
