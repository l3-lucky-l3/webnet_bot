from django.db import models
from django.contrib.auth.models import User
from django.utils import timezone
from config import BOT_TOKEN


class BotSettings(models.Model):
    """Настройки бота"""
    key = models.CharField(max_length=100, unique=True, verbose_name="Ключ настройки")
    value = models.TextField(verbose_name="Значение")
    description = models.TextField(blank=True, null=True, verbose_name="Описание")
    created_at = models.DateTimeField(auto_now_add=True)
    updated_at = models.DateTimeField(auto_now=True)

    class Meta:
        verbose_name = 'Настройка бота'
        verbose_name_plural = 'Настройки бота'

    def __str__(self):
        return f"{self.key}: {self.value}"

    @classmethod
    def get_setting(cls, key, default=None):
        """Получить значение настройки с кэшированием"""
        try:
            from .simple_cache import cache
            cache_key = f'setting_{key}'
            value = cache.get(cache_key)
            if value is not None:
                return value
            
            setting = cls.objects.get(key=key)
            value = setting.value
            # Кэшируем на 10 минут
            cache.set(cache_key, value, ttl=600)
            return value
        except cls.DoesNotExist:
            return default

    @classmethod
    def set_setting(cls, key, value, description=None):
        """Установить значение настройки с обновлением кэша"""
        from .simple_cache import cache
        
        setting, created = cls.objects.get_or_create(
            key=key,
            defaults={'value': value, 'description': description}
        )
        if not created:
            setting.value = value
            if description:
                setting.description = description
            setting.save()
        
        # Обновляем кэш
        cache_key = f'setting_{key}'
        cache.set(cache_key, value, ttl=600)
        
        return setting


class TelegramUser(models.Model):
    ENTRY_METHOD_CHOICES = [
        ('direct', 'Прямой вход (без реферальной ссылки)'),
        ('referral', 'По реферальной ссылке'),
    ]

    user_id = models.BigIntegerField(primary_key=True)
    username = models.CharField(max_length=255, null=True, blank=True, db_index=True)
    first_name = models.CharField(max_length=255, null=True, blank=True)
    last_name = models.CharField(max_length=255, null=True, blank=True)
    balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Баланс")
    referral_balance = models.DecimalField(max_digits=10, decimal_places=2, default=0.00, verbose_name="Реферальный баланс")
    first_entry_method = models.CharField(max_length=20, choices=ENTRY_METHOD_CHOICES, null=True, blank=True, verbose_name="Способ первого входа", db_index=True)
    multi_level_referral_enabled = models.BooleanField(default=False, verbose_name="Включена многоуровневая реферальная система")
    trial_key_used_night = models.BooleanField(default=False, verbose_name="Использован пробный ключ Night VPN", db_index=True)
    trial_key_used_regular = models.BooleanField(default=False, verbose_name="Использован пробный ключ ULTRA FAST VPN", db_index=True)
    trial_key_used_fast = models.BooleanField(default=False, verbose_name="Использован пробный ключ Обычный VPN", db_index=True)
    is_banned = models.BooleanField(default=False, verbose_name="Забанен", db_index=True)
    ban_reason = models.CharField(max_length=255, blank=True, default='', verbose_name="Причина бана")
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    class Meta:
        db_table = 'users'
        verbose_name = 'Пользователь Telegram'
        verbose_name_plural = 'Пользователи Telegram'
        indexes = [
            models.Index(fields=['username']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"@{self.username}" if self.username else f"ID{self.user_id}"


class BalanceTransaction(models.Model):
    TRANSACTION_TYPES = [
        ('deposit', 'Пополнение'),
        ('withdrawal', 'Списание'),
        ('purchase', 'Покупка'),
        ('refund', 'Возврат'),
        ('referral', 'Реферальная награда'),
    ]
    
    STATUS_CHOICES = [
        ('pending', 'В ожидании'),
        ('completed', 'Завершено'),
        ('cancelled', 'Отменено'),
    ]

    transaction_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='balance_transactions', db_index=True)
    transaction_type = models.CharField(max_length=20, choices=TRANSACTION_TYPES, verbose_name="Тип операции", db_index=True)
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="Статус", db_index=True)
    description = models.TextField(blank=True, null=True, verbose_name="Описание")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Дата создания", db_index=True)
    completed_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата завершения")
    payment = models.ForeignKey('Payment', on_delete=models.SET_NULL, null=True, blank=True, related_name='balance_transactions', db_index=True)

    class Meta:
        db_table = 'balance_transactions'
        verbose_name = 'Транзакция баланса'
        verbose_name_plural = 'Транзакции баланса'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['created_at']),
        ]

    def __str__(self):
        return f"{self.get_transaction_type_display()} - {self.amount} ₽ для {self.user}"


