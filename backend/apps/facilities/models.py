from django.db import models


class Facility(models.Model):
    class Type(models.TextChoices):
        DRAINAGE_PIPE = "DRAINAGE_PIPE", "Drainage pipe"
        CATCH_BASIN = "CATCH_BASIN", "Catch basin"
        MANHOLE = "MANHOLE", "Manhole"
        PUMP = "PUMP", "Pump"
        OTHER = "OTHER", "Other"

    name = models.CharField(max_length=100, unique=True)
    facility_type = models.CharField(
        max_length=30,
        choices=Type.choices,
        default=Type.OTHER,
    )
    location = models.CharField(max_length=255, blank=True)
    normal_value = models.FloatField(default=0.0)
    unit = models.CharField(max_length=20, blank=True)
    metadata = models.JSONField(default=dict, blank=True)
    is_active = models.BooleanField(default=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["id"]

    def __str__(self):
        return self.name
