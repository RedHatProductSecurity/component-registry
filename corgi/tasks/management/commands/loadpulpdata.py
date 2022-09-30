from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import ProductComponentRelation, ProductStream
from corgi.tasks.brew import fetch_modular_builds
from corgi.tasks.pulp import logger


class Command(BaseCommand):

    help = "Fetch builds for cdn repos"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "stream_name",
            type=str,
            help="Fetch builds using cdn_repo for variants configured in stream",
        )
        parser.add_argument(
            "-f",
            "--force",
            action="store_true",
            help="Force ingestion even if build exists",
        )

    def handle(self, *args, **options) -> None:
        if options["stream_name"]:
            stream_name = options["stream_name"]
            self.stdout.write(self.style.NOTICE(f"Fetching builds for stream: {stream_name}"))
            get_builds_by_cdn_repo(stream_name, options["force"])
        else:
            self.stderr.write(self.style.ERROR("No stream name passed to command"))


def get_builds_by_cdn_repo(stream_name: str, force_process: bool):
    logger.info("Called save cdn repo with stream %s", stream_name)
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
