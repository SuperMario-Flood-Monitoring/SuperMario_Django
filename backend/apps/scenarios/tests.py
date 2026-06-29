import json

from django.contrib.auth.hashers import make_password
from django.test import TestCase

from apps.auth.models import User
from apps.auth.tokens import issue_token

from .models import Scenario


class ScenarioApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="admin",
            role=User.Role.ADMIN,
            password=make_password("password"),
        )
        self.auth_header = f"Bearer {issue_token(self.user, 'access')}"

    def test_creates_lists_updates_details_and_soft_deletes_scenario(self):
        create_response = self.client.post(
            "/api/scenarios",
            data=json.dumps(
                {
                    "title": " 기본 배수도 ",
                    "description": " 테스트 시나리오 ",
                    "layoutJson": {"version": 1, "nodes": [], "links": []},
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(create_response.status_code, 200)
        created = create_response.json()["scenario"]
        scenario_id = created["id"]
        self.assertEqual(created["title"], "기본 배수도")
        self.assertEqual(created["description"], "테스트 시나리오")
        self.assertEqual(created["version"], 1)
        self.assertTrue(created["isActive"])

        list_response = self.client.get("/api/scenarios", HTTP_AUTHORIZATION=self.auth_header)
        self.assertEqual(list_response.status_code, 200)
        self.assertEqual(len(list_response.json()["scenarios"]), 1)

        update_response = self.client.put(
            f"/api/scenarios/{scenario_id}",
            data=json.dumps(
                {
                    "title": "수정된 배수도",
                    "layoutJson": {"version": 2, "nodes": [{"id": "NODE_1"}], "links": []},
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(update_response.status_code, 200)
        updated = update_response.json()["scenario"]
        self.assertEqual(updated["title"], "수정된 배수도")
        self.assertEqual(updated["version"], 2)
        self.assertEqual(updated["layoutJson"]["nodes"][0]["id"], "NODE_1")

        detail_response = self.client.get(f"/api/scenarios/{scenario_id}", HTTP_AUTHORIZATION=self.auth_header)
        self.assertEqual(detail_response.status_code, 200)
        self.assertEqual(detail_response.json()["scenario"]["id"], scenario_id)

        delete_response = self.client.delete(f"/api/scenarios/{scenario_id}", HTTP_AUTHORIZATION=self.auth_header)
        self.assertEqual(delete_response.status_code, 200)
        self.assertFalse(delete_response.json()["scenario"]["isActive"])

        active_list_response = self.client.get("/api/scenarios", HTTP_AUTHORIZATION=self.auth_header)
        self.assertEqual(active_list_response.status_code, 200)
        self.assertEqual(active_list_response.json()["scenarios"], [])

        inactive_list_response = self.client.get(
            "/api/scenarios?includeInactive=true",
            HTTP_AUTHORIZATION=self.auth_header,
        )
        self.assertEqual(inactive_list_response.status_code, 200)
        self.assertEqual(len(inactive_list_response.json()["scenarios"]), 1)

    def test_rejects_scenario_api_without_admin_token(self):
        response = self.client.get("/api/scenarios")

        self.assertEqual(response.status_code, 401)

    def test_rejects_blank_title(self):
        response = self.client.post(
            "/api/scenarios",
            data=json.dumps(
                {
                    "title": "   ",
                    "layoutJson": {"version": 1, "nodes": [], "links": []},
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 422)
        self.assertEqual(Scenario.objects.count(), 0)
