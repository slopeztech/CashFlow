from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0007_storeuserprofile_monthly_fee_enabled_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='storeuserprofile',
            name='show_all_recent_movements',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='storeuserprofile',
            name='recent_movements_limit',
            field=models.PositiveIntegerField(blank=True, null=True),
        ),
    ]
