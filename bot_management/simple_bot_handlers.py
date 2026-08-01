"""
Упрощенные обработчики для бота
"""
import logging
import aiohttp
from aiogram import Bot
from aiogram.types import InlineKeyboardMarkup, InlineKeyboardButton
from config import BOT_TOKEN

logger = logging.getLogger(__name__)

# Создаем экземпляр бота
bot = Bot(token=BOT_TOKEN)


async def create_simple_balance_payment(user_id: int, amount: float) -> dict:
    """
    Создает простой платеж для пополнения баланса
    
    Args:
        user_id: ID пользователя
        amount: Сумма платежа
        
    Returns:
        Dict с данными платежа
    """
    try:
        api_url = 'http://127.0.0.1:8123/bot_management/api/simple/payment/create/'
        
        data = {
            'user_id': user_id,
            'amount': amount
        }
        
        async with aiohttp.ClientSession() as session:
            async with session.post(api_url, json=data) as response:
                if response.status == 200:
                    result = await response.json()
                    if result.get('success'):
                        return {
                            'success': True,
                            'payment_id': result['payment_id'],
                            'confirmation_url': result['confirmation_url'],
                            'status': result['status'],
                            'amount': result['amount']
                        }
                    else:
                        logger.error(f"Ошибка создания платежа: {result.get('message')}")
                        return {'success': False, 'message': result.get('message', 'Ошибка создания платежа')}
                else:
                    logger.error(f"HTTP ошибка {response.status}")
                    return {'success': False, 'message': 'Ошибка сервера'}
                    
    except Exception as e:
        logger.error(f"Ошибка создания простого платежа: {e}")
        return {'success': False, 'message': 'Ошибка создания платежа'}


async def check_simple_payment_status(payment_id: str, user_id: int) -> dict:
    """
    Проверяет статус простого платежа
    
    Args:
        payment_id: ID платежа в ЮKassa
        user_id: ID пользователя
        
    Returns:
        Dict с данными платежа
    """
    try:
        api_url = f'http://127.0.0.1:8123/bot_management/api/simple/payment/{payment_id}/status/?user_id={user_id}'
        
        async with aiohttp.ClientSession() as session:
            async with session.get(api_url) as response:
                if response.status == 200:
                    result = await response.json()
                    return result
                else:
                    logger.error(f"HTTP ошибка {response.status}")
                    return {'success': False, 'message': 'Ошибка проверки статуса'}
                    
    except Exception as e:
        logger.error(f"Ошибка проверки статуса платежа: {e}")
        return {'success': False, 'message': 'Ошибка проверки статуса'}


async def send_payment_success_message(user_id: int, amount: float, new_balance: float = None):
    """
    Отправляет сообщение об успешном платеже
    
    Args:
        user_id: ID пользователя
        amount: Сумма платежа
        new_balance: Новый баланс (опционально)
    """
    try:
        balance_text = f"\n💳 <b>Новый баланс:</b> {new_balance} ₽" if new_balance else ""
        
        success_text = f"""
✅ <b>Платеж подтвержден!</b>

💰 <b>Баланс пополнен на:</b> {amount} ₽{balance_text}

<i>Спасибо за пополнение! 🚀</i>
"""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="⬅️ Баланс", callback_data="balance")],
            [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
        ])
        
        await bot.send_message(
            chat_id=user_id,
            text=success_text,
            parse_mode="HTML",
            reply_markup=kb
        )
        
        logger.info(f"Сообщение об успешном платеже отправлено пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения об успешном платеже: {e}")


async def send_payment_pending_message(user_id: int, payment_id: str, amount: float):
    """
    Отправляет сообщение о платеже в обработке
    
    Args:
        user_id: ID пользователя
        payment_id: ID платежа
        amount: Сумма платежа
    """
    try:
        pending_text = f"""
⏳ <b>Платеж в обработке</b>

💰 <b>Сумма:</b> {amount} ₽
🆔 <b>ID платежа:</b> {payment_id}

<i>Платеж обрабатывается, попробуйте через несколько минут</i>
"""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔄 Проверить снова", callback_data=f"check_simple_payment_{payment_id}")],
            [InlineKeyboardButton(text="⬅️ Баланс", callback_data="balance")]
        ])
        
        await bot.send_message(
            chat_id=user_id,
            text=pending_text,
            parse_mode="HTML",
            reply_markup=kb
        )
        
        logger.info(f"Сообщение о платеже в обработке отправлено пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения о платеже в обработке: {e}")


async def send_payment_error_message(user_id: int, message: str):
    """
    Отправляет сообщение об ошибке платежа
    
    Args:
        user_id: ID пользователя
        message: Сообщение об ошибке
    """
    try:
        error_text = f"""
❌ <b>Ошибка платежа</b>

{message}

<i>Попробуйте создать новый платеж</i>
"""
        
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="💳 Пополнить снова", callback_data="deposit_balance")],
            [InlineKeyboardButton(text="⬅️ Баланс", callback_data="balance")]
        ])
        
        await bot.send_message(
            chat_id=user_id,
            text=error_text,
            parse_mode="HTML",
            reply_markup=kb
        )
        
        logger.info(f"Сообщение об ошибке платежа отправлено пользователю {user_id}")
        
    except Exception as e:
        logger.error(f"Ошибка отправки сообщения об ошибке платежа: {e}")






