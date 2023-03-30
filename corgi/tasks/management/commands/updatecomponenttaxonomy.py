from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import SoftwareBuild


class Command(BaseCommand):

    help = "Update component taxonomy."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "build_uuids",
            nargs="*",
            type=str,
            help="Specific build UUIDs to update.",
        )

    def handle(self, *args, **options):
        if options["build_uuids"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"updating {options['build_uuids']} component taxonomies",
                )
            )
            builds = SoftwareBuild.objects.filter(pk__in=options["build_uuids"])
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "updating all builds component taxonomies",
                )
            )
            builds = SoftwareBuild.objects.get_queryset()
        for sb in builds:
            self.stdout.write(
                self.style.SUCCESS(
                    f"updating {sb.build_id}: {sb.name}",
                )
            )
            sb.save_product_taxonomy()
            for component in sb.components.get_queryset():
                component.save_component_taxonomy()
