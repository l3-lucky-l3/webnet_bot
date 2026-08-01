from django.contrib import admin
from django.utils.html import format_html
from django.urls import reverse
from django.utils.safestring import mark_safe
from django.utils import timezone
from django.http import HttpResponseRedirect
from django.contrib import messages
from .models import (
    TelegramUser, SubscriptionKey, Payment,
    SupportChat, SupportMessage, AdminUser, Broadcast,
    ReferralCode, Referral, ReferralReward, ReferralWithdrawal, ReferralBalanceTransaction,
    RegularVpnPayout, PromoCode, PromoCodeUsage
)


@admin.register(TelegramUser)
class TelegramUserAdmin(admin.ModelAdmin):
    list_display = ['user_id', 'username', 'first_name', 'last_name', 'balance', 'referral_balance', 'created_at', 'payments_count']
    list_filter = ['created_at', 'first_entry_method']
    search_fields = ['user_id', 'username', 'first_name', 'last_name']
    readonly_fields = ['user_id', 'created_at']
    ordering = ['-created_at']

    def payments_count(self, obj):
        return obj.payments.count()
    payments_count.short_description = 'Количество платежей'


@admin.register(SubscriptionKey)
class SubscriptionKeyAdmin(admin.ModelAdmin):
    list_display = ['key_id', 'key_value', 'subscription_type', 'activations_display', 'is_active', 'is_available_display']
    list_filter = ['subscription_type', 'is_active', 'total_activations']
    search_fields = ['key_value']
    readonly_fields = ['key_id']
    ordering = ['-key_id']

    def activations_display(self, obj):
        return f"{obj.used_activations}/{obj.total_activations} ({obj.remaining_activations} осталось)"
    activations_display.short_description = 'Активации'

    def is_available_display(self, obj):
        if obj.is_available:
            return format_html('<span style="color: green;">✓ Доступен</span>')
        else:
            return format_html('<span style="color: red;">✗ Недоступен</span>')
    is_available_display.short_description = 'Статус'


@admin.register(Payment)
class PaymentAdmin(admin.ModelAdmin):
    list_display = ['payment_id', 'user', 'amount', 'subscription_type', 'status_display', 'yookassa_payment_id', 'created_at']
    list_filter = ['status', 'subscription_type', 'created_at']
    search_fields = ['payment_id', 'user__username', 'user__first_name', 'issued_key', 'yookassa_payment_id']
    readonly_fields = ['payment_id', 'created_at', 'paid_at', 'yookassa_payment_id', 'yookassa_confirmation_url']
    ordering = ['-created_at']

    def status_display(self, obj):
        colors = {
            'pending': 'orange',
            'succeeded': 'green',
            'canceled': 'red',
            'failed': 'red'
        }
        color = colors.get(obj.status, 'gray')
        return format_html(
            '<span style="color: {};">{}</span>',
            color, obj.get_status_display()
        )
    status_display.short_description = 'Статус'


@admin.register(SupportChat)
class SupportChatAdmin(admin.ModelAdmin):
    list_display = ['chat_id', 'user', 'status', 'messages_count', 'created_at', 'last_message']
    list_filter = ['status', 'created_at']
    search_fields = ['user__username', 'user__first_name']
    readonly_fields = ['chat_id', 'created_at']
    ordering = ['-created_at']
    actions = ['delete_selected_chats', 'close_selected_chats', 'open_selected_chats']

    def messages_count(self, obj):
        return obj.messages.count()
    messages_count.short_description = 'Сообщений'

    def last_message(self, obj):
        last_msg = obj.messages.last()
        if last_msg:
            return f"{last_msg.sent_at.strftime('%d.%m.%Y %H:%M')} - {last_msg.text[:50]}..."
        return 'Нет сообщений'
    last_message.short_description = 'Последнее сообщение'

    def delete_selected_chats(self, request, queryset):
        """Удалить выбранные чаты поддержки"""
        count = 0
        for chat in queryset:
            # Удаляем все сообщения чата
            chat.messages.all().delete()
            # Удаляем сам чат
            chat.delete()
            count += 1
        
        self.message_user(request, f'Успешно удалено {count} чатов поддержки.')
    delete_selected_chats.short_description = "🗑️ Удалить выбранные чаты"

    def close_selected_chats(self, request, queryset):
        """Закрыть выбранные чаты поддержки"""
        count = queryset.update(status='closed')
        self.message_user(request, f'Успешно закрыто {count} чатов поддержки.')
    close_selected_chats.short_description = "🔒 Закрыть выбранные чаты"

    def open_selected_chats(self, request, queryset):
        """Открыть выбранные чаты поддержки"""
        count = queryset.update(status='open')
        self.message_user(request, f'Успешно открыто {count} чатов поддержки.')
    open_selected_chats.short_description = "🔓 Открыть выбранные чаты"


