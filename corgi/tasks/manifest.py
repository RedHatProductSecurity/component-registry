import hashlib
import json
import uuid
from datetime import datetime
from pathlib import Path

from celery.utils.log import get_task_logger
from celery_singleton import Singleton
from django.conf import settings
from django.db.models import Count
from django_celery_results.models import TaskResult
from spdx_tools.spdx.model import RelationshipType
from spdx_tools.spdx.parser.parse_anything import parse_file

from config.celery import app
from corgi.core.files import ProductManifestFile
from corgi.core.models import Component, ProductStream
from corgi.tasks.common import RETRY_KWARGS, RETRYABLE_ERRORS

logger = get_task_logger(__name__)

BUF_SIZE = 65536  # 64kb


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
)
def update_manifests():
    for ps in ProductStream.objects.annotate(num_components=Count("components")).filter(
        num_components__gt=0
    ):
        cpu_update_ps_manifest.delay(ps.name)


def same_contents(existing_file: str, stream: ProductStream) -> tuple[bool, dict]:
    """Check if the contents of existing file matches the latest manifest for the stream.
    In the case that the stream manifest needs to be updated the function returns the new content to
     be written. If the existing file is missing, or no successful task result is found new content
     will need to be generated and written for the stream"""
    logger.info(f"Checking if manifest is updated for {stream.name}")
    existing_file_obj = Path(existing_file)
    if not existing_file_obj.is_file():
        logger.info(f"Didn't find existing file {existing_file}")
        return False, {}

    last_update_manifest_task_for_stream = (
        TaskResult.objects.filter(
            task_name="corgi.tasks.manifest.cpu_update_ps_manifest",
            task_args__contains=stream.name,
            result__contains="true",
            status="SUCCESS",
        )
        .order_by("-date_created")
        .first()
    )
    if not last_update_manifest_task_for_stream:
        logger.info(f"Didn't find TaskResult for {stream.name}")
        return False, {}
    task_result = json.loads(last_update_manifest_task_for_stream.result)
    created_at = datetime.strptime(task_result[1], "%Y-%m-%dT%H:%M:%SZ")
    document_uuid = task_result[2]
    # generate some new content with the old document created_at and document_uuid but latest
    # stream data
    new_content = ProductManifestFile(stream).render_content(
        created_at=created_at, document_uuid=document_uuid
    )
    new_content_json = json.dumps(new_content, indent=4)
    new_content_md5_hash = hashlib.md5(new_content_json.encode("utf-8")).hexdigest()
    old_content_md5_hash = calculate_file_md5_hash(existing_file_obj)
    if new_content_md5_hash == old_content_md5_hash:
        logger.info(
            f"Not regenerating content for {stream.name} " f"because the content was not updated"
        )
        return True, {}
    else:
        # The content didn't match. Let's use the new content we just generated, but replace the
        # old created_at, and document_uuid values with some new ones.
        old_created_at = new_content["creationInfo"]["created"]
        existing_document_uuid = get_document_uuid(new_content["relationships"], stream.name)
        logger.info(
            f"The manifest content didn't match for {stream.name},"
            f" old created at date: {old_created_at}, old manifest id: "
            f"{existing_document_uuid}"
        )
        replace_created_at_date_and_document_id(existing_document_uuid, new_content)
        return False, new_content
    # content hash mismatch, fall through to return False


