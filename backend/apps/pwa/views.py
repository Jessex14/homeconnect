from pathlib import Path

from django.conf import settings
from django.http import HttpResponse
from django.shortcuts import render
from django.views.decorators.csrf import ensure_csrf_cookie


@ensure_csrf_cookie
def app_shell(request):
    response = render(request, "pwa/index.html", {"rent_qa_enabled": bool(getattr(settings, "RENT_QA_PROVIDER", ""))})
    response["Referrer-Policy"] = "no-referrer"
    return response


def service_worker(request):
    script = Path(__file__).parent / "static" / "pwa" / "service-worker.js"
    response = HttpResponse(script.read_text(encoding="utf-8"), content_type="application/javascript; charset=utf-8")
    response["Service-Worker-Allowed"] = "/"
    response["Cache-Control"] = "no-cache"
    return response
