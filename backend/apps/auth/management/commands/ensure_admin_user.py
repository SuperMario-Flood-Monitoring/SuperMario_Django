from django.contrib.auth.hashers import make_password
from django.core.management.base import BaseCommand, CommandError

from apps.auth.models import User


class Command(BaseCommand):
    help = "Create or update the initial ADMIN account for the custom auth table."

    def add_arguments(self, parser):
        parser.add_argument("--username", required=True)
        parser.add_argument("--password", required=True)

    def handle(self, *args, **options):
        username = str(options["username"]).strip()
        password = str(options["password"])
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
