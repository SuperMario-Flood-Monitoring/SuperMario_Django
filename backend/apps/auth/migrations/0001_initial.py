from django.db import migrations, models


class Migration(migrations.Migration):
    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="Notification",
            fields=[
                ("token_id", models.BigAutoField(db_column="TOKEN_ID", primary_key=True, serialize=False)),
                ("name", models.CharField(db_column="NAME", max_length=150, unique=True)),
                ("telegram_token", models.CharField(db_column="TELEGRAM_TOKEN", max_length=255)),
            ],
            options={
                "db_table": "notifications",
            },
        ),
        migrations.CreateModel(
            name="User",
            fields=[
                ("user_id", models.BigAutoField(db_column="USER_ID", primary_key=True, serialize=False)),
                ("role", models.CharField(choices=[("ADMIN", "Admin"), ("MEMBER", "Member")], db_column="ROLE", max_length=20)),
                ("username", models.CharField(db_column="USERNAME", max_length=150, unique=True)),
                ("password", models.CharField(db_column="PASSWORD", max_length=128)),
                ("refresh_token", models.CharField(blank=True, db_column="REFRESH_TOKEN", max_length=128, null=True)),
            ],
            options={
                "db_table": "users",
            },
        ),
        migrations.AddConstraint(
            model_name="user",
            constraint=models.CheckConstraint(condition=models.Q(("role__in", ["ADMIN", "MEMBER"])), name="users_role_valid"),
        ),
    ]

