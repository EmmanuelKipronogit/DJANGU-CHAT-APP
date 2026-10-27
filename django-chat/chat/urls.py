from django.urls import path

from . import views

app_name = "chat"

urlpatterns = [
    path("", views.home, name="home"),
    path("c/<uuid:conversation_id>/", views.room, name="room"),
    # JSON API (also what a future mobile client would use)
    path("api/conversations/", views.conversation_list, name="api_conversations"),
    path("api/conversations/<uuid:conversation_id>/", views.conversation_detail, name="api_conversation"),
    path("api/conversations/<uuid:conversation_id>/messages/", views.message_history, name="api_history"),
    path("api/conversations/<uuid:conversation_id>/members/", views.add_members, name="api_add_members"),
    path("api/conversations/<uuid:conversation_id>/read/", views.mark_read, name="api_read"),
    path("api/direct/", views.start_direct, name="api_direct"),
    path("api/groups/", views.create_group, name="api_group"),
    path("api/users/search/", views.user_search, name="api_user_search"),
]