class ReferralWithdrawal(models.Model):
    """Модель для запросов на вывод реферальных средств"""
    STATUS_CHOICES = [
        ('pending', 'В ожидании'),
        ('approved', 'Одобрено'),
        ('completed', 'Выплачено'),
        ('rejected', 'Отклонено'),
    ]
    
    PAYMENT_METHOD_CHOICES = [
        ('bank_card', 'Банковская карта'),
        ('yoomoney', 'ЮMoney'),
        ('sberbank', 'Сбербанк'),
        ('tinkoff', 'Тинькофф'),
    ]
    
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='referral_withdrawals', verbose_name="Пользователь")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма к выводу")
    payment_method = models.CharField(max_length=20, choices=PAYMENT_METHOD_CHOICES, verbose_name="Способ выплаты")
    payment_details = models.TextField(verbose_name="Реквизиты для выплаты")
    status = models.CharField(max_length=20, choices=STATUS_CHOICES, default='pending', verbose_name="Статус")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Дата создания")
    processed_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата обработки")
    processed_by = models.ForeignKey('auth.User', on_delete=models.SET_NULL, null=True, blank=True, verbose_name="Обработал")
    admin_comment = models.TextField(blank=True, null=True, verbose_name="Комментарий администратора")
    
    class Meta:
        verbose_name = 'Запрос на вывод реферальных средств'
        verbose_name_plural = 'Запросы на вывод реферальных средств'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Вывод {self.amount} ₽ от {self.user.username or self.user.user_id} ({self.get_status_display()})"


class ReferralBalanceTransaction(models.Model):
    """Модель для транзакций реферального баланса"""
    TRANSACTION_TYPES = [
        ('referral_reward', 'Реферальная награда'),
        ('withdrawal_request', 'Запрос на вывод'),
        ('withdrawal_completed', 'Вывод завершен'),
        ('withdrawal_cancelled', 'Вывод отменен'),
    ]
    
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='referral_balance_transactions')
    transaction_type = models.CharField(max_length=30, choices=TRANSACTION_TYPES, verbose_name="Тип операции")
    amount = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Сумма")
    description = models.TextField(blank=True, null=True, verbose_name="Описание")
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Дата создания")
    withdrawal_request = models.ForeignKey(ReferralWithdrawal, on_delete=models.SET_NULL, null=True, blank=True, related_name='transactions')
    
    class Meta:
        verbose_name = 'Транзакция реферального баланса'
        verbose_name_plural = 'Транзакции реферального баланса'
        ordering = ['-created_at']
    
    def __str__(self):
        return f"Реф. транзакция: {self.user.username or self.user.user_id} - {self.amount} ({self.get_transaction_type_display()})"




