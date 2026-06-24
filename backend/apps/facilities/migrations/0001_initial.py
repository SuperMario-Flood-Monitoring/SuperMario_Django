# Django 6.0.6이 2026-06-15 16:01에 생성

from django.db import migrations, models


class Migration(migrations.Migration):

    initial = True

    dependencies = [
    ]

    operations = [
        migrations.CreateModel(
            name='Facility',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('name', models.CharField(max_length=100, unique=True)),
                ('facility_type', models.CharField(choices=[('DRAINAGE_PIPE', 'Drainage pipe'), ('CATCH_BASIN', 'Catch basin'), ('MANHOLE', 'Manhole'), ('PUMP', 'Pump'), ('OTHER', 'Other')], default='OTHER', max_length=30)),
                ('location', models.CharField(blank=True, max_length=255)),
                ('normal_value', models.FloatField(default=0.0)),
                ('unit', models.CharField(blank=True, max_length=20)),
                ('metadata', models.JSONField(blank=True, default=dict)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
            ],
            options={
                'ordering': ['id'],
            },
        ),
    ]
