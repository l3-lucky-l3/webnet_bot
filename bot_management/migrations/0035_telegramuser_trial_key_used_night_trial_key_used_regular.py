"""
Добавление раздельных флагов для пробных ключей Night VPN и Regular VPN
"""

from django.db import migrations, models


class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0034_payment_vpn_type_subscriptionkey_remnawave_key_id_and_more'),
    ]

    operations = [
        migrations.RemoveField(
            model_name='telegramuser',
            name='trial_key_used',
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='trial_key_used_night',
            field=models.BooleanField(default=False, verbose_name="Использован пробный ключ Night VPN", db_index=True),
        ),
        migrations.AddField(
            model_name='telegramuser',
            name='trial_key_used_regular',
            field=models.BooleanField(default=False, verbose_name="Использован пробный ключ Обычный VPN", db_index=True),
        ),
    ]