def replace_created_at_date_and_document_id(existing_document_uuid: str, new_content: dict) -> None:
    # Replace the created at date with a new one
    new_created_at = datetime.now()
    new_content["creationInfo"]["created"] = new_created_at.strftime("%Y-%m-%dT%H:%M:%SZ")
    # Replace the document id with a new one
    new_document_uuid = f"SPDXRef-{uuid.uuid4()}"
    last_package_id = new_content["packages"][-1]["SPDXID"]
    if last_package_id != existing_document_uuid:
        raise ValueError(
            "The last package ID didn't match the document describes relationship ID."
            "Did the order of the packages change?"
        )
    new_content["packages"][-1]["SPDXID"] = new_document_uuid
    # Replace the document id in the relationships
    for relationship in new_content["relationships"]:
        relationship_types_to_update = (
            RelationshipType.DESCRIBES.name,
            RelationshipType.PACKAGE_OF.name,
        )
        if relationship["relationshipType"] in relationship_types_to_update:
            if relationship["relatedSpdxElement"] != existing_document_uuid:
                raise ValueError(
                    f"The relationship for {relationship['spdxElementId']} with type "
                    f"{relationship['relationshipType']} didn't match the document "
                    f"describes relationship id {existing_document_uuid}"
                )
            relationship["relatedSpdxElement"] = new_document_uuid


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
def cpu_update_ps_manifest(product_stream: str) -> tuple[bool, str, str]:
    logger.info(f"Updating manifest for {product_stream}")
    ps = ProductStream.objects.get(name=product_stream)
    output_file = f"{settings.STATIC_ROOT}/{product_stream}.json"
    if ps.components.manifest_components(quick=True, ofuri=ps.ofuri).exists():
        match, new_content = same_contents(output_file, ps)
        if match:
            logger.info(f"Not updating {output_file} with same contents")
            return False, "", ""
        if new_content:
            logger.info(f"(Re)-generating manifest for {product_stream}")
            created_at, document_uuid = _write_content(new_content, output_file, product_stream)
            return True, created_at, document_uuid
        # output_file was missing, generate new file with manifest content
        content = ProductManifestFile(ps).render_content()
        created_at, document_uuid = _write_content(content, output_file, product_stream)
        return True, created_at, document_uuid
    else:
        logger.info(
            f"Didn't find any released components for {product_stream}, "
            f"skipping manifest generation"
        )
    return False, "", ""


@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    priority=6,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def cpu_validate_ps_manifest(product_stream: str):
    logger.info(f"Validating manifest for {product_stream}")
    ps = ProductStream.objects.get(name=product_stream)
    manifest_file = ProductManifestFile(ps)
    file_name = f"{settings.STATIC_ROOT}/{ps.external_name}.json"
    document = parse_file(file_name)
    try:
        manifest_file.validate_document(document, ps.external_name)
    except ValueError:
        logger.info(f"Got error validating SPDX document for {product_stream}")
        slow_ensure_root_provides.delay(product_stream)
        slow_ensure_root_upstreams.delay(product_stream)


def _write_content(new_content, output_file, product_stream) -> tuple[str, str]:
    with open(output_file, "w") as fh:
        fh.write(json.dumps(new_content, indent=4))
    created_at = new_content["creationInfo"]["created"]
    new_relationships = new_content["relationships"]
    document_uuid = get_document_uuid(new_relationships, product_stream)
    cpu_validate_ps_manifest.delay(product_stream)
    return created_at, document_uuid


def get_document_uuid(new_relationships, product_stream):
    document_uuid = ""
    describes_relationship_count = 0
    for relationship in new_relationships:
        if relationship["relationshipType"] == RelationshipType.DESCRIBES.name:
            describes_relationship_count += 1
            document_uuid = relationship["relatedSpdxElement"]
    if describes_relationship_count != 1:
        raise ValueError(f"Did not find product manifest id for {product_stream}")
    return document_uuid


# Added because of PSDEVOPS-1068
@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def slow_ensure_root_upstreams(product_stream: str) -> int:
    logger.info(f"slow_ensure_root_upstreams called for {product_stream}")
    saved_count = 0
    ps = ProductStream.objects.get(name=product_stream)
    for root_c in ps.components.filter(type=Component.Type.CONTAINER_IMAGE).manifest_components(
        ofuri=ps.ofuri
    ):
        if root_c.get_upstreams_pks().count() != root_c.upstreams.count():
            logger.info(f"saving component taxonomy for {root_c.purl} in stream {ps.name}")
            root_c.save_component_taxonomy()
            saved_count += 1
    return saved_count


# Added because of PSDEVOPS-1070
@app.task(
    base=Singleton,
    autoretry_for=RETRYABLE_ERRORS,
    retry_kwargs=RETRY_KWARGS,
    soft_time_limit=settings.CELERY_LONGEST_SOFT_TIME_LIMIT,
)
def slow_ensure_root_provides(product_stream: str) -> int:
    logger.info(f"slow_ensure_root_provides called for {product_stream}")
    saved_count = 0
    ps = ProductStream.objects.get(name=product_stream)
    for root_c in ps.components.manifest_components(ofuri=ps.ofuri):
        if len(root_c.get_provides_pks()) != root_c.provides.count():
            logger.info(f"saving component taxonomy for {root_c.purl} in stream {ps.name}")
            root_c.save_component_taxonomy()
            saved_count += 1
    return saved_count
