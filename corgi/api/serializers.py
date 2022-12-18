import logging
from abc import abstractmethod
from typing import Iterable, Optional, Union
from urllib.parse import quote
from uuid import UUID

from django.conf import settings
from django.db.models.manager import Manager
from rest_framework import serializers

from config import utils
from corgi.api.constants import CORGI_API_VERSION
from corgi.core.models import (
    AppStreamLifeCycle,
    Channel,
    Component,
    Product,
    ProductComponentRelation,
    ProductModel,
    ProductStream,
    ProductTaxonomyMixin,
    ProductVariant,
    ProductVersion,
    SoftwareBuild,
)

logger = logging.getLogger(__name__)

# Generic URL prefix
# TODO remove running_community check once domain is established
if not utils.running_dev():
    CORGI_API_URL = f"https://{settings.CORGI_DOMAIN}/api/{CORGI_API_VERSION}"
else:
    CORGI_API_URL = f"http://localhost:8008/api/{CORGI_API_VERSION}"


def get_component_data_list(component_list: Iterable[str]) -> list[dict[str, str]]:
    """Generic method to get a list of {link, purl} data for some list of components."""
    return [
        {
            "link": get_component_purl_link(purl),
            "purl": purl,
        }
        for purl in component_list
    ]


def get_component_purl_link(purl: str) -> str:
    """Generic method to get a pURL link for a Component."""
    return f"{CORGI_API_URL}/components?purl={quote(purl)}"


def get_model_ofuri_link(
    model_name: str,
    ofuri: str,
    related_type: Optional[str] = None,
    related_namespace: Optional[str] = None,
    view: Optional[str] = None,
) -> str:
    """Generic method to get an ofuri link for an arbitrary Model subclass."""
    link = f"{CORGI_API_URL}/{model_name}?ofuri={ofuri}"
    if model_name == "components":
        link = f"{link}"
    if related_type:
        link += f"&type={related_type}"
    if related_namespace:
        link += f"&namespace={related_namespace}"
    if view:
        link += f"&view={view}"
    return link


def get_model_ofuri_type(ofuri: str) -> tuple[Optional[ProductModel], str]:
    """Return a tuple of (model instance, model name) given some ofuri
    Returns (None, "model name") if no matching product / variant was found
    Returns (None, "") if no matching version / stream was found
    Returns (None, "") if an ofuri does not have 3, 4, or 5 parts"""
    missing_or_invalid = None, ""
    if not ofuri:
        return missing_or_invalid
    ofuri_len = len(ofuri.split(":"))

    if ofuri_len == 3:
        return Product.objects.filter(ofuri=ofuri).first(), "Product"
    elif ofuri_len == 5:
        return ProductVariant.objects.filter(ofuri=ofuri).first(), "ProductVariant"
    elif ofuri_len != 4:
        return missing_or_invalid

    # ProductVersions and ProductStreams both have 4 parts in their ofuri
    if version := ProductVersion.objects.filter(ofuri=ofuri).first():
        return version, "ProductVersion"
    elif stream := ProductStream.objects.filter(ofuri=ofuri).first():
        return stream, "ProductStream"
    # TODO: Channels don't define an ofuri - should they?
    # else we know it's a version / stream but couldn't find a match
    return missing_or_invalid


def get_upstream_link(
    product_stream: str,
) -> str:
    """method to return all a product_stream upstream components."""
    link = (
        f"{CORGI_API_URL}/components?product_streams={product_stream}&"
        "namespace=UPSTREAM&view=summary"
    )
    return link


def get_model_id_link(
    model_name: str, uuid_or_build_id: Union[int, str, UUID], manifest=False
) -> str:
    """Generic method to get an ID-based link for an arbitrary Model subclass."""
    link = f"{CORGI_API_URL}/{model_name}/{uuid_or_build_id}"
    return link if not manifest else f"{link}/manifest"


def get_channel_data_list(manager: Manager["Channel"]) -> list[dict[str, str]]:
    """Generic method to get a list of {name, link, uuid} data for a ProductModel subclass."""
    # A little different than get_product_data_list - we're always iterating over a manager
    # And channels have no ofuri, so we return a model UUID link instead
    return [
        {"name": name, "link": get_model_id_link("channels", uuid), "uuid": str(uuid)}
        for (name, uuid) in manager.values_list("name", "uuid")
    ]


