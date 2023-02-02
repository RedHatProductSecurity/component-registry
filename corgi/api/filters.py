import logging

from django_filters.rest_framework import CharFilter, Filter, FilterSet

from corgi.core.models import Channel, Component, SoftwareBuild

logger = logging.getLogger(__name__)


class TagFilter(Filter):
    def filter(self, queryset, value):
        # TODO: currently defaults to AND condition, we should make this configurable for both
        # OR and AND conditions.
        if not value:
            return queryset
        search_tags = value.split(",")
        for tag in search_tags:
            if ":" in tag:
                tag_name, _, tag_value = tag.partition(":")
                queryset = queryset.filter(
                    tags__name__icontains=tag_name, tags__value__icontains=tag_value
                )
            else:
                queryset = queryset.filter(tags__name__icontains=tag)
        return queryset


class ComponentFilter(FilterSet):
    """Class that filters queries to Component list views."""

    class Meta:
        model = Component
        # Fields that are matched to a filter using their Django model field type and default
        # __exact lookups.
        fields = ("type", "namespace", "version", "release", "arch", "nvr", "nevra")

    # Custom filters
    name = CharFilter()
    re_name = CharFilter(lookup_expr="regex", field_name="name")
    re_purl = CharFilter(lookup_expr="regex", field_name="purl")
    description = CharFilter(lookup_expr="icontains")
    tags = TagFilter()

    products = CharFilter(method="filter_productmodel")
    product_versions = CharFilter(method="filter_productmodel")
    product_streams = CharFilter(method="filter_productmodel")
    product_variants = CharFilter(method="filter_productmodel")
    channels = CharFilter(method="filter_productmodel")

    sources = CharFilter(lookup_expr="purl__icontains")
    provides = CharFilter(lookup_expr="purl__icontains")
    upstreams = CharFilter(lookup_expr="purl__icontains")
    re_upstream = CharFilter(lookup_expr="purl__regex", field_name="upstreams")

    @staticmethod
    def filter_productmodel(queryset, name, value):
        if name == "ofuri":
            # User gave a filter like ?ofuri= in URL, assume they wanted a stream
            name = "productstreams"
        if name == "channels":
            # Linked channels have no ofuri, look up by name instead
            lookup = f"{name}__name"
        else:
            # All ProductModel subclasses define an ofuri field
            lookup = f"{name.replace('_', '')}__ofuri"
        return queryset.filter(**{lookup: value})


class ProductDataFilter(FilterSet):
    """Class that filters queries to Product-related list views."""

    name = CharFilter()
    re_name = CharFilter(lookup_expr="regex", field_name="name")
    re_ofuri = CharFilter(lookup_expr="regex", field_name="ofuri")
    tags = TagFilter()

    products = CharFilter(lookup_expr="name__icontains")
    product_versions = CharFilter(field_name="productversions", lookup_expr="name__icontains")
    product_streams = CharFilter(field_name="productstreams", lookup_expr="name__icontains")
    product_variants = CharFilter(field_name="productvariants", lookup_expr="name__icontains")
    channels = CharFilter(lookup_expr="name__icontains")


class ChannelFilter(FilterSet):
    """Class that filters queries to Channel-related list views."""

    name = CharFilter(lookup_expr="icontains")

    class Meta:
        model = Channel
        fields = ("type",)


class SoftwareBuildFilter(FilterSet):
    """Class that filters queries to SoftwareBuild views."""

    name = CharFilter(lookup_expr="icontains")
    tags = TagFilter()

    class Meta:
        model = SoftwareBuild
        fields = ("build_type",)
