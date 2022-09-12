import json
import logging

import django_filters.rest_framework
from django.db import connection
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from mptt.templatetags.mptt_tags import cache_tree_children
from packageurl import PackageURL
from rest_framework import filters, status
from rest_framework.decorators import action, api_view
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from corgi import __version__
from corgi.core.models import (
    AppStreamLifeCycle,
    Channel,
    Component,
    ComponentNode,
    Product,
    ProductComponentRelation,
    ProductStream,
    ProductVariant,
    ProductVersion,
    SoftwareBuild,
)

from .constants import CORGI_API_VERSION
from .filters import (
    ChannelFilter,
    ComponentFilter,
    ProductDataFilter,
    SoftwareBuildFilter,
)
from .serializers import (
    AppStreamLifeCycleSerializer,
    ChannelSerializer,
    ComponentSerializer,
    ProductSerializer,
    ProductStreamSerializer,
    ProductVariantSerializer,
    ProductVersionSerializer,
    RelationSerializer,
    SoftwareBuildSerializer,
    get_component_purl_link,
    get_model_ofuri_link,
)

logger = logging.getLogger(__name__)


@extend_schema(request=None, responses=None)
@api_view(["GET"])
def healthy(request: Request) -> Response:
    """Send empty 200 response as an indicator that the application is up and running."""
    return Response(status=status.HTTP_200_OK)


class StatusViewSet(GenericViewSet):
    @extend_schema(
        request=None,
        responses={
            200: {
                "type": "object",
                "properties": {
                    "status": {"type": "string"},
                    "dt": {"type": "string", "format": "date-time"},
                    "service_version": {"type": "string"},
                    "rest_api_version": {"type": "string"},
                    "db_size": {"type": "string"},
                    "builds": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                    "products": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                    "product_versions": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                    "product_streams": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                    "product_variants": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                    "channels": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                    "components": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                    "relations": {
                        "type": "object",
                        "properties": {"count": {"type": "integer"}},
                    },
                },
            }
        },
    )
    def list(self, request):
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database()));")
            db_size = cursor.fetchone()

        return Response(
            {
                "status": "ok",
                "dt": timezone.now(),
                "service_version": __version__,
                "rest_api_version": CORGI_API_VERSION,
                "db_size": db_size,
                "builds": {
                    "count": SoftwareBuild.objects.count(),
                },
                "components": {
                    "count": Component.objects.count(),
                },
                "relations": {"count": ProductComponentRelation.objects.count()},
                "products": {
                    "count": Product.objects.count(),
                },
                "product_versions": {
                    "count": ProductVersion.objects.count(),
                },
                "product_streams": {
                    "count": ProductStream.objects.count(),
                },
                "product_variants": {
                    "count": ProductVariant.objects.count(),
                },
                "channels": {
                    "count": Channel.objects.count(),
                },
            }
        )


def recursive_component_node_to_dict(node, componenttype):
    result = {}
    if node.type in componenttype:
        result = {
            "purl": node.purl,
            # "node_id": node.pk,
            "node_type": node.type,
            "link": get_component_purl_link(node.purl),
            # "uuid": node.obj.uuid,
            "description": node.obj.description,
        }
    children = [recursive_component_node_to_dict(c, componenttype) for c in node.get_children()]
    if children:
        result["deps"] = children
    return result


def recursive_product_node_to_dict(node):
    product_type = ""
    child_product_type = ""
    if node.level == 0:
        product_type = "products"
        child_product_type = "product_versions"
    if node.level == 1:
        product_type = "product_versions"
        child_product_type = "product_streams"
    if node.level == 2:
        product_type = "product_streams"
        child_product_type = "product_variants"
    if node.level == 3:
        product_type = "product_variants"
        child_product_type = "channels"
    if node.level == 4:
        product_type = "channels"
    result = {
        "link": get_model_ofuri_link(product_type, node.obj.ofuri),
        "ofuri": node.obj.ofuri,
        "name": node.obj.name,
    }
    children = [recursive_product_node_to_dict(c) for c in node.get_children()]

    if children:
        result[child_product_type] = children
    return result


class SoftwareBuildViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    """View for api/v1/builds"""

    queryset = SoftwareBuild.objects.all()
    serializer_class = SoftwareBuildSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = SoftwareBuildFilter
    lookup_url_kwarg = "build_id"


class ProductDataViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name", "description", "meta_attr"]
    filterset_class = ProductDataFilter
    lookup_url_kwarg = "uuid"


class ProductViewSet(ProductDataViewSet):
    """View for api/v1/products"""

    queryset = Product.objects.get_queryset()
    serializer_class = ProductSerializer

    def list(self, request, *args, **kwargs):
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        p = Product.objects.filter(ofuri=ofuri).first()
        if not p:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/products/{p.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def manifest(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        manifest = json.loads(obj.manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        root_nodes = cache_tree_children(obj.pnodes.get_descendants(include_self=True))
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(n))
        return Response(dicts[0])


class ProductVersionViewSet(ProductDataViewSet):
    """View for api/v1/product_versions"""

    queryset = ProductVersion.objects.get_queryset()
    serializer_class = ProductVersionSerializer

    def list(self, request, *args, **kwargs):
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        pv = ProductVersion.objects.filter(ofuri=ofuri).first()
        if not pv:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/product_versions/{pv.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def manifest(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        manifest = json.loads(obj.manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        root_nodes = cache_tree_children(obj.pnodes.get_descendants(include_self=True))
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(n))
        return Response(dicts)


class ProductStreamViewSetSet(ProductDataViewSet):
    """View for api/v1/product_streams"""

    queryset = ProductStream.objects.get_queryset()
    serializer_class = ProductStreamSerializer

    def list(self, request, *args, **kwargs):
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        ps = ProductStream.objects.filter(ofuri=ofuri).first()
        if not ps:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/product_streams/{ps.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def manifest(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        manifest = json.loads(obj.manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        root_nodes = cache_tree_children(obj.pnodes.get_descendants(include_self=True))
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(n))
        return Response(dicts)


class ProductVariantViewSetSet(ProductDataViewSet):
    """View for api/v1/product_variants"""

    queryset = ProductVariant.objects.get_queryset()
    serializer_class = ProductVariantSerializer

    def list(self, request, *args, **kwargs):
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        pv = ProductVariant.objects.filter(ofuri=ofuri).first()
        if not pv:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/product_variants/{pv.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def manifest(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        manifest = json.loads(obj.manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        root_nodes = cache_tree_children(obj.pnodes.get_descendants(include_self=True))
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(n))
        return Response(dicts)


class ChannelViewSet(ReadOnlyModelViewSet):
    """View for api/v1/channels"""

    queryset = Channel.objects.get_queryset()
    serializer_class = ChannelSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ChannelFilter
    lookup_url_kwarg = "uuid"


class ComponentViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    """View for api/v1/components"""

    queryset = Component.objects.get_queryset()
    serializer_class = ComponentSerializer
    search_fields = ["name", "description", "release", "version", "meta_attr"]
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ComponentFilter
    lookup_url_kwarg = "uuid"

    def list(self, request, *args, **kwargs):
        # purl are stored with each segment url encoded as per the specification. The purl query
        # param here is url decoded, to ensure special characters such as '@' and '?'
        # are not interpreted as part of the request.
        purl = request.query_params.get("purl")
        if not purl:
            return super().list(request)
        # We re-encode the purl here to ensure each segment of the purl is url encoded,
        # as it's stored in the DB.
        purl = f"{PackageURL.from_string(purl)}"
        component = Component.objects.filter(purl=purl).first()
        if not component:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/components/{component.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def provides(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        root_nodes = cache_tree_children(obj.cnodes.get_descendants(include_self=True))
        dicts = []
        for n in root_nodes:
            dicts.append(
                recursive_component_node_to_dict(
                    n,
                    [
                        ComponentNode.ComponentNodeType.PROVIDES,
                        ComponentNode.ComponentNodeType.PROVIDES_DEV,
                    ],
                )
            )
        return Response(dicts)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        root_nodes = cache_tree_children(obj.cnodes.get_descendants(include_self=True))
        dicts = []
        for n in root_nodes:
            dicts.append(
                recursive_component_node_to_dict(
                    n,
                    [
                        ComponentNode.ComponentNodeType.SOURCE,
                        ComponentNode.ComponentNodeType.PROVIDES_DEV,
                        ComponentNode.ComponentNodeType.REQUIRES,
                        ComponentNode.ComponentNodeType.PROVIDES,
                    ],
                )
            )
        return Response(dicts)


class AppStreamLifeCycleViewSet(ReadOnlyModelViewSet):
    """View for api/v1/lifecycles"""

    queryset = AppStreamLifeCycle.objects.get_queryset()
    serializer_class = AppStreamLifeCycleSerializer


def coverage_report_node_to_dict(node):
    """Recursively generate a coverage report for a node and all its children"""
    if node.level == 0:
        product_type = "products"
        child_product_type = "product_versions"
    elif node.level == 1:
        product_type = "product_versions"
        child_product_type = "product_streams"
    elif node.level == 2:
        product_type = "product_streams"
        child_product_type = "product_variants"
    elif node.level == 3:
        product_type = "product_variants"
        child_product_type = "channels"
    else:
        raise ValueError("Node level too high!")

    last_build_id = node.obj.builds.order_by("created_at").first()
    last_build = SoftwareBuild.objects.filter(build_id=last_build_id).first()
    result = {
        "link": get_model_ofuri_link(product_type, node.obj.ofuri),
        "ofuri": node.obj.ofuri,
        "name": node.obj.name,
    }
    if not last_build_id or not last_build:
        return result

    result["build_count"] = node.obj.builds.count()
    result["last_build_dt"] = str(last_build.created_at)
    result["component_count"] = node.obj.components.count()

    if node.level < 3:
        result["coverage"] = node.obj.coverage

    children = [coverage_report_node_to_dict(c) for c in node.get_children()]

    if children:
        result[child_product_type] = children
    return result


class CoverageReportViewSet(GenericViewSet):
    def list(self, request):
        include_missing = request.query_params.get("include_missing")

        results = []
        for p in Product.objects.all():
            if p.coverage or include_missing:
                root_nodes = cache_tree_children(p.pnodes.all().get_descendants(include_self=True))
                dicts = []
                for n in root_nodes:
                    dicts.append(coverage_report_node_to_dict(n))
                if dicts:
                    results.append(dicts[0])
        return Response(results)


class RelationsViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    """View for api/v1/relations"""

    queryset = ProductComponentRelation.objects.get_queryset()
    serializer_class = RelationSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    lookup_url_kwarg = "uuid"

    def list(self, request, *args, **kwargs):
        results = []

        # group all relations by external_system_id/type
        for external_system_id in self.queryset.distinct().values_list(
            "external_system_id", flat=True
        ):
            related_set = self.queryset.filter(external_system_id=external_system_id)
            related_pcr = related_set.first()
            pcr_type = related_pcr.type

            ofuri = None
            ofuri_link = None
            expected_build_count = None
            build_count = None

            if pcr_type == ProductComponentRelation.Type.ERRATA:
                pv = ProductVariant.objects.filter(name=related_pcr.product_ref).first()
                if pv:
                    ofuri = pv.ofuri
                    ofuri_link = get_model_ofuri_link("product_variants", ofuri)
                    build_count = pv.builds.count()

            elif pcr_type == ProductComponentRelation.Type.COMPOSE:
                ps = ProductStream.objects.filter(name=related_pcr.product_ref).first()
                if ps:
                    ofuri = ps.ofuri
                    ofuri_link = get_model_ofuri_link("product_streams", ofuri)
                    build_count = ps.builds.count()
                    expected_build_count = (
                        related_set.values_list("build_id", flat=True).distinct().count()
                    )

            result = {
                "type": pcr_type,
                "link": ofuri_link,
                "ofuri": ofuri,
                "external_system_id": external_system_id,
                "build_count": build_count,
            }
            if expected_build_count:
                # TODO: remove after we refine related user stories
                result["expected_build_count"] = expected_build_count
            results.append(result)
        return Response(results)
