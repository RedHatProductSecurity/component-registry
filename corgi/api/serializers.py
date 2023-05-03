import datetime
import logging
from abc import abstractmethod
from collections import defaultdict
from typing import Iterable, Optional, Union
from urllib.parse import quote
from uuid import UUID

from django.conf import settings
from django.db.models.manager import Manager
from drf_spectacular.utils import extend_schema_field
from rest_framework import serializers

from corgi.api.constants import CORGI_API_URL, CORGI_STATIC_URL
from corgi.core.constants import MODEL_FILTER_NAME_MAPPING
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
        return Product.objects.filter(ofuri=ofuri).using("read_only").first(), "Product"
    elif ofuri_len == 5:
        return (
            ProductVariant.objects.filter(ofuri=ofuri).using("read_only").first(),
            "ProductVariant",
        )
    elif ofuri_len != 4:
        return missing_or_invalid

    # ProductVersions and ProductStreams both have 4 parts in their ofuri
    # Looking for matching ProductStreams first where version and stream share an ofuri
    # See CORGI-499
    if stream := ProductStream.objects.filter(ofuri=ofuri).using("read_only").first():
        return stream, "ProductStream"
    elif version := ProductVersion.objects.filter(ofuri=ofuri).using("read_only").first():
        return version, "ProductVersion"
    # TODO: Channels don't define an ofuri - should they?
    # else we know it's a version / stream but couldn't find a match
    return missing_or_invalid


def get_upstream_link(
    model_ofuri: str,
    model_name: str,
) -> str:
    """Return a link to a list of upstream components for some ProductModel subclass."""
    filter_name = MODEL_FILTER_NAME_MAPPING[model_name]
    return f"{CORGI_API_URL}/components?{filter_name}={model_ofuri}&namespace=UPSTREAM&view=summary"


def get_model_id_link(
    model_name: str, uuid_or_build_id: Union[int, str, UUID], manifest=False
) -> str:
    """Generic method to get an ID-based link for an arbitrary Model subclass."""
    link = f"{CORGI_API_URL}/{model_name}/{uuid_or_build_id}"
    if manifest:
        link = f"{link}/manifest?format=json"
    return link


def get_channel_data_list(manager: Manager["Channel"]) -> list[dict[str, str]]:
    """Generic method to get a list of {name, link, uuid} data for a ProductModel subclass."""
    # A little different than get_product_data_list - we're always iterating over a manager
    # And channels have no ofuri, so we return a model UUID link instead
    return [
        {"name": name, "link": get_model_id_link("channels", uuid), "uuid": str(uuid)}
        for (name, uuid) in manager.values_list("name", "uuid").using("read_only").iterator()
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
        .using("read_only")
        .iterator()
    ]


def get_product_relations(instance_name: str) -> list[dict[str, str]]:
    """Generic method to get a distinct list of PCR IDs and types for a ProductModel subclass."""
    related_pcrs = (
        ProductComponentRelation.objects.filter(product_ref=instance_name)
        .values_list("type", "external_system_id")
        .distinct("external_system_id")
        .using("read_only")
    )
    return [{"type": pcr_type, "external_system_id": pcr_id} for (pcr_type, pcr_id) in related_pcrs]


def parse_fields(fields: list[str]) -> tuple[set[str], dict[str, set[str]]]:
    """
    Parse each include/exclude item into list of current level fields
    and dict of next level fields.

    Example:
        [uuid, affects, affects.uuid, affects.trackers.uuid]

        ->

        ["uuid", "affects"]
        {"affects": ["uuid", "trackers.uuid"]}

    """

    current_level_fields = set()
    next_level_fields = defaultdict(set)

    for field in fields:
        if "." in field:
            related_field, next_level_field = field.split(".", maxsplit=1)
            next_level_fields[related_field].add(next_level_field)
        else:
            current_level_fields.add(field)

    return (
        current_level_fields,
        {key: value for key, value in next_level_fields.items()},
    )