class SubscriptionKey(models.Model):
    VPN_TYPE_CHOICES = [
        ('night', 'ОБХОД глушилок + VPN'),
        ('regular', '⚡ ULTRA FAST VPN'),
        ('fast', 'Обычный VPN'),
    ]
    
    SUBSCRIPTION_TYPES = [
        ('trial', 'Пробная (3 дня)'),
        ('month', 'Месячная'),
        ('3months', '3 месяца'),
        ('6months', '6 месяцев'),
        ('year', 'Годовая'),
        ('regular_day', '1 день (ULTRA FAST)'),
        ('regular_month', '1 месяц (ULTRA FAST)'),
        ('regular_3months', '3 месяца (ULTRA FAST)'),
        ('regular_6months', '6 месяцев (ULTRA FAST)'),
        ('regular_year', '1 год (ULTRA FAST)'),
        ('regular_2years', '2 года (ULTRA FAST)'),
        ('fast_day', '1 день (Обычный VPN)'),
        ('fast_month', '1 месяц (Обычный VPN)'),
        ('fast_3months', '3 месяца (Обычный VPN)'),
        ('fast_6months', '6 месяцев (Обычный VPN)'),
        ('fast_year', '1 год (Обычный VPN)'),
    ]

    ACTIVATION_CHOICES = [
        (1, '1 активация'),
        (2, '2 активации'),
        (3, '3 активации'),
        (4, '4 активации'),
        (5, '5 активаций'),
        (6, '6 активаций'),
        (7, '7 активаций'),
        (8, '8 активаций'),
        (9, '9 активаций'),
        (10, '10 активаций'),
    ]

    key_id = models.AutoField(primary_key=True)
    key_value = models.CharField(max_length=255, unique=True, db_index=True)
    vpn_type = models.CharField(max_length=10, choices=VPN_TYPE_CHOICES, default='night', db_index=True, verbose_name='Тип VPN')
    subscription_type = models.CharField(max_length=20, choices=SUBSCRIPTION_TYPES, db_index=True)
    total_activations = models.IntegerField(choices=ACTIVATION_CHOICES)
    used_activations = models.IntegerField(default=0)
    is_active = models.BooleanField(default=True, db_index=True)
    
    # Remnawave API поля для обычного VPN
    remnawave_user_id = models.IntegerField(null=True, blank=True, db_index=True, verbose_name='ID пользователя в Remnawave')
    remnawave_user_uuid = models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name='UUID пользователя в Remnawave')
    remnawave_key_id = models.IntegerField(null=True, blank=True, db_index=True, verbose_name='ID ключа в Remnawave')
    remnawave_subscription_id = models.IntegerField(null=True, blank=True, db_index=True, verbose_name='ID подписки в Remnawave')

    class Meta:
        db_table = 'keys'
        verbose_name = 'Ключ подписки'
        verbose_name_plural = 'Ключи подписок'
        indexes = [
            models.Index(fields=['subscription_type', 'is_active']),
            models.Index(fields=['is_active', 'used_activations']),
            models.Index(fields=['vpn_type', 'is_active']),
        ]

    def __str__(self):
        vpn_label = "Обычный VPN" if self.vpn_type == 'regular' else "Night VPN"
        return f"{vpn_label} - {self.get_subscription_type_display()} - {self.key_value}"

    @property
    def remaining_activations(self):
        return self.total_activations - self.used_activations

    @property
    def is_available(self):
        return self.is_active and self.remaining_activations > 0


