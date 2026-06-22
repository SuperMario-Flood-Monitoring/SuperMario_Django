from django.db import models


class Scenario(models.Model):
    title = models.CharField(max_length=100)
    description = models.TextField(blank=True)
    layout_json = models.JSONField()
    version = models.PositiveIntegerField(default=1)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-updated_at", "-id"]

    def __str__(self):
        return self.title