class IncludeExcludeFieldsSerializer(serializers.ModelSerializer):
    """
    Abstract serializer for include/exclude fields logic with nested serializers

    include_fields and exclude_fields are obtained either from request or in case
    of the nested serializer from the context which is passed from the parent
    serializer

    Filtering on parent serializer:
        include_fields=uuid,cve_id

    Filtering on nested serializer:
        include_fields=affects.uuid,affects.trackers
    """

    class Meta:
        abstract = True

    def __init__(self, *args, **kwargs):
        # Instantiate the superclass normally
        super().__init__(*args, **kwargs)

        request = self.context.get("request")

        # Get include/exclude fields from request
        if request:
            include_fields_param = request.query_params.get("include_fields")
            exclude_fields_param = request.query_params.get("exclude_fields")

            include_fields = include_fields_param.split(",") if include_fields_param else []
            exclude_fields = exclude_fields_param.split(",") if exclude_fields_param else []

        # Get include/exclude fields from context passed from parent serializer
        else:
            include_fields = self.context.get("include_fields", [])
            exclude_fields = self.context.get("exclude_fields", [])

        (
            self._include_fields,
            self._next_level_include_fields,
        ) = parse_fields(include_fields)

        (
            self._exclude_fields,
            self._next_level_exclude_fields,
        ) = parse_fields(exclude_fields)

        # Drop fields based on include/exclude fields
        existing_fields = set(self.fields)
        for field_name in existing_fields:
            if not self._is_field_visible(field_name):
                self.fields.pop(field_name, None)

    def _is_field_visible(self, field: str) -> bool:
        """Get field visibility based on include/exclude fields logic"""
        # Field is needed for next level include fields, don't drop it
        if field in self._next_level_include_fields:
            return True

        # Include fields on current level were given and field is not in it, drop it
        elif self._include_fields and field not in self._include_fields:
            return False

        # Field is in exclude fields and not in include fields, drop it
        elif field in self._exclude_fields and field not in self._include_fields:
            return False

        # Include fields on current level were not given however there are
        # next level include fields, drop the field
        elif not self._include_fields and self._next_level_include_fields:
            return False

        else:
            return True

    def get_include_exclude_serializer(self, fieldname, serializer, instance, many=True):
        """return include exclude Serializer when specified"""
        if self._next_level_include_fields.get(
            fieldname, []
        ) or self._next_level_exclude_fields.get(fieldname, []):

            context = {
                "include_fields": self._next_level_include_fields.get(fieldname, []),
                "exclude_fields": self._next_level_exclude_fields.get(fieldname, []),
            }
            return serializer(instance=instance, many=many, read_only=True, context=context)
        return None


class TagSerializer(serializers.Serializer):
    name = serializers.SlugField(allow_blank=False)
    value = serializers.CharField(max_length=1024, allow_blank=True, default="")
    created_at = serializers.DateTimeField(read_only=True)


class SoftwareBuildSerializer(IncludeExcludeFieldsSerializer):
    """Show detailed information for SoftwareBuild(s).
    Add or remove fields using ?include_fields=&exclude_fields="""

    tags = TagSerializer(many=True, read_only=True)

    link = serializers.SerializerMethodField()
    web_url = serializers.SerializerMethodField()
    components = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: SoftwareBuild) -> str:
        return get_model_id_link("builds", instance.pk)

    @staticmethod
    def get_web_url(build: SoftwareBuild) -> str:
        if build.build_type == SoftwareBuild.Type.BREW:
            return f"{settings.BREW_WEB_URL}/brew/buildinfo?buildID={build.build_id}"
        elif build.build_type == SoftwareBuild.Type.KOJI:
            return f"{settings.BREW_DOWNLOAD_ROOT_URL}/koji/buildinfo?buildID={build.build_id}"
        elif build.build_type == SoftwareBuild.Type.CENTOS:
            return f"{settings.CENTOS_DOWNLOAD_ROOT_URL}/koji/buildinfo?buildID={build.build_id}"
        return ""

    @staticmethod
    def get_components(instance: SoftwareBuild) -> list[dict[str, str]]:
        return get_component_data_list(instance.components.values_list("purl", flat=True))

    class Meta:
        model = SoftwareBuild
        fields = (
            "uuid",
            "link",
            "web_url",
            "build_id",
            "build_type",
            "name",
            "source",
            "tags",
            "created_at",
            "last_changed",
            "components",
        )
        read_only_fields = fields


class SoftwareBuildSummarySerializer(IncludeExcludeFieldsSerializer):
    """Show summary information for a SoftwareBuild.
    Add or remove fields using ?include_fields=&exclude_fields="""

    link = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: SoftwareBuild) -> str:
        return get_model_id_link("builds", instance.pk)

    class Meta:
        model = SoftwareBuild
        fields = ("link", "build_id", "build_type", "name", "source")
        read_only_fields = fields


