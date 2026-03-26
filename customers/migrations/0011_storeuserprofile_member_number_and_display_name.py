from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0010_storeuserprofile_temporary_access_code_plain'),
    ]

    operations = [
        migrations.AddField(
            model_name='storeuserprofile',
            name='display_name',
            field=models.CharField(blank=True, max_length=80),
        ),
        migrations.AddField(
            model_name='storeuserprofile',
            name='member_number',
            field=models.CharField(blank=True, max_length=50, null=True, unique=True),
        ),
    ]
