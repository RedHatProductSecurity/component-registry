import logging

from django.core.exceptions import FieldDoesNotExist
from django.core.validators import EMPTY_VALUES
from django.db.models import QuerySet
from django.http import Http404
from django_filters.rest_framework import BooleanFilter, CharFilter, Filter, FilterSet

from corgi.api.serializers import get_model_ofuri_type
from corgi.core.models import (
    Channel,
    Component,
    ComponentQuerySet,
    Product,
    ProductStream,
    ProductVariant,
    ProductVersion,
    SoftwareBuild,
)

logger = logging.getLogger(__name__)


class IncludeFieldsFilterSet(FilterSet):
    # inspired by OSIDB https://github.com/RedHatProductSecurity/osidb/pull/423
    include_fields = CharFilter(method="include_fields_filter")

    def _preprocess_fields(self, value):
        """
        Converts a comma-separated list of fields into an ORM-friendly format.
        A list of fields passed-in to a filter will look something like:
            cve_id,affects.uuid,affects.trackers.resolution
        This method converts such a string into a Python list like so:
            ["cve_id", "affects__uuid", "affects__trackers__resolution"]
        """
        return value.replace(".", "__").split(",")

    def _filter_fields(self, fields):
        """
        Given a set of field names, returns a set of relations and valid fields.
        The argument `fields` can contain any number of user-provided fields,
        these fields may not exist, or they may be properties or any other
        kind of virtual/computed field. Since the goal of these field names
        would be to use them in SQL, we need to make sure to only return
        database-persisted fields, and optionally relations.
        The result of this method can be safely passed down to
        prefetch_related() / only() / defer().
        """
        prefetch_set = set()
        field_set = set()
        for fname in list(fields):
            try:
                # check that the field actually exists
                field = self._meta.model._meta.get_field(fname)
            except FieldDoesNotExist:
                continue
            if not field.concrete:
                # a field is concrete if it has a column in the database, we don't
                # want non-concrete fields as we cannot filter them via SQL
                if field.is_relation:
                    # related fields are somewhat exceptional in that while we
                    # cannot use them in only(), we can prefetch them
                    prefetch_set.add(fname)
                continue
            field_set.add(fname)
        return prefetch_set, field_set

    def include_fields_filter(self, queryset, name, value):
        """
        Optimizes a view's QuerySet based on user input.
        This filter will attempt to optimize a given view's queryset based on an
        allowlist of fields (value parameter) provided by the user in order to
        improve performance.
        It does so by leveraging the prefetch_related() and only() QuerySet
        methods.
        """
        all_fields = set()
        to_prefetch = set()
        # we want to convert e.g. foo.id to foo__id, so that it's easier to use
        # with Django's QuerySet.prefetch_related() method directly
        fields = self._preprocess_fields(value)
        for field in fields:
            if "__" in field:
                # must use rsplit as the field can contain multiple relationship
                # traversals
                rel = field.rsplit("__", 1)[0]
                to_prefetch.add(rel)
                continue
            all_fields.add(field)
        # include any default prefetch/select related on existing models
        if queryset.model == Component:
            all_fields.add("software_build")
        # must verify that the requested fields are database-persisted fields,
        # properties, descriptors and related fields will yield errors
        prefetch, valid_fields = self._filter_fields(all_fields)
        to_prefetch |= prefetch
        return (
            queryset.prefetch_related(None)
            .prefetch_related(*list(to_prefetch))
            .only(*list(valid_fields))
        )


class EmptyStringFilter(BooleanFilter):
    """Filter or exclude an arbitrary field against an empty string value"""

    def filter(self, qs: QuerySet[Component], value: bool) -> QuerySet[Component]:
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return qs

        # Use .exclude() if the filter declares exclude=True
        # or if the user choose BooleanFilter's "NO" option in the UI
        # e.g. "show empty licenses" -> NO means show only components with licenses
        # Otherwise use .filter()
        exclude = self.exclude ^ (value is False)
        method = qs.exclude if exclude else qs.filter

        return method(**{self.field_name: ""})


class TagFilter(Filter):
    def filter(self, queryset: QuerySet, value: str) -> QuerySet:
        # TODO: currently defaults to AND condition, we should make this configurable for both
        # OR and AND conditions.
        if not value:
            return queryset
        search_tags = value.split(",")
        for tag in search_tags:
            exclude = False
            if tag.startswith("!"):
                tag = tag[1:]
                exclude = True
            if ":" in tag:
                tag_name, _, tag_value = tag.partition(":")
                queryset_kwargs = {"tags__name": tag_name, "tags__value": tag_value}
            else:
                queryset_kwargs = {"tags__name": tag}
            if exclude:
                queryset = queryset.exclude(**queryset_kwargs)
            else:
                queryset = queryset.filter(**queryset_kwargs)
        return queryset


