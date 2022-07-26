import json
import logging

import django_filters.rest_framework
from django.db import connection
from django.utils import timezone
from drf_spectacular.utils import extend_schema
from mptt.templatetags.mptt_tags import cache_tree_children
from rest_framework import filters, status
from rest_framework.decorators import action, api_view
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import ReadOnlyModelViewSet

from corgi import __version__
from corgi.core.models import (
    AppStreamLifeCycle,
    Channel,
    Component,
    ComponentNode,
    Product,
    ProductComponentRelation,
    ProductNode,
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
from .mixins import TagViewMixin
from .serializers import (
    AppStreamLifeCycleSerializer,
    ChannelSerializer,
    ComponentDetailSerializer,
    ComponentSerializer,
    ProductSerializer,
    ProductStreamSerializer,
    ProductVariantSerializer,
    ProductVersionSerializer,
    RelationSerializer,
    SoftwareBuildSerializer,
)

logger = logging.getLogger(__name__)


@extend_schema(request=None, responses=None)
@api_view(["GET"])
def healthy(request: Request) -> Response:
    """Send empty 200 response as an indicator that the application is up and running."""
    return Response(status=status.HTTP_200_OK)


class StatusView(ReadOnlyModelViewSet):

    queryset = SoftwareBuild.objects.all()

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
        """View for api/v1/status"""

        db_size = ""
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
                    "count": self.queryset.count(),
                },
                "components": {
                    "count": Component.objects.all().count(),
                },
                "relations": {"count": ProductComponentRelation.objects.all().count()},
                "products": {
                    "count": Product.objects.all().count(),
                },
                "product_versions": {
                    "count": ProductVersion.objects.all().count(),
                },
                "product_streams": {
                    "count": ProductStream.objects.all().count(),
                },
                "product_variants": {
                    "count": ProductVariant.objects.all().count(),
                },
                "channels": {
                    "count": Channel.objects.all().count(),
                },
            }
        )


def recursive_component_node_to_dict(request, node, componenttype):
    result = {}
    if node.type in componenttype:
        result = {
            "purl": node.purl,
            # "node_id": node.pk,
            "node_type": node.type,
            "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={node.obj.purl}",  # noqa
            # "uuid": node.obj.uuid,
            "description": node.obj.description,
        }
    children = [
        recursive_component_node_to_dict(request, c, componenttype) for c in node.get_children()
    ]
    if children:
        result["deps"] = children
    return result


class ComponentTaxonomyView(APIView):
    """return all components in component taxonomy"""

    def get(self, request, *args, **kwargs):
        """ """
        root_nodes = cache_tree_children(ComponentNode.objects.all())
        dicts = []
        for n in root_nodes:
            dicts.append(
                recursive_component_node_to_dict(
                    request,
                    n,
                    [
                        ComponentNode.ComponentNodeType.SOURCE,
                        ComponentNode.ComponentNodeType.PROVIDES,
                    ],
                )
            )
        return Response(dicts)


def recursive_product_node_to_dict(request, node):
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
        "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/{product_type}?ofuri={node.obj.ofuri}",  # noqa
        "ofuri": node.obj.ofuri,
        "name": node.obj.name,
    }
    children = [recursive_product_node_to_dict(request, c) for c in node.get_children()]

    if children:
        result[child_product_type] = children
    return result


class ProductTaxonomyView(APIView):
    """return all product nodes in product taxonomy"""

    def get(self, request, *args, **kwargs):
        """ """
        root_nodes = cache_tree_children(ProductNode.objects.all())
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(request, n))
        return Response(dicts)


class SoftwareBuildView(ReadOnlyModelViewSet, TagViewMixin):
    """View for api/v1/builds"""

    queryset = SoftwareBuild.objects.all()
    serializer_class = SoftwareBuildSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = SoftwareBuildFilter
    lookup_url_kwarg = "build_id"


