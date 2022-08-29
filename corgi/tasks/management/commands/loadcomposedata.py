import sys

from django.core.management.base import BaseCommand, CommandParser

from corgi.tasks.brew import slow_fetch_brew_build
from corgi.tasks.rhel_compose import (
    get_all_builds,
    get_builds_by_compose,
    get_builds_by_stream,
)


class Command(BaseCommand):

    help = "Fetch builds for composes"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "compose_names",
            nargs="*",
            type=str,
            help="Fetch builds for compose by name",
        )
        parser.add_argument(
            "-s",
            "--stream",
            dest="stream",
            help="Fetch builds for composes in stream",
        )
        parser.add_argument(
            "-i",
            "--inline",
            action="store_true",
            help="Schedule build for ingestion inline (not in celery)",
        )

    def handle(self, *args, **options) -> None:
        if options["compose_names"]:
            compose_names = options["compose_names"]
            self.stderr.write(self.style.NOTICE(f"Fetching builds for composes: {compose_names}"))
            build_ids = get_builds_by_compose(compose_names)
        elif options["stream"]:
            stream = options["stream"]
            self.stderr.write(self.style.NOTICE(f"Fetching builds for stream {stream}"))
            build_ids = get_builds_by_stream(stream)
        else:
            self.stderr.write(self.style.NOTICE("Fetching builds for all composes"))
            build_ids = get_all_builds()

        if not build_ids:
            self.stderr.write(self.style.ERROR("No build IDs found"))
            sys.exit(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Fetching component data for builds: {', '.join(map(str, build_ids))}"
            )
        )

        for build_id in build_ids:
            build_id = int(build_id)
            if options["inline"]:
                slow_fetch_brew_build(build_id)
            else:
                slow_fetch_brew_build.delay(build_id)
