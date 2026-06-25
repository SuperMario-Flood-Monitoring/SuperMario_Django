import json

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from apps.auth.models import User
from apps.auth.tokens import issue_token
from swmm_engine.llm_dispatcher import build_notification_payload

from .models import BotToken, NotificationRecipient


class NotificationRecipientApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="admin",
            role=User.Role.ADMIN,
            password=make_password("password"),
        )
        self.auth_header = f"Bearer {issue_token(self.user, 'access')}"

    def test_creates_notification_recipient(self):
        response = self.client.post(
            "/api/notification/",
            data=json.dumps({"employee_name": "홍길동", "chat_id": "12345"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 201)
        self.assertEqual(NotificationRecipient.objects.count(), 1)
        self.assertEqual(response.json()["data"]["employee_name"], "홍길동")
        self.assertEqual(response.json()["data"]["chat_id"], "12345")

    def test_lists_notification_recipients(self):
        NotificationRecipient.objects.create(employee_name="홍길동", chat_id="12345")
        NotificationRecipient.objects.create(employee_name="김관리", chat_id="67890")

        response = self.client.get("/api/notification/list", HTTP_AUTHORIZATION=self.auth_header)

        self.assertEqual(response.status_code, 200)
        self.assertEqual(
            [item["chat_id"] for item in response.json()["data"]],
            ["12345", "67890"],
        )

    def test_deletes_notification_recipient(self):
        recipient = NotificationRecipient.objects.create(employee_name="홍길동", chat_id="12345")

        response = self.client.delete(
            f"/api/notification/{recipient.id}",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 200)
        self.assertFalse(NotificationRecipient.objects.filter(id=recipient.id).exists())

    def test_rejects_notification_api_without_admin_token(self):
        response = self.client.get("/api/notification/list")

        self.assertEqual(response.status_code, 401)


class NotificationPayloadTests(TestCase):
    def test_builds_notification_payload_for_langchain(self):
        BotToken.objects.create(bot_token="plain-bot-token")
        NotificationRecipient.objects.create(employee_name="홍길동", chat_id="12345")
        NotificationRecipient.objects.create(employee_name="김관리", chat_id="67890")

        payload = build_notification_payload()

        self.assertEqual(payload["TELEGRAM_BOT_TOKEN"], "plain-bot-token")
        self.assertEqual(payload["TELEGRAM_CHAT_ID"], ["12345", "67890"])

    def test_builds_empty_notification_payload_without_rows(self):
        payload = build_notification_payload()

        self.assertIsNone(payload["TELEGRAM_BOT_TOKEN"])
        self.assertEqual(payload["TELEGRAM_CHAT_ID"], [])
