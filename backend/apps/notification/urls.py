from django.urls import path

from .apis import notification_api


app_name = "notification"

urlpatterns = [
    path("", notification_api.urls),
]
