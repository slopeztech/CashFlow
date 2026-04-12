from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('sales', '0007_alter_order_status'),
    ]

    operations = [
        migrations.AddField(
            model_name='orderitem',
            name='is_gift',
            field=models.BooleanField(default=False),
        ),
        migrations.AddField(
            model_name='saleitem',
            name='is_gift',
            field=models.BooleanField(default=False),
        ),
    ]
