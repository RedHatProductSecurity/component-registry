from django.core.management.base import BaseCommand

from corgi.core.models import Component, ProductStream


class Command(BaseCommand):

    help = "Generate coverage report."

    def handle(self, *args, **options):
        self.stdout.write("ofuri, #builds, #components")
        self.stdout.write("---------------------------------------")
        for ps in ProductStream.objects.get_queryset().using("read_only"):
            component_count = (
                Component.objects.filter(product_streams__icontains=ps.ofuri)
                .using("read_only")
                .count()
            )
            ps_builds_count = ps.builds.count()
            if ps_builds_count > 0 or component_count > 0:
                self.stdout.write(f"{ps.ofuri}, {ps_builds_count}, {component_count}")
