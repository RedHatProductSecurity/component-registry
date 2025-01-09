import sys

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

        parser.add_argument(
            "-a",
            "--allow-stream",
            dest="allow-stream",
            help="Allow publishing manifest for the specified stream. This removes the "
            "'no_manifest' tag for the stream so it will be published by SDEngine. We also "
            "need to adjust the config option 'ALLOWED_MIDDLEWARE_MANIFEST_STREAMS' to avoid "
            "re-adding the 'no_manifest' tag.",
        )

    def handle(self, *args, **options) -> None:
        if options["stream"]:
            ps = ProductStream.objects.get(name=options["stream"])
            self.stdout.write(self.style.SUCCESS(f"Updating manifest for {options['stream']}"))
            cpu_update_ps_manifest(ps.name)
        elif options["allow-stream"]:
            ps = ProductStream.objects.get(name=options["allow-stream"])
            no_manifest_tag = ps.tags.filter(name="no_manifest").first()
            if not no_manifest_tag:
                self.stdout.write(
                    self.style.ERROR(f"no_manifest tag not set on {options['allow-stream']}")
                )
                sys.exit(1)
            self.stdout.write(
                self.style.SUCCESS(f"Removing no_manifest tag from {options['allow-stream']}")
            )
            no_manifest_tag.delete()
        else:
            self.stdout.write(self.style.SUCCESS("Updating manifests for all streams"))
            update_manifests()
