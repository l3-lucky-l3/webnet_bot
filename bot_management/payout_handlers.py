"""
Обработчики команд бота для управления выплатами Regular VPN.
"""

import logging
from aiogram import Router, F
from aiogram.types import CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup
from asgiref.sync import sync_to_async
from django.utils import timezone
from django.db.models import Sum, Q
from bot_management.models import RegularVpnPayout, Payment, BotSettings

logger = logging.getLogger(__name__)

router = Router()


# ========== КНОПКИ В АДМИН МЕНЮ ==========
# Добавлять эти кнопки в admin_menu или отдельную админ-панель


async def get_regular_vpn_payout_menu_keyboard() -> InlineKeyboardMarkup:
    """Клавиатура меню выплат"""
    settings = await sync_to_async(BotSettings.get_setting)('regular_vpn_payout_percentage', '50')
    payout_pct = int(settings)

    # Получаем статистику
    stats = await sync_to_async(_get_regular_vpn_stats)(payout_pct)
    
    text = f"""💰 <b>Управление выплатами Обычный VPN</b>

📊 <b>Текущий процент:</b> {payout_pct}%
💵 <b>Всего заработано:</b> {stats['total_amount']}₽
📦 <b>Всего платежей:</b> {stats['total_payments']}

📅 <b>По типам:</b>
• 1 день: {stats['day_count']}
• 1 месяц: {stats['month_count']}
• 3 месяца: {stats['3months_count']}
• 6 месяцев: {stats['6months_count']}
• 1 год: {stats['year_count']}
• 2 года: {stats['2years_count']}

💳 <b>К выплате:</b> {stats['payout_amount']}₽ ({payout_pct}%)"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 Зафиксировать выплату", callback_data="payout_fix")],
        [InlineKeyboardButton(text="📝 История выплат", callback_data="payout_history")],
        [InlineKeyboardButton(text="⚙️ Настроить процент", callback_data="payout_set_percentage")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")],
    ])

    return text, kb


def _get_regular_vpn_stats(payout_percentage=50):
    """Получить статистику по Regular VPN (синхронная)"""
    from django.utils import timezone
    from django.db.models import Sum

    # Находим последнюю зафиксированную/выплаченную выплату
    last_payout = RegularVpnPayout.objects.filter(
        status__in=['fixed', 'paid'],
        fixed_at__isnull=False
    ).order_by('-fixed_at').first()

    if last_payout:
        payments = Payment.objects.filter(
            vpn_type='regular',
            status='succeeded',
            paid_at__gt=last_payout.fixed_at
        )
    else:
        payments = Payment.objects.filter(
            vpn_type='regular',
            status='succeeded'
        )

    total_amount = int(payments.aggregate(total=Sum('amount'))['total'] or 0)
    total_payments = payments.count()

    payout_amount = int(total_amount * payout_percentage / 100)

    return {
        'total_amount': total_amount,
        'total_payments': total_payments,
        'day_count': payments.filter(subscription_type='regular_day').count(),
        'month_count': payments.filter(subscription_type='regular_month').count(),
        '3months_count': payments.filter(subscription_type='regular_3months').count(),
        '6months_count': payments.filter(subscription_type='regular_6months').count(),
        'year_count': payments.filter(subscription_type='regular_year').count(),
        '2years_count': payments.filter(subscription_type='regular_2years').count(),
        'payout_amount': payout_amount,
        'payout_percentage': payout_percentage,
    }


# ========== ОБРАБОТЧИКИ ==========

@router.callback_query(F.data == "payout_menu")
async def payout_menu_handler(callback: CallbackQuery):
    """Главное меню выплат"""
    text, kb = await get_regular_vpn_payout_menu_keyboard()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data == "payout_fix")
async def payout_fix_handler(callback: CallbackQuery):
    """Зафиксировать выплату"""
    admin_id = callback.from_user.id

    pct = await sync_to_async(BotSettings.get_setting)('regular_vpn_payout_percentage', '50')

    # Создаём новую выплату
    payout = await sync_to_async(RegularVpnPayout.objects.create)(
        payout_percentage=int(pct),
        performed_by=admin_id,
    )

    await sync_to_async(payout.calculate_from_payments)()

    if payout.total_payments == 0:
        await callback.answer("ℹ️ Нет новых платежей для фиксации", show_alert=True)
        await sync_to_async(payout.delete)()
        return

    text = f"""📌 <b>Выплата зафиксирована!</b>

📋 <b>Выплата #{payout.payout_id}</b>
💵 <b>Общая сумма:</b> {payout.total_amount}₽
📦 <b>Платежей:</b> {payout.total_payments}
💰 <b>К выплате ({payout.payout_percentage}%):</b> {payout.payout_amount}₽