class ProductTaxonomySerializer(IncludeExcludeFieldsSerializer):
    def get_products(
        self, instance: Union[ProductModel, ProductTaxonomyMixin]
    ) -> list[dict[str, str]]:
        include_exclude_serializer = self.get_include_exclude_serializer(
            "products", ProductSerializer, instance.products
        )
        if include_exclude_serializer:
            return include_exclude_serializer.data
        return get_product_data_list("products", instance.products)

    def get_product_versions(
        self, instance: Union[ProductModel, ProductTaxonomyMixin]
    ) -> list[dict[str, str]]:
        include_exclude_serializer = self.get_include_exclude_serializer(
            "product_versions", ProductVersionSerializer, instance.productversions
        )
        if include_exclude_serializer:
            return include_exclude_serializer.data
        return get_product_data_list("product_versions", instance.productversions)

    def get_product_streams(
        self, instance: Union[ProductModel, ProductTaxonomyMixin]
    ) -> list[dict[str, str]]:
        include_exclude_serializer = self.get_include_exclude_serializer(
            "product_streams", ProductStreamSerializer, instance.productstreams
        )
        if include_exclude_serializer:
            return include_exclude_serializer.data
        return get_product_data_list("product_streams", instance.productstreams)

    def get_product_variants(
        self, instance: Union[ProductModel, ProductTaxonomyMixin]
    ) -> list[dict[str, str]]:
        include_exclude_serializer = self.get_include_exclude_serializer(
            "product_variants", ProductVariantSerializer, instance.productvariants
        )
        if include_exclude_serializer:
            return include_exclude_serializer.data
        return get_product_data_list("product_variants", instance.productvariants)

    def get_channels(self, instance: Union[Component, ProductModel]) -> list[dict[str, str]]:
        include_exclude_serializer = self.get_include_exclude_serializer(
            "channels", ChannelSerializer, instance.channels, many=False
        )
        if include_exclude_serializer:
            return include_exclude_serializer.data
        return get_channel_data_list(instance.channels)

    class Meta:
        abstract = True


class ComponentSerializer(ProductTaxonomySerializer):
    """Show detailed information for a Component.
    Add or remove fields using ?include_fields=&exclude_fields="""

    software_build = serializers.SerializerMethodField()
    tags = TagSerializer(many=True, read_only=True)

    link = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()
    channels = serializers.SerializerMethodField()

    provides = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()

    manifest = serializers.SerializerMethodField()

    @extend_schema_field(SoftwareBuildSummarySerializer(many=False, read_only=True))
    def get_software_build(self, obj):
        context = {
            "include_fields": self._next_level_include_fields.get("software_build", []),
            "exclude_fields": self._next_level_exclude_fields.get("software_build", []),
        }

        serializer = SoftwareBuildSummarySerializer(
            instance=obj.software_build, many=False, read_only=True, context=context
        )
        return serializer.data

    @staticmethod
    def get_link(instance: Component) -> str:
        return get_component_purl_link(instance.purl)

    # @staticmethod
    def get_provides(self, instance: Component):
        include_exclude_serializer = self.get_include_exclude_serializer(
            "provides", ComponentSerializer, instance.provides
        )
        if include_exclude_serializer:
            return include_exclude_serializer.data
        return get_component_data_list(
            instance.provides.values_list("purl", flat=True).using("read_only").iterator()
        )

    def get_sources(self, instance: Component):
        include_exclude_serializer = self.get_include_exclude_serializer(
            "sources", ComponentSerializer, instance.sources
        )
        if include_exclude_serializer:
            return include_exclude_serializer.data
        return get_component_data_list(
            instance.sources.values_list("purl", flat=True).using("read_only").iterator()
        )

    def get_upstreams(self, instance: Component):
        include_exclude_serializer = self.get_include_exclude_serializer(
            "upstreams", ComponentSerializer, instance.upstreams
        )
        if include_exclude_serializer:
            return include_exclude_serializer.data
        return get_component_data_list(
            instance.upstreams.values_list("purl", flat=True).using("read_only").iterator()
        )

    @staticmethod
    def get_manifest(instance: Component) -> str:
        return get_model_id_link("components", instance.uuid, manifest=True)

    class Meta:
        model = Component
        fields = (
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
            "el_match",
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
        )
        read_only_fields = fields


