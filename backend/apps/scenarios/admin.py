from django.contrib import admin

from .models import Scenario


@admin.register(Scenario)
class ScenarioAdmin(admin.ModelAdmin):
    list_display = ("id", "title", "version", "is_active", "created_at", "updated_at")
    list_filter = ("is_active",)
    search_fields = ("title", "description")
    readonly_fields = ("created_at", "updated_at")
