import os

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError

from apps.auth.models import User

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "tnvjakfldh4"


class Command(BaseCommand):
    help = "커스텀 auth 테이블의 초기 ADMIN 계정을 생성하거나 갱신한다."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--password")
        parser.add_argument(
            "--only-if-no-admin",
            action="store_true",
            help="이미 ADMIN 사용자가 있으면 변경 없이 건너뛴다.",
        )

    def handle(self, *args, **options):
        username = str(
            options["username"]
            or os.getenv("SUPERMARIO_INITIAL_ADMIN_USERNAME")
            or DEFAULT_ADMIN_USERNAME
        ).strip()
        password = str(
            options["password"]
            or os.getenv("SUPERMARIO_INITIAL_ADMIN_PASSWORD")
            or DEFAULT_ADMIN_PASSWORD
        )
        if not username:
            raise CommandError("--username이 필요합니다.")
        if not password:
            raise CommandError("--password가 필요합니다.")

        existing_default_admin = User.objects.filter(username=username, role=User.Role.ADMIN)
        should_recreate_default_admin = existing_default_admin.exists()
        if should_recreate_default_admin:
            existing_default_admin.delete()

        if (
            options["only_if_no_admin"]
            and not should_recreate_default_admin
            and User.objects.filter(role=User.Role.ADMIN).exists()
        ):
            self.stdout.write(self.style.SUCCESS("ADMIN 사용자가 이미 있어 건너뜁니다."))
            return

        user, created = User.objects.update_or_create(
            username=username,
            defaults={
                "role": User.Role.ADMIN,
                "password": make_password(password),
                "refresh_token": None,
            },
        )
        action = "생성" if created else "갱신"
        self.stdout.write(self.style.SUCCESS(f"ADMIN 사용자 {user.username} {action} 완료."))
