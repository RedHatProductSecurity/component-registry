import json
import logging
from typing import Any, Type, Union

import django_filters.rest_framework
from django.conf import settings
from django.db import connections
from django.db.models import QuerySet, Value
from django.http import Http404
from django.utils import timezone
from drf_spectacular.types import OpenApiTypes
from drf_spectacular.utils import OpenApiParameter, extend_schema, extend_schema_view
from mozilla_django_oidc.contrib.drf import OIDCAuthentication
from mptt.templatetags.mptt_tags import cache_tree_children
from packageurl import PackageURL
from rest_framework import filters, status
from rest_framework.authentication import TokenAuthentication
from rest_framework.decorators import (
    action,
    api_view,
    authentication_classes,
    permission_classes,
)
from rest_framework.permissions import IsAuthenticated, IsAuthenticatedOrReadOnly
from rest_framework.request import Request
from rest_framework.response import Response
from rest_framework.views import APIView
from rest_framework.viewsets import GenericViewSet, ReadOnlyModelViewSet

from corgi import __version__
from corgi.core.authentication import RedHatRolePermission
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
from .mixins import TagViewMixin
from .serializers import (
    AppStreamLifeCycleSerializer,
    ChannelSerializer,
    ComponentListSerializer,
    ComponentProductStreamSummarySerializer,
    ComponentSerializer,
    ProductSerializer,
    ProductStreamSerializer,
    ProductStreamSummarySerializer,
    ProductVariantSerializer,
    ProductVersionSerializer,
    SoftwareBuildSerializer,
    get_component_purl_link,
    get_model_ofuri_type,
)

logger = logging.getLogger(__name__)

INCLUDE_FIELDS_PARAMETER = OpenApiParameter(
    "include_fields",
    type={"type": "array", "items": {"type": "string"}},
    location=OpenApiParameter.QUERY,
    description=(
        "Include only specified fields in the response. "
        "Multiple values may be separated by commas. "
        "Example: `include_fields=software_build.build_id,name`"
    ),
)

EXCLUDE_FIELDS_PARAMETER = OpenApiParameter(
    "exclude_fields",
    type={"type": "array", "items": {"type": "string"}},
    location=OpenApiParameter.QUERY,
    description=(
        "Exclude only specified fields in the response. "
        "Multiple values may be separated by commas. "
        "Example: `exclude_fields=software_build.build_id,name`"
    ),
)


# Use below as a decorator on all viewsets that support
# ?include_fields&exclude_fields= parameters
# A custom IncludeExcludeFieldsViewSet class that other
# ViewSet classes inherit from does not work
INCLUDE_EXCLUDE_FIELDS_SCHEMA = extend_schema_view(
    list=extend_schema(
        parameters=[INCLUDE_FIELDS_PARAMETER, EXCLUDE_FIELDS_PARAMETER],
    ),
    retrieve=extend_schema(
        parameters=[INCLUDE_FIELDS_PARAMETER, EXCLUDE_FIELDS_PARAMETER],
    ),
)


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


@extend_schema(
    request=None,
    responses={
        200: {
            "type": "object",
            "properties": {
                "oidc_enabled": {"type": "string"},
                "user": {"type": "string"},
                "auth": {"type": "string"},
            },
        }
    },
)
@api_view(["GET"])
@authentication_classes([OIDCAuthentication])
@permission_classes([IsAuthenticated])
def authentication_status(request: Request) -> Response:
    """
    View to determine whether you are currently authenticated and, if so, as whom.
    """
    content = {
        "oidc_enabled": str(settings.OIDC_AUTH_ENABLED),
        "user": str(request.user),
        "auth": str(request.auth),
    }
    return Response(content)


class ControlledAccessTestView(APIView):
    """
    View to determine whether you are authenticated with an account that has a specific
    role.
    """

    authentication_classes = [OIDCAuthentication]
    permission_classes = [RedHatRolePermission]
    roles_permitted = ["prodsec-dev"]

    @extend_schema(
        request=None,
        responses={
            200: {
                "type": "object",
                "properties": {
                    "user": {"type": "string"},
                },
            }
        },
    )
    def get(self, request, format=None):
        content = {"user": str(request.user)}
        return Response(content)