class Payment(models.Model):
    STATUS_CHOICES = [
        ('pending', 'Ожидает оплаты'),
        ('succeeded', 'Оплачен'),
        ('canceled', 'Отменен'),
        ('failed', 'Ошибка оплаты'),
    ]

    VPN_TYPE_CHOICES = [
        ('night', 'ОБХОД глушилок + VPN'),
        ('regular', '⚡ ULTRA FAST VPN'),
        ('fast', 'Обычный VPN'),
    ]

    SUBSCRIPTION_TYPES = [
        ('trial', 'Пробная (3 дня)'),
        ('month', 'Месячная'),
        ('3months', '3 месяца'),
        ('6months', '6 месяцев'),
        ('year', 'Годовая'),
        ('regular_day', '1 день (ULTRA FAST)'),
        ('regular_month', '1 месяц (ULTRA FAST)'),
        ('regular_3months', '3 месяца (ULTRA FAST)'),
        ('regular_6months', '6 месяцев (ULTRA FAST)'),
        ('regular_year', '1 год (ULTRA FAST)'),
        ('regular_2years', '2 года (ULTRA FAST)'),
        ('fast_day', '1 день (Обычный VPN)'),
        ('fast_month', '1 месяц (Обычный VPN)'),
        ('fast_3months', '3 месяца (Обычный VPN)'),
        ('fast_6months', '6 месяцев (Обычный VPN)'),
        ('fast_year', '1 год (Обычный VPN)'),
    ]

    payment_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='payments', db_index=True)
    vpn_type = models.CharField(max_length=10, choices=VPN_TYPE_CHOICES, default='night', db_index=True, verbose_name='Тип VPN')
    amount = models.IntegerField()
    profit = models.IntegerField(default=0, verbose_name='Чистая прибыль')
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', db_index=True)
    subscription_type = models.CharField(max_length=20, choices=SUBSCRIPTION_TYPES, db_index=True)
    issued_key = models.CharField(max_length=255, null=True, blank=True, db_index=True)

    # ЮKassa поля (устарело, используется Platega)
    yookassa_payment_id = models.CharField(max_length=255, null=True, blank=True, unique=True)
    yookassa_confirmation_url = models.URLField(null=True, blank=True)

    # Platega поля
    platega_transaction_id = models.CharField(max_length=255, null=True, blank=True, unique=True, db_index=True)
    platega_payment_url = models.URLField(null=True, blank=True)

    # CryptoBot поля
    cryptobot_invoice_id = models.CharField(max_length=255, null=True, blank=True, unique=True, db_index=True)
    cryptobot_payment_url = models.URLField(null=True, blank=True)
    cryptobot_asset = models.CharField(max_length=50, null=True, blank=True, help_text='Валюта оплаты (USDT, TON, BTC и т.д.)')

    # Antilopay поля
    antilopay_payment_id = models.CharField(max_length=255, null=True, blank=True, unique=True, db_index=True)
    antilopay_order_id = models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name='Order ID Antilopay', help_text='Уникальный идентификатор заказа для вебхуков')
    antilopay_payment_url = models.URLField(null=True, blank=True)
    antilopay_recurrent_id = models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name='ID рекуррента Antilopay', help_text='Идентификатор рекуррентного платежа для автосписания')

    created_at = models.DateTimeField(default=timezone.now, db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True, db_index=True)
    subscription_expires_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name='Дата окончания подписки')
    current_key_expires_at = models.DateTimeField(null=True, blank=True, db_index=True, verbose_name='Дата окончания текущего месячного ключа (для 3м/год)')
    reminder_sent = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено напоминание о незавершенном платеже')
    subscription_reminder_sent = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено напоминание о заканчивающейся подписке (3 дня)')
    subscription_reminder_1d_sent = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено напоминание о заканчивающейся подписке (1 день)')
    subscription_reminder_5h_sent = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено напоминание о заканчивающейся подписке (5 часов)')
    subscription_reminder_1h_sent = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено напоминание о заканчивающейся подписке (1 час)')
    expiry_reminder_sent = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено уведомление о просроченной подписке')
    subscription_just_expired_notified = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено уведомление о только что закончившейся подписке')
    trial_reminder_sent = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено уведомление об окончании пробного периода')
    trial_key_expired_admin_notified = models.BooleanField(default=False, db_index=True, help_text='Было ли отправлено уведомление админу о закончившемся trial ключе (через сутки)')

    # FGN Connection API поля (устарело, оставлено для совместимости)
    fgcn_key_id = models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name='ID ключа в API', help_text='Идентификатор ключа, возвращаемый API')
    fgcn_tg_id = models.BigIntegerField(null=True, blank=True, db_index=True, verbose_name='TG ID в API', help_text='TG ID пользователя в API')
    is_fgn_key = models.BooleanField(default=False, db_index=True, verbose_name='Ключ создан через API', help_text='True если ключ создан через API, False если из пула')
    fgcn_linked_payment_ids = models.TextField(null=True, blank=True, verbose_name='Связанные платежи', help_text='JSON-массив ID платежей, использующих этот ключ')

    # Remnawave Bypass API поля (для обхода)
    bypass_remnawave_uuid = models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name='UUID пользователя в Bypass Remnawave', help_text='UUID пользователя в Remnawave Bypass для продления')

    # Ключ обычного VPN (выдаётся при покупке Night VPN)
    regular_vpn_key = models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name='Ключ обычного VPN', help_text='Ключ обычного VPN, выданный вместе с Night VPN')
    regular_vpn_payment_id = models.IntegerField(null=True, blank=True, db_index=True, verbose_name='ID платежа обычного VPN', help_text='ID связанного платежа обычного VPN')
    regular_vpn_remnawave_uuid = models.CharField(max_length=255, null=True, blank=True, db_index=True, verbose_name='UUID пользователя Remnawave', help_text='UUID пользователя в Remnawave для продления')

    # Поля для продления подписки
    is_renewal = models.BooleanField(default=False, db_index=True, verbose_name='Это продление', help_text='True если это платеж за продление существующей подписки')
    renewal_for_payment = models.ForeignKey('self', on_delete=models.SET_NULL, null=True, blank=True, db_index=True, related_name='renewal_payments', verbose_name='Продление для платежа', help_text='Ссылка на оригинальный платеж, который продлевается')

    class Meta:
        db_table = 'payments'
        verbose_name = 'Платеж'
        verbose_name_plural = 'Платежи'
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['user', 'status']),
            models.Index(fields=['status', 'created_at']),
            models.Index(fields=['platega_transaction_id']),
            models.Index(fields=['vpn_type', 'status']),
        ]

    def __str__(self):
        vpn_label = "Обычный VPN" if self.vpn_type == 'regular' else "Night VPN"
        return f"{vpn_label} - Платеж #{self.payment_id} - {self.user} - {self.amount}₽"


