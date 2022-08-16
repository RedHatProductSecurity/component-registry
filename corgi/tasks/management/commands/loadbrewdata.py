import sys

from django.core.management.base import BaseCommand, CommandParser
from koji import GenericError

from corgi.collectors.brew import Brew
from corgi.core.models import ProductStream
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
            "-s",
            "--stream",
            dest="stream",
            help="Fetch latest builds by tag from product stream",
        )
        parser.add_argument(
            "-i",
            "--inline",
            action="store_true",
            help="Schedule build for ingestion inline (not in celery)",
        )

    def handle(self, *args, **options) -> None:
        if options["build_ids"]:
            build_ids = options["build_ids"]
        elif options["stream"]:
            ps = ProductStream.objects.get(name=options["stream"])
            brew = Brew()
            build_ids = set()
            for brew_tag, inherit in ps.brew_tags.items():
                try:
                    builds = brew.get_builds_with_tag(brew_tag, inherit)
                except GenericError as exc:
                    self.stderr.write(self.style.ERROR(str(exc)))
                    sys.exit(1)
                self.stdout.write(
                    self.style.SUCCESS(f"Found {len(builds)} builds matching {brew_tag}")
                )
                build_ids.update(builds)
        else:
            self.stderr.write(self.style.ERROR("No build IDs or Product Stream specified..."))
            sys.exit(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Fetching component data for builds: {', '.join(map(str, build_ids))}"
            )
        )

        for build_id in build_ids:
            if options["inline"]:
                slow_fetch_brew_build(build_id)
            else:
                slow_fetch_brew_build.delay(build_id)
