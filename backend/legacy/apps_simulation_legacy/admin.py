from django.contrib import admin

from .models import SimulationRun


@admin.register(SimulationRun)
class SimulationRunAdmin(admin.ModelAdmin):
    list_display = (
        "id",
        "rainfall_status",
        "rainfall_amount",
        "duration_minutes",
        "status",
        "created_at",
    )
    list_filter = ("status", "rainfall_status")
