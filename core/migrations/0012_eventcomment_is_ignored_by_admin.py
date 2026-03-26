from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0011_survey_surveyoption_surveyresponse_and_more'),
    ]

    operations = [
        migrations.AddField(
            model_name='eventcomment',
            name='is_ignored_by_admin',
            field=models.BooleanField(default=False),
        ),
    ]
