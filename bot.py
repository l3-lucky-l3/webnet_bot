import asyncio
import logging
from aiogram import Bot, Dispatcher, Router, F
from aiogram.types import (
    Message, CallbackQuery, InlineKeyboardButton, InlineKeyboardMarkup,
    ReplyKeyboardMarkup, KeyboardButton, ContentType
)
from aiogram.filters import Command, CommandObject
from aiogram.fsm.context import FSMContext
from aiogram.fsm.state import State, StatesGroup
from aiogram.fsm.storage.memory import MemoryStorage
from config import BOT_TOKEN, ADMIN_IDS, PRICES, VIDEO_FILE_ID, OPERATORS
from database import init_db, get_db
import os

# Логирование
logging.basicConfig(level=logging.INFO)

# Инициализация
init_db()
bot = Bot(token=BOT_TOKEN)
dp = Dispatcher(storage=MemoryStorage())
router = Router()
dp.include_router(router)

# FSM состояния
class SupportState(StatesGroup):
    in_chat = State()

class UploadKeysState(StatesGroup):
    waiting_keys = State()
    waiting_type = State()
    waiting_activations = State()

class BroadcastState(StatesGroup):
    waiting_message = State()
    waiting_confirmation = State()

class AddCardState(StatesGroup):
    waiting_last4 = State()
    waiting_bank = State()
    waiting_limit = State()

# Утилиты
def is_admin(user_id: int) -> bool:
    return user_id in ADMIN_IDS

def get_next_card():
    with get_db() as conn:
        cursor = conn.cursor()
        # Сначала ищем карты с наименьшей загрузкой
        cursor.execute("""
            SELECT * FROM cards 
            WHERE is_active = 1 AND current_received < max_limit
            ORDER BY 
                (current_received * 1.0 / max_limit) ASC,  -- по проценту загрузки
                card_id ASC  -- если проценты равны, то по ID
            LIMIT 1
        """)
        return cursor.fetchone()

def get_available_key(sub_type: str):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM keys 
            WHERE subscription_type = ? AND used_activations < total_activations AND is_active = 1
            ORDER BY key_id
            LIMIT 1
        """, (sub_type,))
        key = cursor.fetchone()
        if key:
            cursor.execute("""
                UPDATE keys 
                SET used_activations = used_activations + 1,
                    is_active = CASE WHEN used_activations + 1 >= total_activations THEN 0 ELSE 1 END
                WHERE key_id = ?
            """, (key["key_id"],))
            return key
        return None

def get_or_create_support_chat(user_id: int):
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT * FROM support_chats WHERE user_id = ? AND status = 'open'", (user_id,))
        chat = cursor.fetchone()
        if not chat:
            cursor.execute("INSERT INTO support_chats (user_id) VALUES (?)", (user_id,))
            chat_id = cursor.lastrowid
            return chat_id
        return chat["chat_id"]

# --- Основные хендлеры ---

@router.message(Command("start"))
async def cmd_start(message: Message):
    user = message.from_user
    with get_db() as conn:
        conn.execute("""
            INSERT OR IGNORE INTO users (user_id, username, first_name, last_name)
            VALUES (?, ?, ?, ?)
        """, (user.id, user.username, user.first_name, user.last_name))

    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Подписка на месяц", callback_data="sub_month")],
        [InlineKeyboardButton(text="🔓 Подписка навсегда", callback_data="sub_forever")],
        [InlineKeyboardButton(text="🛠 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="ℹ️ О сервисе", callback_data="about")]
    ])

    welcome_text = """
🎬 <b>Добро пожаловать в наш сервис!</b>

💎 <b>Доступные подписки:</b>
• 📅 <b>Месячная подписка</b> - 500 ₽
• 🔓 <b>Пожизненная подписка</b> - 3000 ₽

🛠 <b>Нужна помощь?</b> - нажмите кнопку "Поддержка"

<i>Выберите подходящий вариант ниже ⬇️</i>
"""
    
    # Видео отключено - отправляем только текст
    await message.answer(welcome_text, reply_markup=kb, parse_mode="HTML")

# Выбор подписки через inline кнопки
@router.callback_query(F.data.in_(["sub_month", "sub_forever"]))
async def choose_subscription(callback: CallbackQuery, state: FSMContext):
    sub_type = "month" if callback.data == "sub_month" else "forever"
    amount = PRICES[sub_type]

    card = get_next_card()
    if not card:
        await callback.message.edit_text("""
❌ <b>Все реквизиты временно перегружены</b>

🔧 <b>Что происходит:</b>
• Все карты достигли максимального лимита
• Администратор скоро пополнит лимиты
• Или добавит новые карты

⏰ <b>Попробуйте позже</b> - обычно в течение 1-2 часов

<i>Мы работаем над решением проблемы! 🚀</i>
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="🔄 Попробовать снова", callback_data="sub_month" if sub_type == "month" else "sub_forever")],
    [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
]))
        return

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO payments (user_id, card_id, amount, subscription_type)
            VALUES (?, ?, ?, ?)
        """, (callback.from_user.id, card["card_id"], amount, sub_type))
        payment_id = cursor.lastrowid

    # Запускаем отложенное напоминание через 30 минут
    asyncio.create_task(schedule_pending_payment_reminder(payment_id, callback.from_user.id, sub_type, amount))

    # Проверяем наличие поля card_number
    try:
        card_display = card['card_number'] if card['card_number'] else f"••••{card['last4']}"
    except KeyError:
        # Если поле card_number не существует (старые записи)
        card_display = f"••••{card['last4']}"
    
    sub_name = "📅 Месячная подписка" if sub_type == "month" else "🔓 Пожизненная подписка"
    
    # Получаем информацию о других картах для контекста
    with get_db() as conn:
        other_cards = conn.execute("""
            SELECT COUNT(*) as total_cards, 
                   SUM(CASE WHEN is_active = 1 AND current_received < max_limit THEN 1 ELSE 0 END) as available_cards
            FROM cards
        """).fetchone()
    
    payment_text = f"""
💳 <b>Данные для оплаты</b>

💰 <b>Сумма к оплате:</b> <code>{amount} ₽</code>
🏦 <b>Банк:</b> {card['bank_name']}
💳 <b>Номер карты:</b> <code>{card_display}</code>

📋 <b>Инструкция:</b>
1️⃣ Переведите <b>точно {amount} ₽</b> на указанную карту
2️⃣ Сохраните <b>чек об оплате</b>
3️⃣ Отправьте <b>PDF-чек</b> боту (не скриншот!)
4️⃣ Дождитесь подтверждения администратора

⚠️ <b>Важно:</b> Бот проверит сумму и получателя перед выдачей ключа

🎁 <b>После подтверждения</b> вы получите ключ для {sub_name.lower()}
"""
    
    # Добавляем кнопки для управления
    payment_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📋 Инструкция по оплате", callback_data="payment_help")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(payment_text, parse_mode="HTML", reply_markup=payment_kb)

# Приём PDF
@router.message(F.content_type == ContentType.DOCUMENT)
async def handle_pdf(message: Message):
    if not message.document.mime_type == "application/pdf":
        await message.answer("""
❌ <b>Неверный формат файла!</b>

📎 <b>Пожалуйста, отправьте именно PDF-файл:</b>
• Не скриншот экрана
• Не фотографию чека
• А именно PDF-документ

💡 <b>Как получить PDF:</b>
• Скачайте чек из банковского приложения
• Или попросите банк отправить чек на email
""", parse_mode="HTML")
        return

    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            SELECT * FROM payments 
            WHERE user_id = ? AND status = 'pending'
            ORDER BY created_at DESC LIMIT 1
        """, (message.from_user.id,))
        payment = cursor.fetchone()

        if not payment:
            await message.answer("""
❌ <b>Активный платеж не найден!</b>

🔄 <b>Что делать:</b>
1️⃣ Нажмите /start
2️⃣ Выберите подписку
3️⃣ Получите данные для оплаты
4️⃣ Сделайте перевод
5️⃣ Отправьте PDF-чек

<i>Платеж должен быть активным для загрузки чека</i>
""", parse_mode="HTML")
            return

        cursor.execute("""
            UPDATE payments SET pdf_file_id = ?, has_receipt = 1 WHERE payment_id = ?
        """, (message.document.file_id, payment["payment_id"]))

    # Уведомление админам
    sub_type_text = "📅 Месячная" if payment['subscription_type'] == 'month' else "🔓 Пожизненная"
    
    for admin_id in ADMIN_IDS:
        try:
            await bot.send_message(
                admin_id,
                f"""
📥 <b>Новый платеж требует подтверждения</b>

👤 <b>Пользователь:</b> @{message.from_user.username or message.from_user.id}
💰 <b>Сумма:</b> <code>{payment['amount']} ₽</code>
🎫 <b>Тип подписки:</b> {sub_type_text}
💳 <b>ID карты:</b> <code>{payment['card_id']}</code>

📎 <b>Чек загружен и готов к проверке</b>
""",
                parse_mode="HTML",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="👁 Посмотреть чек", callback_data=f"view_pdf:{payment['payment_id']}")],
                    [InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{payment['payment_id']}"),
                     InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{payment['payment_id']}")]
                ])
            )
        except:
            pass

    await message.answer("""
✅ <b>Чек успешно получен!</b>

📋 <b>Что происходит дальше:</b>
1️⃣ Администратор проверит ваш чек
2️⃣ Сверит сумму и получателя
3️⃣ Подтвердит или отклонит платеж
4️⃣ При подтверждении вы получите ключ

⏰ <b>Время обработки:</b> обычно в течение 15-30 минут

<i>Спасибо за покупку! 🎉</i>
""", parse_mode="HTML")

