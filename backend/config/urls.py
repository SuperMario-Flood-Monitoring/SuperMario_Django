"""Django HTTP URL 라우팅 설정."""
import os

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('api/auth/', include("apps.auth.urls")),
    path('api/', include("apps.simulation.urls")),
    path('api/', include("apps.scenarios.urls")),
    path('api/facilities/', include("apps.facilities.urls")),
    path('admin/', admin.site.urls),
]

if os.getenv("ENABLE_LEGACY_SIMULATION_API", "false").lower() == "true":
    urlpatterns.append(
        path('api/legacy-simulations/', include("legacy.apps_simulation_legacy.urls"))
    )
