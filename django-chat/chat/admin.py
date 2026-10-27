from django.contrib import admin

from .models import Conversation, Membership, Message


class MembershipInline(admin.TabularInline):
    model = Membership
    extra = 0
    raw_id_fields = ["user"]


@admin.register(Conversation)
class ConversationAdmin(admin.ModelAdmin):
    list_display = ["id", "kind", "name", "created_at", "updated_at"]
    list_filter = ["kind"]
    inlines = [MembershipInline]


@admin.register(Message)
class MessageAdmin(admin.ModelAdmin):
    list_display = ["id", "conversation", "sender", "created_at"]
    raw_id_fields = ["conversation", "sender"]
