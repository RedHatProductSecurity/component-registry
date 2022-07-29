import logging
from urllib.parse import quote, unquote

from django.db.models import Q
from rest_framework.response import Response
from rest_framework.views import APIView

from corgi.api.constants import CORGI_API_VERSION
from corgi.core.models import Component

logger = logging.getLogger(__name__)


class SearchDeptopiaView(APIView):
    """ """

    def get(self, request, *args, **kwargs):
        """
        Loop through match components based on search terms
        and return all sources (ancestors), providing product_stream.
        """

        name = None
        if request.query_params.get("name"):
            name = unquote(request.query_params.get("name"))

        re_name = None
        if request.query_params.get("re_name"):
            re_name = unquote(request.query_params.get("re_name"))

        purl = None
        if request.query_params.get("purl"):
            purl = unquote(request.query_params.get("purl"))

        nevra = request.query_params.get("nevra")
        nvr = request.query_params.get("nvr")

        ecosystem = request.query_params.get("ecosystem")
        q = request.query_params.get("q")

        flatten = request.query_params.get("flatten")

        # match_root = True
        # match_dep = True
        # if request.query_params.get("match_root"):
        #     match_root = True
        #     match_dep = False
        # if request.query_params.get("match_dep"):
        #     match_root = False
        #     match_dep = True

        query = Q()
        if name:
            query |= Q(name=name)
        if re_name:
            query |= Q(name__icontains=re_name)
        if purl:
            query |= Q(purl=purl)
        if nevra:
            query |= Q(nevra=nevra)
        if nvr:
            query |= Q(nvr=nvr)

        component_search = Component.objects.filter(query)

        results=[]
        if component_search:
            for c in component_search:

                is_root= True
                if c.cnodes.all().get_ancestors():
                    is_root=False

                c_related_urls=[]
                for c_upstream_purl in c.upstream:
                    upstream_component = Component.objects.get(purl=c_upstream_purl)
                    c_related_urls.append(upstream_component.related_url)

                source_results = []
                for s in c.sources:
                    s_component = Component.objects.get(purl=s)
                    related_urls = []
                    #TODO: rename upstream to upstreams
                    for u in s_component.upstream:
                        upstream_component = Component.objects.get(purl=u)
                        related_urls.append(upstream_component.related_url)
                    if s_component.type != Component.Type.UPSTREAM:
                        source_results.append({
                            "type":s_component.type,
                            "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(s_component.purl)}",# noqa
                            "purl":s_component.purl,
                            "name": s_component.name,
                            "related_urls": related_urls,
                            "product_streams":s_component.product_streams,
                        })

                result = {
                    "is_root": is_root,
                    "type": c.type,
                    "link": f"{request.scheme}://{request.META['HTTP_HOST']}/api/{CORGI_API_VERSION}/components?purl={quote(c.purl)}", # noqa
                    "purl": c.purl,
                    "name": c.name,
                    "related_urls": c_related_urls,
                    "ecosystem": "",
                }
                if not is_root:
                    result["dependency_exists_in"] = source_results

                results.append(result)

        if flatten:
            flat_results=[]
            for result in results:
                for source_component in result["dependency_exists_in"]:
                    flat_results.append(
                        {
                            "product_streams":source_component["product_streams"],
                            "link": source_component["link"],
                            "purl": source_component["purl"],
                            "related_url" : source_component["related_urls"],
                            "link_dep": result["link"],
                            "dep": result["purl"],
                            "dep_related_urls": result["related_urls"]
                        }

                    )
            return Response(flat_results)

        return Response(results)