class ComponentListSerializer(IncludeExcludeFieldsSerializer):
    """List all Components. Add or remove fields using ?include_fields=&exclude_fields="""

    link = serializers.SerializerMethodField()
    build_completion_dt = serializers.SerializerMethodField()

    @staticmethod
    def get_build_completion_dt(instance: Component) -> Optional[datetime.datetime]:
        if instance.software_build:
            # instance is a root component with a linked software_build
            return instance.software_build.completion_time
        # else the software_build is null / we're not a root component
        return None

    @staticmethod
    def get_link(instance: Component) -> str:
        return get_component_purl_link(instance.purl)

    class Meta:
        model = Component
        fields = (
            "link",
            "purl",
            "name",
            "version",
            "nvr",
            "build_completion_dt",
        )
        read_only_fields = fields


class ProductModelSerializer(ProductTaxonomySerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()
    builds = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()
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
        return get_upstream_link(instance.ofuri, type(instance).__name__)

    @staticmethod
    def get_builds(instance: ProductModel) -> str:
        return get_model_ofuri_link("builds", instance.ofuri)

    @staticmethod
    def get_build_count(instance: ProductModel) -> int:
        return instance.builds.count()

    @staticmethod
    def get_manifest(instance: ProductStream) -> str:
        if not instance.components.exists():
            return ""
        return f"{CORGI_STATIC_URL}{instance.name}-{instance.pk}.json"

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
        read_only_fields = fields


class ProductSerializer(ProductModelSerializer):
    """Show detailed information for Product(s).
    Add or remove fields using ?include_fields=&exclude_fields="""

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
        read_only_fields = fields


class ProductVersionSerializer(ProductModelSerializer):
    """Show detailed information for ProductVersion(s).
    Add or remove fields using ?include_fields=&exclude_fields="""

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
        read_only_fields = fields


class ProductStreamSerializer(ProductModelSerializer):
    """Show detailed information for ProductStream(s).
    Add or remove fields using ?include_fields=&exclude_fields="""

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
        read_only_fields = fields


class ProductStreamSummarySerializer(ProductModelSerializer):
    """Show summary information for a ProductStream.
    Add or remove fields using ?include_fields=&exclude_fields="""

    manifest = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: ProductStream) -> str:
        return get_model_ofuri_link("product_streams", instance.ofuri)

    class Meta(ProductModelSerializer.Meta):
        model = ProductStream
        fields = [
            "link",
            "ofuri",
            "name",
            "components",
            "upstreams",
            "manifest",
        ]
        read_only_fields = fields


class ComponentProductStreamSummarySerializer(ProductModelSerializer):
    """custom component view displaying product information."""

    component_link = serializers.SerializerMethodField()
    manifest = serializers.SerializerMethodField()
    component_purl = serializers.SerializerMethodField()

    @staticmethod
    def get_component_purl(obj):
        return obj.component_purl

    @staticmethod
    def get_link(instance: ProductStream) -> str:
        return get_model_ofuri_link("product_streams", instance.ofuri)

    @staticmethod
    def get_component_link(instance: Component) -> str:
        return get_component_purl_link(instance.component_purl)  # type: ignore

    class Meta(ProductModelSerializer.Meta):
        model = ProductStream
        fields = [
            "link",
            "ofuri",
            "name",
            "components",
            "upstreams",
            "manifest",
            "component_link",
            "component_purl",
        ]
        read_only_fields = fields


class ProductVariantSerializer(ProductModelSerializer):
    """Show detailed information for ProductVariant(s).
    Add or remove fields using ?include_fields=&exclude_fields="""

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
        read_only_fields = fields


class ChannelSerializer(ProductTaxonomySerializer):
    """Show detailed information for Channel(s).
    Add or remove fields using ?include_fields=&exclude_fields="""

    link = serializers.SerializerMethodField()
    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    class Meta:
        model = Channel
        fields = (
            "uuid",
            "link",
            "last_changed",
            "created_at",
            "name",
            "relative_url",
            "type",
            "description",
            "products",
            "product_versions",
            "product_streams",
            "product_variants",
        )
        read_only_fields = fields

    @staticmethod
    def get_link(instance: ProductVariant) -> str:
        return get_model_id_link("channels", instance.uuid)


class AppStreamLifeCycleSerializer(IncludeExcludeFieldsSerializer):
    """Show detailed information for AppStreamLifeCycle(s).
    Add or remove fields using ?include_fields=&exclude_fields="""

    class Meta:
        model = AppStreamLifeCycle
        fields = "__all__"
        read_only_fields = fields