class RegularVpnPayout(models.Model):
    """
    Модель для отслеживания выплат по Обычному VPN
    """
    STATUS_CHOICES = [
        ('pending', 'Ожидает фиксации'),
        ('fixed', 'Зафиксировано'),
        ('paid', 'Выплачено'),
    ]

    payout_id = models.AutoField(primary_key=True)
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Дата создания')
    fixed_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата фиксации')
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name='Дата выплаты')

    # Статистика
    total_payments = models.IntegerField(default=0, verbose_name='Всего платежей')
    total_amount = models.IntegerField(default=0, verbose_name='Общая сумма (₽)')

    # Детализация по типам подписок
    regular_day_count = models.IntegerField(default=0, verbose_name='1 день')
    regular_month_count = models.IntegerField(default=0, verbose_name='1 месяц')
    regular_3months_count = models.IntegerField(default=0, verbose_name='3 месяца')
    regular_6months_count = models.IntegerField(default=0, verbose_name='6 месяцев')
    regular_year_count = models.IntegerField(default=0, verbose_name='1 год')
    regular_2years_count = models.IntegerField(default=0, verbose_name='2 года')

    # Процент отчислений и сумма к выплате
    payout_percentage = models.IntegerField(default=50, verbose_name='Процент отчислений (%)', help_text='Какой процент от общей суммы идёт вам')
    payout_amount = models.IntegerField(default=0, verbose_name='Сумма к выплате (₽)', help_text='Рассчитывается автоматически')

    # Кто зафиксировал выплату
    performed_by = models.BigIntegerField(null=True, blank=True, verbose_name='Выполнил (Telegram ID)', help_text='ID админа, который зафиксировал выплату')
    is_deducted = models.BooleanField(default=False, verbose_name='Списано с баланса', help_text='True если сумма уже списана с виртуального баланса')

    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name='Статус')
    comment = models.TextField(blank=True, null=True, verbose_name='Комментарий')

    class Meta:
        db_table = 'regular_vpn_payouts'
        verbose_name = 'Выплата по Обычному VPN'
        verbose_name_plural = 'Выплаты по Обычному VPN'
        ordering = ['-created_at']

    def __str__(self):
        return f"Выплата #{self.payout_id} - {self.total_amount}₽ - {self.get_status_display()}"

    def calculate_from_payments(self):
        """
        Подсчитать статистику из платежей за период.
        Берёт только те платежи, которые ещё не были учтены в предыдущих выплатах.
        """
        from django.utils import timezone
        from django.db.models import Sum

        # Находим все зафиксированные выплаты (кроме текущей)
        fixed_payouts = RegularVpnPayout.objects.filter(
            status__in=['fixed', 'paid'],
            fixed_at__isnull=False
        ).exclude(payout_id=self.payout_id).order_by('fixed_at')

        if fixed_payouts.exists():
            # Берём дату последней зафиксированной выплаты
            last_fixed = fixed_payouts.last()
            payments = Payment.objects.filter(
                vpn_type='regular',
                status='succeeded',
                paid_at__gt=last_fixed.fixed_at
            )
        else:
            # Если нет зафиксированных — берём все платежи
            payments = Payment.objects.filter(
                vpn_type='regular',
                status='succeeded'
            )

        # Исключаем платежи, которые уже привязаны к другим выплатам (через регулярные выплаты)
        # Собираем все payment_id, которые уже учтены
        excluded_payment_ids = set()
        for payout in fixed_payouts:
            # Находим платежи в периоде этой выплаты
            start_date = None
            prev_payouts = RegularVpnPayout.objects.filter(
                status__in=['fixed', 'paid'],
                fixed_at__isnull=False,
                fixed_at__lt=payout.fixed_at
            ).order_by('-fixed_at')
            if prev_payouts.exists():
                start_date = prev_payouts.first().fixed_at

            if start_date:
                period_payments = Payment.objects.filter(
                    vpn_type='regular',
                    status='succeeded',
                    paid_at__gt=start_date,
                    paid_at__lte=payout.fixed_at
                )
            else:
                period_payments = Payment.objects.filter(
                    vpn_type='regular',
                    status='succeeded',
                    paid_at__lte=payout.fixed_at
                )

            for p in period_payments:
                excluded_payment_ids.add(p.payment_id)

        # Фильтруем исключающие payment_id
        if excluded_payment_ids:
            payments = payments.exclude(payment_id__in=excluded_payment_ids)

        self.total_payments = payments.count()
        self.total_amount = int(payments.aggregate(total=Sum('amount'))['total'] or 0)

        # Детализация по типам
        self.regular_day_count = payments.filter(subscription_type='regular_day').count()
        self.regular_month_count = payments.filter(subscription_type='regular_month').count()
        self.regular_3months_count = payments.filter(subscription_type='regular_3months').count()
        self.regular_6months_count = payments.filter(subscription_type='regular_6months').count()
        self.regular_year_count = payments.filter(subscription_type='regular_year').count()
        self.regular_2years_count = payments.filter(subscription_type='regular_2years').count()

        # Рассчитываем сумму к выплате по проценту
        self.payout_amount = int(self.total_amount * self.payout_percentage / 100)

        self.save()

    def recalculate_payout_amount(self):
        """Пересчитать сумму к выплате при изменении процента."""
        self.payout_amount = int(self.total_amount * self.payout_percentage / 100)
        self.save()


