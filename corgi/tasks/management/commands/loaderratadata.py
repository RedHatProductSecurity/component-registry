import sys

from django.core.management.base import BaseCommand, CommandParser

from corgi.tasks.errata_tool import load_errata, update_variant_repos


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
            "-c",
            "--celery",
            action="store_true",
            help="Schedule builds for ingestion as celery tasks.",
        )

    def handle(self, *args, **options) -> None:
        if options["errata_ids"]:
            errata_ids = options["errata_ids"]
            for erratum_id in errata_ids:
                self.stdout.write(self.style.SUCCESS(f"Force loading Errata {erratum_id}"))
                # If we are calling this command directly always make sure
                # we process the errata, even if it's been fully processed before.
                load_errata(erratum_id, force_process=True)
        elif options["repos"]:
            update_variant_repos()
        else:
            self.stderr.write(self.style.ERROR("No errata IDs or repo flag specified"))
            sys.exit(1)
