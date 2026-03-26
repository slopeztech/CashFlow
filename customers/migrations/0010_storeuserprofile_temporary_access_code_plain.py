from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0009_storeuserprofile_password_change_required_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='storeuserprofile',
            name='temporary_access_code_plain',
            field=models.CharField(blank=True, max_length=8),
        ),
    ]
