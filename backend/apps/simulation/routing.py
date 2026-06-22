from django.urls import path

from .consumers import SimulationConsumer


websocket_urlpatterns = [
    path("api/ws/simulation", SimulationConsumer.as_asgi()),
    path("api/ws/simulation/", SimulationConsumer.as_asgi()),
]
