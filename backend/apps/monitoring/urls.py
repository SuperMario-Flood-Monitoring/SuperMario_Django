from django.urls import path

from .apis import hazard_api

app_name = "monitoring"

urlpatterns = [
    path("", hazard_api.urls),
]
