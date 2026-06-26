from django.db import models


class HazardEvent(models.Model):
    class Status(models.TextChoices):
        OPEN = "OPEN", "Open"
        IN_PROGRESS = "IN_PROGRESS", "In progress"
        RESOLVED = "RESOLVED", "Resolved"

    event_key = models.CharField(max_length=255, unique=True)
    run_id = models.CharField(max_length=100, blank=True)
    step_index = models.PositiveIntegerField(default=0)
    model_time = models.CharField(max_length=64, blank=True)
    source = models.CharField(max_length=20, blank=True)
    target_id = models.CharField(max_length=150)
    hazard_level = models.CharField(max_length=20)
    hazard_type = models.CharField(max_length=60)
    hazard_detail = models.TextField()
    status = models.CharField(max_length=20, choices=Status.choices, default=Status.OPEN)
    metrics_snapshot = models.JSONField(default=dict, blank=True)
    is_deleted = models.BooleanField(default=False)
    resolved_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        ordering = ["-created_at", "-id"]
        indexes = [
            models.Index(fields=["status", "is_deleted"]),
            models.Index(fields=["run_id", "hazard_type", "target_id", "hazard_level"]),
        ]

    def __str__(self) -> str:
        return f"{self.hazard_type}:{self.target_id}:{self.hazard_level}"


class HazardAction(models.Model):
    class FastApiSyncStatus(models.TextChoices):
        PENDING = "PENDING", "Pending"
        SENT = "SENT", "Sent"
        FAILED = "FAILED", "Failed"

    event = models.ForeignKey(HazardEvent, related_name="actions", on_delete=models.CASCADE)
    action_detail = models.TextField()
    action_type = models.CharField(max_length=60, blank=True)
    result_detail = models.TextField(blank=True)
    result_status = models.CharField(max_length=60, blank=True)
    recurrence_note = models.TextField(blank=True)
    fastapi_sync_status = models.CharField(
        max_length=20,
        choices=FastApiSyncStatus.choices,
        default=FastApiSyncStatus.PENDING,
    )
    fastapi_vector_id = models.CharField(max_length=120, blank=True)
    fastapi_error_message = models.TextField(blank=True)
    fastapi_requested_at = models.DateTimeField(blank=True, null=True)
    fastapi_completed_at = models.DateTimeField(blank=True, null=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return f"action:{self.event_id}"


class HazardCaseEmbedding(models.Model):
    event = models.ForeignKey(HazardEvent, related_name="case_embeddings", on_delete=models.CASCADE)
    embedding_text = models.TextField()
    vector_id = models.CharField(max_length=120)
    metadata = models.JSONField(default=dict, blank=True)
    created_at = models.DateTimeField(auto_now_add=True)

    class Meta:
        ordering = ["created_at", "id"]

    def __str__(self) -> str:
        return self.vector_id
