"""Django WSGI 설정.

이 파일은 WSGI callable을 모듈 수준의 ``application`` 변수로 노출한다.
자세한 내용은 Django 공식 WSGI 배포 문서를 참고한다.
"""

import os

from django.core.wsgi import get_wsgi_application

os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')

application = get_wsgi_application()