📅 <b>Детализация:</b>
• 1 день: {payout.regular_day_count}
• 1 месяц: {payout.regular_month_count}
• 3 месяца: {payout.regular_3months_count}
• 6 месяцев: {payout.regular_6months_count}
• 1 год: {payout.regular_year_count}
• 2 года: {payout.regular_2years_count}"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отметить как выплачено", callback_data=f"payout_paid_{payout.payout_id}")],
        [InlineKeyboardButton(text="💳 Списать с баланса", callback_data=f"payout_deduct_{payout.payout_id}")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="payout_menu")],
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("payout_paid_"))
async def payout_paid_handler(callback: CallbackQuery):
    """Отметить выплату как выплаченную"""
    payout_id = int(callback.data.split("_")[-1])

    payout = await sync_to_async(RegularVpnPayout.objects.get)(payout_id=payout_id)

    if payout.status != 'fixed':
        await callback.answer("❌ Сначала зафиксируйте выплату", show_alert=True)
        return

    await sync_to_async(setattr)(payout, 'status', 'paid')
    await sync_to_async(setattr)(payout, 'paid_at', timezone.now())
    await sync_to_async(payout.save)()

    await callback.answer(f"✅ Выплата #{payout_id} отмечена как выплаченная!", show_alert=True)

    # Обновляем сообщение
    text, kb = await get_regular_vpn_payout_menu_keyboard()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data.startswith("payout_deduct_"))
async def payout_deduct_handler(callback: CallbackQuery):
    """Списать сумму с баланса"""
    payout_id = int(callback.data.split("_")[-1])
    admin_id = callback.from_user.id

    payout = await sync_to_async(RegularVpnPayout.objects.get)(payout_id=payout_id)

    if payout.is_deducted:
        await callback.answer("ℹ️ Сумма уже списана", show_alert=True)
        return

    # TODO: Здесь логика списания с баланса
    # Например: user.balance -= payout.payout_amount
    # Или: создать транзакцию списания

    await sync_to_async(setattr)(payout, 'is_deducted', True)
    await sync_to_async(payout.save)()

    await callback.answer(f"💳 {payout.payout_amount}₽ списано с баланса", show_alert=True)

    # Обновляем сообщение
    text, kb = await get_regular_vpn_payout_menu_keyboard()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "payout_set_percentage")
async def payout_set_percentage_handler(callback: CallbackQuery):
    """Установка процента отчислений"""
    current_pct = await sync_to_async(BotSettings.get_setting)('regular_vpn_payout_percentage', '50')
    current_pct = int(current_pct)

    text = f"""⚙️ <b>Настройка процента отчислений</b>

📊 <b>Текущий процент:</b> {current_pct}%

Выберите новый процент:"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="30%", callback_data="payout_pct_30")],
        [InlineKeyboardButton(text="40%", callback_data="payout_pct_40")],
        [InlineKeyboardButton(text="50%", callback_data="payout_pct_50")],
        [InlineKeyboardButton(text="60%", callback_data="payout_pct_60")],
        [InlineKeyboardButton(text="70%", callback_data="payout_pct_70")],
        [InlineKeyboardButton(text="80%", callback_data="payout_pct_80")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="payout_menu")],
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()


@router.callback_query(F.data.startswith("payout_pct_"))
async def payout_pct_handler(callback: CallbackQuery):
    """Сохранение процента"""
    new_pct = int(callback.data.split("_")[-1])

    await sync_to_async(BotSettings.set_setting)(
        'regular_vpn_payout_percentage',
        str(new_pct),
        'Процент отчислений за Regular VPN'
    )

    await callback.answer(f"✅ Процент установлен: {new_pct}%", show_alert=True)

    text, kb = await get_regular_vpn_payout_menu_keyboard()
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)


@router.callback_query(F.data == "payout_history")
async def payout_history_handler(callback: CallbackQuery):
    """История выплат"""
    payouts = await sync_to_async(list)(
        RegularVpnPayout.objects.all().order_by('-created_at')[:15]
    )

    if not payouts:
        text = "📭 <b>История выплат пуста</b>"
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="payout_menu")],
        ])
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
        await callback.answer()
        return

    text = "📝 <b>История выплат</b>\n\n"

    for p in payouts:
        status_emoji = {'pending': '⏳', 'fixed': '📌', 'paid': '✅'}.get(p.status, '❓')
        performed_by = f" (админ: {p.performed_by})" if p.performed_by else ""
        deducted = " 💳" if p.is_deducted else ""
        
        text += f"""{status_emoji} <b>#{p.payout_id}</b> — {p.total_amount}₽ → {p.payout_amount}₽
   📅 {p.created_at.strftime('%d.%m.%Y')} | Платежей: {p.total_payments}{performed_by}{deducted}

"""

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="payout_menu")],
    ])

    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.answer()
