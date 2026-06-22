from django.urls import path

from .views import FacilitiesView, FacilityDetailView

app_name = "facilities"

urlpatterns = [
    path("", FacilitiesView.as_view(), name="list-create"),
    path("<int:facility_id>/", FacilityDetailView.as_view(), name="detail"),
]
