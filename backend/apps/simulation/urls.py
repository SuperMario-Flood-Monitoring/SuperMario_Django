from django.urls import path

from .apis import editor_api, engine_api


urlpatterns = [
    path("engine/", engine_api.urls),
    path("editor/", editor_api.urls),
]
