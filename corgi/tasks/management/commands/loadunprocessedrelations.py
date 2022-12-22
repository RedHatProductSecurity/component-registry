from django.core.management.base import BaseCommand

from corgi.core.models import ProductComponentRelation, SoftwareBuild
from corgi.tasks.brew import slow_fetch_brew_build, slow_fetch_modular_build


class Command(BaseCommand):
    help = "Fetch unprocessed builds from the relations table"

    def handle(self, *args, **options):
        self.stdout.write(
            self.style.SUCCESS(
                "Loading unprocessed relations",
            )
        )
        processed_builds = 0
        for build_id, b_type in (
            ProductComponentRelation.objects.filter(
                type__in=(
                    ProductComponentRelation.Type.YUM_REPO,
                    ProductComponentRelation.Type.BREW_TAG,
                    ProductComponentRelation.Type.CDN_REPO,
                )
            )
            .values_list("build_id", "build_type")
            .distinct()
            .using("read_only")
            .iterator()
        ):
            if not build_id:
                continue
            if (
                not SoftwareBuild.objects.filter(build_id=int(build_id), type=b_type)
                .using("read_only")
                .exists()
            ):
                self.stdout.write(
                    self.style.SUCCESS(f"Loading build with id: {build_id}, and type: {b_type}")
                )
                if b_type == SoftwareBuild.Type.CENTOS:
                    # This skips use of the Collector models for builds in the CENTOS koji instance
                    # It was done to avoid updating the collector models not to use build_id as a
                    # primary key
                    # It's possible because the only product stream (openstack-rdo) stored in
                    # CENTOS koji
                    # doesn't use modules
                    slow_fetch_brew_build.delay(build_id, SoftwareBuild.Type.CENTOS)
                    processed_builds += 1
                    continue
                slow_fetch_modular_build.delay(build_id)
                processed_builds += 1
        self.stdout.write(
            self.style.SUCCESS(
                f"Loaded {processed_builds} unprocessed relations",
            )
        )
