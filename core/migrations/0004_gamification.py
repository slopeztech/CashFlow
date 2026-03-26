from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0003_event_paid_fields'),
        migrations.swappable_dependency(settings.AUTH_USER_MODEL),
    ]

    operations = [
        migrations.CreateModel(
            name='Gamification',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('title', models.CharField(max_length=160)),
                ('description', models.TextField()),
                ('reward', models.CharField(max_length=255)),
                (
                    'gamification_type',
                    models.CharField(
                        choices=[
                            ('approved_reviews', 'Approved reviews count'),
                            ('distinct_products_tried', 'Distinct products tried'),
                            ('approved_orders', 'Approved orders count'),
                        ],
                        max_length=40,
                    ),
                ),
                ('target_value', models.PositiveIntegerField()),
                ('start_at', models.DateTimeField()),
                ('end_at', models.DateTimeField()),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'created_by',
                    models.ForeignKey(
                        blank=True,
                        null=True,
                        on_delete=django.db.models.deletion.SET_NULL,
                        related_name='created_gamifications',
                        to=settings.AUTH_USER_MODEL,
                    ),
                ),
            ],
            options={
                'ordering': ['-start_at', '-created_at'],
            },
        ),
    ]
