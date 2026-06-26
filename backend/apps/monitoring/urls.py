from django.urls import path

from .views import (
    HazardActionCompleteView,
    HazardActionView,
    HazardDetailView,
    HazardForecastView,
    HazardListView,
)


app_name = "monitoring"

urlpatterns = [
    path("hazards", HazardListView.as_view(), name="hazard-list"),
    path("hazards/forecast", HazardForecastView.as_view(), name="hazard-forecast"),
    path("hazards/<int:hazard_id>", HazardDetailView.as_view(), name="hazard-detail"),
    path("hazards/<int:hazard_id>/actions", HazardActionView.as_view(), name="hazard-action"),
    path(
        "hazards/<int:hazard_id>/actions/<int:action_id>",
        HazardActionCompleteView.as_view(),
        name="hazard-action-complete",
    ),
]