# --- Админка ---

@router.message(Command("admin"))
async def admin_panel(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    admin_text = """
🔧 <b>Панель администратора</b>

📊 <b>Доступные разделы:</b>
• 💳 <b>Карты</b> - управление картами для приема платежей
• 🔑 <b>Ключи</b> - загрузка и просмотр ключей подписок
• 💰 <b>Платежи</b> - просмотр и обработка платежей
• 📞 <b>Поддержка</b> - ответы на вопросы пользователей

<i>Выберите нужный раздел ниже ⬇️</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Карты", callback_data="admin_cards")],
        [InlineKeyboardButton(text="🔑 Ключи", callback_data="admin_keys")],
        [InlineKeyboardButton(text="💰 Платежи", callback_data="admin_payments")],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="admin_support")],
        [InlineKeyboardButton(text="📢 Рассылки", callback_data="admin_broadcast")]
    ])
    await message.answer(admin_text, reply_markup=kb, parse_mode="HTML")

@router.message(Command("test_cards"))
async def test_cards_selection(message: Message):
    if not is_admin(message.from_user.id):
        return
    
    with get_db() as conn:
        cards = conn.execute("""
            SELECT card_id, bank_name, current_received, max_limit, is_active,
                   (current_received * 1.0 / max_limit) as load_percent
            FROM cards 
            ORDER BY load_percent ASC, card_id ASC
        """).fetchall()
    
    if not cards:
        await message.answer("❌ Карты не найдены")
        return
    
    test_text = """
🧪 <b>Тест системы выбора карт</b>

📊 <b>Порядок выбора карт:</b>
"""
    
    for i, card in enumerate(cards, 1):
        status = "✅" if card["is_active"] and card["current_received"] < card["max_limit"] else "❌"
        test_text += f"""
{i}. <b>ID {card['card_id']}</b> | {card['bank_name']} {status}
   💰 {card['current_received']:,} / {card['max_limit']:,} ₽ ({card['load_percent']:.1f}%)
"""
    
    # Показываем, какая карта будет выбрана следующей
    next_card = get_next_card()
    if next_card:
        test_text += f"""

🎯 <b>Следующая карта для платежа:</b>
• ID: <code>{next_card['card_id']}</code>
• Банк: <code>{next_card['bank_name']}</code>
• Загрузка: <code>{(next_card['current_received'] / next_card['max_limit']) * 100:.1f}%</code>
"""
    else:
        test_text += "\n\n❌ <b>Нет доступных карт</b>"
    
    await message.answer(test_text, parse_mode="HTML")

# Карты
@router.callback_query(F.data == "admin_cards")
async def admin_cards(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    with get_db() as conn:
        cards = conn.execute("SELECT * FROM cards ORDER BY card_id").fetchall()
        
        # Получаем статистику в том же соединении
        stats = conn.execute("""
            SELECT 
                COUNT(*) as total_cards,
                SUM(CASE WHEN is_active = 1 THEN 1 ELSE 0 END) as active_cards,
                SUM(current_received) as total_received,
                SUM(max_limit) as total_limit
            FROM cards
        """).fetchone()
    
    if not cards:
        text = """
💳 <b>Управление картами</b>

❌ <b>Карты не найдены</b>

➕ <b>Добавьте первую карту</b> для начала работы с платежами
"""
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="➕ Добавить карту", callback_data="add_card")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])
    else:
        
        # Статистика
        total_received = stats['total_received'] or 0
        total_limit = stats['total_limit'] or 1
        usage_percent = (total_received / total_limit) * 100
        
        text = f"""
💳 <b>Управление картами</b>

📊 <b>Общая статистика:</b>
• 📋 <b>Всего карт:</b> <code>{stats['total_cards']}</code>
• ✅ <b>Активных:</b> <code>{stats['active_cards']}</code>
• 💰 <b>Получено:</b> <code>{total_received:,} ₽</code>
• 🎯 <b>Лимит:</b> <code>{total_limit:,} ₽</code>
• 📈 <b>Загрузка:</b> <code>{usage_percent:.1f}%</code>

💳 <b>Список карт:</b>
"""
        
        for c in cards:
            status = "✅" if c["is_active"] else "❌"
            try:
                card_display = c['card_number'] if c['card_number'] else f"••••{c['last4']}"
            except KeyError:
                card_display = f"••••{c['last4']}"
            
            usage_percent = (c['current_received'] / c['max_limit']) * 100 if c['max_limit'] > 0 else 0
            
            # Цветовая индикация загрузки
            if usage_percent >= 90:
                load_emoji = "🔴"
            elif usage_percent >= 70:
                load_emoji = "🟡"
            else:
                load_emoji = "🟢"
            
            text += f"""
{load_emoji} <b>ID {c['card_id']}</b> | {c['bank_name']} {status}
   💳 <code>{card_display}</code>
   💰 <code>{c['current_received']:,}</code> / <code>{c['max_limit']:,} ₽</code> ({usage_percent:.1f}%)
"""
        
        # Кнопки управления
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="📊 Детальный просмотр", callback_data="cards_detailed")],
            [InlineKeyboardButton(text="➕ Добавить карту", callback_data="add_card")],
            [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
        ])
    try:
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    except Exception as e:
        await callback.answer("✅ Обновлено!")

# Детальный просмотр карт
@router.callback_query(F.data == "cards_detailed")
async def cards_detailed(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    with get_db() as conn:
        cards = conn.execute("SELECT * FROM cards ORDER BY card_id").fetchall()
    
    if not cards:
        await callback.answer("❌ Карты не найдены!", show_alert=True)
        return
    
    text = "💳 <b>Детальный просмотр карт</b>\n\n"
    
    kb = []
    for c in cards:
        try:
            card_display = c['card_number'] if c['card_number'] else f"••••{c['last4']}"
        except KeyError:
            card_display = f"••••{c['last4']}"
        
        status = "✅ Активна" if c["is_active"] else "❌ Отключена"
        usage_percent = (c['current_received'] / c['max_limit']) * 100 if c['max_limit'] > 0 else 0
        
        # Цветовая индикация загрузки
        if usage_percent >= 90:
            load_emoji = "🔴"
        elif usage_percent >= 70:
            load_emoji = "🟡"
        else:
            load_emoji = "🟢"
        
        text += f"""
{load_emoji} <b>Карта #{c['card_id']}</b>
   🏦 <b>Банк:</b> {c['bank_name']}
   💳 <b>Номер:</b> <code>{card_display}</code>
   📊 <b>Статус:</b> {status}
   💰 <b>Получено:</b> <code>{c['current_received']:,} ₽</code>
   🎯 <b>Лимит:</b> <code>{c['max_limit']:,} ₽</code>
   📈 <b>Загрузка:</b> <code>{usage_percent:.1f}%</code>
"""
        
        # Кнопки управления для каждой карты
        kb.append([
            InlineKeyboardButton(text=f"🔄 {c['card_id']}", callback_data=f"reset_card:{c['card_id']}"),
            InlineKeyboardButton(text="✏️", callback_data=f"set_limit:{c['card_id']}"),
            InlineKeyboardButton(text="🚫" if c["is_active"] else "✅", callback_data=f"toggle_card:{c['card_id']}"),
            InlineKeyboardButton(text="🗑", callback_data=f"delete_card:{c['card_id']}")
        ])
    
    kb.append([InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_cards")])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Обработчики для карт
@router.callback_query(F.data.startswith("reset_card:"))
async def reset_card(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    card_id = int(callback.data.split(":")[1])
    with get_db() as conn:
        conn.execute("UPDATE cards SET current_received = 0 WHERE card_id = ?", (card_id,))
    await callback.answer("✅ Лимит сброшен!")
    # Обновляем список карт
    await admin_cards(callback)

@router.callback_query(F.data.startswith("toggle_card:"))
async def toggle_card(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    card_id = int(callback.data.split(":")[1])
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("SELECT is_active FROM cards WHERE card_id = ?", (card_id,))
        card = cursor.fetchone()
        if card:
            new_status = 0 if card["is_active"] else 1
            conn.execute("UPDATE cards SET is_active = ? WHERE card_id = ?", (new_status, card_id))
    await callback.answer("✅ Статус изменён!")
    # Обновляем список карт
    await admin_cards(callback)

@router.callback_query(F.data.startswith("delete_card:"))
async def delete_card(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    card_id = int(callback.data.split(":")[1])
    
    # Проверяем, есть ли активные платежи с этой картой
    with get_db() as conn:
        active_payments = conn.execute(
            "SELECT COUNT(*) as count FROM payments WHERE card_id = ? AND status = 'pending'", 
            (card_id,)
        ).fetchone()
        
        if active_payments["count"] > 0:
            # Показываем кнопку принудительного удаления
            await callback.message.edit_text(
                f"⚠️ У карты есть {active_payments['count']} активных платежей!\n\n"
                f"Выберите действие:",
                reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                    [InlineKeyboardButton(text="🗑 Принудительно удалить", callback_data=f"force_delete_card:{card_id}")],
                    [InlineKeyboardButton(text="⬅️ Назад к картам", callback_data="admin_cards")]
                ])
            )
            return
        
        # Удаляем карту
        conn.execute("DELETE FROM cards WHERE card_id = ?", (card_id,))
    
    await callback.answer("✅ Карта удалена!")
    # Обновляем список карт
    await admin_cards(callback)

@router.callback_query(F.data.startswith("force_delete_card:"))
async def force_delete_card(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    card_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        # Удаляем карту и все связанные платежи
        conn.execute("DELETE FROM payments WHERE card_id = ?", (card_id,))
        conn.execute("DELETE FROM cards WHERE card_id = ?", (card_id,))
    
    await callback.answer("⚠️ Карта и все связанные платежи удалены!")
    # Обновляем список карт
    await admin_cards(callback)

@router.callback_query(F.data.startswith("set_limit:"))
async def set_limit_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    card_id = int(callback.data.split(":")[1])
    await state.set_data({"card_id": card_id})
    await callback.message.answer("Введите новый лимит в рублях:")
    await state.set_state(AddCardState.waiting_limit)

@router.message(AddCardState.waiting_limit)
async def set_limit_finish(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    try:
        limit = int(message.text)
        data = await state.get_data()
        if "card_id" in data:
            with get_db() as conn:
                conn.execute("UPDATE cards SET max_limit = ? WHERE card_id = ?", (limit, data["card_id"]))
            await message.answer("✅ Лимит обновлён!")
        else:
            # Это добавление новой карты
            data = await state.get_data()
            with get_db() as conn:
                conn.execute("""
                    INSERT INTO cards (card_number, last4, bank_name, max_limit) VALUES (?, ?, ?, ?)
                """, (data["card_number"], data["last4"], data["bank"], limit))
            await message.answer("✅ Карта добавлена!")
    except:
        await message.answer("❌ Ошибка. Попробуйте снова.")
    await state.clear()

@router.callback_query(F.data == "add_card")
async def add_card_start(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    await callback.message.answer("Введите полный номер карты (например: 1234 5678 9012 3456):")
    await state.set_state(AddCardState.waiting_last4)

@router.message(AddCardState.waiting_last4)
async def add_card_last4(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    # Очищаем номер карты от пробелов и извлекаем последние 4 цифры
    card_number = message.text.replace(" ", "").replace("-", "")
    if not card_number.isdigit() or len(card_number) < 4:
        await message.answer("❌ Неверный формат номера карты. Введите полный номер карты:")
        return
    
    last4 = card_number[-4:]
    await state.update_data(card_number=card_number, last4=last4)
    await message.answer("Введите название банка (например, Сбербанк):")
    await state.set_state(AddCardState.waiting_bank)

@router.message(AddCardState.waiting_bank)
async def add_card_bank(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    await state.update_data(bank=message.text)
    await message.answer("Введите лимит в рублях (например, 50000):")
    await state.set_state(AddCardState.waiting_limit)

# Ключи
@router.callback_query(F.data == "admin_keys")
async def admin_keys(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    with get_db() as conn:
        keys = conn.execute("""
            SELECT subscription_type, total_activations, 
                   COUNT(*) as count_keys,
                   SUM(total_activations - used_activations) as remaining
            FROM keys 
            WHERE is_active = 1
            GROUP BY subscription_type, total_activations
        """).fetchall()
    
    if not keys:
        text = """
🔑 <b>Управление ключами</b>

❌ <b>Активные ключи не найдены</b>

➕ <b>Загрузите ключи</b> для начала работы с подписками
"""
    else:
        text = """
🔑 <b>Управление ключами</b>

📊 <b>Статистика ключей:</b>
"""
    for k in keys:
            sub = "📅 Месяц" if k["subscription_type"] == "month" else "🔓 Навсегда"
            text += f"""
• {sub} | {k['total_activations']} активаций
  📦 Всего: <code>{k['count_keys']}</code> шт
  ✅ Осталось: <code>{k['remaining']}</code> активаций
"""
    
    try:
        await callback.message.edit_text(
            text,
            parse_mode="HTML",
            reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📤 Загрузить ключи", callback_data="upload_keys")],
                [InlineKeyboardButton(text="👁 Просмотр всех ключей", callback_data="view_all_keys")],
                [InlineKeyboardButton(text="🗑 Управление ключами", callback_data="manage_keys")],
                [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
            ])
    )
    except Exception as e:
        await callback.answer("✅ Обновлено!")

@router.callback_query(F.data == "upload_keys")
async def upload_keys_start(callback: CallbackQuery, state: FSMContext):
    upload_text = """
📤 <b>Загрузка ключей</b>

📝 <b>Формат загрузки:</b>
<code>ключ активации тип</code>

💡 <b>Примеры:</b>
<code>ABC123-DEF456-GHI789 1 month
XYZ987-UVW654-RST321 3 forever
QWE456-RTY789-UIO123 4 month</code>

🎯 <b>Параметры:</b>
• <b>Активации:</b> 1, 3 или 4
• <b>Тип:</b> "month" или "forever"

⚠️ <b>Важно:</b>
• Каждый ключ с новой строки
• Разделитель - пробел между параметрами
• Тип: "month" для месячных, "forever" для пожизненных

<i>Отправьте список ключей ниже ⬇️</i>
"""
    
    upload_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад к ключам", callback_data="admin_keys")]
    ])
    
    await callback.message.edit_text(upload_text, parse_mode="HTML", reply_markup=upload_kb)
    await callback.message.answer("📤 Отправьте список ключей:")
    await state.set_state(UploadKeysState.waiting_keys)

@router.message(UploadKeysState.waiting_keys)
async def upload_keys_list(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    lines = [line.strip() for line in message.text.strip().split("\n") if line.strip()]
    
    if not lines:
        await message.answer("❌ Список ключей пуст. Попробуйте снова:")
        return
    
    # Парсим новый формат: ключ активации тип
    parsed_keys = []
    errors = []
    
    for i, line in enumerate(lines, 1):
        try:
            parts = line.strip().split()
            if len(parts) != 3:
                errors.append(f"Строка {i}: неверный формат (нужно: ключ активации тип)")
                continue
            
            key_value, activations_str, sub_type = parts
            
            # Проверяем активации
            try:
                activations = int(activations_str)
                if activations not in [1, 3, 4]:
                    errors.append(f"Строка {i}: активации должны быть 1, 3 или 4")
                    continue
            except ValueError:
                errors.append(f"Строка {i}: неверное количество активаций")
                continue
            
            # Проверяем тип подписки
            if sub_type not in ["month", "forever"]:
                errors.append(f"Строка {i}: неверный тип (используйте 'month' или 'forever')")
                continue
            
            parsed_keys.append({
                'key': key_value,
                'activations': activations,
                'sub_type': sub_type
            })
            
        except Exception as e:
            errors.append(f"Строка {i}: ошибка парсинга - {str(e)}")
            continue
    
    if not parsed_keys:
        error_text = "❌ <b>Не удалось распарсить ни одного ключа!</b>\n\n"
        for error in errors[:5]:  # Показываем первые 5 ошибок
            error_text += f"• {error}\n"
        if len(errors) > 5:
            error_text += f"• ... и еще {len(errors) - 5} ошибок\n"
        
        error_text += "\n🔄 <b>Попробуйте снова с правильным форматом</b>"
        await message.answer(error_text, parse_mode="HTML")
        return
    
    # Сохраняем данные
    await state.update_data(parsed_keys=parsed_keys, errors=errors)
    print(f"DEBUG: Сохранено {len(parsed_keys)} ключей в состоянии")
    
    # Показываем результат парсинга
    result_text = f"""
✅ <b>Ключи успешно распарсены!</b>

📊 <b>Результат:</b>
• ✅ <b>Обработано:</b> <code>{len(parsed_keys)}</code> ключей
• ❌ <b>Ошибок:</b> <code>{len(errors)}</code>

📋 <b>Статистика по типам:</b>
"""
    
    # Подсчитываем статистику
    month_count = sum(1 for k in parsed_keys if k['sub_type'] == 'month')
    forever_count = sum(1 for k in parsed_keys if k['sub_type'] == 'forever')
    
    result_text += f"• 📅 <b>Месячные:</b> <code>{month_count}</code> ключей\n"
    result_text += f"• 🔓 <b>Пожизненные:</b> <code>{forever_count}</code> ключей\n"
    
    if errors:
        result_text += f"\n⚠️ <b>Ошибки ({len(errors)}):</b>\n"
        for error in errors[:3]:  # Показываем первые 3 ошибки
            result_text += f"• {error}\n"
        if len(errors) > 3:
            result_text += f"• ... и еще {len(errors) - 3} ошибок\n"
    
    result_text += "\n🚀 <b>Готово к загрузке!</b>"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📤 Загрузить ключи", callback_data="upload_parsed_keys")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_keys")]
    ])
    
    await message.answer(result_text, parse_mode="HTML", reply_markup=kb)

# Загрузка распарсенных ключей
@router.callback_query(F.data == "upload_parsed_keys")
async def upload_parsed_keys(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    data = await state.get_data()
    parsed_keys = data.get('parsed_keys', [])
    print(f"DEBUG: Загрузка ключей - найдено {len(parsed_keys)} ключей")
    
    if not parsed_keys:
        await callback.answer("❌ Нет ключей для загрузки!", show_alert=True)
        return
    
    added = 0
    skipped = 0
    
    with get_db() as conn:
        for key_data in parsed_keys:
            try:
                conn.execute("""
                    INSERT INTO keys (key_value, subscription_type, total_activations)
                    VALUES (?, ?, ?)
                """, (key_data['key'], key_data['sub_type'], key_data['activations']))
                added += 1
            except:
                skipped += 1  # дубликат или ошибка
    
    # Статистика по типам
    month_count = sum(1 for k in parsed_keys if k['sub_type'] == 'month')
    forever_count = sum(1 for k in parsed_keys if k['sub_type'] == 'forever')
    
    result_text = f"""
✅ <b>Ключи успешно загружены!</b>

📊 <b>Результат загрузки:</b>
• 📤 <b>Обработано:</b> <code>{len(parsed_keys)}</code> ключей
• ✅ <b>Добавлено:</b> <code>{added}</code> ключей
• ❌ <b>Пропущено:</b> <code>{skipped}</code> ключей

📋 <b>По типам подписок:</b>
• 📅 <b>Месячные:</b> <code>{month_count}</code> ключей
• 🔓 <b>Пожизненные:</b> <code>{forever_count}</code> ключей

💡 <b>Пропущенные ключи</b> могли быть дубликатами
"""
    
    result_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Просмотр ключей", callback_data="view_all_keys")],
        [InlineKeyboardButton(text="⬅️ К управлению ключами", callback_data="admin_keys")]
    ])
    
    await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=result_kb)
    await state.clear()
    print(f"DEBUG: Состояние сброшено после загрузки ключей")

@router.callback_query(F.data.startswith("key_type:"))
async def upload_keys_type(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    current_state = await state.get_state()
    if current_state != UploadKeysState.waiting_type:
        await callback.answer("Неверное состояние", show_alert=True)
        return
    sub_type = callback.data.split(":")[1]
    await state.update_data(sub_type=sub_type)
    
    sub_name = "📅 Месячная" if sub_type == "month" else "🔓 Пожизненная"
    
    activations_text = f"""
⚙️ <b>Настройка активаций</b>

🎯 <b>Тип подписки:</b> {sub_name}

🔢 <b>Выберите количество активаций:</b>
• <b>1 активация</b> - для одного устройства
• <b>3 активации</b> - для нескольких устройств
• <b>4 активации</b> - максимальная гибкость

💡 <b>Рекомендация:</b>
• Для личного использования: <b>1 активация</b>
• Для семьи/друзей: <b>3-4 активации</b>

<i>Выберите количество активаций ниже ⬇️</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="1️⃣ 1 активация", callback_data="activations:1")],
        [InlineKeyboardButton(text="3️⃣ 3 активации", callback_data="activations:3")],
        [InlineKeyboardButton(text="4️⃣ 4 активации", callback_data="activations:4")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_keys")]
    ])
    
    await callback.message.edit_text(activations_text, parse_mode="HTML", reply_markup=kb)
    await state.set_state(UploadKeysState.waiting_activations)

