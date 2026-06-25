from django.urls import path

from .views import HazardActionView, HazardDetailView, HazardForecastView, HazardListView


app_name = "monitoring"

urlpatterns = [
    path("hazards", HazardListView.as_view(), name="hazard-list"),
    path("hazards/forecast", HazardForecastView.as_view(), name="hazard-forecast"),
    path("hazards/<int:hazard_id>", HazardDetailView.as_view(), name="hazard-detail"),
    path("hazards/<int:hazard_id>/actions", HazardActionView.as_view(), name="hazard-action"),
]
