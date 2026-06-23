from django.contrib import admin

from .models import Notification, User


@admin.register(User)
class UserAdmin(admin.ModelAdmin):
    list_display = ("user_id", "username", "role")
    search_fields = ("username",)
    list_filter = ("role",)


@admin.register(Notification)
class NotificationAdmin(admin.ModelAdmin):
    list_display = ("token_id", "name")
    search_fields = ("name",)

