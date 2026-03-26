from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0002_event_eventimage_eventregistration'),
    ]

    operations = [
        migrations.AddField(
            model_name='event',
            name='is_paid_event',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='event',
            name='registration_fee',
            field=models.DecimalField(decimal_places=2, default=0, max_digits=10),
        ),
    ]
