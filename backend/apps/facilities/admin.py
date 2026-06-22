from django.contrib import admin

from .models import Facility


@admin.register(Facility)
class FacilityAdmin(admin.ModelAdmin):
    list_display = ("id", "name", "facility_type", "normal_value", "unit", "is_active")
    list_filter = ("facility_type", "is_active")
    search_fields = ("name", "location")
