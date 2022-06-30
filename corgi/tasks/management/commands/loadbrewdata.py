import sys

from django.core.management.base import BaseCommand, CommandParser
from koji import GenericError

from corgi.collectors.brew import Brew
from corgi.tasks.brew import slow_fetch_brew_build


class Command(BaseCommand):

    help = "Fetch component data from Brew for a specific build or tag."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "build_ids",
            nargs="*",
            type=int,
            help="Specific build IDs to fetch data for.",
        )
        parser.add_argument(
            "-t",
            "--tag",
            dest="brew_tag",
            help="Fetch builds tagged with a specific Brew tag.",
        )
        parser.add_argument(
            "-c",
            "--celery",
            action="store_true",
            help="Schedule build for ingestion as celery task.",
        )

    def handle(self, *args, **options) -> None:
        if options["build_ids"]:
            build_ids = options["build_ids"]
        elif options["brew_tag"]:
            brew = Brew().get_koji_session()
            try:
                builds = brew.listTagged(options["brew_tag"])
            except GenericError as exc:
                self.stderr.write(self.style.ERROR(str(exc)))
                sys.exit(1)
            build_ids = [b["build_id"] for b in builds]
        else:
            self.stderr.write(self.style.ERROR("No build IDs or tag specified..."))
            sys.exit(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Fetching component data for builds: {', '.join(map(str, build_ids))}"
            )
        )

        for build_id in build_ids:
            if options["celery"]:
                slow_fetch_brew_build.delay(build_id)
            else:
                slow_fetch_brew_build(build_id)
