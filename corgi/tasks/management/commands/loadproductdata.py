from django.core.management.base import BaseCommand

from corgi.tasks.brew import load_brew_tags
from corgi.tasks.errata_tool import load_et_products
from corgi.tasks.prod_defs import update_products
from corgi.tasks.pulp import setup_pulp_relations, update_cdn_repo_channels
from corgi.tasks.rhel_compose import save_composes
from corgi.tasks.yum import load_yum_repositories


class Command(BaseCommand):

    help = "Fetch product data from Product Definitions."

    def handle(self, *args, **options):
        if settings.COMMUNITY_MODE_ENABLED:
            self.do_update_products()
            self.delay_load_yum_repositories()

        else:  # Enterprise mode
            self.do_load_et_products()
            self.do_update_products()
            self.do_update_cdn_repo_channels()
            self.do_save_composes()
            self.do_load_brew_tags()
            self.delay_setup_pulp_relations()
            self.delay_load_yum_repositories()

    def delay_setup_pulp_relations(self):
        self.stdout.write(
            self.style.SUCCESS(
                "Setting up pulp relations",
            )
        )
        setup_pulp_relations.delay()

    def do_load_brew_tags(self):
        self.stdout.write(
            self.style.SUCCESS(
                "Loading Brew Tags",
            )
        )
        load_brew_tags()

    def do_save_composes(self):
        self.stdout.write(
            self.style.SUCCESS(
                "Loading Composes",
            )
        )
        save_composes()

    def do_update_cdn_repo_channels(self):
        self.stdout.write(
            self.style.SUCCESS(
                "Assigning RPM Repos from ET to Product Variants as channels",
            )
        )
        update_cdn_repo_channels()

    def do_load_et_products(self):
        self.stdout.write(
            self.style.SUCCESS(
                "Loading products from ET",
            )
        )
        load_et_products()

    def delay_load_yum_repositories(self):
        self.stdout.write(self.style.SUCCESS("Loading yum repositiroes"))
        load_yum_repositories.delay()

    def do_update_products(self):
        self.stdout.write(
            self.style.SUCCESS(
                "Loading product-definitions",
            )
        )
        update_products()
