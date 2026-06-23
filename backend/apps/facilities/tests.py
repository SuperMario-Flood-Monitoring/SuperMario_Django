import json

from django.contrib.auth.hashers import make_password
from django.test import TestCase
from django.urls import reverse

from apps.auth.models import User
from apps.auth.tokens import issue_token

from .models import Facility


class FacilitiesViewTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="admin",
            role=User.Role.ADMIN,
            password=make_password("password"),
        )
        self.auth_header = f"Bearer {issue_token(self.user, 'access')}"

    def test_initializes_multiple_facilities(self):
        response = self.client.post(
            reverse("facilities:list-create"),
            data=json.dumps(
                {
                    "facilities": [
                        {
                            "name": "catch-basin-1",
                            "facility_type": "CATCH_BASIN",
                            "normal_value": 10,
                            "unit": "cm",
                        },
                        {
                            "name": "pipe-1",
                            "facility_type": "DRAINAGE_PIPE",
                            "normal_value": 20,
                            "unit": "m3/s",
                        },
                    ]
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Facility.objects.count(), 2)
        self.assertEqual(len(response.json()["data"]), 2)

    def test_initialization_updates_facility_with_same_name(self):
        Facility.objects.create(name="pipe-1", normal_value=10)

        response = self.client.post(
            reverse("facilities:list-create"),
            data=json.dumps(
                {
                    "name": "pipe-1",
                    "facility_type": "DRAINAGE_PIPE",
                    "normal_value": 30,
                }
            ),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 200)
        self.assertEqual(Facility.objects.count(), 1)
        self.assertEqual(Facility.objects.get().normal_value, 30)

    def test_rejects_unknown_facility_type(self):
        response = self.client.post(
            reverse("facilities:list-create"),
            data=json.dumps({"name": "unknown", "facility_type": "INVALID"}),
            content_type="application/json",
            HTTP_AUTHORIZATION=self.auth_header,
        )

        self.assertEqual(response.status_code, 400)
