from django.apps import AppConfig


class AuthConfig(AppConfig):
    default_auto_field = "django.db.models.BigAutoField"
    label = "custom_auth"
    name = "apps.auth"
    verbose_name = "SuperMario Auth"

