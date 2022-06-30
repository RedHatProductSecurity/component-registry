from django.http import HttpRequest, HttpResponse
from django.shortcuts import render
from django.views.decorators.http import require_safe


@require_safe
def home(request: HttpRequest) -> HttpResponse:
    """Serve home page"""
    return render(request, "index.html")
