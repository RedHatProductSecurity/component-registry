from django.urls import include, path
from drf_spectacular.views import SpectacularAPIView, SpectacularSwaggerView
from rest_framework import routers

from .views import (
    AppStreamLifeCycleView,
    ChannelView,
    ComponentTaxonomyView,
    ComponentView,
    CoverageReportView,
    ProductStreamView,
    ProductTaxonomyView,
    ProductVariantView,
    ProductVersionView,
    ProductView,
    RelationsView,
    SoftwareBuildView,
    StatusView,
)
from .views_search import SearchDeptopiaView

router = routers.DefaultRouter(trailing_slash=False)
router.register(r"builds", SoftwareBuildView)
router.register(r"components", ComponentView)
router.register(r"lifecycles", AppStreamLifeCycleView)
router.register(r"products", ProductView)
router.register(r"product_versions", ProductVersionView)
router.register(r"product_streams", ProductStreamView)
router.register(r"product_variants", ProductVariantView)
router.register(r"channels", ChannelView)
router.register(r"status", StatusView, basename="status")
router.register(r"reports/coverage", CoverageReportView, basename="coverage-reports")
router.register(r"relations", RelationsView)

urlpatterns = [
    # v1 API
    path(r"schema", SpectacularAPIView.as_view(), name="schema"),
    path(
        r"schema/docs",
        SpectacularSwaggerView.as_view(url_name="schema"),
    ),
    path(r"taxonomy/components", ComponentTaxonomyView.as_view()),
    path(r"taxonomy/products", ProductTaxonomyView.as_view()),
    path(r"search/deptopia", SearchDeptopiaView.as_view()),
    path("", include(router.urls)),
]
