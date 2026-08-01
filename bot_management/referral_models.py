from django.db import models
from django.utils import timezone
import random
import string
from .models import TelegramUser, Payment, SubscriptionKey

class ReferralCode(models.Model):
    user = models.OneToOneField(TelegramUser, on_delete=models.CASCADE, related_name='referral_code', verbose_name="Пользователь")
    code = models.CharField(max_length=20, unique=True, verbose_name="Реферальный код", db_index=True)
    is_active = models.BooleanField(default=True, verbose_name="Активен", db_index=True)
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Дата создания", db_index=True)

    class Meta:
        verbose_name = "Реферальный код"
        verbose_name_plural = "Реферальные коды"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['code']),
            models.Index(fields=['is_active']),
        ]

    def __str__(self):
        return f"Код {self.code} для {self.user.username or self.user.user_id}"
    
    def save(self, *args, **kwargs):
        if not self.code:
            self.code = self.generate_unique_code()
        super().save(*args, **kwargs)
    
    @staticmethod
    def generate_unique_code():
        """Генерирует уникальный реферальный код"""
        while True:
            # Генерируем код из 8 символов (буквы и цифры)
            code = ''.join(random.choices(string.ascii_uppercase + string.digits, k=8))
            if not ReferralCode.objects.filter(code=code).exists():
                return code

class Referral(models.Model):
    referrer = models.ForeignKey(TelegramUser, on_delete=models.CASCADE, related_name='referrals_made', verbose_name="Реферер", db_index=True)
    referred = models.OneToOneField(TelegramUser, on_delete=models.CASCADE, related_name='referred_by', verbose_name="Приглашенный", db_index=True)
    is_active = models.BooleanField(default=True, verbose_name="Активен", db_index=True)
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Дата создания", db_index=True)

    class Meta:
        verbose_name = "Реферал"
        verbose_name_plural = "Рефералы"
        unique_together = ('referrer', 'referred')
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['referrer', 'is_active']),
            models.Index(fields=['referred']),
            models.Index(fields=['is_active', 'created_at']),
        ]

    def __str__(self):
        return f"{self.referrer.username or self.referrer.user_id} пригласил {self.referred.username or self.referred.user_id}"

class ReferralReward(models.Model):
    REWARD_TYPES = [
        ('percent', 'Процент от покупки'),
        ('fixed', 'Фиксированная сумма'),
        ('key', 'Ключ подписки'),
    ]
    STATUS_CHOICES = [
        ('pending', 'В ожидании'),
        ('paid', 'Выплачено'),
        ('canceled', 'Отменено'),
    ]

    referral = models.ForeignKey(Referral, on_delete=models.CASCADE, related_name='rewards', verbose_name="Реферал", db_index=True)
    reward_type = models.CharField(max_length=10, choices=REWARD_TYPES, verbose_name="Тип награды")
    reward_value = models.DecimalField(max_digits=10, decimal_places=2, verbose_name="Значение награды")
    status = models.CharField(max_length=10, choices=STATUS_CHOICES, default='pending', verbose_name="Статус", db_index=True)
    created_at = models.DateTimeField(default=timezone.now, verbose_name="Дата создания", db_index=True)
    paid_at = models.DateTimeField(null=True, blank=True, verbose_name="Дата выплаты")
    payment = models.ForeignKey(Payment, on_delete=models.SET_NULL, null=True, blank=True, related_name='referral_rewards', verbose_name="Платеж", db_index=True)
    subscription_key = models.ForeignKey(SubscriptionKey, on_delete=models.SET_NULL, null=True, blank=True, related_name='referral_rewards', verbose_name="Ключ подписки")

    class Meta:
        verbose_name = "Реферальная награда"
        verbose_name_plural = "Реферальные награды"
        ordering = ['-created_at']
        indexes = [
            models.Index(fields=['referral']),
            models.Index(fields=['payment']),
            models.Index(fields=['status', 'created_at']),
        ]

    def __str__(self):
        return f"Награда {self.reward_value} ({self.get_reward_type_display()}) для {self.referral.referrer.username or self.referral.referrer.user_id}"
