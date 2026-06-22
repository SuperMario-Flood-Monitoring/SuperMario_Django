"""
URL configuration for config project.

The `urlpatterns` list routes URLs to views. For more information please see:
    https://docs.djangoproject.com/en/6.0/topics/http/urls/
Examples:
Function views
    1. Add an import:  from my_app import views
    2. Add a URL to urlpatterns:  path('', views.home, name='home')
Class-based views
    1. Add an import:  from other_app.views import Home
    2. Add a URL to urlpatterns:  path('', Home.as_view(), name='home')
Including another URLconf
    1. Import the include() function: from django.urls import include, path
    2. Add a URL to urlpatterns:  path('blog/', include('blog.urls'))
"""
import os

from django.contrib import admin
from django.urls import path, include

urlpatterns = [
    path('api/', include("apps.simulation.urls")),
    path('api/', include("apps.scenarios.urls")),
    path('api/facilities/', include("apps.facilities.urls")),
    path('admin/', admin.site.urls),
]

if os.getenv("ENABLE_LEGACY_SIMULATION_API", "false").lower() == "true":
    urlpatterns.append(
        path('api/legacy-simulations/', include("legacy.apps_simulation_legacy.urls"))
    )
