from django.urls import path

from .consumers import SimulationConsumer

websocket_urlpatterns = [
    path("ws/simulation/", SimulationConsumer.as_asgi()),
]