@router.callback_query(F.data.startswith("activations:"))
async def upload_keys_activations(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    current_state = await state.get_state()
    if current_state != UploadKeysState.waiting_activations:
        await callback.answer("Неверное состояние", show_alert=True)
        return
    activations = int(callback.data.split(":")[1])
    data = await state.get_data()
    keys = data["keys"]
    sub_type = data["sub_type"]
    added = 0
    with get_db() as conn:
        for key in keys:
            try:
                conn.execute("""
                    INSERT INTO keys (key_value, subscription_type, total_activations)
                    VALUES (?, ?, ?)
                """, (key.strip(), sub_type, activations))
                added += 1
            except:
                pass  # дубликат
    sub_name = "📅 Месячная" if sub_type == "month" else "🔓 Пожизненная"
    
    result_text = f"""
✅ <b>Ключи успешно загружены!</b>

📊 <b>Результат загрузки:</b>
• 📤 <b>Обработано:</b> <code>{len(keys)}</code> ключей
• ✅ <b>Добавлено:</b> <code>{added}</code> ключей
• ❌ <b>Пропущено:</b> <code>{len(keys) - added}</code> ключей

🎯 <b>Настройки:</b>
• Тип: {sub_name}
• Активаций: <code>{activations}</code>
• Длительность: <code>{'30 дней' if sub_type == 'month' else 'бессрочно'}</code>

💡 <b>Пропущенные ключи</b> могли быть дубликатами или иметь неверный формат
"""
    
    result_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="👁 Просмотр ключей", callback_data="view_all_keys")],
        [InlineKeyboardButton(text="⬅️ К управлению ключами", callback_data="admin_keys")]
    ])
    
    await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=result_kb)
    await state.clear()

