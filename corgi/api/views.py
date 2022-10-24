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

from config import utils
from corgi import __version__
from corgi.core.models import (
    AppStreamLifeCycle,
    Channel,
    Component,
    ComponentNode,
    Product,
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
    ComponentListSerializer,
    ComponentSerializer,
    ProductSerializer,
    ProductStreamSerializer,
    ProductVariantSerializer,
    ProductVersionSerializer,
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
    # Note-including a dummy queryset as scheme generation is complaining for reasons unknown
    queryset = Product.objects.all()

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
        # pg has well known limitation with counting
        #        (https://wiki.postgresql.org/wiki/Slow_Counting)
        # the following approach provides an estimate for raw table counts which performs
        # much better.
        with connection.cursor() as cursor:
            cursor.execute("SELECT pg_size_pretty(pg_database_size(current_database()));")
            db_size = cursor.fetchone()
            cursor.execute(
                "SELECT reltuples AS estimate FROM pg_class "
                "WHERE relname = 'core_productcomponentrelation';"
            )
            pcr_count = cursor.fetchone()
            cursor.execute(
                "SELECT reltuples AS estimate FROM pg_class WHERE relname = 'core_component';"
            )
            component_count = cursor.fetchone()
            cursor.execute(
                "SELECT reltuples AS estimate FROM pg_class WHERE relname = 'core_softwarebuild';"
            )
            sb_count = cursor.fetchone()

        return Response(
            {
                "status": "ok",
                "dt": timezone.now(),
                "service_version": __version__,
                "rest_api_version": CORGI_API_VERSION,
                "db_size": db_size,
                "builds": {
                    "count": sb_count,
                },
                "components": {
                    "count": component_count,
                },
                "relations": {"count": pcr_count},
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

    queryset = ProductStream.objects.filter(active=True)
    serializer_class = ProductStreamSerializer

    def list(self, request, *args, **kwargs):
        req = self.request
        active = request.query_params.get("active")
        if active == "all":
            self.queryset = ProductStream.objects.filter()
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

    def get_queryset(self):
        # 'latest' filter only relevent in terms of a specific offering/product
        ofuri = self.request.query_params.get("ofuri")
        if ofuri:
            # Note - originally ofuri explicitly embedded product type (eg. Product,
            # Product Version, Product Stream, Product Variant)
            # ... which would have simplified this code.
            if ProductStream.objects.filter(ofuri=ofuri).exists():
                return ProductStream.objects.get(ofuri=ofuri).get_latest_components()
            if ProductVariant.objects.filter(ofuri=ofuri).exists():
                return Component.objects.filter(product_variants=[ofuri])
            if ProductVersion.objects.filter(ofuri=ofuri).exists():
                return Component.objects.filter(product_versions=[ofuri])
            if Product.objects.filter(ofuri=ofuri).exists():
                return Component.objects.filter(products=[ofuri])
        return Component.objects.all()

    def list(self, request, *args, **kwargs):
        # purl are stored with each segment url encoded as per the specification. The purl query
        # param here is url decoded, to ensure special characters such as '@' and '?'
        # are not interpreted  as part of the request.
        view = request.query_params.get("view")
        if view == "summary":
            self.serializer_class = ComponentListSerializer
            return super().list(request)
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

    @action(methods=["put"], detail=True)
    def olcs_test(self, request, uuid=None):
        """Allow OpenLCS to upload copyright text / license scan results for a component"""
        # In the future these could be separate endpoints
        # For testing we'll just keep it under one endpoint
        if utils.running_prod():
            # This is only temporary for OpenLCS testing
            # Do not enable in production until we add OIDC authentication
            return Response(status=403)
        component = self.queryset.filter(uuid=uuid).first()
        if not component:
            return Response(status=404)

        copyright_text = request.data.get("copyright_text")
        license_concluded = request.data.get("license_concluded")
        openlcs_scan_url = request.data.get("openlcs_scan_url")
        openlcs_scan_version = request.data.get("openlcs_scan_version")
        if (
            not copyright_text
            and not license_concluded
            and not openlcs_scan_url
            and not openlcs_scan_version
        ):
            # At least one of above is required, else Bad Request
            return Response(status=400)

        # if it's None, it wasn't included in the request
        # But it might be "" if the user wants to empty out the value
        if copyright_text is not None:
            component.copyright_text = copyright_text
        if license_concluded is not None:
            component.license_concluded_raw = license_concluded
        if openlcs_scan_url is not None:
            component.openlcs_scan_url = openlcs_scan_url
        if openlcs_scan_version is not None:
            component.openlcs_scan_version = openlcs_scan_version
        component.save()
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
