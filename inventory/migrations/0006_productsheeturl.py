from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('inventory', '0005_productsheetfield'),
    ]

    operations = [
        migrations.CreateModel(
            name='ProductSheetUrl',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('url', models.URLField(max_length=500)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('product', models.ForeignKey(on_delete=models.deletion.CASCADE, related_name='sheet_urls', to='inventory.product')),
            ],
            options={
                'ordering': ['id'],
            },
        ),
    ]
