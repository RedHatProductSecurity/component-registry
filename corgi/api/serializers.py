import logging
from urllib.parse import quote

from rest_framework import serializers

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


class TagSerializer(serializers.Serializer):
    name = serializers.SlugField(allow_blank=False)
    value = serializers.CharField(max_length=1024, allow_blank=True, default="")
    created_at = serializers.DateTimeField(read_only=True)


class SoftwareBuildSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)

    link = serializers.SerializerMethodField()
    components = serializers.SerializerMethodField()

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/builds/{instance.build_id}"  # noqa
        return None

    def get_components(self, instance):
        request = self.context.get("request")
        components = list()
        for purl in instance.components:
            if request and "HTTP_HOST" in request.META:
                components.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(purl)}",  # noqa
                        "purl": purl,
                    }
                )
        return components

    class Meta:
        model = SoftwareBuild
        fields = [
            "link",
            "build_id",
            "type",
            "name",
            "source",
            "tags",
            "created_at",
            "last_changed",
            "components",
            "meta_attr",
        ]


class SoftwareBuildSummarySerializer(serializers.ModelSerializer):

    link = serializers.SerializerMethodField()

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/builds/{instance.build_id}"  # noqa
        return None

    class Meta:
        model = SoftwareBuild
        fields = ["link", "build_id", "type", "name", "source"]


class ComponentSerializer(serializers.ModelSerializer):
    software_build = SoftwareBuildSummarySerializer(many=False)
    tags = TagSerializer(many=True, read_only=True)

    link = serializers.SerializerMethodField()
    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    provides = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(instance.purl)}"  # noqa
        return None

    def get_products(self, instance):
        request = self.context.get("request")
        p = list()
        for ofuri in instance.products:
            if Product.objects.filter(ofuri=ofuri).exists():
                obj = Product.objects.get(ofuri=ofuri)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/products?ofuri={ofuri}",  # noqa
                        "ofuri": ofuri,
                        "name": obj.name,
                    }
                )
            else:
                p.append(
                    {
                        "ofuri": ofuri,
                    }
                )
        return p

    def get_product_versions(self, instance):
        request = self.context.get("request")
        p = list()
        for ofuri in instance.product_versions:
            if ProductVersion.objects.filter(ofuri=ofuri).exists():
                obj = ProductVersion.objects.get(ofuri=ofuri)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_versions?ofuri={ofuri}",  # noqa
                        "ofuri": ofuri,
                        "name": obj.name,
                    }
                )
            else:
                p.append(
                    {
                        "ofuri": ofuri,
                    }
                )
        return p

    def get_product_streams(self, instance):
        request = self.context.get("request")
        p = list()
        for ofuri in instance.product_streams:
            if ProductStream.objects.filter(ofuri=ofuri).exists():
                obj = ProductStream.objects.get(ofuri=ofuri)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_streams?ofuri={ofuri}",  # noqa
                        "ofuri": ofuri,
                        "name": obj.name,
                    }
                )
            else:
                p.append(
                    {
                        "ofuri": ofuri,
                    }
                )
        return p

    def get_product_variants(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_variants:
            if ProductVariant.objects.filter(name=pname).exists():
                obj = ProductVariant.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_variants?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_provides(self, instance):
        request = self.context.get("request")
        components = list()
        for purl in instance.provides:
            if request:
                if request and "HTTP_HOST" in request.META:
                    components.append(
                        {
                            "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(purl)}",  # noqa
                            "purl": purl,
                        }
                    )
        return components

    def get_sources(self, instance):
        request = self.context.get("request")
        components = list()
        for purl in instance.sources:
            if request and "HTTP_HOST" in request.META:
                components.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(purl)}",  # noqa
                        "purl": purl,
                    }
                )
        return components

    def get_upstreams(self, instance):
        request = self.context.get("request")
        components = list()
        for purl in instance.upstreams:
            if request and "HTTP_HOST" in request.META:
                components.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(purl)}",  # noqa
                        "purl": purl,
                    }
                )
        return components

    class Meta:
        model = Component
        fields = [
            "link",
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


class ComponentDetailSerializer(serializers.ModelSerializer):
    software_build = SoftwareBuildSummarySerializer(many=False)
    tags = TagSerializer(many=True, read_only=True)
    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    link = serializers.SerializerMethodField()

    provides = serializers.SerializerMethodField()
    sources = serializers.SerializerMethodField()
    upstreams = serializers.SerializerMethodField()

    def get_products(self, instance):
        request = self.context.get("request")
        p = list()
        for ofuri in instance.products:
            if Product.objects.filter(ofuri=ofuri).exists():
                obj = Product.objects.get(ofuri=ofuri)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/products?ofuri={ofuri}",  # noqa
                        "ofuri": ofuri,
                        "name": obj.name,
                    }
                )
            else:
                p.append(
                    {
                        "ofuri": ofuri,
                    }
                )
        return p

    def get_product_versions(self, instance):
        request = self.context.get("request")
        p = list()
        for ofuri in instance.product_versions:
            if ProductVersion.objects.filter(ofuri=ofuri).exists():
                obj = ProductVersion.objects.get(ofuri=ofuri)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_versions?ofuri={ofuri}",  # noqa
                        "ofuri": ofuri,
                        "name": obj.name,
                    }
                )
            else:
                p.append(
                    {
                        "ofuri": ofuri,
                    }
                )
        return p

    def get_product_streams(self, instance):
        request = self.context.get("request")
        p = list()
        for ofuri in instance.product_streams:
            if ProductStream.objects.filter(ofuri=ofuri).exists():
                obj = ProductStream.objects.get(ofuri=ofuri)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_streams?ofuri={ofuri}",  # noqa
                        "ofuri": ofuri,
                        "name": obj.name,
                    }
                )
            else:
                p.append(
                    {
                        "ofuri": ofuri,
                    }
                )
        return p

    def get_product_variants(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_variants:
            if ProductVariant.objects.filter(name=pname).exists():
                obj = ProductVariant.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_variants?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(instance.purl)}"  # noqa
        return None

    def get_provides(self, instance):
        request = self.context.get("request")
        components = list()
        for purl in instance.provides:
            if request and "HTTP_HOST" in request.META:
                components.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(purl)}",  # noqa
                        "purl": purl,
                    }
                )
        return components

    def get_sources(self, instance):
        request = self.context.get("request")
        components = list()
        for purl in instance.sources:
            if request and "HTTP_HOST" in request.META:
                components.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(purl)}",  # noqa
                        "purl": purl,
                    }
                )
        return components

    def get_upstreams(self, instance):
        request = self.context.get("request")
        components = list()
        for purl in instance.upstreams:
            if request and "HTTP_HOST" in request.META:
                components.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(purl)}",  # noqa
                        "purl": purl,
                    }
                )
        return components

    class Meta:
        model = Component
        fields = [
            "link",
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
            "meta_attr",
        ]


