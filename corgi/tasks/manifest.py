import hashlib
import json
import re
from pathlib import Path

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db.models import Count
from django_celery_results.models import TaskResult

from config.celery import app
from corgi.core.files import ManifestFile, ProductManifestFile
from corgi.core.models import ProductStream
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)

BUF_SIZE = 65536  # 64kb


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
        if ps.external_name not in external_names:
            cpu_update_ps_manifest.delay(ps.name, ps.external_name)
            external_names.add(ps.external_name)
        else:
            logger.info(
                f"Skipping manifest generation for {ps.name} with shared external name "
                f"{ps.external_name}"
            )


def same_contents(existing_file: str, stream: ProductStream) -> tuple[bool, str, str, str]:
    """Check if the contents of existing file matches the latest manifest for the stream.
    Tries to be efficient by reading the previous successful result of generating the same manifest
    and only regenerate a new one if one exists, and the contents have not been updated.
    In the case that the stream manifest needs to be updated the function returns a bool value
     indicating if a manifest should be regenerated along with the new content to be written"""
    logger.info(f"Checking if manifest is updated for {stream.name}")
    existing_file_obj = Path(existing_file)
    if existing_file_obj.is_file():
        last_update_manifest_task_for_stream = (
            TaskResult.objects.filter(
                task_name="corgi.tasks.manifest.cpu_update_ps_manifest",
                task_args__contains=stream.external_name,
                result__contains="true",
                status="SUCCESS",
            )
            .order_by("-date_created")
            .first()
        )
        if not last_update_manifest_task_for_stream:
            return False, "", "", ""
        task_result = json.loads(last_update_manifest_task_for_stream.result)
        created_at = task_result[1]
        document_uuid = task_result[2]
        # generate some new content with the old document created_at and document_uuid but latest
        # stream data
        new_content, _, _ = ProductManifestFile(stream).render_content(
            created_at=created_at, document_uuid=document_uuid
        )
        new_content_md5_hash = hashlib.md5(new_content.encode("utf-8")).hexdigest()
        old_content_md5_hash = calculate_file_md5_hash(existing_file_obj)
        if new_content_md5_hash == old_content_md5_hash:
            return True, "", "", ""
        else:
            # The content didn't match. Let's use the new content we just generated, but replace the
            # old created_at, and document_uuid values with some new ones.
            new_created_at = ManifestFile.get_created_at()
            new_document_uuid = ManifestFile.get_document_uuid()
            new_content_updated = re.sub(created_at, new_created_at, new_content)
            return (
                False,
                str(re.sub(document_uuid, new_document_uuid, new_content_updated)),
                new_created_at,
                new_document_uuid,
            )
        # content hash mismatch, fall through to return False
    # else no existing file with given path exists
    return False, "", "", ""


def calculate_file_md5_hash(existing_file_obj: Path) -> str:
    old_content_md5 = hashlib.md5()
    with existing_file_obj.open("rb") as existing_fh:
        while True:
            data = existing_fh.read(BUF_SIZE)
            if not data:
                break
            old_content_md5.update(data)
    old_content_md5_hash = old_content_md5.hexdigest()
    return old_content_md5_hash


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_update_ps_manifest(product_stream: str, external_name: str) -> tuple[bool, str, str]:
    logger.info(f"Updating manifest for {product_stream}, with external name: {external_name}")
    ps = ProductStream.objects.get(name=product_stream)
    output_file = f"{settings.STATIC_ROOT}/{external_name}.json"
    if ps.components.manifest_components(quick=True, ofuri=ps.ofuri).exists():
        match, new_content, created_at, document_uuid = same_contents(output_file, ps)
        if match:
            logger.info(f"Not updating {output_file} with same contents")
            return False, "", ""
        elif new_content:
            logger.info(f"Regenerating manifest for {product_stream}")
            with open(output_file, "w") as fh:
                fh.write(new_content)
            return True, created_at, document_uuid
        # else existing file didn't exist
        content, created_at, document_uuid = ProductManifestFile(ps).render_content()
        logger.info(f"Generating manifest for {product_stream}")
        with open(output_file, "w") as fh:
            fh.write(content)
        return True, created_at, document_uuid
    else:
        logger.info(
            f"Didn't find any released components for {product_stream}, "
            f"skipping manifest generation"
        )
    return False, "", ""
