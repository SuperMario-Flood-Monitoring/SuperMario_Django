from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    initial = True

    dependencies = []

    operations = [
        migrations.CreateModel(
            name="HazardEvent",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("event_key", models.CharField(max_length=255, unique=True)),
                ("run_id", models.CharField(blank=True, max_length=100)),
                ("step_index", models.PositiveIntegerField(default=0)),
                ("model_time", models.CharField(blank=True, max_length=64)),
                ("source", models.CharField(blank=True, max_length=20)),
                ("target_id", models.CharField(max_length=150)),
                ("hazard_level", models.CharField(max_length=20)),
                ("hazard_type", models.CharField(max_length=60)),
                ("hazard_detail", models.TextField()),
                ("status", models.CharField(choices=[("OPEN", "Open"), ("IN_PROGRESS", "In progress"), ("RESOLVED", "Resolved")], default="OPEN", max_length=20)),
                ("metrics_snapshot", models.JSONField(blank=True, default=dict)),
                ("is_deleted", models.BooleanField(default=False)),
                ("resolved_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("updated_at", models.DateTimeField(auto_now=True)),
            ],
            options={
                "ordering": ["-created_at", "-id"],
            },
        ),
        migrations.CreateModel(
            name="HazardAction",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("action_detail", models.TextField()),
                ("action_type", models.CharField(blank=True, max_length=60)),
                ("result_status", models.CharField(blank=True, max_length=60)),
                ("fastapi_sync_status", models.CharField(choices=[("PENDING", "Pending"), ("SENT", "Sent"), ("FAILED", "Failed")], default="PENDING", max_length=20)),
                ("fastapi_vector_id", models.CharField(blank=True, max_length=120)),
                ("fastapi_error_message", models.TextField(blank=True)),
                ("fastapi_requested_at", models.DateTimeField(blank=True, null=True)),
                ("fastapi_completed_at", models.DateTimeField(blank=True, null=True)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="actions", to="monitoring.hazardevent")),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.CreateModel(
            name="HazardCaseEmbedding",
            fields=[
                ("id", models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name="ID")),
                ("embedding_text", models.TextField()),
                ("vector_id", models.CharField(max_length=120)),
                ("metadata", models.JSONField(blank=True, default=dict)),
                ("created_at", models.DateTimeField(auto_now_add=True)),
                ("event", models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name="case_embeddings", to="monitoring.hazardevent")),
            ],
            options={
                "ordering": ["created_at", "id"],
            },
        ),
        migrations.AddIndex(
            model_name="hazardevent",
            index=models.Index(fields=["status", "is_deleted"], name="monitoring__status_2159e2_idx"),
        ),
        migrations.AddIndex(
            model_name="hazardevent",
            index=models.Index(fields=["run_id", "hazard_type", "target_id", "hazard_level"], name="monitoring__run_id_303461_idx"),
        ),
    ]
