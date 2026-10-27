from django.urls import path

from . import views

app_name = "accounts"

urlpatterns = [
    path("login/", views.login_view, name="login"),
    path("verify/", views.verify_view, name="verify"),
    path("verify/resend/", views.resend_view, name="resend"),
    path("logout/", views.logout_view, name="logout"),
]