class ComponentFilter(IncludeFieldsFilterSet):
    """Class that filters queries to Component list views."""

    class Meta:
        model = Component
        # Fields that are matched to a filter using their Django model field type and default
        # __exact lookups.
        fields = (
            "type",
            "namespace",
            "name",
            "version",
            "release",
            "arch",
            "nvr",
            "nevra",
            "epoch",
        )

    # Custom filters
    re_name = CharFilter(lookup_expr="iregex", field_name="name", distinct=True)
    re_purl = CharFilter(lookup_expr="iregex", field_name="purl", distinct=True)
    description = CharFilter(lookup_expr="icontains")
    related_url = CharFilter(lookup_expr="icontains")
    tags = TagFilter()

    # User gave a filter like ?ofuri= in URL, assume they wanted a stream
    ofuri = CharFilter(
        method="filter_ofuri_components", label="Show only latest root components of product"
    )
    products = CharFilter(method="filter_ofuri_or_name")
    product_versions = CharFilter(field_name="productversions", method="filter_ofuri_or_name")
    product_streams = CharFilter(field_name="productstreams", method="filter_ofuri_or_name")
    product_variants = CharFilter(field_name="productvariants", method="filter_ofuri_or_name")
    channels = CharFilter(lookup_expr="name")

    # Normally we are interested in retrieving provides,sources or upstreams of a specific component
    sources = CharFilter(lookup_expr="purl")
    sources_name = CharFilter(field_name="sources", lookup_expr="name", distinct=True)
    provides = CharFilter(lookup_expr="purl")
    provides_name = CharFilter(field_name="provides", lookup_expr="name", distinct=True)
    upstreams = CharFilter(lookup_expr="purl")
    upstreams_name = CharFilter(field_name="upstreams", lookup_expr="name", distinct=True)
    downstreams = CharFilter(lookup_expr="purl")

    # otherwise use regex to match provides,sources or upstreams purls
    re_sources = CharFilter(field_name="sources", lookup_expr="purl__iregex", distinct=True)
    re_sources_name = CharFilter(field_name="sources", lookup_expr="name__iregex", distinct=True)
    re_provides = CharFilter(field_name="provides", lookup_expr="purl__iregex", distinct=True)
    re_provides_name = CharFilter(field_name="provides", lookup_expr="name__iregex", distinct=True)
    re_downstreams = CharFilter(field_name="downstreams", lookup_expr="purl__iregex", distinct=True)
    re_downstreams_name = CharFilter(
        field_name="downstreams", lookup_expr="name__iregex", distinct=True
    )
    re_upstreams = CharFilter(field_name="upstreams", lookup_expr="purl__iregex", distinct=True)
    re_upstreams_name = CharFilter(
        field_name="upstreams", lookup_expr="name__iregex", distinct=True
    )
    el_match = CharFilter(label="RHEL version for layered products", lookup_expr="icontains")
    released_components = BooleanFilter(
        method="filter_released_components", label="Show only released components"
    )
    root_components = BooleanFilter(
        method="filter_root_components",
        label="Show only root components (source RPMs, index container images)",
    )

    active_streams = BooleanFilter(
        label="Show components from active streams",
        method="filter_active_streams",
    )

    # Filters are applied to querysets in the same order they're defined in
    # so we must keep active_streams here above latest_components_by_streams below
    latest_components_by_streams = BooleanFilter(
        method="filter_latest_components_by_streams",
        label="Show only latest components across product streams",
    )

    missing_copyright = EmptyStringFilter(
        field_name="copyright_text",
        label="Show only unscanned components (where copyright text is empty)",
    )

    missing_license_concluded = EmptyStringFilter(
        field_name="license_concluded_raw",
        label="Show only unscanned components (where license concluded is empty)",
    )

    missing_license_declared = EmptyStringFilter(
        field_name="license_declared_raw",
        label="Show only unscanned components (where license declared is empty)",
    )

    missing_scan_url = EmptyStringFilter(
        field_name="openlcs_scan_url",
        label="Show only unscanned components (where OpenLCS scan URL is empty)",
    )

    gomod_components = BooleanFilter(
        label="Show only gomod components, hide go-packages",
        method="filter_gomod_components",
    )

    @staticmethod
    def filter_gomod_components(
        qs: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet[Component]:
        """Show only GOLANG components that are Go modules, and hide Go packages"""
        # TODO: Probably should be added to ComponentQuerySet instead of here
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return qs

        # Below check only works / keys in meta_attr only exist for GOLANG type
        qs = qs.filter(type=Component.Type.GOLANG)

        # Use .exclude() if the user choose BooleanFilter's "NO" option in the UI
        # e.g. "show gomod components" -> NO means show only go-packages
        # Otherwise use .filter()
        method = qs.exclude if value is False else qs.filter

        return method(**{"meta_attr__go_component_type": "gomod"})

    @staticmethod
    def filter_ofuri_or_name(
        queryset: ComponentQuerySet, name: str, value: str
    ) -> QuerySet[Component]:
        """Filter some field by a ProductModel subclass's ofuri
        Or else by a name, depending on the user's input"""
        if value.startswith("o:redhat:"):
            # User provided an ofuri
            # Filter using "products__ofuri", "productversions__ofuri", etc.
            lookup_expr = f"{name}__ofuri"
        else:
            # We don't have a valid ofuri, so assume the user gave a name
            # Filter using "product__name", "productversions__name", etc.
            lookup_expr = f"{name}__name"
        return queryset.filter(**{lookup_expr: value})

    @staticmethod
    def filter_released_components(
        queryset: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet["Component"]:
        """Show only released components in some queryset if user chose YES / NO"""
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return queryset
        # Else user gave a non-empty value
        # Truthy values return the excluded queryset (only released components)
        # Falsey values return the filtered queryset (only unreleased components)
        return queryset.released_components(include=value)

    @staticmethod
    def filter_root_components(
        queryset: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet["Component"]:
        """Show only root / non-root components in some queryset if user chose YES / NO"""
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return queryset
        # Else user gave a non-empty value
        # Truthy values return the filtered queryset (only root components)
        # Falsey values return the excluded queryset (only non-root components)
        return queryset.root_components(include=value)

    @staticmethod
    def filter_latest_components_by_streams(
        queryset: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet["Component"]:
        """Show only latest ROOT components in some queryset if user chose YES / NO"""
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return queryset
        # Else user gave a non-empty value
        # Truthy values return the filtered queryset (only latest components)
        # Falsey values return the excluded queryset (only older components)
        return queryset.latest_components_by_streams(include=value)

    @staticmethod
    def filter_active_streams(
        queryset: ComponentQuerySet, _name: str, value: bool
    ) -> QuerySet["Component"]:
        """Show only components from active streams when True"""
        if value in EMPTY_VALUES:
            # User gave an empty ?param= so return the unfiltered queryset
            return queryset
        return queryset.active_streams(include=value)

    @staticmethod
    def filter_ofuri_components(
        queryset: ComponentQuerySet, name: str, value: str
    ) -> QuerySet["Component"]:
        """'latest' and 'root components' filter automagically turn on
        when the ofuri parameter is provided"""
        if value in EMPTY_VALUES:
            return queryset
        model, model_type = get_model_ofuri_type(value)
        if isinstance(model, Product):
            queryset = queryset.filter(products__ofuri=value)
        elif isinstance(model, ProductVersion):
            queryset = queryset.filter(productversions__ofuri=value)
        elif isinstance(model, ProductStream):
            queryset = queryset.filter(productstreams__ofuri=value)
        elif isinstance(model, ProductVariant):
            queryset = queryset.filter(productvariants__ofuri=value)
        else:
            # No matching model instance found, or invalid ofuri
            raise Http404
        return queryset.root_components().latest_components(
            model_type=model_type,
            ofuri=value,
            include_inactive_streams=True,
        )


class ProductDataFilter(IncludeFieldsFilterSet):
    """Class that filters queries to Product-related list views."""

    name = CharFilter()
    re_name = CharFilter(lookup_expr="iregex", field_name="name")
    re_ofuri = CharFilter(lookup_expr="iregex", field_name="ofuri")
    tags = TagFilter()

    products = CharFilter(lookup_expr="name__icontains")
    product_versions = CharFilter(field_name="productversions", lookup_expr="name__icontains")
    product_streams = CharFilter(field_name="productstreams", lookup_expr="name__icontains")
    product_variants = CharFilter(field_name="productvariants", lookup_expr="name__icontains")
    channels = CharFilter(lookup_expr="name__icontains")


class ChannelFilter(IncludeFieldsFilterSet):
    """Class that filters queries to Channel-related list views."""

    name = CharFilter(lookup_expr="icontains")

    class Meta:
        model = Channel
        fields = ("type",)


class SoftwareBuildFilter(IncludeFieldsFilterSet):
    """Class that filters queries to SoftwareBuild views."""

    name = CharFilter(lookup_expr="icontains")
    tags = TagFilter()

    class Meta:
        model = SoftwareBuild
        fields = (
            "build_id",
            "build_type",
        )