class ComponentListSerializer(serializers.ModelSerializer):

    link = serializers.SerializerMethodField()

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(instance.purl)}"  # noqa
        return None

    class Meta:
        model = Component
        fields = [
            "link",
            "purl",
        ]


class ProductSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = ComponentListSerializer(many=True, read_only=True)
    link = serializers.SerializerMethodField()
    build_count = serializers.SerializerMethodField()

    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/products?ofuri={instance.ofuri}"  # noqa
        return None

    def get_product_versions(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_versions:
            if ProductVersion.objects.filter(name=pname).exists():
                obj = ProductVersion.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_versions?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_product_streams(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_streams:
            if ProductStream.objects.filter(name=pname).exists():
                obj = ProductStream.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_streams?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_product_variants(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_variants:
            if ProductVariant.objects.filter(name=pname).exists():
                obj = ProductVariant.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_variants?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    @staticmethod
    def get_build_count(instance):
        return instance.builds.count()

    class Meta:
        model = Product
        fields = [
            "link",
            "uuid",
            "ofuri",
            "name",
            "description",
            "coverage",
            "build_count",
            "tags",
            "product_versions",
            "product_streams",
            "product_variants",
            "errata",
            "builds",
            "channels",
            "components",
            "manifest",
            "upstream",
            "meta_attr",
        ]


class ProductVersionSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = ComponentListSerializer(many=True, read_only=True)
    link = serializers.SerializerMethodField()
    build_count = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_versions?ofuri={instance.ofuri}"  # noqa
        return None

    def get_products(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.products:
            if Product.objects.filter(name=pname).exists():
                obj = Product.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/products?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_product_streams(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_streams:
            if ProductStream.objects.filter(name=pname).exists():
                obj = ProductStream.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_streams?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_product_variants(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_variants:
            if ProductVariant.objects.filter(name=pname).exists():
                obj = ProductVariant.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_variants?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    @staticmethod
    def get_build_count(instance):
        return instance.builds.count()

    class Meta:
        model = ProductVersion
        fields = [
            "link",
            "uuid",
            "ofuri",
            "name",
            "description",
            "coverage",
            "build_count",
            "tags",
            "products",
            "product_streams",
            "product_variants",
            "errata",
            "builds",
            "channels",
            "components",
            "manifest",
            "upstream",
            "meta_attr",
        ]


class ProductStreamSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = ComponentListSerializer(many=True, read_only=True)
    link = serializers.SerializerMethodField()
    build_count = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_variants = serializers.SerializerMethodField()

    relations = serializers.SerializerMethodField()

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_streams?ofuri={instance.ofuri}"  # noqa
        return None

    def get_products(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.products:
            if Product.objects.filter(name=pname).exists():
                obj = Product.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/products?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_product_versions(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_versions:
            if ProductVersion.objects.filter(name=pname).exists():
                obj = ProductVersion.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_versions?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_product_variants(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_variants:
            if ProductVariant.objects.filter(name=pname).exists():
                obj = ProductVariant.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_variants?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    @staticmethod
    def get_relations(instance) -> list[dict[str, str]]:
        related_pcrs = ProductComponentRelation.objects.filter(product_ref=instance.name).distinct()
        relations = [
            {"type": pcr.type, "external_system_id": pcr.external_system_id} for pcr in related_pcrs
        ]
        return relations

    @staticmethod
    def get_build_count(instance):
        return instance.builds.count()

    class Meta:
        model = ProductStream
        fields = [
            "link",
            "uuid",
            "ofuri",
            "name",
            "cpe",
            "description",
            "coverage",
            "build_count",
            "relations",
            "tags",
            "products",
            "product_versions",
            "product_variants",
            "errata",
            "builds",
            "channels",
            "components",
            "manifest",
            "upstream",
            "meta_attr",
        ]


class ProductVariantSerializer(serializers.ModelSerializer):
    tags = TagSerializer(many=True, read_only=True)
    components = ComponentListSerializer(many=True, read_only=True)
    link = serializers.SerializerMethodField()
    build_count = serializers.SerializerMethodField()

    products = serializers.SerializerMethodField()
    product_versions = serializers.SerializerMethodField()
    product_streams = serializers.SerializerMethodField()

    relations = serializers.SerializerMethodField()

    def get_link(self, instance):
        request = self.context.get("request")
        if request and "HTTP_HOST" in request.META:
            return f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_variants?ofuri={instance.ofuri}"  # noqa
        return None

    def get_products(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.products:
            if Product.objects.filter(name=pname).exists():
                obj = Product.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/products?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_product_versions(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_versions:
            if ProductVersion.objects.filter(name=pname).exists():
                obj = ProductVersion.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_versions?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    def get_product_streams(self, instance):
        request = self.context.get("request")
        p = list()
        for pname in instance.product_streams:
            if ProductStream.objects.filter(name=pname).exists():
                obj = ProductStream.objects.get(name=pname)
                p.append(
                    {
                        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_streams?ofuri={obj.ofuri}",  # noqa
                        "ofuri": obj.ofuri,
                        "name": pname,
                    }
                )
            else:
                p.append(
                    {
                        "name": pname,
                    }
                )
        return p

    @staticmethod
    def get_relations(instance) -> list[dict[str, str]]:
        related_pcrs = ProductComponentRelation.objects.filter(product_ref=instance.name).distinct()
        relations = [
            {"type": pcr.type, "external_system_id": pcr.external_system_id} for pcr in related_pcrs
        ]
        return relations

    @staticmethod
    def get_build_count(instance):
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
            "tags",
            "relations",
            "products",
            "product_versions",
            "product_streams",
            "errata",
            "builds",
            "channels",
            "components",
            "manifest",
            "upstream",
            "meta_attr",
        ]


class ChannelSerializer(serializers.ModelSerializer):
    class Meta:
        model = Channel
        fields = "__all__"


class AppStreamLifeCycleSerializer(serializers.ModelSerializer):
    class Meta:
        model = AppStreamLifeCycle
        fields = "__all__"


class RelationSerializer(serializers.ModelSerializer):
    class Meta:
        model = ProductComponentRelation
        fields = "__all__"