class SupportChat(models.Model):
    STATUS_CHOICES = [
        ('open', 'Открыт'),
        ('closed', 'Закрыт'),
    ]

    chat_id = models.AutoField(primary_key=True)
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='support_chats', db_index=True)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='open', db_index=True)
    created_at = models.DateTimeField(default=timezone.now, db_index=True)

    # Новые поля
    ticket_number = models.CharField(max_length=20, unique=True, null=True, blank=True, db_index=True)
    unread_admin_messages = models.IntegerField(default=0)  # Непрочитанные сообщения от пользователя
    unread_user_messages = models.IntegerField(default=0)  # Непрочитанные сообщения от админа

    class Meta:
        db_table = 'support_chats'
        verbose_name = 'Чат поддержки'
        verbose_name_plural = 'Чаты поддержки'

    def save(self, *args, **kwargs):
        super().save(*args, **kwargs)
        if not self.ticket_number:
            self.ticket_number = f"T{self.chat_id:06d}"
            super().save(update_fields=['ticket_number'])

    def __str__(self):
        return f"Тикет #{self.ticket_number} - {self.user}"


class SupportMessage(models.Model):
    SENDER_CHOICES = [
        ('user', 'Пользователь'),
        ('admin', 'Администратор'),
    ]

    msg_id = models.AutoField(primary_key=True)
    chat = models.ForeignKey(SupportChat, on_delete=models.CASCADE, related_name='messages', db_index=True)
    sender = models.CharField(max_length=10, choices=SENDER_CHOICES, db_index=True)
    text = models.TextField()
    sent_at = models.DateTimeField(default=timezone.now, db_index=True)
    is_read = models.BooleanField(default=False, db_index=True)  # Прочитано ли сообщение
    photo_file_id = models.CharField(max_length=255, null=True, blank=True, verbose_name="ID фото")  # ID фото в Telegram

    class Meta:
        db_table = 'support_messages'
        verbose_name = 'Сообщение поддержки'
        verbose_name_plural = 'Сообщения поддержки'
        ordering = ['sent_at']
        indexes = [
            models.Index(fields=['chat', 'sent_at']),
            models.Index(fields=['chat', 'is_read']),
        ]

    def __str__(self):
        return f"{self.get_sender_display()} - {self.text[:50]}..."
    
    @property
    def has_photo(self):
        """Проверяет, есть ли у сообщения фото"""
        return bool(self.photo_file_id)
    
    def get_photo_url(self):
        """Возвращает URL фото в Telegram"""
        if self.photo_file_id:
            from .photo_service import PhotoService
            return PhotoService.get_photo_url(self.photo_file_id)
        return None


