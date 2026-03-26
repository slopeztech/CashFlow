import django.db.models.deletion
from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('core', '0008_systemsettings'),
    ]

    operations = [
        migrations.CreateModel(
            name='EventRegistrationField',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('label', models.CharField(max_length=180)),
                ('help_text', models.TextField(blank=True)),
                (
                    'field_type',
                    models.CharField(
                        choices=[
                            ('notice', 'Notice text'),
                            ('short_text', 'Short text'),
                            ('long_text', 'Long text'),
                            ('radio', 'Radio options'),
                            ('select', 'Select options'),
                            ('checkbox', 'Checkbox'),
                        ],
                        default='short_text',
                        max_length=20,
                    ),
                ),
                ('options_text', models.TextField(blank=True)),
                ('is_required', models.BooleanField(default=False)),
                ('sort_order', models.PositiveIntegerField(default=0)),
                ('is_active', models.BooleanField(default=True)),
                ('created_at', models.DateTimeField(auto_now_add=True)),
                ('updated_at', models.DateTimeField(auto_now=True)),
                (
                    'event',
                    models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='registration_fields', to='core.event'),
                ),
            ],
            options={
                'ordering': ['sort_order', 'id'],
            },
        ),
        migrations.AddField(
            model_name='eventregistration',
            name='answers',
            field=models.JSONField(blank=True, default=dict),
        ),
    ]
