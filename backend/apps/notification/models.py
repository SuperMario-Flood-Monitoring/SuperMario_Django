from django.db import models


class BotToken(models.Model):
    id = models.BigAutoField(primary_key=True)
    bot_token = models.CharField(max_length=255)

    class Meta:
        db_table = "bot_token"
        ordering = ["id"]

    def __str__(self) -> str:
        return f"bot_token:{self.id}"


class NotificationRecipient(models.Model):
    id = models.BigAutoField(primary_key=True)
    employee_name = models.CharField(max_length=100)
    chat_id = models.CharField(max_length=100)

    class Meta:
        db_table = "notification_recipients"
        ordering = ["id"]

    def __str__(self) -> str:
        return self.employee_name