class ProductDataView(ReadOnlyModelViewSet, TagViewMixin):
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name", "description", "meta_attr"]
    filterset_class = ProductDataFilter
    lookup_url_kwarg = "uuid"


class ProductView(ProductDataView):
    """View for api/v1/products"""

    queryset = Product.objects.all()
    serializer_class = ProductSerializer

    def list(self, request):
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if ofuri:
            p = Product.objects.get(ofuri=ofuri)
            response = Response(status=302)
            response["Location"] = f"/api/{CORGI_API_VERSION}/products/{p.uuid}"
            return response
        return super().list(request)

    @action(methods=["get"], detail=True)
    def manifest(self, request, uuid=None):
        manifest = json.loads(self.queryset.get(uuid=uuid).manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        root_nodes = cache_tree_children(
            self.queryset.get(uuid=uuid).pnodes.all().get_descendants(include_self=True)
        )
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(request, n))
        return Response(dicts[0])


class ProductVersionView(ProductDataView):
    """View for api/v1/product_versions"""

    queryset = ProductVersion.objects.all()
    serializer_class = ProductVersionSerializer

    def list(self, request):
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if ofuri:
            pv = ProductVersion.objects.get(ofuri=ofuri)
            response = Response(status=302)
            response["Location"] = f"/api/{CORGI_API_VERSION}/product_versions/{pv.uuid}"
            return response
        return super().list(request)

    @action(methods=["get"], detail=True)
    def manifest(self, request, uuid=None):
        manifest = json.loads(self.queryset.get(uuid=uuid).manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        root_nodes = cache_tree_children(
            self.queryset.get(uuid=uuid).pnodes.all().get_descendants(include_self=True)
        )
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(n))
        return Response(dicts)


class ProductStreamView(ProductDataView):
    """View for api/v1/product_streams"""

    queryset = ProductStream.objects.all()
    serializer_class = ProductStreamSerializer

    def list(self, request):
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if ofuri:
            ps = ProductStream.objects.get(ofuri=ofuri)
            response = Response(status=302)
            response["Location"] = f"/api/{CORGI_API_VERSION}/product_streams/{ps.uuid}"
            return response
        return super().list(request)

    @action(methods=["get"], detail=True)
    def manifest(self, request, uuid=None):
        manifest = json.loads(self.queryset.get(uuid=uuid).manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        root_nodes = cache_tree_children(
            self.queryset.get(uuid=uuid).pnodes.all().get_descendants(include_self=True)
        )
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(n))
        return Response(dicts)


class ProductVariantView(ProductDataView):
    """View for api/v1/product_variants"""

    queryset = ProductVariant.objects.all()
    serializer_class = ProductVariantSerializer

    def list(self, request):
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if ofuri:
            pv = ProductVariant.objects.get(ofuri=ofuri)
            response = Response(status=302)
            response["Location"] = f"/api/{CORGI_API_VERSION}/product_variants/{pv.uuid}"
            return response
        return super().list(request)

    @action(methods=["get"], detail=True)
    def manifest(self, request, uuid=None):
        manifest = json.loads(self.queryset.get(uuid=uuid).manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request, uuid=None):
        root_nodes = cache_tree_children(
            self.queryset.get(uuid=uuid).pnodes.all().get_descendants(include_self=True)
        )
        dicts = []
        for n in root_nodes:
            dicts.append(recursive_product_node_to_dict(n))
        return Response(dicts)


class ChannelView(ReadOnlyModelViewSet):
    """View for api/v1/channels"""

    queryset = Channel.objects.all()
    serializer_class = ChannelSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ChannelFilter
    lookup_url_kwarg = "uuid"


