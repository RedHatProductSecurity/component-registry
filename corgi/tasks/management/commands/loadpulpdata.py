import sys

from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import ProductComponentRelation, ProductStream
from corgi.tasks.brew import fetch_modular_builds
from corgi.tasks.pulp import fetch_unprocessed_cdn_relations


class Command(BaseCommand):

    help = "Fetch builds for cdn repos"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "stream_name",
            nargs="*",
            type=str,
            help="Fetch builds using cdn_repo for variants configured in stream",
        )
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Force ingestion even if build exists",
        )
        parser.add_argument(
            "-a", "--all", action="store_true", help="Fetch all unprocessed pulp relations"
        )

    def handle(self, *args, **options) -> None:
        if options["stream_name"]:
            stream_name = options["stream_name"]
            self.stdout.write(self.style.NOTICE(f"Fetching builds for stream: {stream_name}"))
            self.get_builds_by_cdn_repo(
                stream_name=options["stream_name"], force_process=options["force"]
            )
        elif options["all"]:
            self.stdout.write(self.style.NOTICE("Fetching all unprocessed pulp relations"))
            fetch_unprocessed_cdn_relations(
                force_process=options["force"], created_in_last_week=False
            )
        else:
            self.stderr.write(self.style.ERROR("Pass either a stream name or the --all argument"))
            sys.exit(1)

    def get_builds_by_cdn_repo(self, stream_name: str, force_process: bool):
        self.stdout.write(self.style.NOTICE(f"Called save cdn repo with stream {stream_name}"))
        ps = ProductStream.objects.get(name=stream_name)
        relations_query = (
            ProductComponentRelation.objects.filter(
                product_ref__in=ps.product_variants,
                type=ProductComponentRelation.Type.CDN_REPO,
            )
            .values_list("build_id", flat=True)
            .distinct()
        )
        fetch_modular_builds(relations_query, force_process)
