#!/usr/bin/env python
"""Django 관리 명령을 실행하는 진입점."""
import os
import sys


def main():
    """관리 명령을 실행한다."""
    os.environ.setdefault('DJANGO_SETTINGS_MODULE', 'config.settings')
    try:
        from django.core.management import execute_from_command_line
    except ImportError as exc:
        raise ImportError(
            "Django를 import할 수 없습니다. Django가 설치되어 있고 "
            "PYTHONPATH 환경변수에서 접근 가능한지 확인하세요. "
            "가상환경 활성화를 빠뜨렸을 수도 있습니다."
        ) from exc
    execute_from_command_line(sys.argv)


if __name__ == '__main__':
    main()