# Платежи
@router.callback_query(F.data == "admin_payments")
async def admin_payments(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    with get_db() as conn:
        # Показываем только платежи с чеками
        payments = conn.execute("""
            SELECT p.*, u.username, u.first_name, c.bank_name, c.last4
            FROM payments p
            LEFT JOIN users u ON p.user_id = u.user_id
            LEFT JOIN cards c ON p.card_id = c.card_id
            WHERE p.has_receipt = 1
            ORDER BY p.created_at DESC
            LIMIT 20
        """).fetchall()
    
    if not payments:
        text = """
📊 <b>Платежи</b>

❌ <b>Платежей с чеками не найдено</b>

💡 <b>Здесь отображаются только платежи, к которым пользователи приложили чеки</b>
"""
    else:
        text = f"""
📊 <b>Платежи с чеками</b>

📋 <b>Найдено платежей:</b> <code>{len(payments)}</code>

💡 <b>Выберите платеж для управления:</b>
"""
        
        kb = []
        for p in payments[:10]:  # Показываем первые 10 платежей
            status_emoji = "⏳" if p['status'] == 'pending' else "✅" if p['status'] == 'confirmed' else "❌"
            sub_emoji = "📅" if p['subscription_type'] == 'month' else "🔓"
            user_name = p['username'] or p['first_name'] or f"ID{p['user_id']}"
            button_text = f"{status_emoji} {sub_emoji} {p['amount']}₽ | {user_name[:15]}"
            kb.append([InlineKeyboardButton(text=button_text, callback_data=f"payment_detail:{p['payment_id']}")])
        
        kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")])
        
        await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))
        return

