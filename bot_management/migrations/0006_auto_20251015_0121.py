# Generated manually to create referral models

from django.conf import settings
from django.db import migrations, models
import django.db.models.deletion
import django.utils.timezone


class Migration(migrations.Migration):

    dependencies = [
        ('bot_management', '0005_remove_promo_models'),
    ]

    operations = [
        migrations.CreateModel(
            name='ReferralCode',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('code', models.CharField(max_length=20, unique=True, verbose_name='Реферальный код')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Дата создания')),
                ('user', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='referral_code', to='bot_management.telegramuser', verbose_name='Пользователь')),
            ],
            options={
                'verbose_name': 'Реферальный код',
                'verbose_name_plural': 'Реферальные коды',
                'ordering': ['-created_at'],
            },
        ),
        migrations.CreateModel(
            name='Referral',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('is_active', models.BooleanField(default=True, verbose_name='Активен')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Дата создания')),
                ('referred', models.OneToOneField(on_delete=django.db.models.deletion.CASCADE, related_name='referred_by', to='bot_management.telegramuser', verbose_name='Приглашенный')),
                ('referrer', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='referrals_made', to='bot_management.telegramuser', verbose_name='Реферер')),
            ],
            options={
                'verbose_name': 'Реферал',
                'verbose_name_plural': 'Рефералы',
                'ordering': ['-created_at'],
                'unique_together': {('referrer', 'referred')},
            },
        ),
        migrations.CreateModel(
            name='ReferralReward',
            fields=[
                ('id', models.BigAutoField(auto_created=True, primary_key=True, serialize=False, verbose_name='ID')),
                ('reward_type', models.CharField(choices=[('percent', 'Процент от покупки'), ('fixed', 'Фиксированная сумма'), ('key', 'Ключ подписки')], max_length=10, verbose_name='Тип награды')),
                ('reward_value', models.DecimalField(decimal_places=2, max_digits=10, verbose_name='Значение награды')),
                ('status', models.CharField(choices=[('pending', 'В ожидании'), ('paid', 'Выплачено'), ('canceled', 'Отменено')], default='pending', max_length=10, verbose_name='Статус')),
                ('created_at', models.DateTimeField(default=django.utils.timezone.now, verbose_name='Дата создания')),
                ('paid_at', models.DateTimeField(blank=True, null=True, verbose_name='Дата выплаты')),
                ('payment', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referral_rewards', to='bot_management.payment', verbose_name='Платеж')),
                ('referral', models.ForeignKey(on_delete=django.db.models.deletion.CASCADE, related_name='rewards', to='bot_management.referral', verbose_name='Реферал')),
                ('subscription_key', models.ForeignKey(blank=True, null=True, on_delete=django.db.models.deletion.SET_NULL, related_name='referral_rewards', to='bot_management.subscriptionkey', verbose_name='Ключ подписки')),
            ],
            options={
                'verbose_name': 'Реферальная награда',
                'verbose_name_plural': 'Реферальные награды',
                'ordering': ['-created_at'],
            },
        ),
    ]