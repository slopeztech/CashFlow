from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0004_supplier_product_min_stock_productimage_and_more'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductSheetField',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('field_key', models.CharField(max_length=120)),
                ('field_value', models.CharField(max_length=255)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                ('product', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='sheet_fields', to='inventory.product')),
            ],
            options={
                'ordering': ['id'],
            },
        ),
        migrations.AddConstraint(
            model_name='productsheetfield',
            constraint=models.UniqueConstraint(fields=('product', 'field_key'), name='unique_product_sheet_key'),
        ),
    ]
