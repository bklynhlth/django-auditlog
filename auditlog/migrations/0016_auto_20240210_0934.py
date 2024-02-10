# Generated by Django 3.2.19 on 2024-02-10 09:34

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ("auditlog", "0015_alter_logentry_changes"),
    ]

    operations = [
        migrations.RemoveField(
            model_name="logentry",
            name="actor",
        ),
        migrations.RemoveField(
            model_name="logentry",
            name="additional_data",
        ),
        migrations.RemoveField(
            model_name="logentry",
            name="cid",
        ),
        migrations.RemoveField(
            model_name="logentry",
            name="object_id",
        ),
        migrations.RemoveField(
            model_name="logentry",
            name="remote_addr",
        ),
        migrations.RemoveField(
            model_name="logentry",
            name="serialized_data",
        ),
        migrations.AddField(
            model_name="logentry",
            name="database_name",
            field=models.CharField(
                default="RDS", max_length=255, verbose_name="database name"
            ),
        ),
        migrations.AddField(
            model_name="logentry",
            name="developer_name",
            field=models.CharField(
                default="willisaplication",
                max_length=255,
                verbose_name="developer name",
            ),
        ),
    ]