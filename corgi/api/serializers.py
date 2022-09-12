import logging
from urllib.parse import quote

from django.conf import settings
from rest_framework import serializers

from config import utils
from corgi.api.constants import CORGI_API_VERSION
from corgi.core.models import (
    AppStreamLifeCycle,
    Channel,
    Component,
    Product,
    ProductComponentRelation,
    ProductStream,
    ProductVariant,
    ProductVersion,
    SoftwareBuild,
)

logger = logging.getLogger(__name__)

# Generic URL prefix
if not utils.running_dev():
    CORGI_API_URL = f"https://{settings.CORGI_DOMAIN}/api/{CORGI_API_VERSION}"
else:
    CORGI_API_URL = f"http://localhost:8008/api/{CORGI_API_VERSION}"


def get_component_data_list(component_list: list[str]) -> list[dict[str, str]]:
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


def get_model_ofuri_link(model_name: str, ofuri: str, related_type=None) -> str:
    """Generic method to get an ofuri link for an arbitrary Model subclass."""
    link = f"{CORGI_API_URL}/{model_name}?ofuri={ofuri}&type=SRPM&limit=3000"
    return link if not related_type else f"{link}&type={related_type}"


def get_model_id_link(model_name: str, uuid_or_build_id, manifest=False) -> str:
    """Generic method to get an ID-based link for an arbitrary Model subclass."""
    link = f"{CORGI_API_URL}/{model_name}/{uuid_or_build_id}"
    return link if not manifest else f"{link}/manifest"


def get_product_data_list(
    model_class, model_name: str, name_list: list[str]
) -> list[dict[str, str]]:
    """Generic method to get a list of {name, link, ofuri} data for a ProductModel subclass."""
    data_list = []
    for name in name_list:
        data = {"name": name}
        obj = model_class.objects.filter(name=name).first()
        if obj:
            data["link"] = get_model_ofuri_link(model_name, obj.ofuri)
            data["ofuri"] = obj.ofuri
        data_list.append(data)
    return data_list


def get_product_data_list_by_ofuri(
    model_class, model_name: str, ofuri_list: list[str]
) -> list[dict[str, str]]:
    """Special method to get a list of {ofuri, link, name} data for a ProductModel subclass.
    Filters by ofuri instead of name."""
    data_list = []
    for ofuri in ofuri_list:
        data = {"ofuri": ofuri}
        obj = model_class.objects.filter(ofuri=ofuri).first()
        if obj:
            data["link"] = get_model_ofuri_link(model_name, ofuri)
            data["name"] = obj.name
        data_list.append(data)
    return data_list


def get_product_relations(instance_name: str) -> list[dict[str, str]]:
    """Generic method to get a distinct list of PCR IDs and types for a ProductModel subclass."""
    related_pcrs = ProductComponentRelation.objects.filter(product_ref=instance_name).distinct(
        "external_system_id"
    )
    relations = [
        {"type": pcr.type, "external_system_id": pcr.external_system_id} for pcr in related_pcrs
    ]
    return relations


class TagSerializer(serializers.Serializer):
    name = serializers.SlugField(allow_blank=False)
    value = serializers.CharField(max_length=1024, allow_blank=True, default="")
    created_at = serializers.DateTimeField(read_only=True)


class SoftwareBuildSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)

    link = serializers.SerializerMethodField()
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
        return get_component_data_list(instance.components)

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

    link = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: SoftwareBuild) -> str:
        return get_model_id_link("builds", instance.build_id)

    class Meta:
        model = SoftwareBuild
        fields = ["link", "build_id", "type", "name", "source"]


