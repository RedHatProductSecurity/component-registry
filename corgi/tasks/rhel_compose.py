import logging

from config.celery import app
from corgi.collectors.brew import Brew
from corgi.collectors.rhel_compose import RhelCompose
from corgi.core.models import ProductComponentRelation, ProductStream
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = logging.getLogger(__name__)


@app.task
def load_composes():
    composes_by_minor = RhelCompose().fetch_compose_versions()
    rhel_z_stream_names = (
        ProductStream.objects.filter(name__startswith="rhel-")
        .filter(name__endswith=".z")
        .values_list("name", flat=True)
    )
    for minor, composes in composes_by_minor.items():
        zstream_names = [ps for ps in rhel_z_stream_names if ps.startswith(f"rhel-{minor}")]
        if len(zstream_names) == 0:
            continue
        elif len(zstream_names) > 1:
            # Sometims there is both a rhel-8.1.0, and rhel-8.1.1 product stream, take the first one
            logger.warning(
                "Found more than 1 RHEL Z-Stream matching %s: %s, Using %s",
                minor,
                zstream_names,
                zstream_names[0],
            )
        for compose_data in composes:
            save_compose.delay(zstream_names[0], compose_data)


@app.task(autoretry_for=RETRYABLE_ERRORS, retry_kwargs=RETRY_KWARGS)
def save_compose(stream_name, compose_coords) -> None:
    brew = Brew()
    logger.info("Saving compose %s to %s", compose_coords[0], stream_name)
    if not ProductStream.objects.filter(name=stream_name).exists():
        logger.error("Could not find product stream with name: %s", stream_name)
        # TODO: Should raise ValueError or similar here to fail task
        return
    for compose_id, compose_data in RhelCompose.fetch_compose_data(compose_coords).items():
        for variant, compose_type in compose_data["data"].items():
            if "srpms" in compose_type:
                with brew.koji_session.multicall() as m:
                    find_build_id_calls = [
                        (srpm, m.findBuildID(srpm)) for srpm in compose_type["srpms"].keys()
                    ]
                for srpm, call in find_build_id_calls:
                    build_id = call.result
                    if not build_id:
                        for filename in compose_type["srpms"][srpm]:
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
                    ProductComponentRelation.objects.get_or_create(
                        external_system_id=compose_id,
                        product_ref=stream_name,
                        build_id=build_id,
                        defaults={"type": ProductComponentRelation.Type.COMPOSE},
                    )


def get_builds_by_compose(compose_names):
    return list(
        ProductComponentRelation.objects.filter(
            external_system_id__in=compose_names,
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
