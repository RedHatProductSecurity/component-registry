import sys

from django.core.management.base import BaseCommand, CommandParser
from koji import GenericError  # type: ignore[attr-defined]

from corgi.collectors.brew import Brew
from corgi.core.models import ProductStream, SoftwareBuild
from corgi.tasks.brew import fetch_unprocessed_brew_tag_relations, slow_fetch_brew_build
from corgi.tasks.common import BUILD_TYPE


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
            "-a",
            "--all",
            action="store_true",
            help="Fetch all builds with brew_tag relations",
        )
        parser.add_argument(
            "-i",
            "--inline",
            action="store_true",
            help="Schedule build for ingestion inline (not in celery)",
        )
        parser.add_argument(
            "-c",
            "--centos",
            action="store_true",
            help="Fetch builds from CENTOS koji instance",
        )
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Force ingestion even if build exists",
        )
        parser.add_argument(
            "-t",
            "--skip-taxonomy",
            # Default to true if not present
            action="store_false",
            help="Skip saving taxonomy when reprocessing builds",
        )

    def handle(self, *args, **options) -> None:
        if options["build_ids"]:
            build_ids = options["build_ids"]
        elif options["stream"]:
            ps = ProductStream.objects.db_manager("read_only").get(name=options["stream"])

            brew = (
                Brew(SoftwareBuild.Type.CENTOS) if ps.name == "openstack-rdo" else Brew(BUILD_TYPE)
            )

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
        elif options["all"]:
            self.stdout.write(self.style.NOTICE("Fetching all unprocessed brew_tag relations"))
            if options["inline"]:
                fetch_unprocessed_brew_tag_relations(force_process=options["force"])
            else:
                fetch_unprocessed_brew_tag_relations.delay(force_process=options["force"])
            sys.exit(0)
        else:
            self.stderr.write(self.style.ERROR("No build IDs, stream or all flag specified..."))
            sys.exit(1)

        self.stdout.write(
            self.style.SUCCESS(
                f"Fetching component data for builds: {', '.join(map(str, build_ids))}"
            )
        )

        for build_id in build_ids:
            build_type = BUILD_TYPE
            if options["centos"]:
                build_type = SoftwareBuild.Type.CENTOS
            if options["inline"]:
                slow_fetch_brew_build(
                    build_id,
                    build_type,
                    force_process=options["force"],
                    save_product=options["skip-taxonomy"],
                )
            else:
                slow_fetch_brew_build.delay(
                    build_id,
                    build_type,
                    force_process=options["force"],
                    save_product=options["skip-taxonomy"],
                )
