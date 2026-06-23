import os

from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError

from apps.auth.models import User

DEFAULT_ADMIN_USERNAME = "admin"
DEFAULT_ADMIN_PASSWORD = "수퍼마리오4"


class Command(BaseCommand):
    help = "Create or update the initial ADMIN account for the custom auth table."

    def add_arguments(self, parser):
        parser.add_argument("--username")
        parser.add_argument("--password")
        parser.add_argument(
            "--only-if-no-admin",
            action="store_true",
            help="Skip without changes when any ADMIN user already exists.",
        )

    def handle(self, *args, **options):
        if options["only_if_no_admin"] and User.objects.filter(role=User.Role.ADMIN).exists():
            self.stdout.write(self.style.SUCCESS("ADMIN user already exists. Skipped."))
            return

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
            raise CommandError("--username is required.")
        if not password:
            raise CommandError("--password is required.")

        user, created = User.objects.update_or_create(
            username=username,
            defaults={
                "role": User.Role.ADMIN,
                "password": make_password(password),
                "refresh_token": None,
            },
        )
        action = "Created" if created else "Updated"
        self.stdout.write(self.style.SUCCESS(f"{action} ADMIN user {user.username}."))
