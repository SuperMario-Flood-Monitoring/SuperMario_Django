from django.contrib import admin

from .models import HazardAction, HazardCaseEmbedding, HazardEvent


@admin.register(HazardEvent)
class HazardEventAdmin(admin.ModelAdmin):
    list_display = ("id", "target_id", "hazard_type", "hazard_level", "status", "is_deleted", "created_at")
    list_filter = ("hazard_level", "hazard_type", "status", "is_deleted")
    search_fields = ("event_key", "run_id", "target_id", "hazard_detail")


@admin.register(HazardAction)
class HazardActionAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "action_type", "result_status", "fastapi_sync_status", "fastapi_vector_id", "created_at")
    list_filter = ("fastapi_sync_status", "action_type", "result_status")
    search_fields = ("action_detail", "action_type", "result_status", "fastapi_vector_id", "fastapi_error_message")


@admin.register(HazardCaseEmbedding)
class HazardCaseEmbeddingAdmin(admin.ModelAdmin):
    list_display = ("id", "event", "vector_id", "created_at")
    search_fields = ("vector_id", "embedding_text")
