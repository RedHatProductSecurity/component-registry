from celery.utils.log import get_task_logger

from corgi.core.models import Product, ProductStream, ProductStreamTag, ProductVersion

NO_MANIFEST_TAG = "no_manifest"

logger = get_task_logger(__name__)


def apply_stream_no_manifest_tags():
    apply_middleware_stream_no_manifest_tags(NO_MANIFEST_TAG, "")
    apply_rhel_8_9_z_stream_no_manifest_tags(NO_MANIFEST_TAG, "")
    apply_managed_services_no_manifest_tags(NO_MANIFEST_TAG, "")


def apply_middleware_stream_no_manifest_tags(tag_name: str, tag_value: str) -> None:
    for stream_pk in Product.objects.filter(meta_attr__business_unit="Core Middleware").values_list(
        "productstreams", flat=True
    ):
        stream = ProductStream.objects.get(pk=stream_pk)
        _, created = ProductStreamTag.objects.get_or_create(
            name=tag_name, value=tag_value, tagged_model=stream
        )
        if created:
            logger.info(f"Added tag {tag_name}={tag_value} to model {stream.name}")


def apply_rhel_8_9_z_stream_no_manifest_tags(tag_name: str, tag_value: str) -> None:
    for stream_pk in ProductVersion.objects.filter(
        name__in=("rhel-8", "rhel-9"), productstreams__name__endswith=".z"
    ).values_list("productstreams", flat=True):
        stream = ProductStream.objects.get(pk=stream_pk)
        _, created = ProductStreamTag.objects.get_or_create(
            name=tag_name, value=tag_value, tagged_model=stream
        )
        if created:
            logger.info(f"Added tag {tag_name}={tag_value} to model {stream.name}")


def apply_managed_services_no_manifest_tags(tag_name: str, tag_value: str) -> None:
    for stream in ProductStream.objects.filter(meta_attr__managed_service_components__isnull=False):
        _, created = ProductStreamTag.objects.get_or_create(
            name=tag_name, value=tag_value, tagged_model=stream
        )
        if created:
            logger.info(f"Added tag {tag_name}={tag_value} to model {stream.name}")
