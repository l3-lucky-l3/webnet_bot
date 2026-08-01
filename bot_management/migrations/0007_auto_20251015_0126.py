# Generated manually to remove 1 activation option

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0006_auto_20251015_0121'),
    ]

    operations = [
        migrations.AlterField(
            model_name='subscriptionkey',
            name='total_activations',
            field=models.IntegerField(choices=[(3, '3 активации'), (4, '4 активации')]),
        ),
    ]