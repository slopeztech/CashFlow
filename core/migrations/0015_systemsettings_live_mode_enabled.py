from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0014_systemtestrun'),
    ]

    operations = [
        migrations.AddField(
            model_name='systemsettings',
            name='live_mode_enabled',
            field=models.BooleanField(default=True),
        ),
    ]
