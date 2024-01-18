from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db.models import Count

from config.celery import app
from corgi.core.models import ProductStream
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
)
def update_manifests():
    external_names = set()
    for ps in ProductStream.objects.annotate(num_components=Count("components")).filter(
        num_components__gt=0
    ):
        # Don't regenerate a manifests for streams with matching external names, the content will
        # be the same. This happens for the following streams, which share the same brew_tags, and
        # variants
        # CERTSYS-10.4-RHEL-8: ['certificate_system_10.4', 'certificate_system_10.4.z']
        # RHEL-7-DEVTOOLS-2023.2: ['devtools-compilers-2023-2', 'devtools-compilers-2023-2.z']
        # RHEL-7-FAST-DATAPATH: ['fdp-el7', 'fdp-el7-ovs']
        # GITOPS-1.2-RHEL-8: ['gitops-1.2', 'gitops-1.2.z']
        # JAEGER-1.20-RHEL-8: ['jaeger-1.20.0', 'jaeger-1.20.3', 'jaeger-1.20.4']
        # OPENJDK TEXT-ONLY: ['openjdk-11', 'openjdk-17', 'openjdk-1.8']
        # OPENSHIFT-PIPELINES-1.7-RHEL-8: ['pipelines-1.7', 'pipelines-1.7.1']
        # OPENSHIFT-PIPELINES-1.8-RHEL-8: ['pipelines-1.8', 'pipelines-1.8.1']
        # RHEL-8-RHACM-2.7: ['rhacm-2.7', 'rhacm-2.7.z']
        # RHEL-8-RHACM-2.8: ['rhacm-2.8', 'rhacm-2.8.z']
        #
        # Since we use the external name as the filename, all streams will share the same manifest
        if ps.name not in external_names:
            cpu_update_ps_manifest.delay(ps.name, ps.external_name)
            external_names.add(ps.external_name)


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_update_ps_manifest(product_stream: str, external_name: str):
    logger.info(f"Updating manifest for {product_stream}, with external name: {external_name}")
    ps = ProductStream.objects.get(name=product_stream)
    # TODO figure out a way to skip updating files where the content doesnt need updating
    # for example we could render the manifest in a temp file, remove the created_at line and diff
    # the contents only if there is a difference we can overwrite the file.
    # This would could save a lot of resource downstream, because clients could just check if the
    # file has been modified before obtaining the updated copy.
    # collectstatic does not modify a file in staticfiles directory if it
    # hasn't been updated in outputfiles.
    if ps.components.manifest_components(quick=True, ofuri=ps.ofuri).exists():
        logger.info(f"Generating manifest for {product_stream}")
        with open(f"{settings.STATIC_ROOT}/{external_name}.json", "w") as fh:
            fh.write(ps.manifest)
    else:
        logger.info(
            f"Didn't find any released components for {product_stream}, "
            f"skipping manifest generation"
        )