def get_product_data_list(
    model_name: str, obj_or_manager: Union[ProductModel, Manager]
) -> list[dict[str, str]]:
    """Generic method to get a list of {name, link, ofuri} data for a ProductModel subclass."""
    if isinstance(obj_or_manager, ProductModel):
        # When this method receives e.g. a ProductVersion's products property,
        # we're accessing the forward side of a relation with only one object
        return [
            {
                "name": obj_or_manager.name,
                "link": get_model_ofuri_link(model_name, obj_or_manager.ofuri),
                "ofuri": obj_or_manager.ofuri,
            },
        ]
    # When this method receives e.g. a ProductVersion's productstreams property,
    # we're accessing the reverse side of a relation with many objects (via a manager)
    return [
        {"name": name, "link": get_model_ofuri_link(model_name, ofuri), "ofuri": ofuri}
        for (name, ofuri) in obj_or_manager.values_list("name", "ofuri")
    ]


def get_product_relations(instance_name: str) -> list[dict[str, str]]:
    """Generic method to get a distinct list of PCR IDs and types for a ProductModel subclass."""
    related_pcrs = (
        ProductComponentRelation.objects.filter(product_ref=instance_name)
        .values_list("type", "external_system_id")
        .distinct("external_system_id")
    )
    return [{"type": pcr_type, "external_system_id": pcr_id} for (pcr_type, pcr_id) in related_pcrs]


class TagSerializer(serializers.Serializer):
    name = serializers.SlugField(allow_blank=False)
    value = serializers.CharField(max_length=1024, allow_blank=True, default="")
    created_at = serializers.DateTimeField(read_only=True)


class SoftwareBuildSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)

    link = serializers.SerializerMethodField(read_only=True)
    web_url = serializers.SerializerMethodField()
    components = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: SoftwareBuild) -> str:
        return get_model_id_link("builds", instance.build_id)

    @staticmethod
    def get_web_url(build: SoftwareBuild) -> str:
        if build.type == SoftwareBuild.Type.BREW:
            return f"{settings.BREW_WEB_URL}/brew/buildinfo?buildID={build.build_id}"
        return ""

    @staticmethod
    def get_components(instance: SoftwareBuild) -> list[dict[str, str]]:
        return get_component_data_list(instance.components.values_list("purl", flat=True))

    class Meta:
        model = SoftwareBuild
        fields = [
            "link",
            "web_url",
            "build_id",
            "type",
            "name",
            "source",
            "tags",
            "created_at",
            "last_changed",
            "components",
            # "meta_attr",
        ]


class SoftwareBuildSummarySerializer(serializers.ModelSerializer):

    link = serializers.SerializerMethodField(read_only=True)

    @staticmethod
    def get_link(instance: SoftwareBuild) -> str:
        return get_model_id_link("builds", instance.build_id)

    class Meta:
        model = SoftwareBuild
        fields = ["link", "build_id", "type", "name", "source"]


class ProductTaxonomySerializer(serializers.ModelSerializer):
    @staticmethod
    def get_products(instance: Union[ProductModel, ProductTaxonomyMixin]) -> list[dict[str, str]]:
        return get_product_data_list("products", instance.products)

    @staticmethod
    def get_product_versions(
        instance: Union[ProductModel, ProductTaxonomyMixin]
    ) -> list[dict[str, str]]:
        return get_product_data_list("product_versions", instance.productversions)

    @staticmethod
    def get_product_streams(
        instance: Union[ProductModel, ProductTaxonomyMixin]
    ) -> list[dict[str, str]]:
        return get_product_data_list("product_streams", instance.productstreams)

    @staticmethod
    def get_product_variants(
        instance: Union[ProductModel, ProductTaxonomyMixin]
    ) -> list[dict[str, str]]:
        return get_product_data_list("product_variants", instance.productvariants)

    @staticmethod
    def get_channels(instance: Union[Component, ProductModel]) -> list[dict[str, str]]:
        return get_channel_data_list(instance.channels)

    class Meta:
        abstract = True


class ComponentSerializer(ProductTaxonomySerializer):
    software_build = SoftwareBuildSummarySerializer(many=False)
    tags = TagSerializer(many=True, read_only=True)

    link = serializers.SerializerMethodField(read_only=True)

    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()
    channels = serializers.SerializerMethodField()

    provides = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()

    manifest = serializers.SerializerMethodField(read_only=True)

    @staticmethod
    def get_link(instance: Component) -> str:
        return get_component_purl_link(instance.purl)

    @staticmethod
    def get_provides(instance: Component) -> list[dict[str, str]]:
        return get_component_data_list(instance.get_provides_purls())

    @staticmethod
    def get_sources(instance: Component) -> list[dict[str, str]]:
        return get_component_data_list(instance.get_sources_purls())

    @staticmethod
    def get_upstreams(instance: Component) -> list[dict[str, str]]:
        return get_component_data_list(instance.get_upstreams_purls())

    @staticmethod
    def get_manifest(instance: Component) -> str:
        return get_model_id_link("components", instance.uuid, manifest=True)

    class Meta:
        model = Component
        fields = [
            "link",
            "download_url",
            "uuid",
            "type",
            "namespace",
            "purl",
            "name",
            "description",
            "related_url",
            "tags",
            "version",
            "release",
            "arch",
            "nvr",
            "nevra",
            "epoch",
            "copyright_text",
            "license_concluded",
            "license_concluded_list",
            "license_declared",
            "license_declared_list",
            "openlcs_scan_url",
            "openlcs_scan_version",
            "software_build",
            "errata",
            "products",
            "product_versions",
            "product_streams",
            "product_variants",
            "channels",
            "sources",
            "provides",
            "upstreams",
            "manifest",
            "filename",
        ]


