from django.urls import path

from .apis import auth_api


urlpatterns = [
    path("", auth_api.urls),
]

