import sqlite3
from contextlib import contextmanager

DB_NAME = "tg_key_bot.db"

def init_db():
    # Оптимизация SQLite для лучшей производительности
    conn = sqlite3.connect(DB_NAME)
    # Включаем WAL режим для лучшей конкурентности
    conn.execute("PRAGMA journal_mode=WAL")
    # Увеличиваем размер кэша страниц
    conn.execute("PRAGMA cache_size=-64000")  # 64MB кэша
    # Включаем синхронизацию только при критических операциях
    conn.execute("PRAGMA synchronous=NORMAL")
    # Увеличиваем размер страницы
    conn.execute("PRAGMA page_size=4096")
    # Включаем оптимизацию запросов
    conn.execute("PRAGMA optimize")
    cursor = conn.cursor()

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS users (
        user_id INTEGER PRIMARY KEY,
        username TEXT,
        first_name TEXT,
        last_name TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS cards (
        card_id INTEGER PRIMARY KEY AUTOINCREMENT,
        card_number TEXT NOT NULL,
        last4 TEXT NOT NULL,
        bank_name TEXT NOT NULL,
        max_limit INTEGER NOT NULL,
        current_received INTEGER DEFAULT 0,
        is_active BOOLEAN DEFAULT 1
    )""")
    
    # Добавляем колонку card_number если она не существует (для существующих БД)
    try:
        cursor.execute("ALTER TABLE cards ADD COLUMN card_number TEXT")
    except:
        pass  # Колонка уже существует

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS keys (
        key_id INTEGER PRIMARY KEY AUTOINCREMENT,
        key_value TEXT UNIQUE NOT NULL,
        subscription_type TEXT NOT NULL CHECK(subscription_type IN ('month', '3months', 'year')),
        total_activations INTEGER NOT NULL CHECK(total_activations IN (1, 2, 3, 4, 5)),
        used_activations INTEGER DEFAULT 0,
        is_active BOOLEAN DEFAULT 1
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS payments (
        payment_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        card_id INTEGER,
        amount INTEGER NOT NULL,
        pdf_file_id TEXT,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'confirmed', 'rejected')),
        subscription_type TEXT NOT NULL,
        issued_key TEXT,
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        confirmed_at TIMESTAMP
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS support_chats (
        chat_id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER NOT NULL,
        status TEXT DEFAULT 'open' CHECK(status IN ('open', 'closed')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        ticket_number TEXT UNIQUE,
        unread_admin_messages INTEGER DEFAULT 0,
        unread_user_messages INTEGER DEFAULT 0
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS support_messages (
        msg_id INTEGER PRIMARY KEY AUTOINCREMENT,
        chat_id INTEGER NOT NULL,
        sender TEXT NOT NULL CHECK(sender IN ('user', 'admin')),
        text TEXT NOT NULL,
        sent_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
        is_read BOOLEAN DEFAULT 0
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS admin_users (
        admin_id INTEGER PRIMARY KEY,
        name TEXT,
        is_active BOOLEAN DEFAULT 1
    )""")

    cursor.execute("""
    CREATE TABLE IF NOT EXISTS broadcasts (
        broadcast_id INTEGER PRIMARY KEY AUTOINCREMENT,
        admin_id INTEGER NOT NULL,
        message_text TEXT NOT NULL,
        sent_count INTEGER DEFAULT 0,
        total_count INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending' CHECK(status IN ('pending', 'sent', 'failed')),
        created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
    )""")

    # Добавляем колонку has_receipt для платежей
    try:
        cursor.execute("ALTER TABLE payments ADD COLUMN has_receipt BOOLEAN DEFAULT 0")
    except:
        pass  # Колонка уже существует
    
    # Добавляем новые поля для support_chats
    try:
        cursor.execute("ALTER TABLE support_chats ADD COLUMN ticket_number TEXT UNIQUE")
    except:
        pass  # Колонка уже существует
    
    try:
        cursor.execute("ALTER TABLE support_chats ADD COLUMN unread_admin_messages INTEGER DEFAULT 0")
    except:
        pass  # Колонка уже существует
    
    try:
        cursor.execute("ALTER TABLE support_chats ADD COLUMN unread_user_messages INTEGER DEFAULT 0")
    except:
        pass  # Колонка уже существует
    
    # Добавляем поле is_read для support_messages
    try:
        cursor.execute("ALTER TABLE support_messages ADD COLUMN is_read BOOLEAN DEFAULT 0")
    except:
        pass  # Колонка уже существует
    
    # Добавляем поле photo_file_id для support_messages
    try:
        cursor.execute("ALTER TABLE support_messages ADD COLUMN photo_file_id TEXT")
    except:
        pass  # Колонка уже существует
    
    # Добавляем поле subscription_expires_at для payments
    try:
        cursor.execute("ALTER TABLE payments ADD COLUMN subscription_expires_at TIMESTAMP")
    except:
        pass  # Колонка уже существует

    conn.commit()
    conn.close()

@contextmanager
def get_db():
    # Оптимизация SQLite для лучшей производительности
    conn = sqlite3.connect(DB_NAME, isolation_level=None)
    conn.row_factory = sqlite3.Row
    # Включаем WAL режим для лучшей конкурентности
    conn.execute("PRAGMA journal_mode=WAL")
    # Увеличиваем размер кэша страниц
    conn.execute("PRAGMA cache_size=-64000")  # 64MB кэша
    # Включаем синхронизацию только при критических операциях
    conn.execute("PRAGMA synchronous=NORMAL")
    # Включаем оптимизацию запросов
    conn.execute("PRAGMA optimize")
    try:
        yield conn
    finally:
        conn.close()