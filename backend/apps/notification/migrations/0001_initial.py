from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="BotToken",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("bot_token", models.CharField(max_length=255)),
            ],
            options={
                "db_table": "bot_token",
                "ordering": ["id"],
            },
        ),
        migrations.CreateModel(
            name="NotificationRecipient",
            fields=[
                ("id", models.BigAutoField(primary_key=True, serialize=False)),
                ("employee_name", models.CharField(max_length=100)),
                ("chat_id", models.CharField(max_length=100)),
            ],
            options={
                "db_table": "notification_recipients",
                "ordering": ["id"],
            },
        ),
    ]
