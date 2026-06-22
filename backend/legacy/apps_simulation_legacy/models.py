from django.db import models


class SimulationRun(models.Model):
    class Status(models.TextChoices):
        COMPLETED = "COMPLETED", "Completed"
        FAILED = "FAILED", "Failed"

    rainfall_status = models.CharField(max_length=50)
    rainfall_amount = models.FloatField(default=0.0)
    duration_minutes = models.PositiveIntegerField(default=0)
    parameters = models.JSONField(default=dict, blank=True)
    result = models.JSONField(default=dict, blank=True)
    status = models.CharField(
        max_length=20,
        choices=Status.choices,
        default=Status.COMPLETED,
    )
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        app_label = "legacy_simulation"
        db_table = "simulation_simulationrun"
        ordering = ["-created_at"]
