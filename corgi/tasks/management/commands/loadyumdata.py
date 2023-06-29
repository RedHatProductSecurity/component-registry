import sys

from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import ProductComponentRelation, ProductStream
from corgi.tasks.brew import fetch_modular_builds
from corgi.tasks.yum import fetch_unprocessed_yum_relations


class Command(BaseCommand):

    help = "Fetch builds for yum repos"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "stream_names",
            nargs="*",
            type=str,
            help="Fetch builds using yum_repo in streams",
        )
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Force ingestion even if build exists",
        )
        parser.add_argument(
            "-a", "--all", action="store_true", help="Fetch all unprocessed yum relations"
        )

    def handle(self, *args, **options) -> None:
        if options["stream_names"]:
            for stream_name in options["stream_names"]:
                self.stdout.write(self.style.NOTICE(f"Fetching builds for stream: {stream_name}"))
                self.get_builds_by_yum_repo(stream_name=stream_name, force_process=options["force"])
        elif options["all"]:
            self.stdout.write(self.style.NOTICE("Fetching all unprocessed yum relations"))
            fetch_unprocessed_yum_relations(force_process=options["force"])
        else:
            self.stderr.write(self.style.ERROR("Pass either a stream name or the --all argument"))
            sys.exit(1)

    def get_builds_by_yum_repo(self, stream_name: str, force_process: bool):
        self.stdout.write(self.style.NOTICE(f"Called save yum repo with stream {stream_name}"))
        ps = ProductStream.objects.get(name=stream_name)
        stream_or_variant_names = (stream_name, *ps.productvariants.values_list("name", flat=True))
        relations_query = (
            ProductComponentRelation.objects.filter(
                product_ref__in=stream_or_variant_names,
                type=ProductComponentRelation.Type.YUM_REPO,
            )
            .values_list("build_id", flat=True)
            .distinct()
        )
        fetch_modular_builds(relations_query, force_process=force_process)
