import logging

from celery_singleton import Singleton

from config.celery import app
from corgi.collectors.brew import Brew
from corgi.collectors.rhel_compose import RhelCompose
from corgi.core.models import ProductComponentRelation, ProductStream
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def save_composes() -> None:
    logger.info("Setting up relations for all streams with composes")
    for stream in ProductStream.objects.exclude(composes__exact={}):
        save_compose.delay(stream.name)


@app.task(base=Singleton, autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def save_compose(stream_name) -> None:
    brew = Brew()
    logger.info("Called save compose with %s", stream_name)
    ps = ProductStream.objects.get(name=stream_name)
    no_of_relations = 0
    for compose_url, variants in ps.composes.items():
        compose_id, compose_created_date, compose_data = RhelCompose.fetch_compose_data(
            compose_url, variants
        )
        if "srpms" not in compose_data:
            continue
        srpms = compose_data["srpms"].keys()
        find_build_id_calls = _brew_srpm_lookup(brew, srpms)
        for srpm, call in find_build_id_calls:
            build_id = call.result
            if not build_id:
                for filename in compose_data["srpms"][srpm]:
                    logger.debug(
                        "Didn't find build with NVR %s, using rpm filename: %s",
                        srpm,
                        filename,
                    )
                    rpm_data = brew.koji_session.getRPM(filename)
                    if not rpm_data:
                        # Try the next srpm rpm filename
                        continue
                    build_id = rpm_data["build_id"]
                    # found the build_id, stop iterating filenames
                    break
            # TODO: What if build_id still not found?
            _, created = ProductComponentRelation.objects.get_or_create(
                external_system_id=compose_id,
                product_ref=stream_name,
                build_id=build_id,
                defaults={"type": ProductComponentRelation.Type.COMPOSE},
            )
            if created:
                no_of_relations += 1
    logger.info("Created %s new relations for stream %s", no_of_relations, stream_name)


def _brew_srpm_lookup(brew, srpms) -> tuple:
    with brew.koji_session.multicall() as multicall:
        find_build_id_calls = tuple((srpm, multicall.findBuildID(srpm)) for srpm in srpms)
    return find_build_id_calls


def get_builds_by_compose(compose_names):
    return list(
        ProductComponentRelation.objects.filter(
            external_system_id__in=compose_names,
            type=ProductComponentRelation.Type.COMPOSE,
        )
        .values_list("build_id", flat=True)
        .distinct()
    )


def get_builds_by_stream(stream_name):
    return list(
        ProductComponentRelation.objects.filter(
            product_ref=stream_name,
            type=ProductComponentRelation.Type.COMPOSE,
        )
        .values_list("build_id", flat=True)
        .distinct()
    )


def get_all_builds():
    return list(
        ProductComponentRelation.objects.filter(
            type=ProductComponentRelation.Type.COMPOSE,
        )
        .values_list("build_id", flat=True)
        .distinct()
    )