@admin.register(SupportMessage)
class SupportMessageAdmin(admin.ModelAdmin):
    list_display = ['msg_id', 'chat', 'sender', 'text_preview', 'has_photo_display', 'sent_at']
    list_filter = ['sender', 'sent_at']
    search_fields = ['text', 'chat__user__username']
    readonly_fields = ['msg_id', 'sent_at', 'photo_preview']
    ordering = ['-sent_at']

    def text_preview(self, obj):
        return obj.text[:100] + '...' if len(obj.text) > 100 else obj.text
    text_preview.short_description = 'Текст'
    
    def has_photo_display(self, obj):
        return "📸" if obj.has_photo else "❌"
    has_photo_display.short_description = 'Фото'
    has_photo_display.admin_order_field = 'photo_file_id'
    
    def photo_preview(self, obj):
        if obj.has_photo:
            return f'<img src="{obj.get_photo_url()}" style="max-width: 200px; max-height: 200px;" />'
        return "Нет фото"
    photo_preview.short_description = 'Превью фото'
    photo_preview.allow_tags = True


@admin.register(AdminUser)
class AdminUserAdmin(admin.ModelAdmin):
    list_display = ['admin_id', 'name', 'is_active', 'broadcasts_count']
    list_filter = ['is_active']
    search_fields = ['admin_id', 'name']
    readonly_fields = ['admin_id']

    def broadcasts_count(self, obj):
        return obj.broadcasts.count()
    broadcasts_count.short_description = 'Рассылок'


@admin.register(Broadcast)
class BroadcastAdmin(admin.ModelAdmin):
    list_display = ['broadcast_id', 'admin', 'status', 'progress_display', 'success_rate_display', 'created_at']
    list_filter = ['status', 'created_at']
    search_fields = ['message_text', 'admin__name']
    readonly_fields = ['broadcast_id', 'created_at']
    ordering = ['-created_at']
    actions = ['send_broadcast']

    def progress_display(self, obj):
        return f"{obj.sent_count}/{obj.total_count}"
    progress_display.short_description = 'Прогресс'

    def success_rate_display(self, obj):
        rate = obj.success_rate
        color = 'green' if rate >= 90 else 'orange' if rate >= 70 else 'red'
        return format_html('<span style="color: {};">{:.1f}%</span>', color, rate)
    success_rate_display.short_description = 'Успешность'

    def send_broadcast(self, request, queryset):
        from .services import BroadcastService
        service = BroadcastService()
        sent = 0
        for broadcast in queryset.filter(status='pending'):
            if service.send_broadcast(broadcast):
                sent += 1
        self.message_user(request, f'Отправлено рассылок: {sent}')
    send_broadcast.short_description = 'Отправить выбранные рассылки'


@admin.register(ReferralCode)
class ReferralCodeAdmin(admin.ModelAdmin):
    list_display = ['user', 'code', 'is_active', 'created_at', 'referrals_count']
    list_filter = ['is_active', 'created_at']
    search_fields = ['code', 'user__username', 'user__first_name']
    readonly_fields = ['code', 'created_at']
    ordering = ['-created_at']
    
    def referrals_count(self, obj):
        return obj.referrals_made.count()
    referrals_count.short_description = 'Количество рефералов'


@admin.register(Referral)
class ReferralAdmin(admin.ModelAdmin):
    list_display = ['referrer', 'referred', 'is_active', 'created_at', 'rewards_count']
    list_filter = ['is_active', 'created_at']
    search_fields = ['referrer__username', 'referred__username']
    readonly_fields = ['created_at']
    ordering = ['-created_at']
    
    def rewards_count(self, obj):
        return obj.rewards.count()
    rewards_count.short_description = 'Количество наград'


@admin.register(ReferralReward)
class ReferralRewardAdmin(admin.ModelAdmin):
    list_display = ['referral', 'reward_type', 'reward_value', 'status', 'created_at', 'paid_at']
    list_filter = ['status', 'reward_type', 'created_at']
    search_fields = ['referral__referrer__username', 'referral__referred__username']
    readonly_fields = ['created_at', 'paid_at']
    ordering = ['-created_at']


@admin.register(ReferralWithdrawal)
class ReferralWithdrawalAdmin(admin.ModelAdmin):
    list_display = ['user', 'amount', 'payment_method', 'status_display', 'created_at', 'processed_at']
    list_filter = ['status', 'payment_method', 'created_at']
    search_fields = ['user__username', 'user__first_name', 'payment_details']
    readonly_fields = ['created_at', 'processed_at']
    ordering = ['-created_at']
    
    fieldsets = (
        ('Основная информация', {
            'fields': ('user', 'amount', 'status')
        }),
        ('Детали выплаты', {
            'fields': ('payment_method', 'payment_details')
        }),
        ('Обработка', {
            'fields': ('processed_by', 'admin_comment', 'processed_at'),
            'classes': ('collapse',)
        }),
        ('Даты', {
            'fields': ('created_at',),
            'classes': ('collapse',)
        }),
    )
    
    def status_display(self, obj):
        colors = {
            'pending': 'orange',
            'approved': 'blue', 
            'completed': 'green',
            'rejected': 'red'
        }
        color = colors.get(obj.status, 'black')
        return format_html('<span style="color: {};">{}</span>', color, obj.get_status_display())
    status_display.short_description = 'Статус'
    
    def save_model(self, request, obj, form, change):
        if change and obj.status in ['completed', 'rejected']:
            obj.processed_at = timezone.now()
            obj.processed_by = request.user
        super().save_model(request, obj, form, change)


