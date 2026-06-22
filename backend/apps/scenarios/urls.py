from django.urls import path

from .apis import scenario_api


urlpatterns = [
    path("", scenario_api.urls),
]
