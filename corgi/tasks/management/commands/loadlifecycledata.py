from django.core.management.base import BaseCommand, CommandParser

from corgi.tasks.lifecycle import update_appstream_lifecycles


class Command(BaseCommand):

    help = "Fetch lifecycle data."

    def add_arguments(self, parser: CommandParser) -> None:
        group = parser.add_mutually_exclusive_group(required=True)
        group.add_argument(
            "-a",
            "--appstream",
            action="store_true",
            help="Fetch appstream life cycle data.",
        )
        # TODO: add bellow after PSDEVOPS-3343 is implemented
        # group.add_argument(
        #     '-p',
        #     '--product',
        #     action='store_true',
        #     help='Fetch product life cycle data.'
        # )

    def handle(self, *args, **options) -> None:
        if options["appstream"]:
            update_appstream_lifecycles()