class ComponentListSerializer(serializers.ModelSerializer):

    link = serializers.SerializerMethodField(read_only=True)
    build_completion_dt = serializers.DateTimeField(
        source="software_build.completion_time", read_only=True
    )

    @staticmethod
    def get_link(instance: Component) -> str:
        return get_component_purl_link(instance.purl)

    class Meta:
        model = Component
        fields = [
            "link",
            "purl",
            "name",
            "version",
            "nvr",
            "build_completion_dt",
            # "meta_attr",
        ]
        read_only_fields = fields


class ProductModelSerializer(ProductTaxonomySerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()
    builds = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField(read_only=True)
    build_count = serializers.SerializerMethodField()
    channels = serializers.SerializerMethodField()

    @staticmethod
    @abstractmethod
    def get_link(instance) -> str:
        pass

    @staticmethod
    def get_components(instance: ProductModel) -> str:
        return get_model_ofuri_link("components", instance.ofuri, view="summary")

    @staticmethod
    def get_upstreams(instance: ProductModel) -> str:
        return get_upstream_link(instance.ofuri)

    @staticmethod
    def get_builds(instance: ProductModel) -> str:
        return get_model_ofuri_link("builds", instance.ofuri)

    @staticmethod
    def get_build_count(instance: ProductModel) -> int:
        return instance.builds.count()

    @staticmethod
    def get_manifest(instance: ProductStream) -> str:
        return get_model_id_link("product_streams", instance.uuid, manifest=True)

    @staticmethod
    def get_relations(instance: ProductModel) -> list[dict[str, str]]:
        return get_product_relations(instance.name)

    class Meta:
        abstract = True
        fields = [
            "link",
            "uuid",
            "ofuri",
            "name",
            "description",
            "build_count",
            "builds",
            "components",
            "upstreams",
            "tags",
            "channels",
        ]


class ProductSerializer(ProductModelSerializer):
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: Product) -> str:
        return get_model_ofuri_link("products", instance.ofuri)

    class Meta(ProductModelSerializer.Meta):
        model = Product
        fields = [
            *ProductModelSerializer.Meta.fields,
            "product_versions",
            "product_streams",
            "product_variants",
        ]


class ProductVersionSerializer(ProductModelSerializer):
    products = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: ProductVersion) -> str:
        return get_model_ofuri_link("product_versions", instance.ofuri)

    class Meta(ProductModelSerializer.Meta):
        model = ProductVersion
        fields = [
            *ProductModelSerializer.Meta.fields,
            "products",
            "product_streams",
            "product_variants",
        ]


class ProductStreamSerializer(ProductModelSerializer):
    manifest = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    relations = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: ProductStream) -> str:
        return get_model_ofuri_link("product_streams", instance.ofuri)

    class Meta(ProductModelSerializer.Meta):
        model = ProductStream
        fields = [
            *ProductModelSerializer.Meta.fields,
            "cpe",
            "active",
            "brew_tags",
            "yum_repositories",
            "composes",
            "et_product_versions",
            "manifest",
            "relations",
            "products",
            "product_versions",
            "product_variants",
            "channels",
        ]


class ProductVariantSerializer(ProductModelSerializer):
    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()

    relations = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: ProductVariant) -> str:
        return get_model_ofuri_link("product_variants", instance.ofuri)

    class Meta(ProductModelSerializer.Meta):
        model = ProductVariant
        fields = [
            *ProductModelSerializer.Meta.fields,
            "relations",
            "products",
            "product_versions",
            "product_streams",
            "channels",
        ]


class ChannelSerializer(ProductTaxonomySerializer):
    link = serializers.SerializerMethodField(read_only=True)
    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    class Meta:
        model = Channel
        fields = [
            "uuid",
            "link",
            "last_changed",
            "created_at",
            "name",
            "relative_url",
            "type",
            "description",
            "meta_attr",
            "products",
            "product_versions",
            "product_streams",
            "product_variants",
        ]

    @staticmethod
    def get_link(instance: ProductVariant) -> str:
        return get_model_id_link("channels", instance.uuid)


class AppStreamLifeCycleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppStreamLifeCycle
        fields = "__all__"
