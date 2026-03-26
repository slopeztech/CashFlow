from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('customers', '0005_storeuserprofile_language'),
    ]

    operations = [
        migrations.AddField(
            model_name='storeuserprofile',
            name='profile_image',
            field=models.FileField(blank=True, null=True, upload_to='profile_images/'),
        ),
    ]
