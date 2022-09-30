from django.core.management.base import BaseCommand, CommandParser

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
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Force ingestion even if the Compose exists",
        )

    def handle(self, *args, **options) -> None:
        if options["compose_names"]:
            compose_names = options["compose_names"]
            self.stderr.write(self.style.NOTICE(f"Fetching builds for composes: {compose_names}"))
            get_builds_by_compose(compose_names)
        elif options["stream"]:
            stream = options["stream"]
            self.stderr.write(self.style.NOTICE(f"Fetching builds for stream {stream}"))
            get_builds_by_stream(stream)
        else:
            self.stderr.write(self.style.NOTICE("Fetching builds for all composes"))
            get_all_builds()