class ComponentView(ReadOnlyModelViewSet, TagViewMixin):
    """View for api/v1/components"""

    queryset = Component.objects.all()
    search_fields = ["name", "description", "release", "version", "meta_attr"]
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ComponentFilter
    lookup_url_kwarg = "uuid"

    def get_serializer_class(self):
        if self.action == "retrieve":
            return ComponentDetailSerializer
        return ComponentSerializer

    def list(self, request):
        req = self.request
        # Note - purl url param needs to be url encoded a they are a uri.
        purl = req.query_params.get("purl")
        if purl:
            if Component.objects.filter(purl=purl).exists():
                component = Component.objects.get(purl=purl)
                response = Response(status=302)
                response["Location"] = f"/api/{CORGI_API_VERSION}/components/{component.uuid}"
                return response
            else:
                return Response(status=404)
        return super().list(request)

    @action(methods=["get"], detail=True)
    def provides(self, request, uuid=None):
        root_nodes = cache_tree_children(
            self.queryset.get(uuid=uuid).cnodes.all().get_descendants(include_self=True)
        )
        dicts = []
        for n in root_nodes:
            dicts.append(
                recursive_component_node_to_dict(
                    request,
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
        root_nodes = cache_tree_children(
            self.queryset.get(uuid=uuid).cnodes.all().get_descendants(include_self=True)
        )
        dicts = []
        for n in root_nodes:
            dicts.append(
                recursive_component_node_to_dict(
                    request,
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


class AppStreamLifeCycleView(ReadOnlyModelViewSet):
    """View for api/v1/lifecycles"""

    queryset = AppStreamLifeCycle.objects.all()
    serializer_class = AppStreamLifeCycleSerializer


def coverage_report_node_to_dict(request, node):
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
    if node.obj.builds.exists():
        last_build = node.obj.builds.order_by("created_at").first()
        if SoftwareBuild.objects.filter(build_id=last_build).exists():
            last_build_date = SoftwareBuild.objects.get(build_id=last_build).created_at
            result = {
                "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/{product_type}?ofuri={node.obj.ofuri}",  # noqa
                "ofuri": node.obj.ofuri,
                "name": node.obj.name,
            }
            if node.level < 3:
                result.update(
                    {
                        "coverage": node.obj.coverage,
                        "build_count": node.obj.builds.count(),
                        "last_build_dt": str(last_build_date),
                        "component_count": node.obj.components.count(),
                    }
                )
            else:
                result.update(
                    {
                        "build_count": node.obj.builds.count(),
                        "last_build_dt": str(last_build_date),
                        "component_count": node.obj.components.count(),
                    }
                )

            children = [coverage_report_node_to_dict(request, c) for c in node.get_children()]

            if children:
                result[child_product_type] = children
            return result
    else:
        return {
            "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/{product_type}?ofuri={node.obj.ofuri}",  # noqa
            "ofuri": node.obj.ofuri,
            "name": node.obj.name,
        }


class CoverageReportView(ReadOnlyModelViewSet):

    queryset = Product.objects.all()

    def list(self, request):
        """View for api/v1/reports/coverage"""

        include_missing = request.query_params.get("include_missing")

        results = []

        for p in self.queryset:
            if p.coverage or include_missing:
                root_nodes = cache_tree_children(p.pnodes.all().get_descendants(include_self=True))
                dicts = []
                for n in root_nodes:
                    dicts.append(coverage_report_node_to_dict(request, n))
                if dicts:
                    results.append(dicts[0])
        return Response(results)


class RelationsView(ReadOnlyModelViewSet, TagViewMixin):
    """View for api/v1/relations"""

    queryset = ProductComponentRelation.objects.all()
    serializer_class = RelationSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    lookup_url_kwarg = "uuid"

    def list(self, request):
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
                    ofuri_link = f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_variants?ofuri={ofuri}"  # noqa
                    build_count = pv.builds.count()

            if pcr_type == ProductComponentRelation.Type.COMPOSE:
                ps = ProductStream.objects.filter(name=related_pcr.product_ref).first()
                if ps:
                    ofuri = ps.ofuri
                    ofuri_link = f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/product_streams?ofuri={ofuri}"  # noqa
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
