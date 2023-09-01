from django.core.management.base import BaseCommand, CommandParser

from corgi.core.models import Component, ComponentTag


class Command(BaseCommand):

    help = "Component tag management."

    def add_arguments(self, parser: CommandParser) -> None:
        parser.add_argument(
            "--purl",
            dest="component_purl",
            help="Select component to tag using component PURL",
        )
        parser.add_argument(
            "--name",
            dest="component_name",
            help="Select component(s) to tag using component name",
        )
        parser.add_argument(
            "--tag-name",
            dest="tag_name",
            help="Set tag name",
        )
        parser.add_argument(
            "--tag-value",
            dest="tag_value",
            help="Set tag value",
        )
        parser.add_argument(
            "--operation",
            dest="operation",
            help="Set tag operations (get,add,remove)",
        )

    def handle(self, *args, **options):
        operation = "get"
        if options["operation"]:
            operation = options["operation"]
        component_name = None
        if options["component_name"]:
            component_name = options["component_name"]
        component_purl = None
        if options["component_purl"]:
            component_purl = options["component_purl"]
        tag_name = None
        if options["tag_name"]:
            tag_name = options["tag_name"]
        tag_value = ""
        if options["tag_value"]:
            tag_value = options["tag_value"]
        self.stdout.write(
            self.style.SUCCESS(
                f"operation: {operation}",
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"component: {component_name}{component_purl}",
            )
        )
        self.stdout.write(
            self.style.SUCCESS(
                f"tag: {tag_name}:{tag_value}",
            )
        )

        tag_params = {}
        if tag_name:
            tag_params["name"] = tag_name
        if tag_value:
            tag_params["value"] = tag_value

        component_params = {}
        if component_purl:
            component_params["purl"] = component_purl
        if component_name:
            component_params["name"] = component_name

        components = Component.objects.filter(**component_params)

        if operation == "get":
            component_ids = ComponentTag.objects.filter(**tag_params).values_list(
                "tagged_model_id", flat=True
            )
            for c in Component.objects.filter(uuid__in=component_ids):
                self.stdout.write(
                    self.style.SUCCESS(
                        f"{c.name} {c.nvr} {c.purl}",
                    )
                )

        if operation == "remove":
            if components:
                for c in components:
                    ComponentTag.objects.filter(**tag_params, tagged_model_id=c.uuid).delete()
                self.style.SUCCESS(
                    f"{operation} tags successful on {components.count()} components.",
                )
            else:
                componenttags = ComponentTag.objects.filter(**tag_params)
                self.style.SUCCESS(
                    f"{operation} tags successful on {componenttags.count()} components.",
                )
                componenttags.delete()

        if operation == "add":
            for c in components:
                tag = ComponentTag.objects.get_or_create(**tag_params, tagged_model_id=c.uuid)
            self.stdout.write(
                self.style.SUCCESS(
                    f"{operation} tags successful on {components.count()} components.",
                )
            )
