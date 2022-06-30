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
            build_ids = options["build_ids"]
            self.stdout.write(
                self.style.SUCCESS(
                    f"updating {build_ids} component taxonomies",
                )
            )
            for build_id in build_ids:
                sb = SoftwareBuild.objects.get(build_id=build_id)
                self.stdout.write(
                    self.style.SUCCESS(
                        f"updating {sb.build_id}: {sb.name}",
                    )
                )
                sb.save_component_taxonomy
                sb.save_product_taxonomy
        else:
            self.stdout.write(
                self.style.SUCCESS(
                    "updating all builds component taxonomies",
                )
            )
            for sb in SoftwareBuild.objects.all():
                self.stdout.write(
                    self.style.SUCCESS(
                        f"updating {sb.build_id}: {sb.name}",
                    )
                )
                sb.save_component_taxonomy()
                sb.save_product_taxonomy()
