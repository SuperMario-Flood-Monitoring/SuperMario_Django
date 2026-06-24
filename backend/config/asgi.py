"""Django ASGI 설정.

이 파일은 ASGI callable을 모듈 수준의 ``application`` 변수로 노출한다.
자세한 내용은 Django 공식 ASGI 배포 문서를 참고한다.
"""

import os

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

from channels.auth import AuthMiddlewareStack
from channels.routing import ProtocolTypeRouter, URLRouter
from django.core.asgi import get_asgi_application

from apps.simulation.routing import websocket_urlpatterns as simulation_websocket_urlpatterns

application = ProtocolTypeRouter(
    {
        "http": get_asgi_application(),
        "websocket": AuthMiddlewareStack(
            URLRouter(simulation_websocket_urlpatterns)
        ),
    }
)
