import logging

from celery_singleton import Singleton

from config.celery import app
from corgi.collectors.rhel_compose import RhelCompose
from corgi.core.models import ProductComponentRelation, ProductStream
from corgi.tasks.brew import fetch_modular_builds
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS, _create_relations

logger = logging.getLogger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def save_composes() -> None:
    logger.info("Setting up relations for all streams with composes")
    for stream in ProductStream.objects.exclude(composes__exact={}):
        save_compose.delay(stream.name)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def save_compose(stream_name) -> None:
    logger.info("Called save compose with %s", stream_name)
    ps = ProductStream.objects.get(name=stream_name)
    no_of_relations = 0
    for compose_url, variants in ps.composes.items():
        compose_id, compose_created_date, compose_data = RhelCompose.fetch_compose_data(
            compose_url, variants
        )
        for key in "srpms", "rhel_modules":
            if key not in compose_data:
                # Most composes don't have rhel_modules, in that case the rhel_modules
                # key won't exist so we can safely skip creating relations
                continue
            no_of_relations += _create_relations(
                compose_data[key], compose_id, stream_name, ProductComponentRelation.Type.COMPOSE
            )
    logger.info("Created %s new relations for stream %s", no_of_relations, stream_name)


def get_builds_by_compose(compose_names):
    relations_query = (
        ProductComponentRelation.objects.filter(
            external_system_id__in=compose_names,
            type=ProductComponentRelation.Type.COMPOSE,
        )
        .values_list("build_id", flat=True)
        .distinct()
    )
    fetch_modular_builds(relations_query)


def get_builds_by_stream(stream_name):
    relations_query = (
        ProductComponentRelation.objects.filter(
            product_ref=stream_name,
            type=ProductComponentRelation.Type.COMPOSE,
        )
        .values_list("build_id", flat=True)
        .distinct()
    )
    fetch_modular_builds(relations_query)


def get_all_builds():
    relations_query = (
        ProductComponentRelation.objects.filter(
            type=ProductComponentRelation.Type.COMPOSE,
        )
        .values_list("build_id", flat=True)
        .distinct()
    )
    fetch_modular_builds(relations_query)