class TokenAuthTestView(APIView):
    """
    View to test authentication with DRF Tokens.
    """

    authentication_classes = [TokenAuthentication]
    permission_classes = [IsAuthenticatedOrReadOnly]

    @extend_schema(
        request=None,
        responses={
            200: {
                "type": "object",
                "properties": {
                    "user": {"type": "string"},
                },
            },
        },
    )
    def get(self, request: Request, format: Any = None) -> Response:
        user_name = ""
        if request.user.is_authenticated:
            user_name = str(request.user)
        return Response({"user": user_name})

    @extend_schema(
        request=None,
        responses={
            200: {
                "type": "object",
                "properties": {
                    "user": {"type": "string"},
                },
            },
            401: None,
        },
    )
    def post(self, request: Request, format: Any = None) -> Response:
        return Response({"user": str(request.user)})


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
            "node_type": node.type,
            "node_id": node.pk,
            "obj_link": get_component_purl_link(node.purl),
            "obj_uuid": node.object_id,
            "namespace": node.obj.namespace,
            "type": node.obj.type,
            "name": node.obj.name,
            "nvr": node.obj.nvr,
            "release": node.obj.release,
            "version": node.obj.version,
            "arch": node.obj.arch,
        }
    children = tuple(
        recursive_component_node_to_dict(c, component_type)
        for c in node.get_descendants().prefetch_related("obj").using("read_only")
    )
    if children:
        result["provides"] = children
    return result


def get_component_taxonomy(
    obj: Component, component_types: tuple[str, ...]
) -> tuple[taxonomy_dict_type, ...]:
    """Look up and return the taxonomy for a particular Component."""
    root_nodes = cache_tree_children(obj.cnodes.get_queryset().using("read_only"))
    dicts = tuple(
        recursive_component_node_to_dict(
            node,
            component_types,
        )
        for node in root_nodes
    )
    return dicts


@INCLUDE_EXCLUDE_FIELDS_SCHEMA
class SoftwareBuildViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    """View for api/v1/builds"""

    queryset = SoftwareBuild.objects.order_by("build_type", "build_id").using("read_only")
    serializer_class = SoftwareBuildSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = SoftwareBuildFilter


class ProductDataViewSet(ReadOnlyModelViewSet):  # TODO: TagViewMixin disabled until auth is added
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    search_fields = ["name", "description", "meta_attr"]
    filterset_class = ProductDataFilter
    ordering_field = "name"


@INCLUDE_EXCLUDE_FIELDS_SCHEMA
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
        return super().retrieve(request)

    def get_object(self):
        req = self.request
        p_ofuri = req.query_params.get("ofuri")
        p_name = req.query_params.get("name")
        try:
            if p_name:
                p = Product.objects.db_manager("read_only").get(name=p_name)
            elif p_ofuri:
                p = Product.objects.db_manager("read_only").get(ofuri=p_ofuri)
            else:
                pk = req.path.split("/")[-1]  # there must be better ways ...
                p = Product.objects.db_manager("read_only").get(uuid=pk)
            return p
        except Product.DoesNotExist:
            raise Http404


@INCLUDE_EXCLUDE_FIELDS_SCHEMA
class ProductVersionViewSet(ProductDataViewSet):
    """View for api/v1/product_versions"""

    queryset = ProductVersion.objects.order_by(ProductDataViewSet.ordering_field).using("read_only")
    serializer_class = ProductVersionSerializer

    def list(self, request: Request, *args: tuple, **kwargs: dict) -> Response:
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        return super().retrieve(request)

    def get_object(self):
        req = self.request
        pv_ofuri = req.query_params.get("ofuri")
        pv_name = req.query_params.get("name")
        try:
            if pv_name:
                pv = ProductVersion.objects.db_manager("read_only").get(name=pv_name)
            elif pv_ofuri:
                pv = ProductVersion.objects.db_manager("read_only").get(ofuri=pv_ofuri)
            else:
                pk = req.path.split("/")[-1]  # there must be better ways ...
                pv = ProductVersion.objects.db_manager("read_only").get(uuid=pk)
            return pv
        except ProductVersion.DoesNotExist:
            raise Http404


