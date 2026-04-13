from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0015_systemsettings_live_mode_enabled'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='allow_companions',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='allow_negative_balance',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='max_companions',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
