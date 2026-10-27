from django.contrib import admin
from django.http import HttpResponse
from django.urls import include, path


def healthz(request):
    return HttpResponse("ok")


urlpatterns = [
    path("healthz", healthz),
    path("admin/", admin.site.urls),
    path("accounts/", include("allauth.urls")),  # Google OAuth callbacks
    path("", include("accounts.urls")),
    path("", include("chat.urls")),
]
