from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0010_product_flags_and_category_untried'),
    ]

    operations = [
        migrations.AddField(
            model_name='category',
            name='allow_user_ratings',
            field=models.BooleanField(default=True),
        ),
        migrations.AddField(
            model_name='category',
            name='default_expanded',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='category',
            name='display_order',
            field=models.PositiveIntegerField(default=0),
        ),
        migrations.AddField(
            model_name='category',
            name='image',
            field=models.ImageField(blank=True, null=True, upload_to='categories/'),
        ),
    ]
