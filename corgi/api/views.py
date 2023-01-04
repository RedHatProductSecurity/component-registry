import json
import logging
from typing import Type, Union

import django_filters.rest_framework
from django.db import connections
from django.db.models import QuerySet
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema
from mptt.templatetags.mptt_tags import cache_tree_children
from packageurl import PackageURL
from rest_framework import filters, status
from rest_framework.decorators import action, api_view
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from config import utils
from corgi import __version__
from corgi.core.constants import NODE_LEVEL_MODEL_MAPPING
from corgi.core.models import (
    AppStreamLifeCycle,
    Channel,
    Component,
    ComponentNode,
    Product,
    ProductModel,
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
    get_model_ofuri_type,
)

logger = logging.getLogger(__name__)


@extend_schema(request=None, responses=None)
@api_view(["GET"])
def healthy(request: Request) -> Response:
    """Send empty 200 response as an indicator that the application is up and running."""
    return Response(status=status.HTTP_200_OK)


class StatusViewSet(GenericViewSet):
    # Note-including a dummy queryset as scheme generation is complaining for reasons unknown
    queryset = Product.objects.none()

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
    def list(self, request: Request) -> Response:
        # pg has well known limitation with counting
        #        (https://wiki.postgresql.org/wiki/Slow_Counting)
        # the following approach provides an estimate for raw table counts which performs
        # much better.
        with connections["read_only"].cursor() as cursor:
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
                    "count": Product.objects.db_manager("read_only").count(),
                },
                "product_versions": {
                    "count": ProductVersion.objects.db_manager("read_only").count(),
                },
                "product_streams": {
                    "count": ProductStream.objects.db_manager("read_only").count(),
                },
                "product_variants": {
                    "count": ProductVariant.objects.db_manager("read_only").count(),
                },
                "channels": {
                    "count": Channel.objects.db_manager("read_only").count(),
                },
            }
        )


# A dict with string keys and string or tuple values
# The tuples recursively contain taxonomy dicts
taxonomy_dict_type = dict[str, Union[str, tuple["taxonomy_dict_type", ...]]]


def recursive_component_node_to_dict(
    node: ComponentNode, component_type: tuple[str, ...]
) -> taxonomy_dict_type:
    """Recursively build a dict of purls, links, and children for some ComponentNode"""
    if not node.obj:
        raise ValueError(f"Node {node} had no linked obj")

    result = {}
    if node.type in component_type:
        result = {
            "purl": node.purl,
            # "node_id": node.pk,
            "node_type": node.type,
            "link": get_component_purl_link(node.purl),
            # "uuid": node.obj.uuid,
            "description": node.obj.description,
        }
    children = tuple(
        recursive_component_node_to_dict(c, component_type) for c in node.get_children()
    )
    if children:
        result["deps"] = children
    return result


def recursive_product_node_to_dict(node: ProductNode) -> taxonomy_dict_type:
    """Recursively build a dict of ofuris, links, and children for some ProductNode"""
    product_type = NODE_LEVEL_MODEL_MAPPING.get(node.level, "")
    if not product_type:
        raise ValueError(f"Node {node} had level {node.level} which is invalid")

    if not node.obj:
        raise ValueError(f"Node {node} had no linked obj")

    # Usually e.g. "products" and "product_versions"
    # or "channels" and "" since channels is the lowest level in our taxonomy
    product_type = f"{product_type}s"
    child_product_type = NODE_LEVEL_MODEL_MAPPING.get(node.level + 1, "")
    child_product_type = f"{child_product_type}s" if child_product_type else ""

    result = {
        "link": get_model_ofuri_link(product_type, node.obj.ofuri),
        "ofuri": node.obj.ofuri,
        "name": node.obj.name,
    }
    children = tuple(recursive_product_node_to_dict(c) for c in node.get_children())

    if children:
        result[child_product_type] = children
    return result


def get_component_taxonomy(
    obj: Component, component_types: tuple[str, ...]
) -> tuple[taxonomy_dict_type, ...]:
    """Look up and return the taxonomy for a particular Component."""
    root_nodes = cache_tree_children(
        obj.cnodes.get_queryset().get_descendants(include_self=True).using("read_only")
    )
    dicts = tuple(
        recursive_component_node_to_dict(
            node,
            component_types,
        )
        for node in root_nodes
    )
    return dicts