# Детальный просмотр платежа
@router.callback_query(F.data.startswith("payment_detail:"))
async def payment_detail(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    payment_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        payment = conn.execute("""
            SELECT p.*, u.username, u.first_name, u.last_name, c.bank_name, c.last4, c.card_number
            FROM payments p
            LEFT JOIN users u ON p.user_id = u.user_id
            LEFT JOIN cards c ON p.card_id = c.card_id
            WHERE p.payment_id = ?
        """, (payment_id,)).fetchone()
        
        if not payment:
            await callback.answer("❌ Платеж не найден!", show_alert=True)
            return
    
    # Форматируем информацию о платеже
    status_emoji = "⏳" if payment['status'] == 'pending' else "✅" if payment['status'] == 'confirmed' else "❌"
    status_text = "Ожидает" if payment['status'] == 'pending' else "Подтвержден" if payment['status'] == 'confirmed' else "Отклонен"
    sub_emoji = "📅" if payment['subscription_type'] == 'month' else "🔓"
    sub_text = "Месячная" if payment['subscription_type'] == 'month' else "Пожизненная"
    
    # Форматируем номер карты
    try:
        card_display = payment['card_number'] if payment['card_number'] else f"••••{payment['last4']}"
    except:
        card_display = f"••••{payment['last4']}"
    
    user_display = f"@{payment['username']}" if payment['username'] else f"{payment['first_name']} {payment['last_name']}" or f"ID{payment['user_id']}"
    
    detail_text = f"""
💳 <b>Детали платежа #{payment_id}</b>

👤 <b>Пользователь:</b> {user_display}
💰 <b>Сумма:</b> <code>{payment['amount']} ₽</code>
🏦 <b>Банк:</b> {payment['bank_name']}
💳 <b>Карта:</b> <code>{card_display}</code>
🎫 <b>Подписка:</b> {sub_emoji} {sub_text}
📊 <b>Статус:</b> {status_emoji} {status_text}

📅 <b>Дата создания:</b> {payment['created_at']}
"""
    
    if payment['confirmed_at']:
        detail_text += f"✅ <b>Дата подтверждения:</b> {payment['confirmed_at']}\n"
    
    if payment['issued_key']:
        detail_text += f"🔑 <b>Выданный ключ:</b> {payment['issued_key']}\n"
    
    detail_text += f"\n📎 <b>Чек:</b> {'✅ Загружен' if payment['pdf_file_id'] else '❌ Не загружен'}"
    
    # Создаем кнопки управления
    kb = []
    
    # Кнопки для просмотра чека
    if payment['pdf_file_id']:
        kb.append([InlineKeyboardButton(text="👁 Посмотреть чек", callback_data=f"view_pdf:{payment_id}")])
    
    # Кнопки управления статусом
    if payment['status'] == 'pending':
        kb.append([
            InlineKeyboardButton(text="✅ Подтвердить", callback_data=f"confirm:{payment_id}"),
            InlineKeyboardButton(text="❌ Отклонить", callback_data=f"reject:{payment_id}")
        ])
    elif payment['status'] == 'confirmed':
        kb.append([InlineKeyboardButton(text="❌ Отменить подтверждение", callback_data=f"unconfirm:{payment_id}")])
    elif payment['status'] == 'rejected':
        kb.append([InlineKeyboardButton(text="✅ Восстановить", callback_data=f"restore:{payment_id}")])
    
    # Кнопки навигации
    kb.append([InlineKeyboardButton(text="⬅️ К списку платежей", callback_data="admin_payments")])
    kb.append([InlineKeyboardButton(text="🏠 В админ панель", callback_data="admin_panel")])
    
    await callback.message.edit_text(detail_text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Отмена подтверждения платежа
@router.callback_query(F.data.startswith("unconfirm:"))
async def unconfirm_payment(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    payment_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        # Получаем информацию о платеже
        payment = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not payment:
            await callback.answer("❌ Платеж не найден!", show_alert=True)
            return
        
        # Отменяем подтверждение
        conn.execute("""
            UPDATE payments 
            SET status = 'pending', confirmed_at = NULL, issued_key = NULL
            WHERE payment_id = ?
        """, (payment_id,))
    
    await callback.answer("✅ Подтверждение отменено!")
    
    # Возвращаемся к деталям платежа
    await payment_detail(callback)

# Восстановление отклоненного платежа
@router.callback_query(F.data.startswith("restore:"))
async def restore_payment(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    payment_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        # Получаем информацию о платеже
        payment = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not payment:
            await callback.answer("❌ Платеж не найден!", show_alert=True)
            return
        
        # Восстанавливаем платеж
        conn.execute("""
            UPDATE payments 
            SET status = 'pending'
            WHERE payment_id = ?
        """, (payment_id,))
    
    await callback.answer("✅ Платеж восстановлен!")
    
    # Возвращаемся к деталям платежа
    await payment_detail(callback)

# Обработка чека и подтверждения
@router.callback_query(F.data.startswith("view_pdf:"))
async def view_pdf(callback: CallbackQuery):
    payment_id = int(callback.data.split(":")[1])
    with get_db() as conn:
        pdf = conn.execute("SELECT pdf_file_id FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    if pdf and pdf["pdf_file_id"]:
        await bot.send_document(callback.from_user.id, pdf["pdf_file_id"])
    else:
        await callback.answer("Чек не найден", show_alert=True)

@router.callback_query(F.data.startswith("confirm:"))
async def confirm_payment(callback: CallbackQuery):
    payment_id = int(callback.data.split(":")[1])
    with get_db() as conn:
        payment = conn.execute("SELECT * FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
        if not payment or payment["status"] != "pending":
            await callback.answer("Платёж уже обработан", show_alert=True)
            return

        # Обновить карту
        conn.execute("""
            UPDATE cards SET current_received = current_received + ?
            WHERE card_id = ?
        """, (payment["amount"], payment["card_id"]))

        # Выдать ключ
        key = get_available_key(payment["subscription_type"])
        issued_key = key["key_value"] if key else None

        # Проверяем, остались ли мало ключей для данного типа подписки
        if key:
            from bot_management.services import PaymentService
            PaymentService()._check_low_key_count(payment["subscription_type"])

        # Вычисляем дату окончания подписки
        from datetime import datetime, timedelta
        confirmed_at = datetime.now()
        subscription_expires_at = None

        if payment["subscription_type"] == "month":
            subscription_expires_at = confirmed_at + timedelta(days=30)
        elif payment["subscription_type"] == "3months":
            subscription_expires_at = confirmed_at + timedelta(days=90)
        elif payment["subscription_type"] == "year":
            subscription_expires_at = confirmed_at + timedelta(days=365)
        # Для "forever" оставляем None

        # Обновить платёж
        if subscription_expires_at:
            conn.execute("""
                UPDATE payments
                SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP, issued_key = ?, subscription_expires_at = ?
                WHERE payment_id = ?
            """, (issued_key, subscription_expires_at, payment_id))

            # Запускаем отложенные напоминания об истечении подписки
            import asyncio
            from bot_with_django import schedule_subscription_expiry_reminders
            asyncio.create_task(schedule_subscription_expiry_reminders(
                payment_id, payment["user_id"], payment["subscription_type"], subscription_expires_at
            ))
        else:
            conn.execute("""
                UPDATE payments
                SET status = 'confirmed', confirmed_at = CURRENT_TIMESTAMP, issued_key = ?
                WHERE payment_id = ?
            """, (issued_key, payment_id))

    # Отправить пользователю
    try:
        if key:
            sub_type_text = "📅 Месячная подписка" if payment['subscription_type'] == 'month' else "🔓 Пожизненная подписка"
            duration_text = "30 дней" if payment['subscription_type'] == 'month' else "бессрочно"
            
            await bot.send_message(
                payment["user_id"],
                f"""
🎉 <b>Платеж подтвержден!</b>

🎁 <b>Ваш ключ подписки:</b>
{key['key_value']}

📋 <b>Детали подписки:</b>
• Тип: {sub_type_text}
• Срок действия: {duration_text}

🔑 <b>Как активировать:</b>
1️⃣ Скопируйте ключ выше
2️⃣ Вставьте в соответствующее поле
3️⃣ Нажмите "Активировать"

<i>Спасибо за покупку! 🚀</i>
""",
                parse_mode="HTML"
            )
        else:
            await bot.send_message(payment["user_id"], """
❌ <b>Ключи временно закончились</b>

🔧 <b>Что происходит:</b>
• Ваш платеж подтвержден
• Ключи для вашего типа подписки закончились
• Администратор скоро пополнит склад

⏰ <b>Время ожидания:</b> обычно в течение 1-2 часов

<i>Мы свяжемся с вами, как только ключи появятся</i>
""", parse_mode="HTML")
    except:
        pass

    await callback.answer("✅ Платеж подтвержден!")
    
    # Возвращаемся к деталям платежа
    await payment_detail(callback)

@router.callback_query(F.data.startswith("reject:"))
async def reject_payment(callback: CallbackQuery):
    payment_id = int(callback.data.split(":")[1])
    with get_db() as conn:
        conn.execute("UPDATE payments SET status = 'rejected' WHERE payment_id = ?", (payment_id,))
    # Уведомить пользователя
    with get_db() as conn:
        payment = conn.execute("SELECT user_id FROM payments WHERE payment_id = ?", (payment_id,)).fetchone()
    if payment:
        try:
            await bot.send_message(payment["user_id"], """
❌ <b>Платеж отклонен</b>

🔍 <b>Возможные причины:</b>
• Неверная сумма перевода
• Неправильный получатель
• Некорректный формат чека
• Проблемы с качеством документа

🛠 <b>Что делать:</b>
1️⃣ Проверьте данные перевода
2️⃣ Убедитесь, что сумма точно совпадает
3️⃣ Свяжитесь с поддержкой через кнопку "🛠 Поддержка"
4️⃣ При необходимости сделайте новый перевод

<i>Мы поможем решить проблему! 💬</i>
""", parse_mode="HTML")
        except:
            pass
    await callback.answer("❌ Платеж отклонен!")
    
    # Возвращаемся к деталям платежа
    await payment_detail(callback)

# Обработчик кнопки "Назад" в админ панели
@router.callback_query(F.data == "admin_panel")
async def admin_panel_callback(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="💳 Карты", callback_data="admin_cards")],
        [InlineKeyboardButton(text="🔑 Ключи", callback_data="admin_keys")],
        [InlineKeyboardButton(text="💰 Платежи", callback_data="admin_payments")],
        [InlineKeyboardButton(text="📞 Поддержка", callback_data="admin_support")],
        [InlineKeyboardButton(text="📢 Рассылки", callback_data="admin_broadcast")]
    ])
    await callback.message.edit_text("Панель администратора:", reply_markup=kb)

# Обработчик поддержки в админ панели
@router.callback_query(F.data == "admin_support")
async def admin_support(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    with get_db() as conn:
        # Получаем статистику по тикетам
        stats = conn.execute("""
            SELECT 
                COUNT(*) as total_chats,
                SUM(CASE WHEN status = 'open' THEN 1 ELSE 0 END) as open_chats,
                SUM(CASE WHEN status = 'closed' THEN 1 ELSE 0 END) as closed_chats
            FROM support_chats
        """).fetchone()
        
        # Получаем активные чаты
        chats = conn.execute("""
            SELECT sc.*, u.username, u.first_name, u.last_name,
                   COUNT(sm.msg_id) as message_count
            FROM support_chats sc
            LEFT JOIN users u ON sc.user_id = u.user_id
            LEFT JOIN support_messages sm ON sc.chat_id = sm.chat_id
            WHERE sc.status = 'open'
            GROUP BY sc.chat_id
            ORDER BY sc.created_at DESC
        """).fetchall()
    
    if not chats:
        text = f"""
📞 <b>Система поддержки</b>

📊 <b>Статистика:</b>
• 🟢 <b>Активных:</b> <code>{stats['open_chats']}</code>
• 🔴 <b>Закрытых:</b> <code>{stats['closed_chats']}</code>
• 📈 <b>Всего:</b> <code>{stats['total_chats']}</code>

❌ <b>Нет активных тикетов</b>

💡 <b>Здесь отображаются открытые чаты поддержки</b>
"""
    else:
        text = f"""
📞 <b>Система поддержки</b>

📊 <b>Статистика:</b>
• 🟢 <b>Активных:</b> <code>{stats['open_chats']}</code>
• 🔴 <b>Закрытых:</b> <code>{stats['closed_chats']}</code>
• 📈 <b>Всего:</b> <code>{stats['total_chats']}</code>

🎫 <b>Активные тикеты:</b>
"""
        for chat in chats[:10]:  # первые 10
            user_info = f"@{chat['username']}" if chat['username'] else f"{chat['first_name']} {chat['last_name']}" or f"ID{chat['user_id']}"
            text += f"• <b>{user_info}</b> | Сообщений: <code>{chat['message_count']}</code>\n"
    
    kb = []
    for chat in chats[:10]:
        user_display = f"@{chat['username']}" if chat['username'] else f"ID{chat['user_id']}"
        kb.append([InlineKeyboardButton(text=f"💬 {user_display}", callback_data=f"support_chat:{chat['chat_id']}")])
    
    kb.append([InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")])
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Просмотр конкретного чата поддержки
@router.callback_query(F.data.startswith("support_chat:"))
async def view_support_chat(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    chat_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        # Получаем информацию о чате
        chat_info = conn.execute("""
            SELECT sc.*, u.username, u.first_name, u.last_name
            FROM support_chats sc
            LEFT JOIN users u ON sc.user_id = u.user_id
            WHERE sc.chat_id = ?
        """, (chat_id,)).fetchone()
        
        if not chat_info:
            await callback.answer("❌ Чат не найден!", show_alert=True)
            return
        
        # Получаем сообщения чата
        messages = conn.execute("""
            SELECT * FROM support_messages
            WHERE chat_id = ?
            ORDER BY sent_at ASC
        """, (chat_id,)).fetchall()
    
    user_display = f"@{chat_info['username']}" if chat_info['username'] else f"{chat_info['first_name']} {chat_info['last_name']}" or f"ID{chat_info['user_id']}"
    status_emoji = "🟢" if chat_info['status'] == 'open' else "🔴"
    
    text = f"""
💬 <b>Чат поддержки #{chat_id}</b>

👤 <b>Пользователь:</b> {user_display}
📊 <b>Статус:</b> {status_emoji} {chat_info['status'].upper()}
📅 <b>Создан:</b> {chat_info['created_at']}
💬 <b>Сообщений:</b> <code>{len(messages)}</code>

📝 <b>История сообщений:</b>
"""
    
    for msg in messages[-10:]:  # последние 10 сообщений
        sender_emoji = "👤" if msg['sender'] == 'user' else "👨‍💼"
        text += f"\n{sender_emoji} <b>{msg['sender'].upper()}:</b>\n{msg['text']}\n"
    
    if chat_info['status'] == 'open':
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🔴 Закрыть тикет", callback_data=f"close_chat:{chat_id}")],
            [InlineKeyboardButton(text="💬 Ответить", callback_data=f"reply_chat:{chat_id}")],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_support")]
        ])
    else:
        kb = InlineKeyboardMarkup(inline_keyboard=[
            [InlineKeyboardButton(text="🟢 Открыть тикет", callback_data=f"open_chat:{chat_id}")],
            [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_support")]
        ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

# Закрытие/открытие чата поддержки
@router.callback_query(F.data.startswith("close_chat:"))
async def close_support_chat(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    chat_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        conn.execute("UPDATE support_chats SET status = 'closed' WHERE chat_id = ?", (chat_id,))
    
    await callback.answer("✅ Тикет закрыт!")
    await callback.message.edit_text("✅ <b>Тикет успешно закрыт!</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_support")]
    ]))

@router.callback_query(F.data.startswith("open_chat:"))
async def open_support_chat(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    chat_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        conn.execute("UPDATE support_chats SET status = 'open' WHERE chat_id = ?", (chat_id,))
    
    await callback.answer("✅ Тикет открыт!")
    await callback.message.edit_text("✅ <b>Тикет успешно открыт!</b>", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ К списку", callback_data="admin_support")]
    ]))

# Система рассылок
@router.callback_query(F.data == "admin_broadcast")
async def admin_broadcast(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    with get_db() as conn:
        # Получаем статистику рассылок
        stats = conn.execute("""
            SELECT 
                COUNT(*) as total_broadcasts,
                SUM(CASE WHEN status = 'sent' THEN 1 ELSE 0 END) as sent_broadcasts,
                SUM(CASE WHEN status = 'pending' THEN 1 ELSE 0 END) as pending_broadcasts,
                SUM(sent_count) as total_sent_messages
            FROM broadcasts
        """).fetchone()
        
        # Получаем последние рассылки
        recent_broadcasts = conn.execute("""
            SELECT * FROM broadcasts
            ORDER BY created_at DESC
            LIMIT 5
        """).fetchall()
    
    text = f"""
📢 <b>Система рассылок</b>

📊 <b>Статистика:</b>
• 📤 <b>Всего рассылок:</b> <code>{stats['total_broadcasts']}</code>
• ✅ <b>Отправлено:</b> <code>{stats['sent_broadcasts']}</code>
• ⏳ <b>В ожидании:</b> <code>{stats['pending_broadcasts']}</code>
• 💬 <b>Сообщений отправлено:</b> <code>{stats['total_sent_messages']}</code>

📋 <b>Последние рассылки:</b>
"""
    
    for broadcast in recent_broadcasts:
        status_emoji = "✅" if broadcast['status'] == 'sent' else "⏳" if broadcast['status'] == 'pending' else "❌"
        text += f"• {status_emoji} <b>{broadcast['sent_count']}/{broadcast['total_count']}</b> | {broadcast['created_at'][:16]}\n"
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📝 Создать рассылку", callback_data="create_broadcast")],
        [InlineKeyboardButton(text="📊 Статистика", callback_data="broadcast_stats")],
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)

# Поддержка
@router.callback_query(F.data == "support")
async def support_start(callback: CallbackQuery, state: FSMContext):
    # Принудительно сбрасываем состояние перед началом поддержки
    await state.clear()
    print(f"DEBUG: Состояние сброшено перед началом поддержки для {callback.from_user.id}")
    
    chat_id = get_or_create_support_chat(callback.from_user.id)
    print(f"DEBUG: Создан/найден чат поддержки с ID: {chat_id}")
    await state.set_state(SupportState.in_chat)
    await state.set_data({"chat_id": chat_id})
    print(f"DEBUG: Установлено состояние поддержки для {callback.from_user.id}")
    
    support_text = """
🛠 <b>Служба поддержки</b>

💬 <b>Мы готовы помочь!</b>

📝 <b>Опишите вашу проблему:</b>
• Проблемы с оплатой
• Вопросы по подписке
• Технические неполадки
• Другие вопросы

⏰ <b>Время ответа:</b> обычно в течение 15-30 минут

<i>Напишите ваш вопрос ниже ⬇️</i>
"""
    
    support_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="❓ Частые вопросы", callback_data="faq")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(support_text, parse_mode="HTML", reply_markup=support_kb)

# Обработчики inline кнопок
@router.callback_query(F.data == "main_menu")
async def main_menu(callback: CallbackQuery):
    welcome_text = """
🎬 <b>Добро пожаловать в наш сервис!</b>

💎 <b>Доступные подписки:</b>
• 📅 <b>Месячная подписка</b> - 500 ₽
• 🔓 <b>Пожизненная подписка</b> - 3000 ₽

🛠 <b>Нужна помощь?</b> - нажмите кнопку "Поддержка"

<i>Выберите подходящий вариант ниже ⬇️</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Подписка на месяц", callback_data="sub_month")],
        [InlineKeyboardButton(text="🔓 Подписка навсегда", callback_data="sub_forever")],
        [InlineKeyboardButton(text="🛠 Поддержка", callback_data="support")],
        [InlineKeyboardButton(text="ℹ️ О сервисе", callback_data="about")]
    ])
    
    await callback.message.edit_text(welcome_text, parse_mode="HTML", reply_markup=kb)

@router.callback_query(F.data.startswith("renew_subscription:"))
async def renew_subscription(callback: CallbackQuery, state: FSMContext):
    """Обработчик кнопки 'Продлить подписку'"""
    try:
        payment_id = int(callback.data.split(":")[1])
        
        # Получаем информацию о платеже из базы данных
        with get_db() as conn:
            cursor = conn.cursor()
            cursor.execute("""
                SELECT subscription_type, amount, user_id
                FROM payments
                WHERE payment_id = ?
            """, (payment_id,))
            payment = cursor.fetchone()
        
        if not payment:
            await callback.answer("❌ Платеж не найден!", show_alert=True)
            return
        
        # Определяем тип подписки
        sub_type = payment['subscription_type']
        
        # Если это старая база данных без subscription_type, используем month по умолчанию
        if sub_type not in ['month', '3months', 'year', 'forever']:
            sub_type = 'month'
        
        # Перенаправляем на выбор подписки (используем тот же тип, что был раньше)
        await callback.answer("✅ Переходим к выбору подписки...")
        
        # Вызываем функцию выбора подписки
        if sub_type == 'month' or sub_type == 'forever':
            # Используем существующую логику выбора подписки
            callback.data = "sub_month" if sub_type == 'month' else "sub_forever"
            await choose_subscription(callback, state)
        else:
            # Для других типов показываем меню выбора
            welcome_text = """
🔄 <b>Продление подписки</b>

💎 <b>Выберите тип подписки:</b>
• 📅 <b>Месячная подписка</b> - 500 ₽
• 🔓 <b>Пожизненная подписка</b> - 3000 ₽

<i>Выберите подходящий вариант ниже ⬇️</i>
"""
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="📅 Подписка на месяц", callback_data="sub_month")],
                [InlineKeyboardButton(text="🔓 Подписка навсегда", callback_data="sub_forever")],
                [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
            ])
            await callback.message.edit_text(welcome_text, parse_mode="HTML", reply_markup=kb)
            
    except Exception as e:
        await callback.answer("❌ Ошибка при обработке запроса", show_alert=True)
        logging.error(f"Ошибка в renew_subscription: {e}")

@router.callback_query(F.data == "about")
async def about_service(callback: CallbackQuery):
    about_text = """
ℹ️ <b>О нашем сервисе</b>

🎯 <b>Что мы предлагаем:</b>
• Высококачественные подписки
• Быстрая обработка платежей
• Круглосуточная поддержка
• Безопасные платежи

💎 <b>Типы подписок:</b>
• 📅 <b>Месячная</b> - доступ на 30 дней
• 🔓 <b>Пожизненная</b> - доступ навсегда

🔒 <b>Безопасность:</b>
• Все платежи проверяются администратором
• Защищенные реквизиты
• Гарантия качества

<i>Выберите подходящую подписку и начните пользоваться сервисом!</i>
"""
    
    about_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📅 Подписка на месяц", callback_data="sub_month")],
        [InlineKeyboardButton(text="🔓 Подписка навсегда", callback_data="sub_forever")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(about_text, parse_mode="HTML", reply_markup=about_kb)

@router.callback_query(F.data == "payment_help")
async def payment_help(callback: CallbackQuery):
    help_text = """
📋 <b>Инструкция по оплате</b>

💳 <b>Как правильно оплатить:</b>

1️⃣ <b>Переведите точную сумму</b>
   • Сумма должна совпадать до копейки
   • Проверьте получателя

2️⃣ <b>Сохраните чек</b>
   • Скачайте PDF-чек из банковского приложения
   • Или попросите банк отправить на email

3️⃣ <b>Отправьте PDF боту</b>
   • НЕ скриншот экрана
   • НЕ фотографию чека
   • А именно PDF-документ

4️⃣ <b>Дождитесь подтверждения</b>
   • Администратор проверит чек
   • При подтверждении получите ключ

⚠️ <b>Важно:</b>
• При проблемах обращайтесь в поддержку
"""
    
    help_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(help_text, parse_mode="HTML", reply_markup=help_kb)

@router.callback_query(F.data == "faq")
async def faq(callback: CallbackQuery):
    faq_text = """
❓ <b>Часто задаваемые вопросы</b>

<b>Q: Как долго обрабатывается платеж?</b>
A: Обычно в течение 15-30 минут после отправки чека.

<b>Q: Что делать, если платеж отклонен?</b>
A: Проверьте сумму и получателя, свяжитесь с поддержкой.

<b>Q: Можно ли вернуть деньги?</b>
A: При технических проблемах - да, обращайтесь в поддержку.

<b>Q: Как активировать ключ?</b>
A: Скопируйте ключ и вставьте в соответствующее поле.

<b>Q: Сколько активаций у ключа?</b>
A: У каждого ключа 1 активация.

<b>Q: Что если ключи закончились?</b>
A: Мы пополним склад в течение 1-2 часов.
"""
    
    faq_kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="🛠 Задать вопрос", callback_data="support")],
        [InlineKeyboardButton(text="⬅️ Главное меню", callback_data="main_menu")]
    ])
    
    await callback.message.edit_text(faq_text, parse_mode="HTML", reply_markup=faq_kb)

# Просмотр всех ключей
@router.callback_query(F.data == "view_all_keys")
async def view_all_keys(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    with get_db() as conn:
        keys = conn.execute("""
            SELECT key_id, key_value, subscription_type, total_activations, 
                   used_activations, is_active, 
                   (total_activations - used_activations) as remaining
            FROM keys 
            ORDER BY subscription_type, total_activations DESC, key_id
        """).fetchall()
    
    if not keys:
        await callback.message.edit_text("""
🔑 <b>Просмотр ключей</b>

❌ <b>Ключи не найдены</b>

➕ <b>Загрузите ключи</b> для начала работы
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📤 Загрузить ключи", callback_data="upload_keys")],
    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_keys")]
]))
        return
    
    # Разбиваем ключи на страницы (по 10 на страницу)
    page = 0
    keys_per_page = 10
    total_pages = (len(keys) + keys_per_page - 1) // keys_per_page
    
    start_idx = page * keys_per_page
    end_idx = min(start_idx + keys_per_page, len(keys))
    page_keys = keys[start_idx:end_idx]
    
    text = f"""
🔑 <b>Просмотр всех ключей</b>

📊 <b>Всего ключей:</b> <code>{len(keys)}</code>
📄 <b>Страница:</b> <code>{page + 1} из {total_pages}</code>

"""
    
    for key in page_keys:
        status = "✅" if key["is_active"] else "❌"
        sub_icon = "📅" if key["subscription_type"] == "month" else "🔓"
        sub_name = "Месяц" if key["subscription_type"] == "month" else "Навсегда"
        
        text += f"""
🔹 <b>ID {key['key_id']}</b> {status}
   🔑 {key['key_value']}
   {sub_icon} {sub_name} | {key['used_activations']}/{key['total_activations']} активаций
   ⏳ Осталось: {key['remaining']}
"""
    
    kb = []
    if total_pages > 1:
        kb.append([
            InlineKeyboardButton(text="⬅️ Предыдущая", callback_data=f"keys_page:{max(0, page-1)}"),
            InlineKeyboardButton(text="➡️ Следующая", callback_data=f"keys_page:{min(total_pages-1, page+1)}")
        ])
    
    kb.extend([
        [InlineKeyboardButton(text="🗑 Управление ключами", callback_data="manage_keys")],
        [InlineKeyboardButton(text="⬅️ Назад к ключам", callback_data="admin_keys")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Управление ключами (удаление)
@router.callback_query(F.data == "manage_keys")
async def manage_keys(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    with get_db() as conn:
        keys = conn.execute("""
            SELECT key_id, key_value, subscription_type, total_activations, 
                   used_activations, is_active
            FROM keys 
            WHERE is_active = 1
            ORDER BY subscription_type, key_id
            LIMIT 20
        """).fetchall()
    
    if not keys:
        await callback.message.edit_text("""
🗑 <b>Управление ключами</b>

❌ <b>Активные ключи не найдены</b>

➕ <b>Загрузите ключи</b> для управления
""", parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=[
    [InlineKeyboardButton(text="📤 Загрузить ключи", callback_data="upload_keys")],
    [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_keys")]
]))
        return
    
    text = """
🗑 <b>Управление ключами</b>

⚠️ <b>Внимание:</b> Удаление ключей необратимо!

📋 <b>Выберите ключ для удаления:</b>

"""
    
    kb = []
    for key in keys:
        sub_icon = "📅" if key["subscription_type"] == "month" else "🔓"
        sub_name = "Месяц" if key["subscription_type"] == "month" else "Навсегда"
        
        text += f"• <b>ID {key['key_id']}</b> | {sub_icon} {sub_name} | {key['used_activations']}/{key['total_activations']} активаций\n"
        
        kb.append([InlineKeyboardButton(
            text=f"🗑 ID {key['key_id']} ({sub_name})", 
            callback_data=f"delete_key:{key['key_id']}"
        )])
    
    kb.append([InlineKeyboardButton(text="⬅️ Назад к ключам", callback_data="admin_keys")])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=InlineKeyboardMarkup(inline_keyboard=kb))

# Удаление ключа
@router.callback_query(F.data.startswith("delete_key:"))
async def delete_key(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    key_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        # Получаем информацию о ключе
        key = conn.execute("SELECT * FROM keys WHERE key_id = ?", (key_id,)).fetchone()
        if not key:
            await callback.answer("❌ Ключ не найден!", show_alert=True)
            return
        
        # Проверяем, есть ли активные платежи с этим ключом
        active_payments = conn.execute("""
            SELECT COUNT(*) as count FROM payments 
            WHERE issued_key = ? AND status = 'confirmed'
        """, (key["key_value"],)).fetchone()
        
        if active_payments["count"] > 0:
            # Показываем предупреждение с возможностью принудительного удаления
            sub_name = "📅 Месячная" if key["subscription_type"] == "month" else "🔓 Пожизненная"
            
            warning_text = f"""
⚠️ <b>Внимание!</b>

🔑 <b>Ключ:</b> {sub_name} | {key['total_activations']} активаций
💳 <b>Активных платежей:</b> <code>{active_payments['count']}</code>

❌ <b>Этот ключ используется в активных платежах!</b>

⚠️ <b>Принудительное удаление:</b>
• Удалит ключ из базы данных
• Платежи останутся, но ключ станет недоступен
• Пользователи не смогут использовать этот ключ

🤔 <b>Вы уверены, что хотите продолжить?</b>
"""
            
            kb = InlineKeyboardMarkup(inline_keyboard=[
                [InlineKeyboardButton(text="🗑 Принудительно удалить", callback_data=f"force_delete_key:{key_id}")],
                [InlineKeyboardButton(text="⬅️ Отменить", callback_data="manage_keys")]
            ])
            
            await callback.message.edit_text(warning_text, parse_mode="HTML", reply_markup=kb)
            return
        
        # Удаляем ключ
        conn.execute("DELETE FROM keys WHERE key_id = ?", (key_id,))
    
    sub_name = "📅 Месячная" if key["subscription_type"] == "month" else "🔓 Пожизненная"
    
    await callback.answer(f"✅ Ключ {sub_name} удален!")
    
    # Возвращаемся к управлению ключами
    await manage_keys(callback)

# Принудительное удаление ключа
@router.callback_query(F.data.startswith("force_delete_key:"))
async def force_delete_key(callback: CallbackQuery):
    if not is_admin(callback.from_user.id):
        return
    
    key_id = int(callback.data.split(":")[1])
    
    with get_db() as conn:
        # Получаем информацию о ключе
        key = conn.execute("SELECT * FROM keys WHERE key_id = ?", (key_id,)).fetchone()
        if not key:
            await callback.answer("❌ Ключ не найден!", show_alert=True)
            return
        
        # Принудительно удаляем ключ
        conn.execute("DELETE FROM keys WHERE key_id = ?", (key_id,))
    
    sub_name = "📅 Месячная" if key["subscription_type"] == "month" else "🔓 Пожизненная"
    
    await callback.answer(f"✅ Ключ {sub_name} принудительно удален!")
    
    # Возвращаемся к управлению ключами
    await manage_keys(callback)


# Обработчик ответа на поддержку
@router.callback_query(F.data.startswith("reply:"))
async def reply_support(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    chat_id = int(callback.data.split(":")[1])
    await state.set_data({"support_chat_id": chat_id})
    await callback.message.answer("Введите ответ пользователю:")
    await state.set_state(SupportState.in_chat)

@router.message(SupportState.in_chat)
async def support_admin_reply(message: Message, state: FSMContext):
    print(f"DEBUG: Получено сообщение в поддержку от {message.from_user.id}")
    print(f"DEBUG: Текущее состояние: {await state.get_state()}")
    
    data = await state.get_data()
    
    # Если это админ отвечает на тикет
    if is_admin(message.from_user.id) and "support_chat_id" in data:
        chat_id = data["support_chat_id"]
        with get_db() as conn:
            # Получаем user_id из chat_id
            user_data = conn.execute("SELECT user_id FROM support_chats WHERE chat_id = ?", (chat_id,)).fetchone()
            if user_data:
                user_id = user_data["user_id"]
                # Сохраняем сообщение админа
                conn.execute("""
                    INSERT INTO support_messages (chat_id, sender, text)
                    VALUES (?, 'admin', ?)
                """, (chat_id, message.text))
                # Отправляем пользователю
                try:
                    await bot.send_message(user_id, f"💬 Ответ от поддержки:\n{message.text}")
                    await message.answer("✅ Ответ отправлен пользователю!")
                except:
                    await message.answer("❌ Не удалось отправить ответ пользователю.")
        await state.clear()
    else:
        # Обычное сообщение пользователя в поддержку
        print(f"DEBUG: Обработка сообщения пользователя {message.from_user.id}")
        data = await state.get_data()
        print(f"DEBUG: Данные состояния: {data}")
        chat_id = data.get("chat_id")
        if not chat_id:
            print("DEBUG: chat_id не найден в состоянии!")
            await message.answer("❌ Ошибка: не удалось найти чат поддержки. Попробуйте начать заново.")
            await state.clear()
            return
        with get_db() as conn:
            conn.execute("""
                INSERT INTO support_messages (chat_id, sender, text)
                VALUES (?, 'user', ?)
            """, (chat_id, message.text))
        # Уведомить админов
        for admin_id in ADMIN_IDS:
            try:
                await bot.send_message(
                    admin_id,
                    f"📬 Новое сообщение от @{message.from_user.username or message.from_user.id}:\n{message.text}",
                    reply_markup=InlineKeyboardMarkup(inline_keyboard=[
                        [InlineKeyboardButton(text="Ответить", callback_data=f"reply:{chat_id}")]
                    ])
                )
            except:
                pass
        await message.answer("""
✅ <b>Сообщение отправлено!</b>

📨 <b>Ваш вопрос передан администратору</b>

⏰ <b>Ожидайте ответа в течение 15-30 минут</b>

💡 <b>Пока ждете:</b>
• Проверьте раздел "Часто задаваемые вопросы"
• Убедитесь, что проблема не решается самостоятельно

<i>Спасибо за обращение! 🙏</i>
""", parse_mode="HTML")
        print(f"DEBUG: Сообщение пользователя обработано, сбрасываем состояние")
        await state.clear()

# Обработчик для админских сообщений в поддержке
@router.message(lambda message: is_admin(message.from_user.id))
async def admin_support_message(message: Message, state: FSMContext):
    print(f"DEBUG: Админское сообщение от {message.from_user.id}: {message.text}")
    # Проверяем, есть ли активный чат поддержки
    data = await state.get_data()
    print(f"DEBUG: Данные состояния админа: {data}")
    if "support_chat_id" in data:
        chat_id = data["support_chat_id"]
        with get_db() as conn:
            # Получаем user_id из chat_id
            user_data = conn.execute("SELECT user_id FROM support_chats WHERE chat_id = ?", (chat_id,)).fetchone()
            if user_data:
                user_id = user_data["user_id"]
                # Сохраняем сообщение админа
                conn.execute("""
                    INSERT INTO support_messages (chat_id, sender, text)
                    VALUES (?, 'admin', ?)
                """, (chat_id, message.text))
                # Отправляем пользователю
                try:
                    await bot.send_message(user_id, f"💬 Ответ от поддержки:\n{message.text}")
                    await message.answer("✅ Ответ отправлен пользователю!")
                except:
                    await message.answer("❌ Не удалось отправить ответ пользователю.")
        await state.clear()

# Запуск
# Создание рассылки
@router.callback_query(F.data == "create_broadcast")
async def create_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    text = """
📝 <b>Создание рассылки</b>

💬 <b>Отправьте текст сообщения для рассылки:</b>

⚠️ <b>Важно:</b>
• Используйте HTML разметку для форматирования
• Сообщение будет отправлено всем пользователям бота
• Рассылка начнется сразу после подтверждения

<i>Отправьте текст сообщения ниже ⬇️</i>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="⬅️ Назад", callback_data="admin_broadcast")]
    ])
    
    await callback.message.edit_text(text, parse_mode="HTML", reply_markup=kb)
    await callback.message.answer("📝 Отправьте текст рассылки:")
    await state.set_state(BroadcastState.waiting_message)

# Обработка текста рассылки
@router.message(BroadcastState.waiting_message)
async def process_broadcast_message(message: Message, state: FSMContext):
    if not is_admin(message.from_user.id):
        return
    
    # Получаем количество пользователей
    with get_db() as conn:
        user_count = conn.execute("SELECT COUNT(*) as count FROM users").fetchone()['count']
    
    # Сохраняем сообщение
    await state.update_data(message_text=message.text, user_count=user_count)
    
    preview_text = f"""
📢 <b>Предварительный просмотр рассылки</b>

👥 <b>Получателей:</b> <code>{user_count}</code> пользователей

📝 <b>Текст сообщения:</b>
{message.text}

⚠️ <b>Внимание:</b> Рассылка начнется сразу после подтверждения!
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="✅ Отправить рассылку", callback_data="confirm_broadcast")],
        [InlineKeyboardButton(text="❌ Отменить", callback_data="admin_broadcast")]
    ])
    
    await message.answer(preview_text, parse_mode="HTML", reply_markup=kb)
    await state.set_state(BroadcastState.waiting_confirmation)

# Подтверждение рассылки
@router.callback_query(F.data == "confirm_broadcast")
async def confirm_broadcast(callback: CallbackQuery, state: FSMContext):
    if not is_admin(callback.from_user.id):
        return
    
    data = await state.get_data()
    message_text = data['message_text']
    user_count = data['user_count']
    
    # Создаем запись о рассылке
    with get_db() as conn:
        cursor = conn.cursor()
        cursor.execute("""
            INSERT INTO broadcasts (admin_id, message_text, total_count, status)
            VALUES (?, ?, ?, 'pending')
        """, (callback.from_user.id, message_text, user_count))
        broadcast_id = cursor.lastrowid
    
    # Отправляем рассылку
    sent_count = 0
    failed_count = 0
    
    with get_db() as conn:
        users = conn.execute("SELECT user_id FROM users").fetchall()
        
        for user in users:
            try:
                await bot.send_message(user['user_id'], message_text, parse_mode="HTML")
                sent_count += 1
                
                # Обновляем счетчик каждые 10 сообщений
                if sent_count % 10 == 0:
                    conn.execute("""
                        UPDATE broadcasts SET sent_count = ? WHERE broadcast_id = ?
                    """, (sent_count, broadcast_id))
                
            except Exception as e:
                failed_count += 1
                print(f"Ошибка отправки пользователю {user['user_id']}: {e}")
        
        # Обновляем статус рассылки
        conn.execute("""
            UPDATE broadcasts SET sent_count = ?, status = 'sent' WHERE broadcast_id = ?
        """, (sent_count, broadcast_id))
    
    result_text = f"""
✅ <b>Рассылка завершена!</b>

📊 <b>Результат:</b>
• 👥 <b>Всего получателей:</b> <code>{user_count}</code>
• ✅ <b>Отправлено:</b> <code>{sent_count}</code>
• ❌ <b>Ошибок:</b> <code>{failed_count}</code>
• 📈 <b>Успешность:</b> <code>{(sent_count/user_count*100):.1f}%</code>

💬 <b>Рассылка сохранена в истории</b>
"""
    
    kb = InlineKeyboardMarkup(inline_keyboard=[
        [InlineKeyboardButton(text="📊 К статистике", callback_data="admin_broadcast")],
        [InlineKeyboardButton(text="⬅️ В админ панель", callback_data="admin_panel")]
    ])
    
    await callback.message.edit_text(result_text, parse_mode="HTML", reply_markup=kb)
    await state.clear()

async def main():
    # Добавим админов в БД
    with get_db() as conn:
        for aid in ADMIN_IDS:
            conn.execute("INSERT OR IGNORE INTO admin_users (admin_id) VALUES (?)", (aid,))
    await dp.start_polling(bot)

if __name__ == "__main__":
    asyncio.run(main())