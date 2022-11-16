import sys

from django.core.management.base import BaseCommand, CommandParser

from corgi.tasks.errata_tool import load_errata
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

    def handle(self, *args, **options) -> None:
        if options["errata_ids"]:
            errata_ids = options["errata_ids"]
            for erratum_id in errata_ids:
                self.stdout.write(self.style.SUCCESS(f"Loading Errata {erratum_id}"))
                if options["inline"]:
                    load_errata(erratum_id)
                else:
                    load_errata.delay(erratum_id)
        elif options["repos"]:
            self.stdout.write(self.style.SUCCESS("Loading channels"))
            if options["inline"]:
                update_cdn_repo_channels()
            else:
                update_cdn_repo_channels.delay()
        else:
            self.stderr.write(self.style.ERROR("No errata IDs or repo flag specified"))
            sys.exit(1)
