from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0006_productsheeturl'),
    ]

    operations = [
        migrations.AddField(
            model_name='product',
            name='purchase_options',
            field=models.CharField(choices=[('both', 'Both'), ('units_only', 'Units only'), ('amount_only', 'Amount only')], default='both', max_length=20),
        ),
    ]