def get_product_taxonomy(obj: ProductModel) -> tuple[taxonomy_dict_type, ...]:
    """Look up and return the taxonomy for a particular ProductModel instance."""
    root_nodes = cache_tree_children(
        obj.pnodes.get_queryset().get_descendants(include_self=True).using("read_only")
    )
    dicts = tuple(recursive_product_node_to_dict(node) for node in root_nodes)
    return dicts


class SoftwareBuildViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    """View for api/v1/builds"""

    queryset = SoftwareBuild.objects.order_by("-build_id").using("read_only")
    serializer_class = SoftwareBuildSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = SoftwareBuildFilter
    lookup_url_kwarg = "build_id"


class ProductDataViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name", "description", "meta_attr"]
    filterset_class = ProductDataFilter
    lookup_url_kwarg = "uuid"
    ordering_field = "name"


class ProductViewSet(ProductDataViewSet):
    """View for api/v1/products"""

    # Can't use self / super() yet
    queryset = Product.objects.order_by(ProductDataViewSet.ordering_field).using("read_only")
    serializer_class = ProductSerializer

    def list(self, request: Request, *args: tuple, **kwargs: dict) -> Response:
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        p = Product.objects.filter(ofuri=ofuri).using("read_only").first()
        if not p:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/products/{p.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def taxonomy(self, request: Request, uuid: Union[str, None] = None) -> Response:
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        dicts = get_product_taxonomy(obj)
        return Response(dicts)


class ProductVersionViewSet(ProductDataViewSet):
    """View for api/v1/product_versions"""

    queryset = ProductVersion.objects.order_by(ProductDataViewSet.ordering_field).using("read_only")
    serializer_class = ProductVersionSerializer

    def list(self, request: Request, *args: tuple, **kwargs: dict) -> Response:
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        pv = ProductVersion.objects.filter(ofuri=ofuri).using("read_only").first()
        if not pv:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/product_versions/{pv.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def taxonomy(self, request: Request, uuid: Union[str, None] = None) -> Response:
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        dicts = get_product_taxonomy(obj)
        return Response(dicts)


class ProductStreamViewSetSet(ProductDataViewSet):
    """View for api/v1/product_streams"""

    queryset = (
        ProductStream.objects.filter(active=True)
        .order_by(ProductDataViewSet.ordering_field)
        .using("read_only")
    )
    serializer_class = ProductStreamSerializer

    @extend_schema(
        parameters=[OpenApiParameter("active", OpenApiTypes.STR, OpenApiParameter.QUERY)]
    )
    def list(self, request: Request, *args: tuple, **kwargs: dict) -> Response:
        req = self.request
        active = request.query_params.get("active")
        if active == "all":
            self.queryset = ProductStream.objects.order_by(super().ordering_field).using(
                "read_only"
            )
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        ps = ProductStream.objects.filter(ofuri=ofuri).using("read_only").first()
        if not ps:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/product_streams/{ps.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def manifest(self, request: Request, uuid: Union[str, None] = None) -> Response:
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        manifest = json.loads(obj.manifest)
        return Response(manifest)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request: Request, uuid: Union[str, None] = None) -> Response:
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        dicts = get_product_taxonomy(obj)
        return Response(dicts)


class ProductVariantViewSetSet(ProductDataViewSet):
    """View for api/v1/product_variants"""

    queryset = ProductVariant.objects.order_by(ProductDataViewSet.ordering_field).using("read_only")
    serializer_class = ProductVariantSerializer

    def list(self, request: Request, *args: tuple, **kwargs: dict) -> Response:
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        pv = ProductVariant.objects.filter(ofuri=ofuri).using("read_only").first()
        if not pv:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/product_variants/{pv.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def taxonomy(self, request: Request, uuid: Union[str, None] = None) -> Response:
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        dicts = get_product_taxonomy(obj)
        return Response(dicts)


class ChannelViewSet(ReadOnlyModelViewSet):
    """View for api/v1/channels"""

    queryset = Channel.objects.order_by("name").using("read_only")
    serializer_class = ChannelSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ChannelFilter
    lookup_url_kwarg = "uuid"


class ComponentViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    """View for api/v1/components"""

    queryset = Component.objects.order_by("name", "type", "arch", "version", "release").using(
        "read_only"
    )
    serializer_class: Union[
        Type[ComponentSerializer], Type[ComponentListSerializer]
    ] = ComponentSerializer
    search_fields = ["name", "description", "release", "version", "meta_attr"]
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ComponentFilter
    lookup_url_kwarg = "uuid"

    def get_queryset(self) -> QuerySet[Component]:
        # 'latest' filter only relevant in terms of a specific offering/product
        ofuri = self.request.query_params.get("ofuri")
        if not ofuri:
            return self.queryset

        model, _ = get_model_ofuri_type(ofuri)
        if isinstance(model, Product):
            return self.queryset.filter(products__ofuri=ofuri)
        elif isinstance(model, ProductVersion):
            return self.queryset.filter(productversions__ofuri=ofuri)
        elif isinstance(model, ProductStream):
            # only ProductStream defines get_latest_components()
            # TODO: Should this be a ProductModel method? For e.g. Products,
            #  we could return get_latest_components() for each child stream
            return model.get_latest_components()
        elif isinstance(model, ProductVariant):
            return self.queryset.filter(productvariants__ofuri=ofuri)
        else:
            # No matching model instance found, or invalid ofuri
            return self.queryset

    @extend_schema(
        parameters=[
            OpenApiParameter("ofuri", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("view", OpenApiTypes.STR, OpenApiParameter.QUERY),
            OpenApiParameter("purl", OpenApiTypes.STR, OpenApiParameter.QUERY),
        ]
    )
    def list(self, request: Request, *args: tuple, **kwargs: dict) -> Response:
        # purl are stored with each segment url encoded as per the specification. The purl query
        # param here is url decoded, to ensure special characters such as '@' and '?'
        # are not interpreted as part of the request.
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
        component = Component.objects.filter(purl=purl).using("read_only").first()
        if not component:
            return Response(status=404)
        response = Response(status=302)
        response["Location"] = f"/api/{CORGI_API_VERSION}/components/{component.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def manifest(self, request: Request, uuid: str = "") -> Response:
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        manifest = json.loads(obj.manifest)
        return Response(manifest)

    @action(methods=["put"], detail=True)
    def olcs_test(self, request: Request, uuid: Union[str, None] = None) -> Response:
        """Allow OpenLCS to upload copyright text / license scan results for a component"""
        # In the future these could be separate endpoints
        # For testing we'll just keep it under one endpoint
        if utils.running_prod():
            # This is only temporary for OpenLCS testing
            # Do not enable in production until we add OIDC authentication
            return Response(status=403)
        component = self.queryset.filter(uuid=uuid).using("default").first()
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
    def provides(self, request: Request, uuid: Union[str, None] = None) -> Response:
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        dicts = get_component_taxonomy(
            obj,
            (
                ComponentNode.ComponentNodeType.PROVIDES,
                ComponentNode.ComponentNodeType.PROVIDES_DEV,
            ),
        )
        return Response(dicts)

    @action(methods=["get"], detail=True)
    def taxonomy(self, request: Request, uuid: Union[str, None] = None) -> Response:
        obj = self.queryset.filter(uuid=uuid).first()
        if not obj:
            return Response(status=404)
        dicts = get_component_taxonomy(
            obj,
            (
                ComponentNode.ComponentNodeType.SOURCE,
                ComponentNode.ComponentNodeType.PROVIDES_DEV,
                ComponentNode.ComponentNodeType.REQUIRES,
                ComponentNode.ComponentNodeType.PROVIDES,
            ),
        )
        return Response(dicts)


class AppStreamLifeCycleViewSet(ReadOnlyModelViewSet):
    """View for api/v1/lifecycles"""

    queryset = AppStreamLifeCycle.objects.order_by(
        "name", "type", "product", "initial_product_version", "stream"
    ).using("read_only")
    serializer_class = AppStreamLifeCycleSerializer
