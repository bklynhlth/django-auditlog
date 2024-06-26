# Generated by Django 3.2.19 on 2024-02-26 06:03

from django.db import migrations, models
from django.conf import settings


class Migration(migrations.Migration):

    dependencies = [
        ("auditlog", "0016_auto_20240210_1014"),
    ]

    operations = [
        migrations.AlterField(
            model_name="logentry",
            name="database_name",
            field=models.CharField(
                default=settings.DATABASE_NAME,
                max_length=255,
                verbose_name="database name",
            ),
        ),
    ]