@admin.register(ReferralBalanceTransaction)
class ReferralBalanceTransactionAdmin(admin.ModelAdmin):
    list_display = ['user', 'transaction_type', 'amount', 'description', 'created_at']
    list_filter = ['transaction_type', 'created_at']
    search_fields = ['user__username', 'user__first_name', 'description']
    readonly_fields = ['created_at']
    ordering = ['-created_at']


@admin.register(RegularVpnPayout)
class RegularVpnPayoutAdmin(admin.ModelAdmin):
    list_display = ['payout_id', 'created_at', 'total_payments', 'total_amount', 'payout_percentage', 'payout_amount', 'status_display', 'fixed_at']
    list_filter = ['status', 'created_at', 'fixed_at']
    search_fields = ['payout_id', 'comment']
    readonly_fields = [
        'payout_id', 'created_at', 'fixed_at', 'paid_at',
        'total_payments', 'total_amount',
        'regular_day_count', 'regular_month_count', 'regular_3months_count',
        'regular_6months_count', 'regular_year_count', 'regular_2years_count',
        'payout_amount'
    ]
    fields = [
        'payout_id', 'created_at', 'fixed_at', 'paid_at',
        'total_payments', 'total_amount',
        'payout_percentage', 'payout_amount',
        'regular_day_count', 'regular_month_count', 'regular_3months_count',
        'regular_6months_count', 'regular_year_count', 'regular_2years_count',
        'status', 'comment'
    ]
    ordering = ['-created_at']

    actions = ['fix_payout', 'mark_as_paid', 'recalculate_payout']

    def save_model(self, request, obj, form, change):
        """При сохранении пересчитываем сумму к выплате."""
        super().save_model(request, obj, form, change)
        obj.recalculate_payout_amount()

    def status_display(self, obj):
        colors = {
            'pending': 'orange',
            'fixed': 'blue',
            'paid': 'green'
        }
        color = colors.get(obj.status, 'black')
        return format_html('<span style="color: {};">{}</span>', color, obj.get_status_display())
    status_display.short_description = 'Статус'

    def fix_payout(self, request, queryset):
        """Зафиксировать выплату"""
        for payout in queryset:
            if payout.status == 'pending':
                payout.status = 'fixed'
                payout.fixed_at = timezone.now()
                payout.calculate_from_payments()
                payout.save()
                self.message_user(request, f"✅ Выплата #{payout.payout_id} зафиксирована: {payout.total_amount}₽ (ваша доля: {payout.payout_amount}₽)")
            else:
                self.message_user(request, f"Выплата #{payout.payout_id} уже зафиксирована", level='warning')
    fix_payout.short_description = "📌 Зафиксировать выбранные выплаты"

    def mark_as_paid(self, request, queryset):
        """Отметить как выплаченное"""
        for payout in queryset:
            if payout.status == 'fixed':
                payout.status = 'paid'
                payout.paid_at = timezone.now()
                payout.save()
                self.message_user(request, f"✅ Выплата #{payout.payout_id} отмечена как выплаченная")
            else:
                self.message_user(request, f"Выплата #{payout.payout_id} не зафиксирована", level='warning')
    mark_as_paid.short_description = "💰 Отметить как выплаченное"

    def recalculate_payout(self, request, queryset):
        """Пересчитать суммы к выплате"""
        for payout in queryset:
            payout.calculate_from_payments()
            self.message_user(request, f"🔄 Выплата #{payout.payout_id} пересчитана: {payout.total_amount}₽ → {payout.payout_amount}₽ ({payout.payout_percentage}%)")
    recalculate_payout.short_description = "🔄 Пересчитать суммы к выплате"


@admin.register(PromoCode)
class PromoCodeAdmin(admin.ModelAdmin):
    list_display = ['code', 'discount_percent', 'max_uses', 'current_uses', 'is_active', 'expires_at', 'created_at']
    list_filter = ['is_active', 'created_at']
    search_fields = ['code']
    readonly_fields = ['current_uses', 'created_at']
    ordering = ['-created_at']


@admin.register(PromoCodeUsage)
class PromoCodeUsageAdmin(admin.ModelAdmin):
    list_display = ['promo_code', 'user', 'payment', 'used_at']
    list_filter = ['used_at']
    search_fields = ['promo_code__code', 'user__username', 'user__user_id']
    readonly_fields = ['used_at']


# Настройка админки
admin.site.site_header = 'Администрирование Telegram бота'
admin.site.site_title = 'TG Bot Admin'
admin.site.index_title = 'Панель управления'