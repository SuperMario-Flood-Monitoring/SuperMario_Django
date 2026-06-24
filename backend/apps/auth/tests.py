import json
from io import StringIO

from django.contrib.auth.hashers import check_password, make_password
from django.core.management import call_command
from django.test import TestCase

from .cookies import REFRESH_COOKIE_NAME
from .models import User
from .tokens import issue_token


class AuthApiTests(TestCase):
    def setUp(self):
        self.user = User.objects.create(
            username="admin",
            role=User.Role.ADMIN,
            password=make_password("correct-password"),
        )

    def test_login_issues_access_token_and_refresh_cookie(self):
        response = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": "admin", "password": "correct-password"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 200)
        self.assertIn("accessToken", response.json())
        self.assertIn(REFRESH_COOKIE_NAME, response.cookies)
        self.user.refresh_from_db()
        self.assertTrue(self.user.refresh_token)

    def test_login_rejects_wrong_password(self):
        response = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": "admin", "password": "wrong"}),
            content_type="application/json",
        )

        self.assertEqual(response.status_code, 401)
        self.assertEqual(response.json()["status"], "UNAUTHORIZED")

    def test_refresh_rotates_tokens(self):
        login_response = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": "admin", "password": "correct-password"}),
            content_type="application/json",
        )
        old_refresh = login_response.cookies[REFRESH_COOKIE_NAME].value
        self.user.refresh_from_db()
        old_hash = self.user.refresh_token

        response = self.client.post("/api/auth/refresh")

        self.assertEqual(response.status_code, 200)
        self.assertIn("accessToken", response.json())
        self.assertIn(REFRESH_COOKIE_NAME, response.cookies)
        self.assertNotEqual(old_refresh, response.cookies[REFRESH_COOKIE_NAME].value)
        self.user.refresh_from_db()
        self.assertNotEqual(old_hash, self.user.refresh_token)

    def test_refresh_rejects_reused_refresh_token(self):
        login_response = self.client.post(
            "/api/auth/login",
            data=json.dumps({"username": "admin", "password": "correct-password"}),
            content_type="application/json",
        )
        old_refresh = login_response.cookies[REFRESH_COOKIE_NAME].value
        self.client.post("/api/auth/refresh")
        self.client.cookies[REFRESH_COOKIE_NAME] = old_refresh

        response = self.client.post("/api/auth/refresh")

        self.assertEqual(response.status_code, 403)
        self.user.refresh_from_db()
        self.assertIsNone(self.user.refresh_token)

    def test_protected_api_requires_access_token(self):
        response = self.client.get("/api/scenarios")

        self.assertEqual(response.status_code, 401)

    def test_protected_api_accepts_admin_access_token(self):
        token = issue_token(self.user, "access")

        response = self.client.get("/api/scenarios", HTTP_AUTHORIZATION=f"Bearer {token}")

        self.assertEqual(response.status_code, 200)


class EnsureAdminUserCommandTests(TestCase):
    def test_creates_default_admin_when_missing(self):
        stdout = StringIO()

        call_command("ensure_admin_user", "--only-if-no-admin", stdout=stdout)

        user = User.objects.get(username="admin")
        self.assertEqual(user.role, User.Role.ADMIN)
        self.assertTrue(check_password("tnvjakfldh4", user.password))
        self.assertIn("ADMIN 사용자 admin 생성 완료.", stdout.getvalue())

    def test_recreates_existing_default_admin(self):
        User.objects.create(
            username="admin",
            role=User.Role.ADMIN,
            password=make_password("old-password"),
        )
        stdout = StringIO()

        call_command("ensure_admin_user", "--only-if-no-admin", stdout=stdout)

        user = User.objects.get(username="admin")
        self.assertTrue(check_password("tnvjakfldh4", user.password))
        self.assertIn("ADMIN 사용자 admin 생성 완료.", stdout.getvalue())

    def test_recreates_default_admin_even_when_other_admin_exists(self):
        User.objects.create(
            username="admin",
            role=User.Role.ADMIN,
            password=make_password("old-password"),
        )
        User.objects.create(
            username="root",
            role=User.Role.ADMIN,
            password=make_password("root-password"),
        )
        stdout = StringIO()

        call_command("ensure_admin_user", "--only-if-no-admin", stdout=stdout)

        user = User.objects.get(username="admin")
        self.assertTrue(check_password("tnvjakfldh4", user.password))
        self.assertTrue(User.objects.filter(username="root").exists())
        self.assertIn("ADMIN 사용자 admin 생성 완료.", stdout.getvalue())

    def test_skips_default_admin_when_admin_exists(self):
        existing = User.objects.create(
            username="root",
            role=User.Role.ADMIN,
            password=make_password("keep-this-password"),
        )
        stdout = StringIO()

        call_command("ensure_admin_user", "--only-if-no-admin", stdout=stdout)

        existing.refresh_from_db()
        self.assertTrue(check_password("keep-this-password", existing.password))
        self.assertFalse(User.objects.filter(username="admin").exists())
        self.assertIn("ADMIN 사용자가 이미 있어 건너뜁니다.", stdout.getvalue())
