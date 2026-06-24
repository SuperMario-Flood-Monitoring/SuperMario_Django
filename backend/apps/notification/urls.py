from django.urls import path

from .views import NotificationRecipientDetailView, NotificationRecipientListView


app_name = "notification"

urlpatterns = [
    path("", NotificationRecipientListView.as_view(), name="create"),
    path("list", NotificationRecipientListView.as_view(), name="list"),
    path("<int:recipient_id>", NotificationRecipientDetailView.as_view(), name="delete"),
]