@INCLUDE_EXCLUDE_FIELDS_SCHEMA
class ProductStreamViewSetSet(ProductDataViewSet):
    """View for api/v1/product_streams"""

    queryset = (
        ProductStream.objects.filter(active=True)
        .order_by(ProductDataViewSet.ordering_field)
        .using("read_only")
    )
    serializer_class: Union[
        Type[ProductStreamSerializer], Type[ProductStreamSummarySerializer]
    ] = ProductStreamSerializer

    @extend_schema(
        parameters=[OpenApiParameter("active", OpenApiTypes.STR, OpenApiParameter.QUERY)]
    )
    def list(self, request: Request, *args: tuple, **kwargs: dict) -> Response:
        view = request.query_params.get("view")
        ps_ofuri = request.query_params.get("ofuri")
        ps_name = request.query_params.get("name")
        active = request.query_params.get("active")
        if active == "all":
            self.queryset = ProductStream.objects.order_by(super().ordering_field).using(
                "read_only"
            )
        if not ps_ofuri and not ps_name:
            if view == "summary":
                self.serializer_class = ProductStreamSummarySerializer
            return super().list(request)
        return super().retrieve(request)

    def get_object(self):
        req = self.request
        ps_ofuri = req.query_params.get("ofuri")
        ps_name = req.query_params.get("name")
        try:
            if ps_name:
                ps = ProductStream.objects.db_manager("read_only").get(name=ps_name)
            elif ps_ofuri:
                ps = ProductStream.objects.db_manager("read_only").get(ofuri=ps_ofuri)
            else:
                pk = req.path.split("/")[-1]  # there must be better ways ...
                ps = ProductStream.objects.db_manager("read_only").get(uuid=pk)
            return ps
        except ProductStream.DoesNotExist:
            raise Http404

    @action(methods=["get"], detail=True)
    def manifest(self, request: Request, pk: str = "") -> Response:
        obj = self.queryset.filter(pk=pk).first()
        if not obj:
            return Response(status=status.HTTP_404_NOT_FOUND)
        manifest = json.loads(obj.manifest)
        return Response(manifest)


@INCLUDE_EXCLUDE_FIELDS_SCHEMA
class ProductVariantViewSetSet(ProductDataViewSet):
    """View for api/v1/product_variants"""

    queryset = ProductVariant.objects.order_by(ProductDataViewSet.ordering_field).using("read_only")
    serializer_class = ProductVariantSerializer

    def list(self, request: Request, *args: tuple, **kwargs: dict) -> Response:
        req = self.request
        ofuri = req.query_params.get("ofuri")
        if not ofuri:
            return super().list(request)
        return super().retrieve(request)

    def get_object(self):
        req = self.request
        pv_ofuri = req.query_params.get("ofuri")
        pv_name = req.query_params.get("name")
        try:
            if pv_name:
                pv = ProductVariant.objects.db_manager("read_only").get(name=pv_name)
            elif pv_ofuri:
                pv = ProductVariant.objects.db_manager("read_only").get(ofuri=pv_ofuri)
            else:
                pk = req.path.split("/")[-1]  # there must be better ways ...
                pv = ProductVariant.objects.db_manager("read_only").get(uuid=pk)
            return pv
        except ProductVariant.DoesNotExist:
            raise Http404


@INCLUDE_EXCLUDE_FIELDS_SCHEMA
class ChannelViewSet(ReadOnlyModelViewSet):
    """View for api/v1/channels"""

    queryset = Channel.objects.order_by("name").using("read_only")
    serializer_class = ChannelSerializer
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ChannelFilter


