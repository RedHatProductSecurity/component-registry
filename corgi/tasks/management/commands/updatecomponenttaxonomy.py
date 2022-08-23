from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import SoftwareBuild


class Command(BaseCommand):

    help = "Update component taxonomy."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "build_ids",
            nargs="*",
            type=int,
            help="Specific build IDs to update.",
        )

    def handle(self, *args, **options):
        if options["build_ids"]:
            self.stdout.write(
                self.style.SUCCESS(
                    f"updating {options['build_ids']} component taxonomies",
                )
            )
            builds = SoftwareBuild.objects.filter(build_id__in=options["build_ids"])
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
            sb.save_component_taxonomy()
            sb.save_product_taxonomy()
