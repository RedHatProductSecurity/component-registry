from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import ProductStream
from corgi.tasks.manifest import cpu_update_ps_manifest, update_manifests


class Command(BaseCommand):
    help = "Generate manifests for product streams"

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "-s",
            "--stream",
            dest="stream",
            help="Update the manifest for the named product stream",
        )

    def handle(self, *args, **options) -> None:
        if options["stream"]:
            ps = ProductStream.objects.get(name=options["stream"])
            self.stdout.write(self.style.SUCCESS(f"Updating manifest for {options['stream']}"))
            cpu_update_ps_manifest(ps.name, ps.external_name)
        else:
            self.stdout.write(self.style.SUCCESS("Updating manifests for all streams"))
            update_manifests()
