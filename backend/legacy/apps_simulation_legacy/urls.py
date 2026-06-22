from django.urls import path

from .views import SimulationDemoView, SimulationStopView, SimulationView

app_name = "simulation"

urlpatterns = [
    path("", SimulationView.as_view(), name="list-start"),
    path("demo/", SimulationDemoView.as_view(), name="demo"),
    path("stop/", SimulationStopView.as_view(), name="stop"),
]
