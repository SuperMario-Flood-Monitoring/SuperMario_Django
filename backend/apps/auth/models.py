from django.db import models


class User(models.Model):
    class Role(models.TextChoices):
        ADMIN = "ADMIN", "Admin"
        MEMBER = "MEMBER", "Member"

    user_id = models.BigAutoField(db_column="USER_ID", primary_key=True)
    role = models.CharField(db_column="ROLE", max_length=20, choices=Role.choices)
    username = models.CharField(db_column="USERNAME", max_length=150, unique=True)
    password = models.CharField(db_column="PASSWORD", max_length=128)
    refresh_token = models.CharField(
        db_column="REFRESH_TOKEN",
        max_length=128,
        blank=True,
        null=True,
    )

    class Meta:
        db_table = "users"
        constraints = [
            models.CheckConstraint(
                condition=models.Q(role__in=["ADMIN", "MEMBER"]),
                name="users_role_valid",
            )
        ]

    def __str__(self):
        return self.username


class Notification(models.Model):
    token_id = models.BigAutoField(db_column="TOKEN_ID", primary_key=True)
    name = models.CharField(db_column="NAME", max_length=150, unique=True)
    telegram_token = models.CharField(db_column="TELEGRAM_TOKEN", max_length=255)

    class Meta:
        db_table = "notifications"

    def __str__(self):
        return self.name