@INCLUDE_EXCLUDE_FIELDS_SCHEMA
class ComponentViewSet(ReadOnlyModelViewSet, TagViewMixin):
    """View for api/v1/components"""

    queryset = (
        Component.objects.order_by("name", "type", "arch", "version", "release")
        .using("read_only")
        .select_related("software_build")
    )
    serializer_class: Union[
        Type[ComponentSerializer], Type[ComponentListSerializer]
    ] = ComponentSerializer
    search_fields = ["name", "description", "release", "version", "meta_attr"]
    filter_backends = [django_filters.rest_framework.DjangoFilterBackend, filters.SearchFilter]
    filterset_class = ComponentFilter

    def get_queryset(self) -> QuerySet[Component]:
        # 'latest' and 'root components' filter automagically turn on
        # when the ofuri parameter is given
        # We should remove this parameter and rely on standard Django filters instead
        ofuri = self.request.query_params.get("ofuri")
        if not ofuri:
            return self.queryset

        model, _ = get_model_ofuri_type(ofuri)
        if isinstance(model, Product):
            components_for_model = self.queryset.filter(products__ofuri=ofuri)
        elif isinstance(model, ProductVersion):
            components_for_model = self.queryset.filter(productversions__ofuri=ofuri)
        elif isinstance(model, ProductStream):
            components_for_model = self.queryset.filter(productstreams__ofuri=ofuri)
        elif isinstance(model, ProductVariant):
            components_for_model = self.queryset.filter(productvariants__ofuri=ofuri)
        else:
            # No matching model instance found, or invalid ofuri
            raise Http404
        return components_for_model.root_components().latest_components()

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
        purl = request.query_params.get("purl")
        if not purl:
            if view == "product":
                component_name = self.request.query_params.get("name", "")
                product_streams_arr = []
                for c in (
                    self.get_queryset()
                    .filter(name=component_name)
                    .prefetch_related("productstreams")
                ):
                    annotated_ps_qs = c.productstreams.annotate(component_purl=Value(c.purl)).using(
                        "read_only"
                    )
                    product_streams_arr.append(annotated_ps_qs)

                ps_qs = ProductStream.objects.none()
                productstreams = ps_qs.union(*product_streams_arr).using("read_only")
                serializer = ComponentProductStreamSummarySerializer(
                    productstreams, many=True, read_only=True
                )
                return Response({"count": productstreams.count(), "results": serializer.data})
            if view == "summary":
                self.serializer_class = ComponentListSerializer
            return super().list(request)
        return super().retrieve(request)

    def get_object(self):
        req = self.request
        purl = req.query_params.get("purl")
        try:
            if purl:
                # We re-encode the purl here to ensure each segment of the purl is url encoded,
                # as it's stored in the DB.
                purl = f"{PackageURL.from_string(purl)}"
                component = Component.objects.db_manager("read_only").get(purl=purl)
            else:
                pk = req.path.split("/")[-1]  # there must be better ways ...
                component = Component.objects.db_manager("read_only").get(pk=pk)
            return component
        except Component.DoesNotExist:
            raise Http404

    @action(methods=["get"], detail=True)
    def manifest(self, request: Request, pk: str = "") -> Response:
        obj = self.queryset.filter(pk=pk).first()
        if not obj:
            return Response(status=status.HTTP_404_NOT_FOUND)
        manifest = json.loads(obj.manifest)
        return Response(manifest)

    @action(
        methods=["put"],
        detail=True,
        authentication_classes=[TokenAuthentication],
        permission_classes=[IsAuthenticatedOrReadOnly],
    )
    def update_license(self, request: Request, pk: str = "") -> Response:
        """Allow OpenLCS to upload copyright text / license scan results for a component"""
        # In the future these could be separate endpoints
        # For testing we'll just keep it under one endpoint
        component = self.queryset.filter(pk=pk).using("default").first()
        if not component:
            return Response(status=status.HTTP_404_NOT_FOUND)

        copyright_text = request.data.get("copyright_text")
        license_concluded = request.data.get("license_concluded")
        license_declared = request.data.get("license_declared")
        openlcs_scan_url = request.data.get("openlcs_scan_url")
        openlcs_scan_version = request.data.get("openlcs_scan_version")
        if (
            not copyright_text
            and not license_concluded
            and not license_declared
            and not openlcs_scan_url
            and not openlcs_scan_version
        ):
            # At least one of above is required, else Bad Request
            return Response(status=status.HTTP_400_BAD_REQUEST)

        # if it's None, it wasn't included in the request
        # But it might be "" if the user wants to empty out the value
        if copyright_text is not None:
            component.copyright_text = copyright_text
        if license_concluded is not None:
            component.license_concluded_raw = license_concluded
        if license_declared is not None:
            if component.license_declared_raw:
                # The field already has an existing value, don't allow overwrites
                return Response(status=status.HTTP_400_BAD_REQUEST)
            component.license_declared_raw = license_declared
        if openlcs_scan_url is not None:
            component.openlcs_scan_url = openlcs_scan_url
        if openlcs_scan_version is not None:
            component.openlcs_scan_version = openlcs_scan_version
        component.save()
        response = Response(status=status.HTTP_302_FOUND)
        response["Location"] = f"/api/{CORGI_API_VERSION}/components/{component.uuid}"
        return response

    @action(methods=["get"], detail=True)
    def taxonomy(self, request: Request, pk: str = "") -> Response:
        obj = self.queryset.filter(pk=pk).first()
        if not obj:
            return Response(status=status.HTTP_404_NOT_FOUND)
        dicts = get_component_taxonomy(obj, tuple(ComponentNode.ComponentNodeType.values))
        return Response(dicts)


class AppStreamLifeCycleViewSet(ReadOnlyModelViewSet):
    """View for api/v1/lifecycles"""

    queryset = AppStreamLifeCycle.objects.order_by(
        "name", "type", "product", "initial_product_version", "stream"
    ).using("read_only")
    serializer_class = AppStreamLifeCycleSerializer
