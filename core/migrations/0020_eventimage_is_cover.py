from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0019_event_is_visible_alter_asset_pricing_mode'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventimage',
            name='is_cover',
            field=models.BooleanField(default=False),
        ),
    ]
