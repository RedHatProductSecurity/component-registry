from django.core.management.base import BaseCommand

from corgi.core.models import ComponentNode


class Command(BaseCommand):

    help = "Generate component dupe report listing components containing duplicate children."

    def handle(self, *args, **options):
        self.stdout.write("componentnode id, purl, software_build_id")
        for cn in ComponentNode.objects.all():
            children = cn.get_children().values_list("object_id", flat=True)
            if children:
                if len(set(children)) != len(children):
                    self.stdout.write(f"{cn.id}, {cn.purl}, {cn.obj.software_build_id}")