class ComponentSerializer(serializers.ModelSerializer):
    software_build = SoftwareBuildSummarySerializer(many=False)
    tags = TagSerializer(many=True, read_only=True)

    link = serializers.SerializerMethodField()
    download_url = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    provides = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: Component) -> str:
        return get_component_purl_link(instance.purl)

    @staticmethod
    def get_download_url(component: Component) -> str:
        if component.software_build and component.software_build.type == SoftwareBuild.Type.BREW:
            # RPM ex:
            # /vol/rhel-7/packages/emacs/24.3/23.el7/ppc64le/emacs-common-24.3-23.el7.ppc64le.rpm
            if component.type in (Component.Type.RPM, Component.Type.SRPM):
                return (
                    f"{settings.BREW_DOWNLOAD_ROOT_URL}/vol/"
                    f"{component.software_build.meta_attr['volume_name']}/packages/"
                    f"{component.software_build.name}/{component.version}/{component.release}/"
                    f"{component.arch}/{component.nvr}.{component.arch}.rpm"
                )
            # Image ex:
            # /packages/hco-bundle-registry-container/v4.13.0.rhel9/152/images/docker-image-sha256:bba727643de6be9ad835b23614ecd83b55ef6bcc63a7a67d64588e7241da15b4.x86_64.tar.gz
            # TODO: ensure all container images store the filename of the image archive they are
            #  included in. This is not the digest SHA but the config layer SHA.
            # elif component.type == Component.Type.CONTAINER_IMAGE:
            #     return (
            #         f"{settings.BREW_DOWNLOAD_ROOT_URL}/packages/{component.name}/"
            #         f"{component.version}/{component.release}/images/{component.filename}"
            #     )

            # All other component types are either not currently supported or have no downloadable
            # artifacts (e.g. RHEL module builds).
        return ""

    @staticmethod
    def get_products(instance: Component) -> list[dict[str, str]]:
        return get_product_data_list_by_ofuri(Product, "products", instance.products)

    @staticmethod
    def get_product_versions(instance: Component) -> list[dict[str, str]]:
        return get_product_data_list_by_ofuri(
            ProductVersion, "product_versions", instance.product_versions
        )

    @staticmethod
    def get_product_streams(instance: Component) -> list[dict[str, str]]:
        return get_product_data_list_by_ofuri(
            ProductStream, "product_streams", instance.product_streams
        )

    # Above 3 are special - they filter on ofuri= instead of name=
    @staticmethod
    def get_product_variants(instance: Component) -> list[dict[str, str]]:
        return get_product_data_list(ProductVariant, "product_variants", instance.product_variants)

    @staticmethod
    def get_provides(instance: Component) -> list[dict[str, str]]:
        return get_component_data_list(instance.get_provides())

    @staticmethod
    def get_sources(instance: Component) -> list[dict[str, str]]:
        return get_component_data_list(instance.get_source())

    @staticmethod
    def get_upstreams(instance: Component) -> list[dict[str, str]]:
        # return get_component_data_list(instance.get_upstreams())
        return []

    class Meta:
        model = Component
        fields = [
            "link",
            "download_url",
            "uuid",
            "type",
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
            "license",
            "license_list",
            "software_build",
            "errata",
            "products",
            "product_versions",
            "product_streams",
            "product_variants",
            # "channels",
            "sources",
            "provides",
            "upstreams",
            # "meta_attr",
        ]


class ComponentListSerializer(serializers.ModelSerializer):

    link = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: Component) -> str:
        return get_component_purl_link(instance.purl)

    class Meta:
        model = Component
        fields = [
            "link",
            "purl",
        ]


class ProductSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()
    builds = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()
    build_count = serializers.SerializerMethodField()

    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: Product) -> str:
        return get_model_ofuri_link("products", instance.ofuri)

    @staticmethod
    def get_product_versions(instance: Product) -> list[dict[str, str]]:
        return get_product_data_list(ProductVersion, "product_versions", instance.product_versions)

    @staticmethod
    def get_product_streams(instance: Product) -> list[dict[str, str]]:
        return get_product_data_list(ProductStream, "product_streams", instance.product_streams)

    @staticmethod
    def get_product_variants(instance: Product) -> list[dict[str, str]]:
        return get_product_data_list(ProductVariant, "product_variants", instance.product_variants)

    @staticmethod
    def get_components(instance: Product) -> str:
        return get_model_ofuri_link("components", instance.ofuri)

    @staticmethod
    def get_upstreams(instance: Product) -> str:
        return get_model_ofuri_link("components", instance.ofuri, related_type="UPSTREAM")

    @staticmethod
    def get_builds(instance: Product) -> str:
        return get_model_ofuri_link("builds", instance.ofuri)

    @staticmethod
    def get_build_count(instance: Product) -> int:
        return instance.builds.count()

    class Meta:
        model = Product
        fields = [
            "link",
            "uuid",
            "ofuri",
            "name",
            "description",
            # "coverage",
            "build_count",
            "builds",
            "components",
            "upstreams",
            "tags",
            "product_versions",
            "product_streams",
            "product_variants",
            # "channels",
            # "meta_attr",
        ]


class ProductVersionSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()
    builds = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()
    build_count = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: ProductVersion) -> str:
        return get_model_ofuri_link("product_versions", instance.ofuri)

    @staticmethod
    def get_products(instance: ProductVersion) -> list[dict[str, str]]:
        return get_product_data_list(Product, "products", instance.products)

    @staticmethod
    def get_product_streams(instance: ProductVersion) -> list[dict[str, str]]:
        return get_product_data_list(ProductStream, "product_streams", instance.product_streams)

    @staticmethod
    def get_product_variants(instance: ProductVersion) -> list[dict[str, str]]:
        return get_product_data_list(ProductVariant, "product_variants", instance.product_variants)

    @staticmethod
    def get_components(instance: ProductVersion) -> str:
        return get_model_ofuri_link("components", instance.ofuri)

    @staticmethod
    def get_upstreams(instance: ProductVersion) -> str:
        return get_model_ofuri_link("components", instance.ofuri, related_type="UPSTREAM")

    @staticmethod
    def get_manifest(instance: ProductVersion) -> str:
        return get_model_id_link("product_versions", instance.uuid, manifest=True)

    @staticmethod
    def get_builds(instance: ProductVersion) -> str:
        return get_model_ofuri_link("builds", instance.ofuri)

    @staticmethod
    def get_build_count(instance: ProductVersion) -> int:
        return instance.builds.count()

    class Meta:
        model = ProductVersion
        fields = [
            "link",
            "uuid",
            "ofuri",
            "name",
            "description",
            # "coverage",
            "build_count",
            "builds",
            "components",
            "upstreams",
            "tags",
            "products",
            "product_streams",
            "product_variants",
            # "channels",
            # "meta_attr",
        ]


class ProductStreamSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()
    manifest = serializers.SerializerMethodField()
    builds = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()
    build_count = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    relations = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: ProductStream) -> str:
        return get_model_ofuri_link("product_streams", instance.ofuri)

    @staticmethod
    def get_products(instance: ProductStream) -> list[dict[str, str]]:
        return get_product_data_list(Product, "products", instance.products)

    @staticmethod
    def get_product_versions(instance: ProductStream) -> list[dict[str, str]]:
        return get_product_data_list(ProductVersion, "product_versions", instance.product_versions)

    @staticmethod
    def get_product_variants(instance: ProductStream) -> list[dict[str, str]]:
        return get_product_data_list(ProductVariant, "product_variants", instance.product_variants)

    @staticmethod
    def get_components(instance: ProductVersion) -> str:
        return get_model_ofuri_link("components", instance.ofuri)

    @staticmethod
    def get_upstreams(instance: ProductStream) -> str:
        return get_model_ofuri_link("components", instance.ofuri, related_type="UPSTREAM")

    @staticmethod
    def get_manifest(instance: ProductStream) -> str:
        return get_model_id_link("product_streams", instance.uuid, manifest=True)

    @staticmethod
    def get_builds(instance: ProductStream) -> str:
        return get_model_ofuri_link("builds", instance.ofuri)

    @staticmethod
    def get_relations(instance: ProductStream) -> list[dict[str, str]]:
        return get_product_relations(instance.name)

    @staticmethod
    def get_build_count(instance: ProductStream) -> int:
        return instance.builds.count()

    class Meta:
        model = ProductStream
        fields = [
            "link",
            "uuid",
            "ofuri",
            "name",
            "cpe",
            "active",
            "brew_tags",
            "yum_repositories",
            "composes",
            "description",
            # "coverage",
            "build_count",
            "builds",
            "manifest",
            "components",
            "upstreams",
            "relations",
            "tags",
            "products",
            "product_versions",
            "product_variants",
            # "channels",
            # "meta_attr",
        ]


class ProductVariantSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()
    manifest = serializers.SerializerMethodField()
    builds = serializers.SerializerMethodField()
    link = serializers.SerializerMethodField()
    build_count = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()

    relations = serializers.SerializerMethodField()

    @staticmethod
    def get_link(instance: ProductVariant) -> str:
        return get_model_ofuri_link("product_variants", instance.ofuri)

    @staticmethod
    def get_products(instance: ProductVariant) -> list[dict[str, str]]:
        return get_product_data_list(Product, "products", instance.products)

    @staticmethod
    def get_product_versions(instance: ProductVariant) -> list[dict[str, str]]:
        return get_product_data_list(ProductVersion, "product_versions", instance.product_versions)

    @staticmethod
    def get_product_streams(instance: ProductVariant) -> list[dict[str, str]]:
        return get_product_data_list(ProductStream, "product_streams", instance.product_streams)

    @staticmethod
    def get_components(instance: ProductVersion) -> str:
        return get_model_ofuri_link("components", instance.ofuri)

    @staticmethod
    def get_upstreams(instance: ProductVersion) -> str:
        return get_model_ofuri_link("components", instance.ofuri, related_type="UPSTREAM")

    @staticmethod
    def get_manifest(instance: ProductVersion) -> str:
        return get_model_id_link("product_variants", instance.uuid, manifest=True)

    @staticmethod
    def get_builds(instance: ProductVariant) -> str:
        return get_model_ofuri_link("builds", instance.ofuri)

    @staticmethod
    def get_relations(instance: ProductVariant) -> list[dict[str, str]]:
        return get_product_relations(instance.name)

    @staticmethod
    def get_build_count(instance: ProductVariant) -> int:
        return instance.builds.count()

    class Meta:
        model = ProductVariant
        fields = [
            "link",
            "uuid",
            "ofuri",
            "name",
            "description",
            "build_count",
            "builds",
            "manifest",
            "components",
            "upstreams",
            "tags",
            "relations",
            "products",
            "product_versions",
            "product_streams",
            # "channels",
            # "meta_attr",
        ]


class ChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Channel
        fields = "__all__"


class AppStreamLifeCycleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppStreamLifeCycle
        fields = "__all__"