class AdminUser(models.Model):
    admin_id = models.BigIntegerField(primary_key=True)
    name = models.CharField(max_length=255, null=True, blank=True)
    is_active = models.BooleanField(default=True)

    class Meta:
        db_table = 'admin_users'
        verbose_name = 'Администратор'
        verbose_name_plural = 'Администраторы'

    def __str__(self):
        return f"Админ {self.admin_id}"


class Broadcast(models.Model):
    STATUS_CHOICES = [
        ('pending', 'В ожидании'),
        ('sent', 'Отправлено'),
        ('failed', 'Ошибка'),
    ]

    broadcast_id = models.AutoField(primary_key=True)
    admin = models.ForeignKey(AdminUser, on_delete=models.CASCADE, related_name='broadcasts')
    message_text = models.TextField()
    sent_count = models.IntegerField(default=0)
    total_count = models.IntegerField(default=0)
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending')
    created_at = models.DateTimeField(default=timezone.now)

    class Meta:
        db_table = 'broadcasts'
        verbose_name = 'Рассылка'
        verbose_name_plural = 'Рассылки'
        ordering = ['-created_at']

    def __str__(self):
        return f"Рассылка #{self.broadcast_id} - {self.sent_count}/{self.total_count}"

    @property
    def success_rate(self):
        if self.total_count > 0:
            return (self.sent_count / self.total_count) * 100
        return 0


class PromoCode(models.Model):
    """Промокод на скидку"""
    code = models.CharField(max_length=50, unique=True, db_index=True, verbose_name='Код')
    discount_percent = models.IntegerField(verbose_name='Скидка (%)')
    max_uses = models.IntegerField(default=0, verbose_name='Макс. использований (0 = безлимит)')
    current_uses = models.IntegerField(default=0, verbose_name='Текущее использований')
    is_active = models.BooleanField(default=True, verbose_name='Активен')
    created_at = models.DateTimeField(default=timezone.now, verbose_name='Создан')
    expires_at = models.DateTimeField(null=True, blank=True, verbose_name='Истекает')

    class Meta:
        db_table = 'promo_codes'
        verbose_name = 'Промокод'
        verbose_name_plural = 'Промокоды'
        ordering = ['-created_at']

    def __str__(self):
        return f"{self.code} (-{self.discount_percent}%)"


class PromoCodeUsage(models.Model):
    """Использование промокода пользователем"""
    promo_code = models.ForeignKey(PromoCode, on_delete=models.CASCADE, related_name='usages')
    user = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='promo_usages')
    payment = models.ForeignKey('Payment', on_delete=models.SET_NULL, null=True, blank=True, related_name='promo_usages')
    used_at = models.DateTimeField(default=timezone.now, verbose_name='Использован')

    class Meta:
        db_table = 'promo_code_usages'
        verbose_name = 'Использование промокода'
        verbose_name_plural = 'Использования промокодов'

    def __str__(self):
        return f"{self.promo_code.code} - {self.user}"


# Добавляем поля в Payment
Payment.add_to_class('promo_code', models.ForeignKey(PromoCode, null=True, blank=True, on_delete=models.SET_NULL, verbose_name='Промокод'))
Payment.add_to_class('original_amount', models.IntegerField(null=True, blank=True, verbose_name='Сумма до скидки'))

# Добавляем поле макс. использований на пользователя в PromoCode
PromoCode.add_to_class('max_uses_per_user', models.IntegerField(default=1, verbose_name='Макс. использований на пользователя (0 = безлимит)'))


# Импорт моделей рефералов
from .referral_models import ReferralCode, Referral, ReferralReward