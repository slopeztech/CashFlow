from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0011_category_display_and_ratings_fields'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='display_order',
            field=models.PositiveIntegerField(default=0),
        ),
    ]
