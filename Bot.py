#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Shop Bot для автозапчастей
Версия: 16.0.8 - FULLY DEBUGGED AND STABLE
"""

import os
import re
import sqlite3
import random
import string
import ast
import logging
import warnings
import shutil
from datetime import datetime
from functools import wraps
from typing import Dict, Optional, List, Tuple, Any
from collections import defaultdict
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, ConversationHandler, filters
)
from telegram.warnings import PTBUserWarning

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

# ========== КОНСТАНТЫ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_ID = int(os.environ.get("MANAGER_ID", 804070528))

# Настройки доставки
DELIVERY_BASE = 500
DELIVERY_RATE_UP_TO_50 = 25
DELIVERY_RATE_UP_TO_100 = 35
DELIVERY_RATE_OVER_100 = 50

# Настройки бонусов
MAX_BONUS_SPEND_PERCENT = 20
MIN_ORDER_FOR_BONUS = 500
MIN_CASH_PAYMENT = 100
RESTRICTED_BRANDS = ['ravenol', 'равенол', 'raven0l', 'ravenol ', 'ravenol-', 'ravenol_']

# Настройки базы данных
DATA_DIR = os.getenv('DATA_DIR', '/app/data')
DB_PATH = os.path.join(DATA_DIR, 'shop_bot.db')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

# Rate limiting
RATE_LIMIT_SECONDS = 2
user_last_command = defaultdict(datetime)

# Глобальное хранилище для выбора запчастей
user_selections = {}

# Глобальное хранилище для сессий админа при удалении товаров
admin_remove_sessions = {}

# Глобальное хранилище для сессий клиента при удалении товаров
client_remove_sessions = {}

# Безопасные колонки для UPDATE
ALLOWED_ORDER_COLUMNS = {
    'phone', 'our_cost', 'tracking_number', 'final_order', 
    'total_price', 'status', 'status_text', 'delivery_type',
    'delivery_price', 'distance', 'city', 'delivery_address', 'selected_products',
    'style_city', 'style_highway'
}

# Статусы заказов
STATUS_TRANSITIONS = {
    'pending': ['waiting_selection', 'cancelled'],
    'waiting_selection': ['waiting_payment', 'cancelled'],
    'waiting_payment': ['paid', 'cancelled', 'cancelled_by_user'],
    'paid': ['ordered', 'cancelled', 'refunded'],
    'ordered': ['arrived', 'cancelled'],
    'arrived': ['ready', 'cancelled'],
    'ready': ['shipped', 'issued', 'cancelled'],
    'shipped': ['delivered', 'cancelled'],
    'delivered': ['issued', 'cancelled'],
    'issued': ['cancelled'],
    'cancelled': [],
    'cancelled_by_user': [],
    'refunded': []
}

STATUS_TEXT_MAP = {
    'pending': '🆕 Ожидает подбора',
    'waiting_selection': '🟡 Ожидает выбора запчастей',
    'waiting_payment': '💰 Ожидает оплаты',
    'paid': '✅ Оплачен',
    'ordered': '📦 Заказан у поставщика',
    'arrived': '📦✅ Товар поступил',
    'ready': '✅ Готов к выдаче',
    'shipped': '🚚 Отправлен',
    'delivered': '🏠 Доставлен',
    'issued': '📋 Выдан',
    'cancelled': '❌ Отменён менеджером',
    'cancelled_by_user': '❌ Отменён пользователем',
    'refunded': '🔄 Возврат'
}

# Узлы, для которых нужна информация об оси
AXLE_REQUIRED_NODES = ["🔧 Подвеска", "🛑 Тормозная система", "🛞 Рулевое управление"]

# Иконки для статусов
STATUS_ICONS = {
    'Ожидает подбора': '🆕', 'Ожидает выбора': '🟡', 'Ожидает оплаты': '💰',
    'Оплачен': '✅', 'Заказан': '📦', 'Товар поступил': '📦✅',
    'Готов к выдаче': '✅', 'Отправлен': '🚚', 'Доставлен': '🏠',
    'Выдан': '📋', 'Отменён': '❌', 'Возврат': '🔄'
}

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ========== СОСТОЯНИЯ ДЛЯ CONVERSATIONHANDLER ==========
class OrderStates:
    VIN, MILEAGE, STYLE_CITY, STYLE_HIGHWAY, DELIVERY_TYPE, \
    ADDRESS, PHONE, PART_NODE, AXLE, PARTS, CONFIRM = range(11)

class GarageStates:
    VIN, DESCRIPTION = range(20, 22)

class BonusStates:
    SPEND = 30

class SaveStates:
    COMMENT = 40

class RemoveStates:
    COMMENT = 50

class AdminAddItemStates:
    NAME = 60
    PRICE = 61

class AdminChangePriceStates:
    NEW_PRICE = 70

# ========== КЛАВИАТУРЫ ==========
main_menu = ReplyKeyboardMarkup([
    ["🛒 Новый заказ", "🚗 Мой гараж"],
    ["📦 Мои заказы", "🎁 Бонусы"],
    ["🔗 Рефералы", "🚚 Доставка"],
    ["ℹ️ Помощь"]
], resize_keyboard=True)

city_style_kb = ReplyKeyboardMarkup([
    ["Спокойный (до 60 км/ч)"], ["Умеренный (60-90 км/ч)"],
    ["Активный (90-120 км/ч)"], ["Спортивный (120+ км/ч)"]
], resize_keyboard=True)

highway_style_kb = ReplyKeyboardMarkup([
    ["Спокойный (80-100 км/ч)"], ["Умеренный (100-120 км/ч)"],
    ["Активный (120-140 км/ч)"], ["Спортивный (140+ км/ч)"]
], resize_keyboard=True)

delivery_type_kb = ReplyKeyboardMarkup([
    ["Курьером"], ["Самовывоз"], ["Сторонняя фирма"]
], resize_keyboard=True)

part_node_kb = ReplyKeyboardMarkup([
    ["🔧 Двигатель", "🔩 Подвеска"],
    ["🛑 Тормозная система", "⚙️ Трансмиссия (КПП)"],
    ["🔋 Электрика", "❄️ Охлаждение"],
    ["🌡️ Отопление", "💨 Выхлопная система"],
    ["🛞 Рулевое управление", "📦 Другое"]
], resize_keyboard=True)

axle_kb = ReplyKeyboardMarkup([
    ["🔧 Передняя ось"], ["🔧 Задняя ось"], ["🔧 Передняя + Задняя"]
], resize_keyboard=True)

confirm_order_kb = ReplyKeyboardMarkup([
    ["✅ Готово", "✏️ Редактировать"]
], resize_keyboard=True)

# ========== КНОПКИ САМОВЫВОЗА ==========
pickup_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("📍 Метро Давыдково", callback_data="pickup_davydkovo")],
    [InlineKeyboardButton("📍 Метро Южная", callback_data="pickup_yuzhnaya")],
    [InlineKeyboardButton("📍 Метро Строгино", callback_data="pickup_strogino")]
])

# ========== БЕЗОПАСНЫЕ ФУНКЦИИ ==========

def safe_len(obj: Any) -> int:
    """Безопасное получение длины объекта"""
    if obj is None:
        return 0
    try:
        return len(obj)
    except (TypeError, AttributeError):
        return 0

def safe_get(data: Dict, key: str, default: Any = None) -> Any:
    """Безопасное получение значения из словаря"""
    if data is None or not isinstance(data, dict):
        return default
    return data.get(key, default)

def safe_list(data: Any, default: List = None) -> List:
    """Безопасное преобразование в список"""
    if default is None:
        default = []
    if data is None:
        return default
    if isinstance(data, list):
        return data
    return default

def safe_dict(data: Any, default: Dict = None) -> Dict:
    """Безопасное преобразование в словарь"""
    if default is None:
        default = {}
    if data is None:
        return default
    if isinstance(data, dict):
        return data
    return default

def check_rate_limit(user_id: int) -> bool:
    """Проверка rate limiting"""
    if user_id is None:
        return True
    now = datetime.now()
    if user_id in user_last_command:
        if (now - user_last_command[user_id]).seconds < RATE_LIMIT_SECONDS:
            return False
    user_last_command[user_id] = now
    return True

def safe_int(val: Any, default: int = 0) -> int:
    """Безопасное преобразование в int"""
    if val is None:
        return default
    if isinstance(val, bool):
        return default
    try:
        if isinstance(val, str):
            val = val.strip()
            if not val:
                return default
        return int(float(val))
    except (ValueError, TypeError):
        return default

def safe_str(val: Any, default: str = '') -> str:
    """Безопасное преобразование в str"""
    if val is None:
        return default
    return str(val)

def clean_order_number(order_num: str) -> str:
    """Безопасная очистка номера заказа"""
    if not order_num:
        return ""
    order_num = safe_str(order_num)
    # Ищем RVN-XXXXXX формат
    match = re.search(r'RVN-[A-Z0-9]{6}', order_num)
    if match:
        return match.group(0)
    
    cleaned = re.sub(r'[^A-Za-z0-9-]', '', order_num)
    if re.match(r'^RVN-[A-Z0-9]{6}$', cleaned):
        return cleaned
    logger.warning(f"Invalid order number format: {order_num}")
    return ""

def validate_vin(vin: str) -> bool:
    """Полная валидация VIN номера"""
    if not vin:
        return False
    vin = safe_str(vin).upper().strip()
    if safe_len(vin) != 17:
        return False
    if re.search(r'[IOQ]', vin):
        return False
    if not vin.isalnum():
        return False
    return True

def is_ravenol_product(product_name: str) -> bool:
    """Проверка, является ли продукт Ravenol"""
    if not product_name:
        return False
    product_lower = safe_str(product_name).lower()
    for brand in RESTRICTED_BRANDS:
        if brand in product_lower:
            return True
    return False

def wrap_text(text: str, max_length: int = 25) -> str:
    """Перенос текста по словам"""
    if not text:
        return ""
    if safe_len(text) <= max_length:
        return text
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        if safe_len(current_line) + safe_len(word) + 1 <= max_length:
            if current_line:
                current_line += " " + word
            else:
                current_line = word
        else:
            if current_line:
                lines.append(current_line)
            current_line = word
    if current_line:
        lines.append(current_line)
    return "\n   ".join(lines)

def get_status_icon(status_text: str) -> str:
    """Получение иконки для статуса"""
    if not status_text:
        return '📦'
    status_text = safe_str(status_text)
    for key, icon in STATUS_ICONS.items():
        if key in status_text:
            return icon
    return '📦'

def safe_get_user_data(ctx: ContextTypes.DEFAULT_TYPE, key: str, default: Any = None) -> Any:
    """Безопасное получение данных из user_data"""
    try:
        if ctx and hasattr(ctx, 'user_data') and ctx.user_data:
            return ctx.user_data.get(key, default)
        return default
    except Exception as e:
        logger.error(f"Error getting user_data[{key}]: {e}")
        return default

def safe_set_user_data(ctx: ContextTypes.DEFAULT_TYPE, key: str, value: Any) -> None:
    """Безопасная установка данных в user_data"""
    try:
        if ctx and hasattr(ctx, 'user_data'):
            if ctx.user_data is None:
                ctx.user_data = {}
            ctx.user_data[key] = value
    except Exception as e:
        logger.error(f"Error setting user_data[{key}]: {e}")

def safe_del_user_data(ctx: ContextTypes.DEFAULT_TYPE, key: str) -> None:
    """Безопасное удаление данных из user_data"""
    try:
        if ctx and hasattr(ctx, 'user_data') and ctx.user_data and key in ctx.user_data:
            del ctx.user_data[key]
    except Exception as e:
        logger.error(f"Error deleting user_data[{key}]: {e}")

def safe_clear_user_data(ctx: ContextTypes.DEFAULT_TYPE) -> None:
    """Безопасная очистка user_data"""
    try:
        if ctx and hasattr(ctx, 'user_data') and ctx.user_data:
            ctx.user_data.clear()
    except Exception as e:
        logger.error(f"Error clearing user_data: {e}")

# ========== БАЗА ДАННЫХ ==========

def init_db():
    """Инициализация базы данных с индексами"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('''CREATE TABLE IF NOT EXISTS orders (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT UNIQUE,
            user_id INTEGER, user_name TEXT, phone TEXT, vin TEXT, mileage TEXT,
            style_city TEXT, style_highway TEXT, city TEXT,
            distance INTEGER DEFAULT 0, delivery_type TEXT,
            delivery_price INTEGER DEFAULT 500, delivery_address TEXT,
            part_node TEXT, axle TEXT, needed_parts TEXT,
            selected_products TEXT, final_order TEXT, status TEXT,
            status_text TEXT, tracking_number TEXT,
            total_price INTEGER DEFAULT 0, our_cost INTEGER DEFAULT 0,
            created_at TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS garage (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, vin TEXT, description TEXT, comment TEXT, created_at TEXT,
            UNIQUE(user_id, vin)
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS bonuses (
            user_id INTEGER PRIMARY KEY,
            balance INTEGER DEFAULT 0, total_earned INTEGER DEFAULT 0,
            total_spent INTEGER DEFAULT 0, referrer_id INTEGER DEFAULT NULL
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS bonus_history (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER, order_number TEXT, amount INTEGER,
            type TEXT, description TEXT, created_at TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS referrals (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            referrer_id INTEGER, referred_id INTEGER, created_at TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS feedback (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT, user_id INTEGER, rating INTEGER,
            comment TEXT, created_at TEXT
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS order_changes (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT,
            user_id INTEGER,
            action TEXT,
            old_value TEXT,
            new_value TEXT,
            comment TEXT,
            created_at TEXT
        )''')
        
        # Добавление недостающих колонок
        columns_to_add = ['phone', 'our_cost', 'tracking_number', 'final_order', 'comment', 'selected_products', 'style_city', 'style_highway']
        for col in columns_to_add:
            try:
                c.execute(f'ALTER TABLE orders ADD COLUMN {col} TEXT')
                logger.info(f"Added column {col} to orders")
            except sqlite3.OperationalError:
                pass
        
        try:
            c.execute('ALTER TABLE garage ADD COLUMN comment TEXT')
        except sqlite3.OperationalError:
            pass
        
        # Создание индексов
        indexes = [
            'CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)',
            'CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at)',
            'CREATE INDEX IF NOT EXISTS idx_bonus_history_user_id ON bonus_history(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_bonus_history_order_number ON bonus_history(order_number)',
            'CREATE INDEX IF NOT EXISTS idx_bonus_history_created_at ON bonus_history(created_at)',
            'CREATE INDEX IF NOT EXISTS idx_garage_user_id ON garage(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number)',
            'CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at)',
            'CREATE INDEX IF NOT EXISTS idx_order_changes_order_number ON order_changes(order_number)'
        ]
        
        for idx in indexes:
            try:
                c.execute(idx)
            except Exception as e:
                logger.error(f"Error creating index: {e}")
        
        conn.commit()
        logger.info(f"Database initialized: {DB_PATH}")
    except Exception as e:
        logger.error(f"Database init error: {e}")
    finally:
        if conn:
            conn.close()

def backup_db():
    """Создание резервной копии базы данных"""
    try:
        if os.path.exists(DB_PATH):
            backup_name = f"shop_bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            backup_path = os.path.join(BACKUP_DIR, backup_name)
            shutil.copy2(DB_PATH, backup_path)
            
            # Удаляем старые бэкапы (старше 7 дней)
            for f in os.listdir(BACKUP_DIR):
                f_path = os.path.join(BACKUP_DIR, f)
                if os.path.isfile(f_path):
                    if datetime.now().timestamp() - os.path.getmtime(f_path) > 7 * 24 * 3600:
                        os.remove(f_path)
            
            logger.info(f"Backup created: {backup_name}")
    except Exception as e:
        logger.error(f"Backup error: {e}")

# ========== ОПЕРАЦИИ С БАЗОЙ ДАННЫХ ==========

def generate_order_number() -> str:
    """Генерирует уникальный номер заказа"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        conn.execute('BEGIN IMMEDIATE')
        c = conn.cursor()
        
        for _ in range(10):
            num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
            try:
                c.execute('INSERT INTO orders (order_number) VALUES (?)', (num,))
                conn.commit()
                return num
            except sqlite3.IntegrityError:
                continue
        
        # Fallback с timestamp
        num = f"RVN-{datetime.now().strftime('%Y%m%d%H%M%S')}"
        try:
            c.execute('INSERT INTO orders (order_number) VALUES (?)', (num,))
            conn.commit()
            return num
        except sqlite3.IntegrityError:
            num = f"RVN-{int(datetime.now().timestamp())}"
            c.execute('INSERT INTO orders (order_number) VALUES (?)', (num,))
            conn.commit()
            return num
    except Exception as e:
        logger.error(f"Generate order number error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def update_order(order_number: str, **kwargs) -> bool:
    """Безопасное обновление заказа"""
    order_number = clean_order_number(order_number)
    if not order_number:
        return False
    
    safe_kwargs = {k: v for k, v in kwargs.items() if k in ALLOWED_ORDER_COLUMNS and v is not None}
    if not safe_kwargs:
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        # Проверка статуса
        if 'status' in safe_kwargs:
            c.execute('SELECT status FROM orders WHERE order_number = ?', (order_number,))
            row = c.fetchone()
            if row:
                current_status = row[0] if row[0] else 'pending'
                new_status = safe_kwargs['status']
                allowed = STATUS_TRANSITIONS.get(current_status, [])
                if new_status not in allowed:
                    logger.warning(f"Invalid status transition: {current_status} -> {new_status}")
                    return False
        
        # Обновление полей
        for key, val in safe_kwargs.items():
            c.execute(f"UPDATE orders SET {key} = ? WHERE order_number = ?", (val, order_number))
        
        # Обновление текста статуса
        if 'status' in safe_kwargs and 'status_text' not in safe_kwargs:
            new_status_text = STATUS_TEXT_MAP.get(safe_kwargs['status'], 'Неизвестно')
            c.execute("UPDATE orders SET status_text = ? WHERE order_number = ?", 
                     (new_status_text, order_number))
        
        conn.commit()
        logger.info(f"Order {order_number} updated: {safe_kwargs}")
        return True
    except Exception as e:
        logger.error(f"Update order error: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def get_order(order_number: str) -> Optional[Dict]:
    """Получение заказа по номеру"""
    order_number = clean_order_number(order_number)
    if not order_number:
        return None
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        
        c.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,))
        row = c.fetchone()
        
        if not row:
            return None
        
        # Получаем колонки
        c.execute('PRAGMA table_info(orders)')
        columns = [col[1] for col in c.fetchall()]
        
        order = {}
        for i, col in enumerate(columns):
            if i < safe_len(row):
                order[col] = row[i]
            else:
                order[col] = None
        
        # Преобразование числовых полей
        for num_field in ['distance', 'delivery_price', 'total_price', 'our_cost', 'user_id']:
            order[num_field] = safe_int(order.get(num_field))
        
        return order
    except Exception as e:
        logger.error(f"Get order error: {e}")
        return None
    finally:
        if conn:
            conn.close()

def save_order(data: Dict) -> Optional[str]:
    """Сохранение нового заказа"""
    if not data:
        return None
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        num = generate_order_number()
        if not num:
            return None
        
        c.execute('''INSERT INTO orders (
            order_number, user_id, user_name, phone, vin, mileage,
            style_city, style_highway, city, distance,
            delivery_type, delivery_price, delivery_address,
            part_node, axle, needed_parts,
            status, status_text, created_at, total_price
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (num, 
             safe_int(data.get('user_id')),
             safe_str(data.get('user_name')),
             safe_str(data.get('phone')),
             safe_str(data.get('vin')),
             safe_str(data.get('mileage')),
             safe_str(data.get('style_city')),
             safe_str(data.get('style_highway')),
             safe_str(data.get('city')),
             safe_int(data.get('distance')),
             safe_str(data.get('delivery_type')),
             safe_int(data.get('delivery_price', 500)),
             safe_str(data.get('delivery_address')),
             safe_str(data.get('part_node')),
             safe_str(data.get('axle')),
             safe_str(data.get('needed_parts')),
             'pending',
             '🆕 Ожидает подбора',
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             0))
        
        conn.commit()
        logger.info(f"New order saved: {num}")
        return num
    except Exception as e:
        logger.error(f"Save order error: {e}")
        if conn:
            conn.rollback()
        return None
    finally:
        if conn:
            conn.close()

def get_user_orders(user_id: int) -> List[Tuple]:
    """Получение заказов пользователя"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT order_number, status_text, created_at, total_price, 
                            delivery_price, final_order, needed_parts 
                     FROM orders WHERE user_id = ? ORDER BY id DESC''', (user_id,))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Get user orders error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_all_orders() -> List[Tuple]:
    """Получение всех заказов"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT order_number, user_name, status_text, created_at FROM orders ORDER BY id DESC')
        return c.fetchall()
    except Exception as e:
        logger.error(f"Get all orders error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def get_order_changes(order_number: str, limit: int = 20) -> List[Tuple]:
    """Получение истории изменений заказа"""
    order_number = clean_order_number(order_number)
    if not order_number:
        return []
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''SELECT action, old_value, new_value, comment, created_at 
                     FROM order_changes 
                     WHERE order_number = ? 
                     ORDER BY created_at DESC 
                     LIMIT ?''', (order_number, limit))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Get order changes error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def add_order_change(order_number: str, user_id: int, action: str, 
                     old_value: str = '', new_value: str = '', comment: str = '') -> bool:
    """Добавление записи в историю изменений"""
    order_number = clean_order_number(order_number)
    if not order_number:
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''INSERT INTO order_changes (order_number, user_id, action, old_value, new_value, comment, created_at)
                     VALUES (?,?,?,?,?,?,?)''',
                  (order_number, user_id, action, safe_str(old_value), 
                   safe_str(new_value), safe_str(comment), 
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Add order change error: {e}")
        return False
    finally:
        if conn:
            conn.close()

# ========== БОНУСЫ ==========

def get_bonus(user_id: int) -> Dict:
    """Получение информации о бонусах"""
    if user_id is None:
        return {'balance': 0, 'total_earned': 0, 'total_spent': 0}
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT balance, total_earned, total_spent FROM bonuses WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        if r:
            return {
                'balance': safe_int(r[0]),
                'total_earned': safe_int(r[1]),
                'total_spent': safe_int(r[2])
            }
        return {'balance': 0, 'total_earned': 0, 'total_spent': 0}
    except Exception as e:
        logger.error(f"Get bonus error: {e}")
        return {'balance': 0, 'total_earned': 0, 'total_spent': 0}
    finally:
        if conn:
            conn.close()

def add_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    """Начисление бонусов (только после оплаты)"""
    if amount <= 0:
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        
        c.execute('''INSERT INTO bonuses (user_id, balance, total_earned) 
                     VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET 
                     balance = balance + ?, total_earned = total_earned + ?''',
                  (user_id, amount, amount, amount, amount))
        
        c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (user_id, safe_str(order_num), amount, 'earned', safe_str(desc), 
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        logger.info(f"Bonus added: user={user_id}, amount={amount}, order={order_num}")
        return True
    except Exception as e:
        logger.error(f"Add bonus error: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def use_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    """Списание бонусов"""
    if amount <= 0:
        return False
    
    if not order_num:
        order_num = "welcome"
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        
        c.execute('SELECT balance FROM bonuses WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        
        if not row:
            c.execute('INSERT INTO bonuses (user_id, balance) VALUES (?, 0)', (user_id,))
            balance = 0
        else:
            balance = row[0]
        
        if balance < amount:
            conn.rollback()
            return False
        
        c.execute('UPDATE bonuses SET balance = balance - ?, total_spent = total_spent + ? WHERE user_id = ?',
                  (amount, amount, user_id))
        
        c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (user_id, safe_str(order_num), amount, 'spent', safe_str(desc),
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        logger.info(f"Bonus used: user={user_id}, amount={amount}, order={order_num}")
        return True
    except Exception as e:
        logger.error(f"Use bonus error: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def refund_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    """Возврат бонусов"""
    if amount <= 0:
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        
        c.execute('SELECT balance FROM bonuses WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        
        if not row:
            conn.rollback()
            return False
        
        if row[0] < amount:
            conn.rollback()
            return False
        
        c.execute('UPDATE bonuses SET balance = balance - ?, total_earned = total_earned - ? WHERE user_id = ?',
                  (amount, amount, user_id))
        
        c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (user_id, safe_str(order_num), amount, 'refund', safe_str(desc),
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        logger.info(f"Bonus refunded: user={user_id}, amount={amount}, order={order_num}")
        return True
    except Exception as e:
        logger.error(f"Refund bonus error: {e}")
        if conn:
            conn.rollback()
        return False
    finally:
        if conn:
            conn.close()

def get_user_total(user_id: int) -> int:
    """Общая сумма покупок пользователя"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE user_id = ? AND status != "pending"', (user_id,))
        r = c.fetchone()
        return safe_int(r[0])
    except Exception as e:
        logger.error(f"Get user total error: {e}")
        return 0
    finally:
        if conn:
            conn.close()

def get_bonus_percent(user_id: int) -> int:
    """Процент начисления бонусов"""
    total = get_user_total(user_id)
    if total >= 900000: return 10
    if total >= 800000: return 9
    if total >= 700000: return 8
    if total >= 600000: return 7
    if total >= 500000: return 6
    if total >= 400000: return 5
    if total >= 300000: return 4
    if total >= 200000: return 3
    if total >= 100000: return 2
    return 1

def get_bonus_history(user_id: int, limit: int = 50) -> List[Tuple]:
    """Получение истории бонусов"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('''
            SELECT order_number, amount, type, description, created_at
            FROM bonus_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (user_id, limit))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Get bonus history error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def calculate_bonus_eligible_sum(order: Dict) -> int:
    """Рассчитывает сумму, с которой можно списать бонусы"""
    if not order:
        return 0
    
    total_eligible = 0
    final_order = safe_str(order.get('final_order', ''))
    
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        try:
            selected_parts = ast.literal_eval(final_order)
            if isinstance(selected_parts, list):
                for part in selected_parts:
                    if isinstance(part, dict):
                        part_name = safe_str(part.get('name', ''))
                        part_price = safe_int(part.get('price', 0))
                        
                        if not is_ravenol_product(part_name) and part_price > 0:
                            total_eligible += part_price
        except (ValueError, SyntaxError, MemoryError) as e:
            logger.error(f"Error parsing final_order: {e}")
    
    return total_eligible

def has_ravenol_only(order: Dict) -> Tuple[bool, bool, int, int]:
    """Проверяет, есть ли в заказе Ravenol"""
    ravenol_sum = 0
    other_sum = 0
    has_ravenol = False
    has_other = False
    
    if not order:
        return has_ravenol, has_other, ravenol_sum, other_sum
    
    final_order = safe_str(order.get('final_order', ''))
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        try:
            selected_parts = ast.literal_eval(final_order)
            if isinstance(selected_parts, list):
                for part in selected_parts:
                    if isinstance(part, dict):
                        part_name = safe_str(part.get('name', ''))
                        part_price = safe_int(part.get('price', 0))
                        
                        if is_ravenol_product(part_name):
                            ravenol_sum += part_price
                            has_ravenol = True
                        else:
                            other_sum += part_price
                            has_other = True
        except Exception as e:
            logger.error(f"Error parsing for ravenol: {e}")
    
    return has_ravenol, has_other, ravenol_sum, other_sum

def get_bonus_spend_details(order: Dict) -> str:
    """Возвращает детали для отображения пользователю"""
    if not order:
        return "❌ Заказ не найден"
    
    eligible_sum = calculate_bonus_eligible_sum(order)
    delivery_price = safe_int(order.get('delivery_price', 0))
    total_parts = safe_int(order.get('total_price', 0))
    total_sum = total_parts + delivery_price
    has_ravenol, has_other, ravenol_sum, other_sum = has_ravenol_only(order)
    
    details = f"💰 Детали заказа:\n"
    details += f"• Сумма запчастей: {total_parts} руб.\n"
    
    if ravenol_sum > 0:
        details += f"• Из них Ravenol: {ravenol_sum} руб. (❌ бонусы не начисляются)\n"
    if other_sum > 0:
        details += f"• Сумма для бонусов: {other_sum} руб.\n"
    
    details += f"• Доставка: {delivery_price} руб. (❌ бонусы не списываются)\n"
    details += f"• Итого к оплате: {total_sum} руб.\n\n"
    
    if eligible_sum > 0:
        max_bonus = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100)
        details += f"🎁 Максимум списания бонусами: {max_bonus} руб. ({MAX_BONUS_SPEND_PERCENT}% от {eligible_sum} руб.)\n"
    else:
        details += f"❌ Нет товаров для списания бонусов\n"
    
    return details

# ========== РАСЧЁТЫ ==========

def calc_delivery_price(km: int) -> int:
    """Расчёт стоимости доставки"""
    km = safe_int(km)
    if km <= 0:
        return DELIVERY_BASE
    if km <= 50:
        return DELIVERY_BASE + km * DELIVERY_RATE_UP_TO_50
    if km <= 100:
        return DELIVERY_BASE + km * DELIVERY_RATE_UP_TO_100
    return DELIVERY_BASE + km * DELIVERY_RATE_OVER_100

def extract_city_from_address(address: str) -> str:
    """Определение города из адреса"""
    if not address:
        return 'Москва'
    address_lower = address.lower()
    cities = {
        'химки': 'Химки', 'мытищи': 'Мытищи', 'люберцы': 'Люберцы',
        'красногорск': 'Красногорск', 'одинцово': 'Одинцово',
        'подольск': 'Подольск', 'балашиха': 'Балашиха'
    }
    for key, city in cities.items():
        if key in address_lower:
            return city
    return 'Москва'

def extract_distance_from_address(address: str) -> int:
    """Определение расстояния от МКАД"""
    if not address:
        return 30
    address_lower = address.lower()
    distances = {
        'химки': 5, 'мытищи': 8, 'люберцы': 10,
        'красногорск': 7, 'одинцово': 10, 'подольск': 25, 'балашиха': 15
    }
    for key, dist in distances.items():
        if key in address_lower:
            return dist
    return 0 if ('москва' in address_lower or 'мск' in address_lower) else 30

def delivery_discount(order_sum: int) -> int:
    """Скидка на доставку от суммы заказа"""
    order_sum = safe_int(order_sum)
    if order_sum < 10000:
        return 0
    steps = (order_sum - 10000) // 5000
    return min(100, 5 + steps * 5)

def parse_products(text: str) -> List[Dict]:
    """Парсинг запчастей из текста"""
    if not text:
        return []
    products = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        
        # Ищем цену в строке
        match = re.search(r'(\d{1,3}(?:[\s\.]?\d{3})*)\s*(?:руб|₽|р\.|рублей)', line, re.I)
        if not match:
            continue
        
        price_str = match.group(1).replace(' ', '').replace('.', '')
        try:
            price = float(price_str)
            if price <= 0 or price > 1_000_000:
                continue
        except (ValueError, OverflowError):
            continue
        
        # Извлекаем название
        name = line[:match.start()].strip()
        name = re.sub(r'^[=\-•–—]+|[=\-•–—]+$', '', name).strip()
        name = re.sub(r'арт\.?\s*\S+', '', name).strip()
        name = name[:40] + ".." if safe_len(name) > 40 else name
        
        if name and price > 0:
            products.append({'name': name, 'price': int(price)})
    
    return products

# ========== ГАРАЖ ==========

def save_car(user_id: int, vin: str, description: str, comment: str = "") -> bool:
    """Сохранение автомобиля в гараж"""
    if not vin:
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT 1 FROM garage WHERE user_id = ? AND vin = ?', (user_id, safe_str(vin)))
        if c.fetchone():
            return False
        
        c.execute('INSERT INTO garage (user_id, vin, description, comment, created_at) VALUES (?,?,?,?,?)',
                  (user_id, safe_str(vin), safe_str(description), safe_str(comment), 
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        logger.info(f"Car saved: user={user_id}, vin={vin}")
        return True
    except Exception as e:
        logger.error(f"Save car error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_cars(user_id: int) -> List[Tuple]:
    """Получение списка автомобилей пользователя"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT vin, description, comment, created_at FROM garage WHERE user_id = ? ORDER BY id DESC', (user_id,))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Get cars error: {e}")
        return []
    finally:
        if conn:
            conn.close()

def delete_car(user_id: int, vin: str) -> bool:
    """Удаление автомобиля из гаража"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM garage WHERE user_id = ? AND vin = ?', (user_id, safe_str(vin)))
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logger.error(f"Delete car error: {e}")
        return False
    finally:
        if conn:
            conn.close()

def update_car_comment(user_id: int, vin: str, comment: str) -> bool:
    """Обновление комментария к автомобилю"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('UPDATE garage SET comment = ? WHERE user_id = ? AND vin = ?', 
                  (safe_str(comment), user_id, safe_str(vin)))
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logger.error(f"Update car comment error: {e}")
        return False
    finally:
        if conn:
            conn.close()

# ========== ДЕКОРАТОРЫ ==========

def require_manager(func):
    """Декоратор для ограничения доступа только менеджеру"""
    @wraps(func)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            if not upd or not upd.effective_user:
                return
            if upd.effective_user.id != MANAGER_ID:
                if upd.message:
                    await upd.message.reply_text("⛔ Доступ запрещён")
                return
            return await func(upd, ctx, *args, **kwargs)
        except Exception as e:
            logger.error(f"require_manager error in {func.__name__}: {e}")
            return
    return wrapper

def require_order_owner(func):
    """Декоратор для проверки владельца заказа"""
    @wraps(func)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            if not upd or not upd.callback_query:
                return await func(upd, ctx, *args, **kwargs)
            
            query = upd.callback_query
            await query.answer()
            
            data = safe_str(query.data)
            
            # Ищем номер заказа в callback_data
            order_num = None
            # Ищем RVN-XXXXXX формат
            match = re.search(r'RVN-[A-Z0-9]{6}', data)
            if match:
                order_num = match.group(0)
            else:
                # Пробуем найти просто 6-символьный код
                parts = data.split('_')
                for part in parts:
                    if part and re.match(r'^[A-Z0-9]{6}$', part):
                        order_num = f"RVN-{part}"
                        break
            
            if not order_num:
                await query.edit_message_text("❌ Ошибка: заказ не найден")
                return
            
            order = get_order(order_num)
            if not order:
                await query.edit_message_text("❌ Заказ не найден")
                return
            
            if order.get('user_id') != query.from_user.id:
                await query.answer("❌ Это не ваш заказ!", show_alert=True)
                return
            
            safe_set_user_data(ctx, 'current_order', order)
            return await func(upd, ctx, *args, **kwargs)
        except Exception as e:
            logger.error(f"require_order_owner error in {func.__name__}: {e}")
            return
    return wrapper

def rate_limit(func):
    """Декоратор для ограничения частоты запросов"""
    @wraps(func)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            if not upd or not upd.effective_user:
                return
            if not check_rate_limit(upd.effective_user.id):
                if upd.message:
                    await upd.message.reply_text("⏳ Слишком часто! Подождите пару секунд.")
                return
            return await func(upd, ctx, *args, **kwargs)
        except Exception as e:
            logger.error(f"rate_limit error in {func.__name__}: {e}")
            return
    return wrapper

def safe_async(func):
    """Декоратор для безопасного выполнения асинхронных функций"""
    @wraps(func)
    async def wrapper(*args, **kwargs):
        try:
            return await func(*args, **kwargs)
        except Exception as e:
            logger.error(f"Error in {func.__name__}: {e}")
            # Пытаемся получить update для отправки сообщения
            for arg in args:
                if isinstance(arg, Update) and arg.effective_message:
                    try:
                        await arg.effective_message.reply_text(
                            "⚠️ Произошла ошибка. Пожалуйста, попробуйте позже."
                        )
                    except:
                        pass
                    break
            return None
    return wrapper

# ========== ОСНОВНЫЕ КОМАНДЫ ==========

@safe_async
async def start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    if not upd or not upd.effective_user:
        return
    
    # Обработка реферальной ссылки
    if ctx.args and safe_len(ctx.args) > 0 and ctx.args[0].startswith('ref_'):
        ref_id = safe_int(ctx.args[0][4:])
        if ref_id and ref_id != upd.effective_user.id:
            conn = None
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?,?,?)',
                          (ref_id, upd.effective_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                c.execute('INSERT INTO bonuses (user_id, referrer_id) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET referrer_id = ?',
                          (upd.effective_user.id, ref_id, ref_id))
                conn.commit()
                add_bonus(upd.effective_user.id, None, 500, "Приветственные бонусы по реферальной ссылке")
                await ctx.bot.send_message(ref_id, f"👋 {upd.effective_user.full_name} перешёл по вашей реферальной ссылке и получил 500 бонусов!")
                await upd.message.reply_text("🎉 +500 бонусов за регистрацию по реферальной ссылке!")
            except Exception as e:
                logger.error(f"Referral error: {e}")
            finally:
                if conn:
                    conn.close()
    
    text = ("🏎️ Добро пожаловать в магазин автозапчастей!\n\n"
            "Что я умею:\n"
            "🛒 Новый заказ - подбор запчастей по вашему автомобилю\n"
            "🚗 Мой гараж - храните VIN и описание автомобилей\n"
            "📦 Мои заказы - история ваших заказов\n"
            "🎁 Бонусы - накапливайте бонусы от покупок\n"
            "🔗 Рефералы - приглашайте друзей и получайте бонусы\n"
            "🚚 Доставка - расчёт стоимости доставки\n\n"
            "Нажмите 🛒 Новый заказ, чтобы начать!")
    
    await upd.message.reply_text(text, reply_markup=main_menu)

# ========== НОВЫЙ ЗАКАЗ ==========

@safe_async
async def new_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Начало оформления заказа"""
    if not upd or not upd.effective_user:
        return ConversationHandler.END
    
    try:
        cars = get_cars(upd.effective_user.id)
        if cars and safe_len(cars) > 0:
            keyboard = [[InlineKeyboardButton("🆕 Ввести VIN вручную", callback_data="order_manual")]]
            for car in cars:
                if car and safe_len(car) > 0:
                    vin = safe_str(car[0])
                    description = safe_str(car[1]) if safe_len(car) > 1 else ""
                    comment = safe_str(car[2]) if safe_len(car) > 2 else ""
                    desc_short = description[:20] if description else ""
                    display_text = f"🚗 {vin}"
                    if desc_short:
                        display_text += f" ({desc_short})"
                    if comment:
                        display_text += f" [{comment[:15]}]"
                    keyboard.append([InlineKeyboardButton(display_text, callback_data=f"order_auto_{vin}")])
            
            await upd.message.reply_text(
                "🔧 ВЫБЕРИТЕ АВТОМОБИЛЬ из гаража или введите VIN вручную:",
                reply_markup=InlineKeyboardMarkup(keyboard)
            )
            return OrderStates.VIN
    except Exception as e:
        logger.error(f"New order error: {e}")
    
    await upd.message.reply_text("🔧 Отправьте VIN номер (17 символов):")
    return OrderStates.VIN

@safe_async
async def order_auto_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора авто из гаража"""
    query = upd.callback_query
    await query.answer()
    
    if query.data == "order_manual":
        await query.edit_message_text("🔧 Отправьте VIN номер (17 символов):")
        return OrderStates.VIN
    
    if query.data.startswith("order_auto_"):
        vin = query.data[11:]
        safe_set_user_data(ctx, 'vin', vin)
        await query.edit_message_text(f"🚗 Выбран автомобиль: {vin}\n\n📊 Теперь введите пробег (км):")
        return OrderStates.MILEAGE
    
    return OrderStates.VIN

@rate_limit
@safe_async
async def get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение VIN номера"""
    if not upd or not upd.message:
        return OrderStates.VIN
    
    vin = upd.message.text.upper().strip()
    
    if not validate_vin(vin):
        await upd.message.reply_text("❌ Неверный VIN\n\nVIN должен содержать 17 символов (только буквы и цифры, без I, O, Q).\nПопробуйте ещё раз:")
        return OrderStates.VIN
    
    safe_set_user_data(ctx, 'vin', vin)
    await upd.message.reply_text("📊 Введите пробег (км):")
    return OrderStates.MILEAGE

@rate_limit
@safe_async
async def get_mileage(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение пробега"""
    if not upd or not upd.message:
        return OrderStates.MILEAGE
    
    try:
        mileage = int(upd.message.text)
        if mileage < 0:
            raise ValueError
        safe_set_user_data(ctx, 'mileage', str(mileage))
    except ValueError:
        await upd.message.reply_text("❌ Пожалуйста, введите число (пробег в км):")
        return OrderStates.MILEAGE
    
    await upd.message.reply_text("🏙️ Стиль вождения в городе:", reply_markup=city_style_kb)
    return OrderStates.STYLE_CITY

@safe_async
async def get_style_city(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение стиля вождения в городе"""
    if not upd or not upd.message:
        return OrderStates.STYLE_CITY
    
    safe_set_user_data(ctx, 'style_city', upd.message.text)
    await upd.message.reply_text("🛣️ Стиль вождения на трассе:", reply_markup=highway_style_kb)
    return OrderStates.STYLE_HIGHWAY

@safe_async
async def get_style_highway(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение стиля вождения на трассе"""
    if not upd or not upd.message:
        return OrderStates.STYLE_HIGHWAY
    
    safe_set_user_data(ctx, 'style_highway', upd.message.text)
    await upd.message.reply_text("🚚 Способ доставки:", reply_markup=delivery_type_kb)
    return OrderStates.DELIVERY_TYPE

@safe_async
async def get_delivery_type(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение типа доставки"""
    if not upd or not upd.message:
        return OrderStates.DELIVERY_TYPE
    
    choice = upd.message.text
    safe_set_user_data(ctx, 'delivery_type', choice)
    
    if choice == "Курьером":
        await upd.message.reply_text("📍 Введите ПОЛНЫЙ АДРЕС доставки\n\nПример: г. Москва, ул. Тверская, д. 15, кв. 78")
        return OrderStates.ADDRESS
    elif choice == "Самовывоз":
        safe_set_user_data(ctx, 'delivery_price', 0)
        await upd.message.reply_text(
            "📍 САМОВЫВОЗ\n\n"
            "Доступные пункты выдачи:\n"
            "1️⃣ Метро Давыдково\n"
            "2️⃣ Метро Южная\n"
            "3️⃣ Метро Строгино\n\n"
            "Выберите пункт выдачи:",
            reply_markup=pickup_keyboard
        )
        return OrderStates.ADDRESS
    else:
        safe_set_user_data(ctx, 'delivery_price', 0)
        await upd.message.reply_text("🚛 Сторонняя фирма (стоимость рассчитает менеджер)\n\n📍 Введите адрес доставки:")
        return OrderStates.ADDRESS

@safe_async
async def pickup_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора пункта самовывоза"""
    query = upd.callback_query
    await query.answer()
    
    station_map = {
        "pickup_davydkovo": "Метро Давыдково",
        "pickup_yuzhnaya": "Метро Южная",
        "pickup_strogino": "Метро Строгино"
    }
    
    station = station_map.get(query.data, "Метро")
    safe_set_user_data(ctx, 'delivery_address', station)
    safe_set_user_data(ctx, 'city', "Москва")
    safe_set_user_data(ctx, 'distance', 0)
    safe_set_user_data(ctx, 'delivery_price', 0)
    
    await query.edit_message_text(
        f"📍 Пункт самовывоза: {station}\n"
        f"🏙️ Город: Москва\n"
        f"🚚 Доставка: 0 руб.\n\n"
        f"📞 Введите ваш контактный телефон:"
    )
    return OrderStates.PHONE

@rate_limit
@safe_async
async def get_address(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение адреса доставки"""
    if not upd or not upd.message:
        return OrderStates.ADDRESS
    
    full_address = upd.message.text.strip()
    if len(full_address) < 5:
        await upd.message.reply_text("❌ Введите полный адрес (минимум 5 символов):")
        return OrderStates.ADDRESS
    
    safe_set_user_data(ctx, 'delivery_address', full_address)
    
    if safe_get_user_data(ctx, 'delivery_type') == "Самовывоз":
        city = safe_get_user_data(ctx, 'city', "Москва")
        distance = safe_get_user_data(ctx, 'distance', 0)
        price = safe_get_user_data(ctx, 'delivery_price', 0)
        
        await upd.message.reply_text(
            f"📍 Пункт самовывоза: {full_address}\n"
            f"🏙️ Город: {city}\n"
            f"🚚 Доставка: {price} руб.\n\n"
            f"📞 Введите ваш контактный телефон:"
        )
        return OrderStates.PHONE
    
    city = extract_city_from_address(full_address)
    distance = extract_distance_from_address(full_address)
    safe_set_user_data(ctx, 'city', city)
    safe_set_user_data(ctx, 'distance', distance)
    
    if safe_get_user_data(ctx, 'delivery_type') == "Курьером":
        price = calc_delivery_price(distance)
        safe_set_user_data(ctx, 'delivery_price', price)
        await upd.message.reply_text(
            f"📍 Адрес: {full_address}\n"
            f"🏙️ Город: {city}\n"
            f"📏 Расстояние от МКАД: {distance} км\n"
            f"🚚 Стоимость доставки: {price} руб.\n\n"
            f"📞 Введите ваш контактный телефон:"
        )
    else:
        await upd.message.reply_text(f"📍 Адрес: {full_address}\n\n📞 Введите ваш контактный телефон:")
    
    return OrderStates.PHONE

@rate_limit
@safe_async
async def get_phone(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение номера телефона"""
    if not upd or not upd.message:
        return OrderStates.PHONE
    
    phone = upd.message.text.strip()
    if len(phone) < 5:
        await upd.message.reply_text("❌ Пожалуйста, введите корректный номер телефона:")
        return OrderStates.PHONE
    
    safe_set_user_data(ctx, 'phone', phone)
    await upd.message.reply_text("🔧 Выберите узел запчасти:", reply_markup=part_node_kb)
    return OrderStates.PART_NODE

@safe_async
async def get_part_node(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение узла запчасти"""
    if not upd or not upd.message:
        return OrderStates.PART_NODE
    
    safe_set_user_data(ctx, 'part_node', upd.message.text)
    
    if upd.message.text in AXLE_REQUIRED_NODES:
        await upd.message.reply_text("🔧 Выберите ось:", reply_markup=axle_kb)
        return OrderStates.AXLE
    else:
        safe_set_user_data(ctx, 'axle', "Не требуется")
        await upd.message.reply_text(
            "🔧 Какие запчасти нужны? (каждая с новой строки)\n\n"
            "Пример:\n"
            "Колодки тормозные\n"
            "Диски тормозные"
        )
        return OrderStates.PARTS

@safe_async
async def get_axle(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение оси"""
    if not upd or not upd.message:
        return OrderStates.AXLE
    
    safe_set_user_data(ctx, 'axle', upd.message.text)
    await upd.message.reply_text(
        "🔧 Какие запчасти нужны? (каждая с новой строки)\n\n"
        "Пример:\n"
        "Колодки тормозные передние\n"
        "Диски тормозные задние"
    )
    return OrderStates.PARTS

@rate_limit
@safe_async
async def get_parts(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение списка запчастей"""
    if not upd or not upd.message:
        return OrderStates.PARTS
    
    if not upd.message.text.strip():
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Продолжить без запчастей", callback_data="continue_order")],
            [InlineKeyboardButton("❌ Отменить заказ", callback_data="cancel_order")]
        ])
        await upd.message.reply_text(
            "❌ Вы не ввели запчасти.\n\n"
            "Хотите продолжить оформление заказа или отменить его?",
            reply_markup=kb
        )
        return OrderStates.PARTS
    
    safe_set_user_data(ctx, 'needed_parts', upd.message.text)
    
    data = ctx.user_data if ctx.user_data else {}
    delivery_price = safe_int(data.get('delivery_price', 500))
    
    summary = (f"📋 ПРОВЕРЬТЕ ЗАКАЗ\n\n"
               f"🚗 VIN: {data.get('vin', 'не указан')}\n"
               f"📊 Пробег: {data.get('mileage', 'не указан')} км\n"
               f"🏎️ Стиль город: {data.get('style_city', 'не указан')}\n"
               f"🛣️ Стиль трасса: {data.get('style_highway', 'не указан')}\n"
               f"🏙️ Город: {data.get('city', 'не указан')}\n"
               f"🚚 Доставка: {data.get('delivery_type', 'не указана')}\n"
               f"📍 Адрес: {data.get('delivery_address', 'не указан')}\n"
               f"📞 Телефон: {data.get('phone', 'не указан')}\n"
               f"🔧 Узел: {data.get('part_node', 'не указан')}\n"
               f"🔧 Ось: {data.get('axle', 'не указана')}\n"
               f"📝 Запчасти:\n{data.get('needed_parts', 'не указаны')}\n\n"
               f"💰 Доставка: {delivery_price} руб.\n\n"
               "✅ Всё верно? Нажмите Готово или Редактировать")
    
    await upd.message.reply_text(summary, reply_markup=confirm_order_kb)
    return OrderStates.CONFIRM

@safe_async
async def confirm_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение заказа"""
    if not upd or not upd.message:
        return ConversationHandler.END
    
    if upd.message.text == "✅ Готово":
        data = safe_dict(ctx.user_data if ctx.user_data else {})
        
        order_num = save_order({
            'user_id': upd.effective_user.id,
            'user_name': upd.effective_user.full_name,
            'phone': data.get('phone', ''),
            'vin': data.get('vin', ''),
            'mileage': data.get('mileage', ''),
            'style_city': data.get('style_city', ''),
            'style_highway': data.get('style_highway', ''),
            'city': data.get('city', ''),
            'distance': data.get('distance', 0),
            'delivery_type': data.get('delivery_type', ''),
            'delivery_price': data.get('delivery_price', 500),
            'delivery_address': data.get('delivery_address', ''),
            'part_node': data.get('part_node', ''),
            'axle': data.get('axle', ''),
            'needed_parts': data.get('needed_parts', '')
        })
        
        safe_clear_user_data(ctx)
        
        if not order_num:
            await upd.message.reply_text("❌ Ошибка при создании заказа. Попробуйте позже.")
            return ConversationHandler.END
        
        vin = data.get('vin', '')
        if vin and validate_vin(vin):
            cars = get_cars(upd.effective_user.id)
            vin_exists = False
            for car in cars:
                if car and safe_len(car) > 0 and car[0] == vin:
                    vin_exists = True
                    break
            
            if not vin_exists:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Да, сохранить в гараж", callback_data=f"save_vin_{vin}")],
                    [InlineKeyboardButton("❌ Нет, спасибо", callback_data="no_save_vin")]
                ])
                await upd.message.reply_text(
                    f"🚗 Хотите сохранить автомобиль с VIN {vin} в ваш гараж?\n\n"
                    f"В следующий раз вам не придётся вводить VIN заново!",
                    reply_markup=kb
                )
        
        await upd.message.reply_text(
            f"✅ ЗАКАЗ #{order_num} ПРИНЯТ!\n\n"
            f"📋 Детали заказа:\n"
            f"🚚 Доставка: {data.get('delivery_price', 500)} руб.\n"
            f"📍 Адрес: {data.get('delivery_address', 'не указан')}\n\n"
            f"🔧 Менеджер скоро свяжется с вами для уточнения деталей.\n\n"
            f"Вы можете вернуться в главное меню:",
            reply_markup=main_menu
        )
        return ConversationHandler.END
    
    elif upd.message.text == "✏️ Редактировать":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, начать заново", callback_data="confirm_edit")],
            [InlineKeyboardButton("❌ Нет, продолжить", callback_data="cancel_edit")]
        ])
        await upd.message.reply_text(
            "⚠️ Внимание!\n\n"
            "При редактировании все введённые данные будут потеряны.\n"
            "Вы уверены, что хотите начать заказ заново?",
            reply_markup=kb
        )
        return OrderStates.CONFIRM
    
    return ConversationHandler.END

@safe_async
async def confirm_edit_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение редактирования заказа"""
    query = upd.callback_query
    await query.answer()
    
    if query.data == "confirm_edit":
        safe_clear_user_data(ctx)
        await query.edit_message_text(
            "✏️ Давайте начнём заказ заново. Нажмите 🛒 Новый заказ",
            reply_markup=main_menu
        )
        return ConversationHandler.END
    else:
        await query.edit_message_text("✅ Продолжаем оформление заказа. Введите запчасти:")
        return OrderStates.PARTS

@safe_async
async def continue_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Продолжить заказ без запчастей"""
    query = upd.callback_query
    await query.answer()
    
    safe_set_user_data(ctx, 'needed_parts', "Без запчастей (уточнить у менеджера)")
    
    data = safe_dict(ctx.user_data if ctx.user_data else {})
    delivery_price = safe_int(data.get('delivery_price', 500))
    
    summary = (f"📋 ПРОВЕРЬТЕ ЗАКАЗ\n\n"
               f"🚗 VIN: {data.get('vin', 'не указан')}\n"
               f"📊 Пробег: {data.get('mileage', 'не указан')} км\n"
               f"🏎️ Стиль город: {data.get('style_city', 'не указан')}\n"
               f"🛣️ Стиль трасса: {data.get('style_highway', 'не указан')}\n"
               f"🏙️ Город: {data.get('city', 'не указан')}\n"
               f"🚚 Доставка: {data.get('delivery_type', 'не указана')}\n"
               f"📍 Адрес: {data.get('delivery_address', 'не указан')}\n"
               f"📞 Телефон: {data.get('phone', 'не указан')}\n"
               f"🔧 Узел: {data.get('part_node', 'не указан')}\n"
               f"🔧 Ось: {data.get('axle', 'не указана')}\n"
               f"📝 Запчасти: Без запчастей (уточнить у менеджера)\n\n"
               f"💰 Доставка: {delivery_price} руб.\n\n"
               "✅ Всё верно? Нажмите Готово или Редактировать")
    
    await query.edit_message_text(summary, reply_markup=confirm_order_kb)
    return OrderStates.CONFIRM

@safe_async
async def cancel_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отмена заказа"""
    query = upd.callback_query
    await query.answer()
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, отменить", callback_data="confirm_cancel_order")],
        [InlineKeyboardButton("❌ Нет, продолжить", callback_data="continue_order")]
    ])
    await query.edit_message_text(
        "⚠️ Вы уверены, что хотите отменить создание заказа?",
        reply_markup=kb
    )

@safe_async
async def confirm_cancel_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение отмены заказа"""
    query = upd.callback_query
    await query.answer()
    
    uid = query.from_user.id
    if uid in user_selections:
        del user_selections[uid]
    
    safe_clear_user_data(ctx)
    await query.edit_message_text(
        "❌ Заказ отменён. Вы можете начать новый заказ в главном меню.",
        reply_markup=main_menu
    )
    return ConversationHandler.END

# ========== СОХРАНЕНИЕ VIN ==========

@safe_async
async def save_vin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Сохранение VIN в гараж"""
    query = upd.callback_query
    await query.answer()
    vin = query.data[9:]  # убираем "save_vin_"
    
    safe_set_user_data(ctx, 'save_vin', vin)
    await query.edit_message_text(
        f"🚗 СОХРАНЕНИЕ АВТОМОБИЛЯ\n\n"
        f"VIN: {vin}\n\n"
        f"Добавьте комментарий (например, «зимняя резина», «жена», «служебный»)\n"
        f"Максимум 100 символов.\n\n"
        f"Или отправьте '-' чтобы пропустить:"
    )
    return SaveStates.COMMENT

@safe_async
async def save_vin_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение комментария для сохранения VIN"""
    user_id = upd.effective_user.id
    vin = safe_get_user_data(ctx, 'save_vin')
    
    if not vin:
        await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return ConversationHandler.END
    
    comment = upd.message.text.strip()
    if len(comment) > 100:
        await upd.message.reply_text("❌ Комментарий слишком длинный (максимум 100 символов).")
        return SaveStates.COMMENT
    
    if comment == "-":
        comment = ""
    
    if save_car(user_id, vin, "", comment):
        if comment:
            await upd.message.reply_text(f"✅ Автомобиль {vin} сохранён в гараж!\n💬 Комментарий: {comment}")
        else:
            await upd.message.reply_text(f"✅ Автомобиль {vin} сохранён в гараж!")
    else:
        await upd.message.reply_text(f"❌ Автомобиль {vin} уже есть в вашем гараже!")
    
    safe_del_user_data(ctx, 'save_vin')
    return ConversationHandler.END

@safe_async
async def no_save_vin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отказ от сохранения VIN"""
    query = upd.callback_query
    await query.answer()
    await query.edit_message_text("OK, в следующий раз вы сможете сохранить автомобиль в гараж при создании заказа.")

# ========== БОНУСЫ ПОЛЬЗОВАТЕЛЯ ==========

@safe_async
async def bonus_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /bonus - информация о бонусах"""
    if not upd or not upd.effective_user:
        return
    
    uid = upd.effective_user.id
    bonus_data = get_bonus(uid)
    bal = bonus_data['balance']
    total_earned = bonus_data['total_earned']
    total_spent = bonus_data['total_spent']
    total_purchases = get_user_total(uid)
    percent = get_bonus_percent(uid)
    
    text = (f"🎁 БОНУСНАЯ ПРОГРАММА\n\n"
            f"💰 Текущий баланс: {bal} бонусов\n"
            f"📈 Всего начислено: {total_earned} бонусов\n"
            f"📉 Всего потрачено: {total_spent} бонусов\n"
            f"🛒 Накоплено по покупкам: {total_purchases} руб.\n"
            f"⭐ Текущий процент начисления: {percent}%\n\n"
            f"📋 ПРАВИЛА ИСПОЛЬЗОВАНИЯ:\n"
            f"• ✅ Можно списать до {MAX_BONUS_SPEND_PERCENT}% от суммы запчастей\n"
            f"• ❌ Не действует на продукцию RAVENOL\n"
            f"• ❌ Не действует на доставку\n"
            f"• 💳 Минимальная оплата деньгами: {MIN_CASH_PAYMENT} руб.\n\n"
            f"📊 ГРАДАЦИЯ НАЧИСЛЕНИЯ:\n"
            f"1% → до 100 000 руб.\n2% → 100 000 - 200 000 руб.\n3% → 200 000 - 300 000 руб.\n"
            f"4% → 300 000 - 400 000 руб.\n5% → 400 000 - 500 000 руб.\n6% → 500 000 - 600 000 руб.\n"
            f"7% → 600 000 - 700 000 руб.\n8% → 700 000 - 800 000 руб.\n"
            f"9% → 800 000 - 900 000 руб.\n10% → от 900 000 руб.\n")
    
    await upd.message.reply_text(text)
    
    history = get_bonus_history(uid, 10)
    if history and safe_len(history) > 0:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📜 Показать историю", callback_data="bonus_history")]
        ])
        await upd.message.reply_text("📊 Дополнительная информация:", reply_markup=kb)

@safe_async
async def bonus_history_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать историю бонусов"""
    query = upd.callback_query
    await query.answer()
    
    uid = query.from_user.id
    history = get_bonus_history(uid, 20)
    
    if not history:
        await query.edit_message_text("📭 История операций пока пуста")
        return
    
    text = "📜 ИСТОРИЯ БОНУСОВ\n\n"
    for h in history:
        if h and safe_len(h) >= 5:
            order_num = safe_str(h[0])
            amount = safe_int(h[1])
            h_type = safe_str(h[2])
            desc = safe_str(h[3])
            created = safe_str(h[4])
            sign = "➕" if h_type == 'earned' else "➖" if h_type == 'spent' else "🔄"
            text += f"{sign} {amount} руб. - {desc}\n"
            text += f"   📅 {created[:10]}\n\n"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data="bonus_back")]
    ])
    await query.edit_message_text(text, reply_markup=kb)

@safe_async
async def bonus_back_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Назад к бонусам"""
    query = upd.callback_query
    await query.answer()
    await bonus_cmd(upd, ctx)

# ========== ПРИМЕНЕНИЕ БОНУСОВ ==========

@require_order_owner
@safe_async
async def apply_bonus_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Применение бонусов к заказу"""
    query = upd.callback_query
    order = safe_get_user_data(ctx, 'current_order')
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    order_num = order.get('order_number')
    uid = query.from_user.id
    
    if order.get('status') != 'waiting_payment':
        await query.edit_message_text("❌ Бонусы можно применить только к заказу в статусе 'Ожидает оплаты'")
        return
    
    bonus_data = get_bonus(uid)
    balance = bonus_data['balance']
    eligible_sum = calculate_bonus_eligible_sum(order)
    
    if eligible_sum <= 0:
        await query.edit_message_text(
            f"❌ НЕВОЗМОЖНО СПИСАТЬ БОНУСЫ\n\n"
            f"{get_bonus_spend_details(order)}\n\n"
            f"Причина: в заказе нет товаров, подходящих для списания бонусов."
        )
        return
    
    max_spend = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100)
    max_spend = min(max_spend, balance)
    
    if max_spend <= 0:
        await query.edit_message_text(
            f"❌ НЕВОЗМОЖНО СПИСАТЬ БОНУСЫ\n\n"
            f"{get_bonus_spend_details(order)}\n"
            f"🎁 Ваш баланс: {balance} руб.\n\n"
            f"Недостаточно бонусов или сумма слишком мала."
        )
        return
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Списать {MAX_BONUS_SPEND_PERCENT}% ({max_spend} руб.)", 
                            callback_data=f"spend_bonus_percent_{order_num}")],
        [InlineKeyboardButton("✏️ Ввести свою сумму", callback_data=f"spend_bonus_custom_{order_num}")],
        [InlineKeyboardButton("◀️ Назад к заказу", callback_data=f"view_{order_num}")]
    ])
    
    details = get_bonus_spend_details(order)
    
    await query.edit_message_text(
        f"🎁 СПИСАНИЕ БОНУСОВ\n\n"
        f"{details}\n"
        f"🎁 Ваш баланс: {balance} руб.\n"
        f"📊 Максимум списания: {max_spend} руб.\n\n"
        f"Выберите действие:",
        reply_markup=kb
    )

@rate_limit
@safe_async
async def spend_bonus_percent_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Списание максимального процента бонусов"""
    query = upd.callback_query
    await query.answer("⏳ Списание бонусов...", show_alert=False)
    
    # Извлекаем номер заказа из callback_data
    order_num_match = re.search(r'RVN-[A-Z0-9]{6}', query.data)
    if not order_num_match:
        await query.edit_message_text("❌ Не удалось определить заказ")
        return
    order_num = order_num_match.group(0)
    uid = query.from_user.id
    
    order = get_order(order_num)
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    if order.get('status') != 'waiting_payment':
        await query.edit_message_text("❌ Заказ не в статусе 'Ожидает оплаты'")
        return
    
    eligible_sum = calculate_bonus_eligible_sum(order)
    bonus_data = get_bonus(uid)
    balance = bonus_data['balance']
    
    if eligible_sum <= 0:
        await query.edit_message_text("❌ Нет товаров для списания бонусов")
        return
    
    max_allowed = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100)
    spend_amount = min(balance, max_allowed)
    
    if spend_amount <= 0:
        await query.edit_message_text("❌ Недостаточно бонусов или сумма слишком мала")
        return
    
    current_total_parts = order.get('total_price', 0)
    delivery_price = order.get('delivery_price', 0)
    new_parts_total = current_total_parts - spend_amount
    
    if new_parts_total < 0:
        await query.edit_message_text("❌ Ошибка: сумма списания превышает стоимость товаров")
        return
    
    new_total = new_parts_total + delivery_price
    if new_total < MIN_CASH_PAYMENT and new_total > 0:
        spend_amount = current_total_parts + delivery_price - MIN_CASH_PAYMENT
        new_parts_total = current_total_parts - spend_amount
        new_total = MIN_CASH_PAYMENT
    
    if spend_amount <= 0:
        await query.edit_message_text(f"❌ Нельзя списать бонусы. Минимальная оплата деньгами: {MIN_CASH_PAYMENT} руб.")
        return
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, списать", callback_data=f"confirm_spend_{order_num}_{spend_amount}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"view_{order_num}")]
    ])
    
    await query.edit_message_text(
        f"⚠️ ПОДТВЕРЖДЕНИЕ СПИСАНИЯ\n\n"
        f"📦 Заказ: {order_num}\n"
        f"💰 Сумма запчастей: {current_total_parts} руб.\n"
        f"🎁 Будет списано: {spend_amount} руб.\n"
        f"💳 К оплате после списания: {new_total} руб.\n\n"
        f"Вы уверены?",
        reply_markup=kb
    )

@safe_async
async def confirm_spend_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение списания бонусов"""
    query = upd.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    if len(parts) < 4:
        await query.edit_message_text("❌ Ошибка формата данных")
        return
    
    order_num = parts[2]
    spend_amount = safe_int(parts[3])
    uid = query.from_user.id
    
    order = get_order(order_num)
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    current_total_parts = order.get('total_price', 0)
    delivery_price = order.get('delivery_price', 0)
    new_parts_total = current_total_parts - spend_amount
    new_total = new_parts_total + delivery_price
    
    if use_bonus(uid, order_num, spend_amount, f"Списание бонусов по заказу {order_num} (только non-Ravenol)"):
        update_order(order_num, total_price=new_parts_total)
        
        _, _, ravenol_sum, other_sum = has_ravenol_only(order)
        ravenol_info = ""
        if ravenol_sum > 0:
            ravenol_info = f"\n\n📊 Состав заказа:\n• Ravenol: {ravenol_sum} руб. (❌ без изменений)\n• Остальные: {other_sum} → {other_sum - spend_amount} руб."
        
        bonus_data = get_bonus(uid)
        
        await query.edit_message_text(
            f"✅ БОНУСЫ УСПЕШНО СПИСАНЫ!\n\n"
            f"📦 Заказ: {order_num}\n"
            f"💰 Сумма запчастей: {current_total_parts} → {new_parts_total} руб.\n"
            f"🚚 Доставка: {delivery_price} руб. (❌ без изменений)\n"
            f"🎁 Списано бонусов: {spend_amount} руб.\n"
            f"💳 ИТОГО К ОПЛАТЕ: {new_total} руб.{ravenol_info}\n\n"
            f"🎁 Остаток бонусов: {bonus_data['balance']} руб."
        )
    else:
        await query.edit_message_text("❌ Ошибка при списании бонусов")

@safe_async
async def spend_bonus_custom_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ввод произвольной суммы списания"""
    query = upd.callback_query
    await query.answer()
    
    # Извлекаем номер заказа
    order_num_match = re.search(r'RVN-[A-Z0-9]{6}', query.data)
    if not order_num_match:
        await query.edit_message_text("❌ Не удалось определить заказ")
        return
    order_num = order_num_match.group(0)
    
    safe_set_user_data(ctx, 'bonus_order', order_num)
    
    order = get_order(order_num)
    if order:
        eligible_sum = calculate_bonus_eligible_sum(order)
        max_allowed = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100)
        balance = get_bonus(query.from_user.id)['balance']
        max_possible = min(max_allowed, balance)
        
        await query.edit_message_text(
            f"✏️ ВВЕДИТЕ СУММУ СПИСАНИЯ\n\n"
            f"📦 Заказ: {order_num}\n"
            f"💰 Сумма для бонусов: {eligible_sum} руб.\n"
            f"📊 Максимум списания (20%): {max_allowed} руб.\n"
            f"🎁 Ваш баланс: {balance} руб.\n"
            f"✅ Доступно для списания: {max_possible} руб.\n\n"
            f"Введите целое число (рублей):"
        )
    else:
        await query.edit_message_text("❌ Заказ не найден")
    
    return BonusStates.SPEND

@safe_async
async def spend_bonus_custom_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка введённой суммы списания"""
    user_id = upd.effective_user.id
    order_num = safe_get_user_data(ctx, 'bonus_order')
    
    if not order_num:
        await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return ConversationHandler.END
    
    try:
        spend_amount = int(upd.message.text)
        if spend_amount <= 0:
            raise ValueError
    except ValueError:
        await upd.message.reply_text("❌ Введите целое положительное число.")
        return BonusStates.SPEND
    
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text("❌ Заказ не найден")
        return ConversationHandler.END
    
    if order.get('status') != 'waiting_payment':
        await upd.message.reply_text("❌ Заказ не в статусе 'Ожидает оплаты'")
        return ConversationHandler.END
    
    eligible_sum = calculate_bonus_eligible_sum(order)
    bonus_data = get_bonus(user_id)
    balance = bonus_data['balance']
    
    max_allowed = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100)
    
    if spend_amount > max_allowed:
        await upd.message.reply_text(
            f"❌ Сумма списания не может превышать {max_allowed} руб. (20% от {eligible_sum} руб.)\n"
            f"Попробуйте снова:"
        )
        return BonusStates.SPEND
    
    if spend_amount > balance:
        await upd.message.reply_text(
            f"❌ У вас только {balance} бонусов.\n"
            f"Попробуйте снова (максимум {min(max_allowed, balance)} руб.):"
        )
        return BonusStates.SPEND
    
    current_parts = order.get('total_price', 0)
    delivery_price = order.get('delivery_price', 0)
    new_parts = current_parts - spend_amount
    
    if new_parts < 0:
        await upd.message.reply_text("❌ Сумма списания не может превышать стоимость запчастей")
        return BonusStates.SPEND
    
    new_total = new_parts + delivery_price
    
    if new_total < MIN_CASH_PAYMENT and new_total > 0:
        max_spend = current_parts + delivery_price - MIN_CASH_PAYMENT
        await upd.message.reply_text(
            f"❌ После списания сумма к оплате составит {new_total} руб.\n"
            f"Минимальная оплата деньгами: {MIN_CASH_PAYMENT} руб.\n"
            f"Максимум списания: {max_spend} руб.\n\n"
            f"Попробуйте снова:"
        )
        return BonusStates.SPEND
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, списать", callback_data=f"confirm_spend_{order_num}_{spend_amount}")],
        [InlineKeyboardButton("❌ Отмена", callback_data=f"view_{order_num}")]
    ])
    
    await upd.message.reply_text(
        f"⚠️ ПОДТВЕРЖДЕНИЕ СПИСАНИЯ\n\n"
        f"📦 Заказ: {order_num}\n"
        f"💰 Сумма запчастей: {current_parts} руб.\n"
        f"🎁 Будет списано: {spend_amount} руб.\n"
        f"💳 К оплате после списания: {new_total} руб.\n\n"
        f"Вы уверены?",
        reply_markup=kb
    )
    
    safe_del_user_data(ctx, 'bonus_order')
    return ConversationHandler.END

# ========== МОИ ЗАКАЗЫ ==========

@safe_async
async def my_orders(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать заказы пользователя"""
    if not upd or not upd.effective_user:
        return
    
    await upd.effective_chat.send_action(action="typing")
    
    orders = get_user_orders(upd.effective_user.id)
    
    if not orders or safe_len(orders) == 0:
        await upd.message.reply_text("📭 У вас пока нет заказов", reply_markup=main_menu)
        return
    
    text = "📦 ВАШИ ЗАКАЗЫ:\n\n"
    kb = []
    
    for order in orders:
        if order and safe_len(order) > 0:
            order_num = safe_str(order[0])
            status_text = safe_str(order[1]) if safe_len(order) > 1 else ""
            created = safe_str(order[2]) if safe_len(order) > 2 else ""
            tp = safe_int(order[3]) if safe_len(order) > 3 else 0
            dp = safe_int(order[4]) if safe_len(order) > 4 else 0
            total = tp + dp
            icon = get_status_icon(status_text)
            
            text += f"{icon} {order_num} — {created[:10]} — {total} руб.\n"
            kb.append([InlineKeyboardButton(f"🔍 Заказ {order_num}", callback_data=f"view_{order_num}")])
    
    kb.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")])
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

@require_order_owner
@safe_async
async def view_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Просмотр деталей заказа с возможностью удаления товаров"""
    query = upd.callback_query
    order = safe_get_user_data(ctx, 'current_order')
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    order_num = order.get('order_number')
    
    total_parts = order.get('total_price', 0)
    delivery_price = order.get('delivery_price', 0)
    total_sum = total_parts + delivery_price
    
    eligible_sum = calculate_bonus_eligible_sum(order)
    eligible_percent = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100) if eligible_sum > 0 else 0
    
    status = order.get('status', '')
    can_edit = status == 'waiting_payment'
    
    text = (f"📋 ЗАКАЗ {order_num}\n\n"
            f"🚗 VIN: {order.get('vin', 'не указан')}\n"
            f"📊 Пробег: {order.get('mileage', 'не указан')} км\n"
            f"🏙️ Стиль город: {order.get('style_city', 'не указан')}\n"
            f"🛣️ Стиль трасса: {order.get('style_highway', 'не указан')}\n"
            f"📍 Адрес: {order.get('delivery_address', 'не указан')}\n"
            f"📞 Телефон: {order.get('phone', 'не указан')}\n"
            f"💰 Запчасти: {total_parts} руб.\n"
            f"🚚 Доставка: {delivery_price} руб.\n"
            f"💳 ИТОГО: {total_sum} руб.\n"
            f"📦 Статус: {order.get('status_text', 'неизвестен')}")
    
    if order.get('tracking_number'):
        text += f"\n📮 Трек-номер: {order.get('tracking_number')}"
    
    if eligible_sum > 0:
        text += f"\n\n🎁 Доступно для бонусов: {eligible_sum} руб.\n"
        text += f"   (можно списать до {eligible_percent} руб.)"
    
    final_order = order.get('final_order', '')
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        text += "\n\n📦 ВАШИ ТОВАРЫ:\n"
        try:
            selected_parts = ast.literal_eval(final_order)
            if isinstance(selected_parts, list) and selected_parts:
                for i, part in enumerate(selected_parts):
                    if isinstance(part, dict):
                        part_name = part.get('name', 'неизвестно')
                        part_price = part.get('price', 0)
                        ravenol_mark = " (Ravenol)" if is_ravenol_product(part_name) else ""
                        text += f"{i+1}. {part_name}\n   → {part_price} руб.{ravenol_mark}\n"
            else:
                text += f"{final_order[:300]}"
        except:
            text += f"{final_order[:300]}"
    elif order.get('needed_parts'):
        text += f"\n\n📝 ИЗНАЧАЛЬНЫЙ ЗАПРОС:\n{order.get('needed_parts', 'не указан')[:300]}"
    
    kb = []
    
    if can_edit:
        kb.append([InlineKeyboardButton("🗑️ Удалить товары из заказа", callback_data=f"remove_items_{order_num}")])
        kb.append([InlineKeyboardButton("❌ Отменить заказ", callback_data=f"cancel_by_user_{order_num}")])
    
    if status == 'waiting_payment':
        bonus_data = get_bonus(order.get('user_id'))
        eligible_sum = calculate_bonus_eligible_sum(order)
        if bonus_data['balance'] > 0 and eligible_sum > 0:
            kb.append([InlineKeyboardButton("🎁 Списать бонусы", callback_data=f"apply_bonus_{order_num}")])
    
    kb.append([InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders_list")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

@safe_async
async def back_orders_list(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Назад к списку заказов"""
    query = upd.callback_query
    await query.answer()
    await my_orders(upd, ctx)

@safe_async
async def main_menu_back(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Назад в главное меню"""
    query = upd.callback_query
    await query.answer()
    await start(upd, ctx)

# ========== КЛИЕНТ УДАЛЯЕТ ТОВАРЫ ==========

@require_order_owner
@safe_async
async def remove_items_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выбор товаров для удаления из заказа"""
    query = upd.callback_query
    await query.answer()
    
    # Извлекаем номер заказа
    order_num_match = re.search(r'RVN-[A-Z0-9]{6}', query.data)
    if not order_num_match:
        await query.edit_message_text("❌ Не удалось определить заказ")
        return
    order_num = order_num_match.group(0)
    uid = query.from_user.id
    
    order = get_order(order_num)
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    if order.get('user_id') != uid:
        await query.answer("❌ Это не ваш заказ!", show_alert=True)
        return
    
    if order.get('status') != 'waiting_payment':
        await query.edit_message_text("❌ Удалять товары можно только в статусе 'Ожидает оплаты'")
        return
    
    final_order = order.get('final_order', '')
    if not final_order or final_order in [None, 'None', '[]', '{}']:
        await query.edit_message_text("❌ В заказе нет товаров для удаления")
        return
    
    try:
        selected_parts = ast.literal_eval(final_order)
        if not isinstance(selected_parts, list) or not selected_parts:
            await query.edit_message_text("❌ Нет товаров для удаления")
            return
        
        session_key = f"{uid}_{order_num}"
        client_remove_sessions[session_key] = {
            'parts': selected_parts.copy(),
            'selected': set(),
            'step': 'selecting'
        }
        
        kb = []
        for i, part in enumerate(selected_parts):
            if isinstance(part, dict):
                part_name = part.get('name', 'Неизвестно')[:35]
                part_price = part.get('price', 0)
                kb.append([InlineKeyboardButton(f"⬜ {part_name} — {part_price} руб.", 
                                               callback_data=f"client_toggle_{order_num}_{i}")])
            else:
                kb.append([InlineKeyboardButton(f"⬜ {safe_str(part)[:35]}", 
                                               callback_data=f"client_toggle_{order_num}_{i}")])
        
        kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ УДАЛЕНИЕ", callback_data=f"client_confirm_remove_{order_num}")])
        kb.append([InlineKeyboardButton("◀️ Назад к заказу", callback_data=f"view_{order_num}")])
        
        text = f"🗑️ УДАЛЕНИЕ ТОВАРОВ ИЗ ЗАКАЗА {order_num}\n\n"
        text += "Нажмите на товар, чтобы отметить его для удаления.\n"
        text += "Отмеченные товары будут удалены из заказа.\n\n"
        text += "⬜ - товар остаётся\n"
        text += "✅ - товар будет удалён\n\n"
        text += f"🚚 Доставка: {order.get('delivery_type', 'не указана')} | {order.get('delivery_price', 0)} руб.\n"
        text += f"💰 Сумма запчастей: {order.get('total_price', 0)} руб.\n"
        text += f"💳 Итого: {order.get('total_price', 0) + order.get('delivery_price', 0)} руб.\n"
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        
    except Exception as e:
        logger.error(f"Remove items error: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")

@safe_async
async def client_toggle_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Переключение выбора товара для удаления (клиент)"""
    query = upd.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    if len(parts) < 4:
        await query.edit_message_text("❌ Ошибка формата данных")
        return
    
    order_num = parts[2]
    item_idx = safe_int(parts[3])
    uid = query.from_user.id
    
    session_key = f"{uid}_{order_num}"
    
    if session_key not in client_remove_sessions:
        await query.edit_message_text("❌ Сессия истекла. Начните заново.")
        return
    
    selected = client_remove_sessions[session_key]['selected']
    if item_idx in selected:
        selected.remove(item_idx)
    else:
        selected.add(item_idx)
    
    selected_parts = client_remove_sessions[session_key]['parts']
    
    kb = []
    for i, part in enumerate(selected_parts):
        if isinstance(part, dict):
            part_name = part.get('name', 'Неизвестно')[:35]
            part_price = part.get('price', 0)
            check = "✅" if i in selected else "⬜"
            kb.append([InlineKeyboardButton(f"{check} {part_name} — {part_price} руб.", 
                                           callback_data=f"client_toggle_{order_num}_{i}")])
        else:
            check = "✅" if i in selected else "⬜"
            kb.append([InlineKeyboardButton(f"{check} {safe_str(part)[:35]}", 
                                           callback_data=f"client_toggle_{order_num}_{i}")])
    
    kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ УДАЛЕНИЕ", callback_data=f"client_confirm_remove_{order_num}")])
    kb.append([InlineKeyboardButton("◀️ Назад к заказу", callback_data=f"view_{order_num}")])
    
    order = get_order(order_num)
    text = f"🗑️ УДАЛЕНИЕ ТОВАРОВ ИЗ ЗАКАЗА {order_num}\n\n"
    text += "Нажмите на товар, чтобы отметить его для удаления.\n\n"
    text += "⬜ - товар остаётся\n"
    text += "✅ - товар будет удалён\n\n"
    if order:
        text += f"🚚 Доставка: {order.get('delivery_type', 'не указана')} | {order.get('delivery_price', 0)} руб.\n"
        text += f"💰 Сумма запчастей: {order.get('total_price', 0)} руб.\n"
    
    try:
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        if "Message is not modified" not in str(e):
            logger.error(f"Error updating message: {e}")

@safe_async
async def client_confirm_remove_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления выбранных товаров (клиент)"""
    query = upd.callback_query
    await query.answer()
    
    # Извлекаем номер заказа
    order_num_match = re.search(r'RVN-[A-Z0-9]{6}', query.data)
    if not order_num_match:
        await query.edit_message_text("❌ Не удалось определить заказ")
        return
    order_num = order_num_match.group(0)
    uid = query.from_user.id
    
    session_key = f"{uid}_{order_num}"
    
    if session_key not in client_remove_sessions:
        await query.edit_message_text("❌ Сессия истекла. Начните заново.")
        return
    
    selected_items = client_remove_sessions[session_key]['selected']
    selected_parts = client_remove_sessions[session_key]['parts']
    
    if not selected_items:
        await query.edit_message_text("❌ Не выбрано ни одного товара для удаления")
        return
    
    if len(selected_items) >= len(selected_parts):
        await query.edit_message_text(
            "❌ Нельзя удалить все товары из заказа!\n\n"
            "В заказе должен остаться хотя бы один товар.\n"
            "Если хотите полностью отменить заказ, используйте кнопку «Отменить заказ»."
        )
        return
    
    removed_parts = []
    remaining_parts = []
    removed_names = []
    
    for i, part in enumerate(selected_parts):
        if i in selected_items:
            removed_parts.append(part)
            if isinstance(part, dict):
                removed_names.append(part.get('name', 'Товар'))
            else:
                removed_names.append(safe_str(part))
        else:
            remaining_parts.append(part)
    
    client_remove_sessions[session_key]['removed_parts'] = removed_parts
    client_remove_sessions[session_key]['remaining_parts'] = remaining_parts
    client_remove_sessions[session_key]['removed_names'] = removed_names
    client_remove_sessions[session_key]['step'] = 'comment'
    
    await query.edit_message_text(
        f"🗑️ УДАЛЕНИЕ ТОВАРОВ ИЗ ЗАКАЗА {order_num}\n\n"
        f"Будет удалено товаров: {len(removed_parts)}\n"
        f"Останется товаров: {len(remaining_parts)}\n\n"
        f"Укажите причину удаления (необязательно):\n\n"
        f"Отправьте сообщение с комментарием\n"
        f"Или отправьте '-' чтобы пропустить"
    )
    return RemoveStates.COMMENT

@safe_async
async def remove_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение комментария при удалении товаров (клиент)"""
    user_id = upd.effective_user.id
    comment = upd.message.text.strip()
    
    session_key = None
    for key in list(client_remove_sessions.keys()):
        if key.startswith(f"{user_id}_") and client_remove_sessions[key].get('step') == 'comment':
            session_key = key
            break
    
    if not session_key:
        await upd.message.reply_text("❌ Ошибка. Сессия не найдена. Попробуйте снова.")
        return ConversationHandler.END
    
    order_num = session_key.split('_')[1]
    
    if comment == "-":
        comment = ""
    
    remaining_parts = client_remove_sessions[session_key].get('remaining_parts', [])
    removed_names = client_remove_sessions[session_key].get('removed_names', [])
    
    new_total = sum(p.get('price', 0) for p in remaining_parts if isinstance(p, dict))
    order = get_order(order_num)
    delivery_price = order.get('delivery_price', 0) if order else 0
    
    update_order(order_num, final_order=str(remaining_parts), total_price=new_total)
    
    removed_names_str = ", ".join(removed_names)
    
    add_order_change(order_num, user_id, 'remove_items', removed_names_str, 
                     f"Удалено {len(removed_names)} товаров", comment)
    
    if order:
        try:
            await upd.message.bot.send_message(
                MANAGER_ID,
                text=f"🔄 КЛИЕНТ УДАЛИЛ ТОВАРЫ ИЗ ЗАКАЗА\n\n"
                     f"📦 Заказ: {order_num}\n"
                     f"👤 Клиент: {order.get('user_name', '')}\n"
                     f"🗑️ Удалено: {removed_names_str}\n"
                     f"💰 Новая сумма: {new_total + delivery_price} руб.\n"
                     f"📝 Комментарий: {comment if comment else 'Не указан'}"
            )
        except Exception as e:
            logger.error(f"Error notifying manager: {e}")
    
    await upd.message.reply_text(
        f"✅ Товары успешно удалены из заказа {order_num}!\n\n"
        f"🗑️ Удалено: {len(removed_names)} товаров\n"
        f"💰 Новая сумма к оплате: {new_total + delivery_price} руб.\n\n"
        f"Менеджер получил уведомление."
    )
    
    del client_remove_sessions[session_key]
    return ConversationHandler.END

# ========== КЛИЕНТ ОТМЕНЯЕТ ЗАКАЗ ==========

@require_order_owner
@safe_async
async def cancel_by_user_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Клиент отменяет заказ"""
    query = upd.callback_query
    order = safe_get_user_data(ctx, 'current_order')
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    order_num = order.get('order_number')
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, отменить заказ", callback_data=f"confirm_user_cancel_{order_num}")],
        [InlineKeyboardButton("❌ Нет, вернуться", callback_data=f"view_{order_num}")]
    ])
    
    await query.edit_message_text(
        f"⚠️ ВНИМАНИЕ!\n\n"
        f"Вы уверены, что хотите отменить заказ {order_num}?\n\n"
        f"После отмены:\n"
        f"• Заказ перейдёт в статус «Отменён пользователем»\n"
        f"• Если вы списывали бонусы, они будут возвращены\n\n"
        f"Это действие нельзя отменить!",
        reply_markup=kb
    )

@safe_async
async def confirm_user_cancel_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение отмены заказа клиентом"""
    query = upd.callback_query
    await query.answer()
    
    # Извлекаем номер заказа
    order_num_match = re.search(r'RVN-[A-Z0-9]{6}', query.data)
    if not order_num_match:
        await query.edit_message_text("❌ Не удалось определить заказ")
        return
    order_num = order_num_match.group(0)
    uid = query.from_user.id
    
    order = get_order(order_num)
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    if order.get('user_id') != uid:
        await query.answer("❌ Это не ваш заказ!", show_alert=True)
        return
    
    # Возвращаем бонусы, если были списаны
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT amount FROM bonus_history WHERE order_number = ? AND type = "spent"', (order_num,))
        bonus_row = c.fetchone()
        if bonus_row and bonus_row[0] > 0:
            add_bonus(uid, order_num, bonus_row[0], f"Возврат бонусов при отмене заказа {order_num} пользователем")
    except Exception as e:
        logger.error(f"Bonus refund error: {e}")
    finally:
        if conn:
            conn.close()
    
    add_order_change(order_num, uid, 'cancel_by_user', order.get('status', ''), 'cancelled_by_user', "Отмена заказа пользователем")
    
    update_order(order_num, status='cancelled_by_user')
    
    await upd.message.bot.send_message(
        MANAGER_ID,
        text=f"🔄 КЛИЕНТ ОТМЕНИЛ ЗАКАЗ\n\n"
             f"📦 Заказ: {order_num}\n"
             f"👤 Клиент: {order.get('user_name', '')}\n"
             f"💰 Сумма: {order.get('total_price', 0) + order.get('delivery_price', 0)} руб."
    )
    
    await query.edit_message_text(
        f"✅ Заказ {order_num} отменён!\n\n"
        f"Статус заказа: ❌ Отменён пользователем\n\n"
        f"Если были списаны бонусы, они возвращены на ваш счёт.\n"
        f"Менеджер получил уведомление."
    )

# ========== ГАРАЖ ==========

@safe_async
async def garage_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Меню гаража"""
    if not upd or not upd.effective_user:
        return
    
    cars = get_cars(upd.effective_user.id)
    
    if not cars or safe_len(cars) == 0:
        await upd.message.reply_text(
            "🚗 МОЙ ГАРАЖ\n\nУ вас пока нет добавленных автомобилей.\n\n"
            "➕ Добавить автомобиль:\n"
            "Просто отправьте мне VIN номер (17 символов) или нажмите кнопку ниже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить авто", callback_data="garage_add")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="garage_back_to_menu")]
            ])
        )
        return
    
    text = "🚗 МОЙ ГАРАЖ\n\n"
    keyboard = []
    
    for car in cars:
        if car and safe_len(car) > 0:
            vin = safe_str(car[0])
            description = safe_str(car[1]) if safe_len(car) > 1 else ""
            comment = safe_str(car[2]) if safe_len(car) > 2 else ""
            created = safe_str(car[3]) if safe_len(car) > 3 else ""
            desc = description if description else "Описание не указано"
            text += f"🔹 {vin}\n"
            text += f"   📝 {desc}\n"
            if comment:
                text += f"   💬 {comment}\n"
            text += f"   📅 Добавлен: {created[:10]}\n\n"
            
            keyboard.append([InlineKeyboardButton(f"✏️ Комментарий", callback_data=f"garage_comment_{vin}")])
            keyboard.append([InlineKeyboardButton(f"🗑️ Удалить", callback_data=f"garage_del_{vin}")])
    
    keyboard.append([InlineKeyboardButton("➕ Добавить авто", callback_data="garage_add")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="garage_back_to_menu")])
    
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

@safe_async
async def garage_add_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Начало добавления автомобиля"""
    query = upd.callback_query
    await query.answer()
    await query.edit_message_text(
        "🚗 ДОБАВЛЕНИЕ АВТОМОБИЛЯ\n\n"
        "Шаг 1/2: Отправьте VIN номер автомобиля (17 символов):"
    )
    return GarageStates.VIN

@safe_async
async def garage_get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение VIN для гаража"""
    if not upd or not upd.message:
        return GarageStates.VIN
    
    vin = upd.message.text.upper().strip()
    
    if not validate_vin(vin):
        await upd.message.reply_text(
            "❌ Неверный VIN\n\n"
            "VIN должен содержать 17 символов (только буквы и цифры, без I, O, Q).\n"
            "Попробуйте ещё раз:"
        )
        return GarageStates.VIN
    
    safe_set_user_data(ctx, 'new_car_vin', vin)
    await upd.message.reply_text(
        f"🚗 VIN: {vin}\n\n"
        f"Шаг 2/2: Введите описание автомобиля\n\n"
        f"Пример: BMW X5 3.0d, 2018, чёрный\n\n"
        f"Или отправьте '-' чтобы пропустить:"
    )
    return GarageStates.DESCRIPTION

@safe_async
async def garage_get_description(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение описания для гаража"""
    if not upd or not upd.message:
        return ConversationHandler.END
    
    description = upd.message.text.strip()
    if description == "-":
        description = ""
    
    vin = safe_get_user_data(ctx, 'new_car_vin')
    if not vin:
        await upd.message.reply_text("❌ Ошибка. Начните добавление заново.")
        return ConversationHandler.END
    
    if save_car(upd.effective_user.id, vin, description, ""):
        await upd.message.reply_text(f"✅ Автомобиль {vin} успешно добавлен в ваш гараж!")
    else:
        await upd.message.reply_text(f"❌ Автомобиль {vin} уже есть в вашем гараже!")
    
    safe_del_user_data(ctx, 'new_car_vin')
    await garage_menu(upd, ctx)
    return ConversationHandler.END

@safe_async
async def garage_delete(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Удаление автомобиля из гаража"""
    query = upd.callback_query
    await query.answer()
    vin = query.data[12:]  # убираем "garage_del_"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"garage_confirm_del_{vin}")],
        [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="garage_back_to_menu")]
    ])
    await query.edit_message_text(
        f"⚠️ Удалить автомобиль {vin} из гаража?\n\nЭто действие нельзя отменить.",
        reply_markup=kb
    )

@safe_async
async def garage_confirm_delete(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления автомобиля"""
    query = upd.callback_query
    await query.answer()
    vin = query.data[18:]  # убираем "garage_confirm_del_"
    
    if delete_car(query.from_user.id, vin):
        await query.edit_message_text(f"✅ Автомобиль {vin} удалён из гаража!")
    else:
        await query.edit_message_text(f"❌ Автомобиль {vin} не найден в гараже.")
    
    await garage_menu(upd, ctx)

@safe_async
async def garage_comment_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Начало добавления комментария к автомобилю"""
    query = upd.callback_query
    await query.answer()
    vin = query.data[16:]  # убираем "garage_comment_"
    safe_set_user_data(ctx, 'comment_vin', vin)
    
    await query.edit_message_text(
        f"✏️ КОММЕНТАРИЙ К АВТОМОБИЛЮ\n\n"
        f"Автомобиль: {vin}\n\n"
        f"Введите комментарий для этого автомобиля.\n"
        f"Например: «зимняя резина», «жена», «служебный»\n\n"
        f"Или отправьте '-' чтобы удалить комментарий.\n\n"
        f"Максимум 100 символов."
    )
    return GarageStates.DESCRIPTION

@safe_async
async def garage_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение комментария для автомобиля"""
    if not upd or not upd.message:
        return ConversationHandler.END
    
    user_id = upd.effective_user.id
    vin = safe_get_user_data(ctx, 'comment_vin')
    
    if not vin:
        await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return ConversationHandler.END
    
    comment = upd.message.text.strip()
    if len(comment) > 100:
        await upd.message.reply_text("❌ Комментарий слишком длинный (максимум 100 символов).")
        return GarageStates.DESCRIPTION
    
    if comment == "-":
        comment = ""
    
    update_car_comment(user_id, vin, comment)
    
    if comment:
        await upd.message.reply_text(f"✅ Комментарий «{comment}» добавлен к автомобилю {vin}!")
    else:
        await upd.message.reply_text(f"✅ Комментарий к автомобилю {vin} удалён!")
    
    safe_del_user_data(ctx, 'comment_vin')
    await garage_menu(upd, ctx)
    return ConversationHandler.END

@safe_async
async def garage_back_to_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Назад в главное меню из гаража"""
    query = upd.callback_query
    await query.answer()
    await start(upd, ctx)

# ========== РЕФЕРАЛЫ ==========

@safe_async
async def referral_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /referral - реферальная система"""
    if not upd or not upd.effective_user:
        return
    
    bot_username = (await ctx.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{upd.effective_user.id}"
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', (upd.effective_user.id,))
        referrals_count = c.fetchone()[0]
    except Exception as e:
        logger.error(f"Referral count error: {e}")
        referrals_count = 0
    finally:
        if conn:
            conn.close()
    
    text = (f"🔗 РЕФЕРАЛЬНАЯ ПРОГРАММА\n\n"
            f"Ваша реферальная ссылка:\n"
            f"{link}\n\n"
            f"👥 Приглашено друзей: {referrals_count}\n"
            f"📊 Вы получаете 0.5% от суммы заказов ваших друзей бонусами!\n"
            f"🎁 Друг получает 500 приветственных бонусов!\n\n"
            f"💰 Текущий баланс: {get_bonus(upd.effective_user.id)['balance']} бонусов")
    
    await upd.message.reply_text(text)

# ========== ДОСТАВКА И ПОМОЩЬ ==========

@safe_async
async def delivery_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /delivery - информация о доставке"""
    text = (f"🚚 РАСЧЁТ ДОСТАВКИ ОТ МКАД\n\n"
            f"Базовая стоимость: {DELIVERY_BASE} руб.\n\n"
            f"📌 Тарифы:\n"
            f"• 0 км (Москва): {DELIVERY_BASE} руб.\n"
            f"• 1-50 км: {DELIVERY_BASE} + км × {DELIVERY_RATE_UP_TO_50}\n"
            f"• 51-100 км: {DELIVERY_BASE} + км × {DELIVERY_RATE_UP_TO_100}\n"
            f"• 101+ км: {DELIVERY_BASE} + км × {DELIVERY_RATE_OVER_100}\n\n"
            f"📌 Самовывоз (бесплатно):\n"
            f"• Метро Давыдково\n"
            f"• Метро Южная\n"
            f"• Метро Строгино\n\n"
            f"📌 Скидка на доставку от суммы заказа:\n"
            f"• от 10 000 руб. → 5%\n"
            f"• от 15 000 руб. → 10%\n"
            f"• от 20 000 руб. → 15%\n"
            f"... до 100% бесплатно")
    
    await upd.message.reply_text(text)

@safe_async
async def help_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /help - помощь"""
    text = ("📖 ПОМОЩЬ\n\n"
            "Основные команды:\n"
            "/start - Главное меню\n"
            "/my_orders - Мои заказы\n"
            "/bonus - Бонусы\n"
            "/referral - Рефералы\n"
            "/delivery - Доставка\n"
            "/help - Эта справка\n\n"
            "Мой гараж:\n"
            "Храните VIN и описание автомобилей\n"
            "Быстрый выбор при создании заказа\n\n"
            "Бонусная система:\n"
            f"• Начисление: от 1% до 10% от суммы заказа\n"
            f"• Списание: до {MAX_BONUS_SPEND_PERCENT}% от суммы запчастей\n"
            f"• ❌ Не действует на RAVENOL и доставку\n"
            f"• Минимальная оплата деньгами: {MIN_CASH_PAYMENT} руб.\n\n"
            "Вопросы:\n"
            "По всем вопросам обращайтесь к менеджеру")
    
    await upd.message.reply_text(text)

# ========== АДМИН ПАНЕЛЬ ==========

@require_manager
@safe_async
async def admin_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, message=None):
    """Панель управления менеджера"""
    try:
        if not upd:
            return
        
        orders = get_all_orders()
        if orders is None:
            orders = []
        
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("➕ Тестовый заказ", callback_data="admin_fix")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_refresh")]
        ]
        
        for o in orders[:10]:
            if o and safe_len(o) > 0:
                order_num = clean_order_number(o[0])
                if not order_num:
                    continue
                user_name = safe_str(o[1])[:15] if safe_len(o) > 1 else "Неизвестно"
                status_text = safe_str(o[2]) if safe_len(o) > 2 else ''
                icon = get_status_icon(status_text)
                
                keyboard.append([InlineKeyboardButton(f"{icon} {order_num} | {user_name}", callback_data=f"admin_order_{order_num}")])
        
        text = "👨‍💼 АДМИН ПАНЕЛЬ\n\nВыберите заказ для управления:"
        
        if message:
            try:
                await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                if "Message is not modified" not in str(e):
                    logger.error(f"Admin menu edit error: {e}")
        else:
            if upd.message:
                await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Admin menu error: {e}")
        if upd and upd.message:
            await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

@require_manager
@safe_async
async def admin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка админ-панели"""
    if not upd or not upd.callback_query:
        return
    
    query = upd.callback_query
    data = safe_str(query.data)
    logger.info(f"[ADMIN_CALLBACK] Получен callback: {data}")
    
    try:
        await query.answer()
    except Exception as e:
        logger.error(f"Answer error: {e}")
    
    # ========== ОСНОВНЫЕ ДЕЙСТВИЯ ==========
    if data == "admin_refresh":
        await admin_menu(upd, ctx, query.message)
        return
    
    if data == "admin_stats":
        try:
            orders = get_all_orders()
            total_orders = safe_len(orders)
            total_sum = 0
            status_count = {}
            
            for o in orders:
                if o and safe_len(o) > 0:
                    order = get_order(o[0])
                    if order:
                        total_sum += order.get('total_price', 0) + order.get('delivery_price', 0)
                    status = safe_str(o[2]) if safe_len(o) > 2 else 'Неизвестно'
                    status_count[status] = status_count.get(status, 0) + 1
            
            status_text = "\n".join([f"• {s}: {c}" for s, c in status_count.items()])
            
            await query.edit_message_text(
                f"📊 СТАТИСТИКА\n\n"
                f"📦 Заказов: {total_orders}\n"
                f"💰 Сумма: {total_sum:,} руб.\n\n"
                f"По статусам:\n{status_text}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]])
            )
        except Exception as e:
            logger.error(f"Stats error: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
    if data == "admin_fix":
        try:
            conn = sqlite3.connect(DB_PATH)
            try:
                c = conn.cursor()
                num = generate_order_number()
                if num:
                    c.execute('''INSERT INTO orders (
                        order_number, user_id, user_name, vin, mileage,
                        style_city, style_highway, city, distance,
                        delivery_type, delivery_price, needed_parts,
                        status, status_text, created_at, total_price
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
                        (num, MANAGER_ID, 'Тестовый Клиент', 'TEST123', '50000',
                         'Спокойный', 'Спокойный', 'Москва', 0, 'Курьером', 500,
                         'Тестовый заказ', 'pending', '🆕 Ожидает подбора',
                         datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 0))
                    conn.commit()
                    await query.edit_message_text(f"✅ Тестовый заказ {num} создан!")
                else:
                    await query.edit_message_text("❌ Ошибка генерации номера заказа")
            finally:
                conn.close()
        except Exception as e:
            logger.error(f"Test order error: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        await admin_menu(upd, ctx, query.message)
        return
    
    if data == "admin_back":
        await admin_menu(upd, ctx, query.message)
        return
    
    # ========== ПРОСМОТР ЗАКАЗА ==========
    if data.startswith("admin_order_"):
        order_num = data[12:]  # убираем "admin_order_"
        order_num = clean_order_number(order_num)
        if not order_num:
            await query.edit_message_text("❌ Неверный номер заказа")
            return
        
        try:
            order = get_order(order_num)
            if not order:
                await query.edit_message_text("❌ Заказ не найден")
                return
            
            total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
            text = (f"📋 ЗАКАЗ {order.get('order_number', '')}\n\n"
                    f"👤 {order.get('user_name', '')}\n"
                    f"📞 {order.get('phone', 'не указан')}\n"
                    f"🏙️ Стиль город: {order.get('style_city', 'не указан')}\n"
                    f"🛣️ Стиль трасса: {order.get('style_highway', 'не указан')}\n"
                    f"🏙️ Город: {order.get('city', 'не указан')}\n"
                    f"📍 Адрес: {order.get('delivery_address', 'не указан')}\n"
                    f"🚚 Доставка: {order.get('delivery_type', 'не указана')} | {order.get('delivery_price', 0)} руб.\n"
                    f"💰 Сумма: {total_sum} руб.\n"
                    f"📦 Статус: {order.get('status_text', 'неизвестен')}")
            
            kb = [
                [InlineKeyboardButton("💰 Оплачен", callback_data=f"pay_{order_num}")],
                [InlineKeyboardButton("📦 Заказан", callback_data=f"ordered_{order_num}")],
                [InlineKeyboardButton("📦✅ Товар поступил", callback_data=f"arrived_{order_num}")],
                [InlineKeyboardButton("✅ Готов к выдаче", callback_data=f"ready_{order_num}")],
                [InlineKeyboardButton("🚚 Отправлен", callback_data=f"ship_{order_num}")],
                [InlineKeyboardButton("🏠 Доставлен", callback_data=f"del_{order_num}")],
                [InlineKeyboardButton("📋 Выдан", callback_data=f"issued_{order_num}")],
                [InlineKeyboardButton("✏️ Изменить доставку", callback_data=f"edit_delivery_{order_num}")],
                [InlineKeyboardButton("✏️ Редактировать товары", callback_data=f"admin_edit_items_{order_num}")],
                [InlineKeyboardButton("📜 История изменений", callback_data=f"order_changes_{order_num}")],
                [InlineKeyboardButton("🔍 Детали", callback_data=f"detail_{order_num}")],
                [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_{order_num}")],
                [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
            ]
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"View order error: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
    # ========== ИСТОРИЯ ИЗМЕНЕНИЙ ==========
    if data.startswith("order_changes_"):
        order_num = data[14:]  # убираем "order_changes_"
        order_num = clean_order_number(order_num)
        
        changes = get_order_changes(order_num, 20)
        
        if not changes:
            await query.edit_message_text(f"📜 ИСТОРИЯ ИЗМЕНЕНИЙ ЗАКАЗА {order_num}\n\nНет записей об изменениях.")
            return
        
        text = f"📜 ИСТОРИЯ ИЗМЕНЕНИЙ ЗАКАЗА {order_num}\n\n"
        for change in changes:
            if change and safe_len(change) >= 5:
                action = safe_str(change[0])
                old_val = safe_str(change[1])[:100]
                new_val = safe_str(change[2])[:100]
                comment = safe_str(change[3])
                created = safe_str(change[4])
                
                text += f"🕐 {created[:16]}\n"
                if action == 'remove_items':
                    text += f"   🗑️ Удалены товары: {old_val}\n"
                elif action == 'add_item':
                    text += f"   ➕ Добавлен товар: {new_val}\n"
                elif action == 'change_price':
                    text += f"   💰 Изменена цена: {old_val} → {new_val}\n"
                elif action == 'cancel_by_user':
                    text += f"   ❌ Отменён пользователем\n"
                else:
                    text += f"   📝 {action}: {new_val}\n"
                if comment:
                    text += f"   💬 Комментарий: {comment}\n"
                text += "\n"
        
        kb = [[InlineKeyboardButton("◀️ Назад к заказу", callback_data=f"admin_order_{order_num}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    # ========== ДЕТАЛИ ЗАКАЗА ==========
    if data.startswith("detail_"):
        order_num = data[7:]  # убираем "detail_"
        order_num = clean_order_number(order_num)
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        text = (f"🔍 ПОЛНАЯ ИНФОРМАЦИЯ О ЗАКАЗЕ {order_num}\n\n"
                f"👤 Клиент: {order.get('user_name', 'Не указан')}\n"
                f"📞 Телефон: {order.get('phone', 'Не указан')}\n"
                f"🚗 VIN: {order.get('vin', 'Не указан')}\n"
                f"📊 Пробег: {order.get('mileage', 'Не указан')} км\n"
                f"🏙️ Стиль город: {order.get('style_city', 'Не указан')}\n"
                f"🛣️ Стиль трасса: {order.get('style_highway', 'Не указан')}\n"
                f"🏙️ Город: {order.get('city', 'Не указан')}\n"
                f"📍 Адрес: {order.get('delivery_address', 'Не указан')}\n"
                f"🚚 Доставка: {order.get('delivery_type', 'Не указана')} | {order.get('delivery_price', 0)} руб.\n"
                f"💰 Сумма запчастей: {order.get('total_price', 0)} руб.\n"
                f"📦 Статус: {order.get('status_text', 'Неизвестен')}\n"
                f"📅 Создан: {order.get('created_at', 'Не указана')}")
        
        if order.get('tracking_number'):
            text += f"\n📮 Трек-номер: {order.get('tracking_number')}"
        
        if order.get('selected_products'):
            text += f"\n\n📦 ПОДБОР МЕНЕДЖЕРА:\n{order.get('selected_products')[:500]}"
        
        if order.get('needed_parts'):
            text += f"\n\n📝 ЗАПЧАСТИ КЛИЕНТА:\n{order.get('needed_parts')[:500]}"
        
        if order.get('final_order') and order.get('final_order') not in [None, 'None', '[]', '{}']:
            text += f"\n\n✅ ВЫБРАННЫЕ ЗАПЧАСТИ:\n{order.get('final_order')[:500]}"
        
        kb = [[InlineKeyboardButton("◀️ Назад к заказу", callback_data=f"admin_order_{order_num}")]]
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    # ========== РЕДАКТИРОВАНИЕ ТОВАРОВ ==========
    if data.startswith("admin_edit_items_"):
        order_num = data[17:]  # убираем "admin_edit_items_"
        order_num = clean_order_number(order_num)
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        final_order = order.get('final_order', '')
        
        kb = [
            [InlineKeyboardButton("🗑️ Удалить товары", callback_data=f"admin_remove_items_{order_num}")],
            [InlineKeyboardButton("➕ Добавить товар", callback_data=f"admin_add_item_{order_num}")],
            [InlineKeyboardButton("💰 Изменить цену", callback_data=f"admin_change_price_{order_num}")],
            [InlineKeyboardButton("◀️ Назад", callback_data=f"admin_order_{order_num}")]
        ]
        
        text = f"✏️ РЕДАКТИРОВАНИЕ ЗАКАЗА {order_num}\n\n"
        text += f"💰 Текущая сумма: {order.get('total_price', 0)} руб.\n\n"
        text += "Товары в заказе:\n"
        
        if final_order and final_order not in [None, 'None', '[]', '{}']:
            try:
                selected_parts = ast.literal_eval(final_order)
                if isinstance(selected_parts, list) and selected_parts:
                    for i, part in enumerate(selected_parts):
                        if isinstance(part, dict):
                            part_name = part.get('name', 'неизвестно')
                            part_price = part.get('price', 0)
                            text += f"{i+1}. {part_name} — {part_price} руб.\n"
                else:
                    text += "Нет товаров\n"
            except:
                text += "Ошибка отображения\n"
        else:
            text += "Нет товаров\n"
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    # ========== АДМИН УДАЛЯЕТ ТОВАРЫ ==========
    if data.startswith("admin_remove_items_"):
        order_num = data[18:]  # убираем "admin_remove_items_"
        order_num = clean_order_number(order_num)
        logger.info(f"[ADMIN_REMOVE] Начало удаления товаров для заказа: {order_num}")
        
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        final_order = order.get('final_order', '')
        
        if not final_order or final_order in [None, 'None', '[]', '{}']:
            await query.edit_message_text("❌ В заказе нет товаров для удаления")
            return
        
        try:
            selected_parts = ast.literal_eval(final_order)
            if not isinstance(selected_parts, list) or not selected_parts:
                await query.edit_message_text("❌ Нет товаров для удаления")
                return
            
            admin_remove_sessions[order_num] = {
                'parts': selected_parts.copy(),
                'selected': set()
            }
            
            kb = []
            for i, part in enumerate(selected_parts):
                if isinstance(part, dict):
                    part_name = part.get('name', 'Неизвестно')[:35]
                    part_price = part.get('price', 0)
                    kb.append([InlineKeyboardButton(f"⬜ {part_name} — {part_price} руб.", 
                                                   callback_data=f"admin_toggle_{order_num}_{i}")])
                else:
                    kb.append([InlineKeyboardButton(f"⬜ {safe_str(part)[:35]}", 
                                                   callback_data=f"admin_toggle_{order_num}_{i}")])
            
            kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ УДАЛЕНИЕ", callback_data=f"admin_remove_confirm_{order_num}")])
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"admin_edit_items_{order_num}")])
            
            text = f"🗑️ УДАЛЕНИЕ ТОВАРОВ ИЗ ЗАКАЗА {order_num}\n\n"
            text += "Нажмите на товар, чтобы отметить его для удаления.\n\n"
            text += "⬜ - товар остаётся\n"
            text += "✅ - товар будет удалён\n\n"
            text += f"💰 Текущая сумма: {order.get('total_price', 0)} руб.\n"
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
            
        except Exception as e:
            logger.error(f"[ADMIN_REMOVE] Ошибка: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
    if data.startswith("admin_toggle_"):
        await query.answer()
        parts = data.split('_')
        if len(parts) < 4:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        
        order_num = parts[2]
        item_idx = safe_int(parts[3])
        
        if order_num not in admin_remove_sessions:
            await query.edit_message_text("❌ Сессия истекла. Начните заново.")
            return
        
        selected = admin_remove_sessions[order_num]['selected']
        if item_idx in selected:
            selected.remove(item_idx)
        else:
            selected.add(item_idx)
        
        selected_parts = admin_remove_sessions[order_num]['parts']
        
        kb = []
        for i, part in enumerate(selected_parts):
            if isinstance(part, dict):
                part_name = part.get('name', 'Неизвестно')[:35]
                part_price = part.get('price', 0)
                check = "✅" if i in selected else "⬜"
                kb.append([InlineKeyboardButton(f"{check} {part_name} — {part_price} руб.", 
                                               callback_data=f"admin_toggle_{order_num}_{i}")])
            else:
                check = "✅" if i in selected else "⬜"
                kb.append([InlineKeyboardButton(f"{check} {safe_str(part)[:35]}", 
                                               callback_data=f"admin_toggle_{order_num}_{i}")])
        
        kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ УДАЛЕНИЕ", callback_data=f"admin_remove_confirm_{order_num}")])
        kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"admin_edit_items_{order_num}")])
        
        order = get_order(order_num)
        text = f"🗑️ УДАЛЕНИЕ ТОВАРОВ ИЗ ЗАКАЗА {order_num}\n\n"
        text += "Нажмите на товар, чтобы отметить его для удаления.\n\n"
        text += "⬜ - товар остаётся\n"
        text += "✅ - товар будет удалён\n\n"
        if order:
            text += f"💰 Текущая сумма: {order.get('total_price', 0)} руб.\n"
        
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Error updating message: {e}")
        return
    
    if data.startswith("admin_remove_confirm_"):
        order_num = data[20:]  # убираем "admin_remove_confirm_"
        order_num = clean_order_number(order_num)
        logger.info(f"[ADMIN_CONFIRM] Подтверждение удаления для заказа: {order_num}")
        
        if order_num not in admin_remove_sessions:
            await query.edit_message_text("❌ Сессия истекла. Начните заново.")
            return
        
        selected_items = admin_remove_sessions[order_num]['selected']
        selected_parts = admin_remove_sessions[order_num]['parts']
        
        if not selected_items:
            await query.edit_message_text("❌ Не выбрано ни одного товара")
            return
        
        if len(selected_items) >= len(selected_parts):
            await query.edit_message_text(
                "❌ Нельзя удалить все товары из заказа!\n\n"
                "В заказе должен остаться хотя бы один товар."
            )
            return
        
        remaining_parts = []
        removed_names = []
        
        for i, part in enumerate(selected_parts):
            if i not in selected_items:
                remaining_parts.append(part)
            else:
                if isinstance(part, dict):
                    removed_names.append(part.get('name', 'Товар'))
                else:
                    removed_names.append(safe_str(part))
        
        new_total = sum(p.get('price', 0) for p in remaining_parts if isinstance(p, dict))
        order = get_order(order_num)
        delivery_price = order.get('delivery_price', 0) if order else 0
        
        update_order(order_num, final_order=str(remaining_parts), total_price=new_total)
        
        add_order_change(order_num, MANAGER_ID, 'remove_items', ', '.join(removed_names), 
                         f"Удалено {len(selected_items)} товаров", "Удаление товаров администратором")
        
        if order:
            try:
                await ctx.bot.send_message(
                    order.get('user_id'),
                    text=f"✏️ Заказ {order_num} изменён менеджером!\n\n"
                         f"🗑️ Удалены товары: {', '.join(removed_names)}\n"
                         f"💰 Новая сумма: {new_total + delivery_price} руб.\n\n"
                         f"По вопросам обращайтесь к менеджеру."
                )
            except Exception as e:
                logger.error(f"Error notifying user: {e}")
        
        del admin_remove_sessions[order_num]
        
        await query.edit_message_text(
            f"✅ Товары удалены!\n\n"
            f"📦 Заказ: {order_num}\n"
            f"🗑️ Удалено: {len(selected_items)} товаров\n"
            f"💰 Новая сумма: {new_total + delivery_price} руб.\n\n"
            f"Клиент получил уведомление."
        )
        return
    
    # ========== АДМИН ДОБАВЛЯЕТ ТОВАР ==========
    if data.startswith("admin_add_item_"):
        order_num = data[16:]  # убираем "admin_add_item_"
        order_num = clean_order_number(order_num)
        safe_set_user_data(ctx, 'admin_add_item_order', order_num)
        await query.edit_message_text(
            f"➕ ДОБАВЛЕНИЕ ТОВАРА В ЗАКАЗ {order_num}\n\n"
            f"Введите название товара:"
        )
        return AdminAddItemStates.NAME
    
    # ========== АДМИН МЕНЯЕТ ЦЕНУ ==========
    if data.startswith("admin_change_price_"):
        order_num = data[18:]  # убираем "admin_change_price_"
        order_num = clean_order_number(order_num)
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        final_order = order.get('final_order', '')
        if not final_order or final_order in [None, 'None', '[]', '{}']:
            await query.edit_message_text("❌ В заказе нет товаров для изменения цены")
            return
        
        try:
            selected_parts = ast.literal_eval(final_order)
            if not isinstance(selected_parts, list) or not selected_parts:
                await query.edit_message_text("❌ Нет товаров для изменения цены")
                return
            
            safe_set_user_data(ctx, 'admin_change_price_order', order_num)
            safe_set_user_data(ctx, 'admin_change_price_parts', selected_parts.copy())
            
            kb = []
            for i, part in enumerate(selected_parts):
                if isinstance(part, dict):
                    part_name = part.get('name', 'Неизвестно')[:35]
                    part_price = part.get('price', 0)
                    kb.append([InlineKeyboardButton(f"💰 {part_name} — {part_price} руб.", 
                                                   callback_data=f"admin_select_price_item_{order_num}_{i}")])
            
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"admin_edit_items_{order_num}")])
            
            await query.edit_message_text(
                f"💰 ИЗМЕНЕНИЕ ЦЕНЫ ТОВАРА В ЗАКАЗЕ {order_num}\n\n"
                f"Выберите товар, цену которого хотите изменить:",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception as e:
            logger.error(f"Change price error: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
    if data.startswith("admin_select_price_item_"):
        await query.answer()
        parts = data.split('_')
        if len(parts) < 6:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[4]
        item_idx = safe_int(parts[5])
        
        if safe_get_user_data(ctx, 'admin_change_price_order') != order_num:
            await query.edit_message_text("❌ Сессия истекла")
            return
        
        safe_set_user_data(ctx, 'admin_change_price_idx', item_idx)
        selected_parts = safe_get_user_data(ctx, 'admin_change_price_parts', [])
        
        if item_idx < safe_len(selected_parts) and isinstance(selected_parts[item_idx], dict):
            part_name = selected_parts[item_idx].get('name', 'Товар')
            part_price = selected_parts[item_idx].get('price', 0)
            
            await query.edit_message_text(
                f"💰 ИЗМЕНЕНИЕ ЦЕНЫ ТОВАРА\n\n"
                f"📦 Заказ: {order_num}\n"
                f"📝 Товар: {part_name}\n"
                f"💵 Текущая цена: {part_price} руб.\n\n"
                f"Введите новую цену (целое число, руб.):"
            )
            return AdminChangePriceStates.NEW_PRICE
        else:
            await query.edit_message_text("❌ Товар не найден")
        return
    
    # ========== ИЗМЕНЕНИЕ ДОСТАВКИ ==========
    if data.startswith("edit_delivery_"):
        order_num = data[14:]  # убираем "edit_delivery_"
        order_num = clean_order_number(order_num)
        kb = [
            [InlineKeyboardButton("🚚 Курьером", callback_data=f"set_delivery_{order_num}_Курьером")],
            [InlineKeyboardButton("📦 Самовывоз", callback_data=f"set_delivery_{order_num}_Самовывоз")],
            [InlineKeyboardButton("🚛 Сторонняя фирма", callback_data=f"set_delivery_{order_num}_Сторонняя фирма")],
            [InlineKeyboardButton("◀️ Назад", callback_data=f"admin_order_{order_num}")]
        ]
        await query.edit_message_text("✏️ Выберите способ доставки:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data.startswith("set_delivery_"):
        parts = data.split('_')
        if len(parts) < 4:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[2]
        new_delivery = parts[3]
        order_num = clean_order_number(order_num)
        order = get_order(order_num)
        if order:
            km = order.get('distance', 0)
            if new_delivery == "Курьером":
                price = calc_delivery_price(km)
                desc = f"Курьер: {km} км = {price} руб."
            elif new_delivery == "Самовывоз":
                price = 0
                desc = "Самовывоз (бесплатно)"
            else:
                price = 0
                desc = "Сторонняя фирма (стоимость уточнит менеджер)"
            update_order(order_num, delivery_type=new_delivery, delivery_price=price)
            await ctx.bot.send_message(order.get('user_id'), text=f"✏️ Доставка изменена: {new_delivery}\n{desc}")
            await query.edit_message_text(f"✅ Доставка изменена!\n\n{desc}")
        return
    
    # ========== ОТПРАВЛЕН (SHIP) ==========
    if data.startswith("ship_"):
        order_num = data[5:]  # убираем "ship_"
        order_num = clean_order_number(order_num)
        
        if not order_num:
            await query.edit_message_text("❌ Не удалось определить номер заказа")
            return
        
        order = get_order(order_num)
        if not order:
            await query.edit_message_text(f"❌ Заказ {order_num} не найден!")
            return
        
        safe_set_user_data(ctx, 'track_for', order_num)
        await query.edit_message_text(
            f"📦 Введите трек-номер для заказа {order_num}:\n\n"
            f"⬇️ ВАЖНО! ⬇️\n\n"
            f"1️⃣ НАЖМИТЕ НА ЭТО СООБЩЕНИЕ\n"
            f"2️⃣ Выберите «ОТВЕТИТЬ» (Reply)\n"
            f"3️⃣ Введите трек-номер\n"
            f"4️⃣ Отправьте\n\n"
            f"❌ Не пишите просто в чат - бот не поймёт!"
        )
        return
    
    # ========== ОСТАЛЬНЫЕ СТАТУСЫ ==========
    if data.startswith("pay_"):
        order_num = data[4:]  # убираем "pay_"
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='paid'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order.get('user_id'), text=f"✅ Заказ {order_num} оплачен! Спасибо за покупку!")
                final_order = order.get('final_order', '')
                if final_order and final_order not in [None, 'None', '[]', '{}']:
                    try:
                        selected_parts = ast.literal_eval(final_order)
                        if isinstance(selected_parts, list):
                            bonus_percent = get_bonus_percent(order.get('user_id'))
                            eligible_for_bonus = 0
                            for p in selected_parts:
                                if isinstance(p, dict) and not is_ravenol_product(p.get('name', '')):
                                    eligible_for_bonus += p.get('price', 0)
                            bonus = int(eligible_for_bonus * bonus_percent / 100)
                            if bonus > 0:
                                add_bonus(order.get('user_id'), order_num, bonus, f"Заказ {order_num} ({bonus_percent}% от {eligible_for_bonus} руб.)")
                    except Exception as e:
                        logger.error(f"Bonus calculation error: {e}")
            await query.edit_message_text(query.message.text + "\n\n✅ СТАТУС: ОПЛАЧЕН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("ordered_"):
        order_num = data[8:]  # убираем "ordered_"
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='ordered'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order.get('user_id'), text=f"📦 Заказ {order_num} заказан у поставщика! Ожидайте поступления.")
            await query.edit_message_text(query.message.text + "\n\n✅ СТАТУС: ЗАКАЗАН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("arrived_"):
        order_num = data[8:]  # убираем "arrived_"
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='arrived'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order.get('user_id'), text=f"📦✅ Заказ {order_num}\n\nТовар поступил на склад!")
            await query.edit_message_text(query.message.text + "\n\n✅ СТАТУС: ТОВАР ПОСТУПИЛ")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("ready_"):
        order_num = data[6:]  # убираем "ready_"
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='ready'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order.get('user_id'), text=f"✅ Заказ {order_num} готов к выдаче! Можете забрать.")
            await query.edit_message_text(query.message.text + "\n\n✅ СТАТУС: ГОТОВ К ВЫДАЧЕ")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("del_"):
        order_num = data[4:]  # убираем "del_"
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='delivered'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order.get('user_id'), text=f"🏠 Заказ {order_num} доставлен! Спасибо за покупку!")
            await query.edit_message_text(query.message.text + "\n\n✅ СТАТУС: ДОСТАВЛЕН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("issued_"):
        order_num = data[7:]  # убираем "issued_"
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='issued'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order.get('user_id'), text=f"📋 Заказ {order_num} ВЫДАН!\n\nСпасибо за покупку!")
            await query.edit_message_text(query.message.text + "\n\n✅ СТАТУС: ВЫДАН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("cancel_"):
        order_num = data[7:]  # убираем "cancel_"
        order_num = clean_order_number(order_num)
        
        order = get_order(order_num)
        if order:
            conn = None
            try:
                conn = sqlite3.connect(DB_PATH)
                c = conn.cursor()
                c.execute('SELECT amount FROM bonus_history WHERE order_number = ? AND type = "earned"', (order_num,))
                bonus_row = c.fetchone()
                if bonus_row and bonus_row[0] > 0:
                    refund_bonus(order.get('user_id'), order_num, bonus_row[0], f"Возврат бонусов при отмене заказа {order_num}")
                    await ctx.bot.send_message(order.get('user_id'), text=f"❌ Заказ {order_num} отменён менеджером.\n\n💰 Бонусы в размере {bonus_row[0]} руб. были списаны.")
                else:
                    await ctx.bot.send_message(order.get('user_id'), text=f"❌ Заказ {order_num} отменён менеджером.")
            except Exception as e:
                logger.error(f"Cancel bonus error: {e}")
            finally:
                if conn:
                    conn.close()
        
        if update_order(order_num, status='cancelled'):
            await query.edit_message_text(query.message.text + "\n\n✅ СТАТУС: ОТМЕНЁН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    # Если ничего не подошло
    await admin_menu(upd, ctx, query.message)

# ========== АДМИН ДОБАВЛЯЕТ ТОВАР - ВВОД НАЗВАНИЯ ==========

@safe_async
async def admin_add_item_name_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ввод названия товара для добавления"""
    if not upd or not upd.message:
        return ConversationHandler.END
    
    order_num = safe_get_user_data(ctx, 'admin_add_item_order')
    if not order_num:
        await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return ConversationHandler.END
    
    name = upd.message.text.strip()
    if not name:
        await upd.message.reply_text("❌ Название товара не может быть пустым. Попробуйте снова:")
        return AdminAddItemStates.NAME
    
    safe_set_user_data(ctx, 'admin_add_item_name', name)
    await upd.message.reply_text(
        f"➕ ДОБАВЛЕНИЕ ТОВАРА В ЗАКАЗ {order_num}\n\n"
        f"📝 Название: {name}\n\n"
        f"Введите цену товара (целое число, руб.):"
    )
    return AdminAddItemStates.PRICE

@safe_async
async def admin_add_item_price_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ввод цены товара и добавление"""
    if not upd or not upd.message:
        return ConversationHandler.END
    
    order_num = safe_get_user_data(ctx, 'admin_add_item_order')
    if not order_num:
        await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return ConversationHandler.END
    
    try:
        price = int(upd.message.text.strip())
        if price <= 0:
            raise ValueError
    except ValueError:
        await upd.message.reply_text("❌ Введите корректную цену (целое положительное число):")
        return AdminAddItemStates.PRICE
    
    name = safe_get_user_data(ctx, 'admin_add_item_name', 'Новый товар')
    
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text("❌ Заказ не найден")
        return ConversationHandler.END
    
    final_order = order.get('final_order', '')
    selected_parts = []
    
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        try:
            selected_parts = ast.literal_eval(final_order)
            if not isinstance(selected_parts, list):
                selected_parts = []
        except:
            selected_parts = []
    
    new_item = {'name': name, 'price': price}
    selected_parts.append(new_item)
    
    new_total = sum(p.get('price', 0) for p in selected_parts if isinstance(p, dict))
    delivery_price = order.get('delivery_price', 0)
    
    update_order(order_num, final_order=str(selected_parts), total_price=new_total)
    
    add_order_change(order_num, MANAGER_ID, 'add_item', '', f"{name} - {price} руб.", "Добавление товара администратором")
    
    await upd.message.bot.send_message(
        order.get('user_id'),
        text=f"✏️ Заказ {order_num} изменён менеджером!\n\n"
             f"➕ Добавлен товар: {name} - {price} руб.\n"
             f"💰 Новая сумма: {new_total + delivery_price} руб.\n\n"
             f"По вопросам обращайтесь к менеджеру."
    )
    
    await upd.message.reply_text(
        f"✅ Товар добавлен в заказ {order_num}!\n\n"
        f"➕ {name} — {price} руб.\n"
        f"💰 Новая сумма: {new_total + delivery_price} руб.\n\n"
        f"Клиент получил уведомление."
    )
    
    safe_del_user_data(ctx, 'admin_add_item_order')
    safe_del_user_data(ctx, 'admin_add_item_name')
    
    return ConversationHandler.END

# ========== АДМИН МЕНЯЕТ ЦЕНУ - ВВОД НОВОЙ ЦЕНЫ ==========

@safe_async
async def admin_change_price_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ввод новой цены для товара"""
    if not upd or not upd.message:
        return ConversationHandler.END
    
    order_num = safe_get_user_data(ctx, 'admin_change_price_order')
    if not order_num:
        await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return ConversationHandler.END
    
    try:
        new_price = int(upd.message.text.strip())
        if new_price <= 0:
            raise ValueError
    except ValueError:
        await upd.message.reply_text("❌ Введите корректную цену (целое положительное число):")
        return AdminChangePriceStates.NEW_PRICE
    
    item_idx = safe_get_user_data(ctx, 'admin_change_price_idx', -1)
    selected_parts = safe_get_user_data(ctx, 'admin_change_price_parts', [])
    
    if item_idx < 0 or item_idx >= safe_len(selected_parts):
        await upd.message.reply_text("❌ Ошибка: товар не найден")
        return ConversationHandler.END
    
    old_price = selected_parts[item_idx].get('price', 0)
    old_name = selected_parts[item_idx].get('name', 'Товар')
    selected_parts[item_idx]['price'] = new_price
    
    new_total = sum(p.get('price', 0) for p in selected_parts if isinstance(p, dict))
    delivery_price = get_order(order_num).get('delivery_price', 0)
    
    update_order(order_num, final_order=str(selected_parts), total_price=new_total)
    
    add_order_change(order_num, MANAGER_ID, 'change_price', f"{old_name}: {old_price}", f"{old_name}: {new_price}", "Изменение цены администратором")
    
    order = get_order(order_num)
    if order:
        await upd.message.bot.send_message(
            order.get('user_id'),
            text=f"✏️ Заказ {order_num} изменён менеджером!\n\n"
                 f"💰 Изменена цена товара: {old_name}\n"
                 f"💵 Было: {old_price} руб.\n"
                 f"💵 Стало: {new_price} руб.\n"
                 f"💰 Новая сумма заказа: {new_total + delivery_price} руб.\n\n"
                 f"По вопросам обращайтесь к менеджеру."
        )
    
    await upd.message.reply_text(
        f"✅ Цена изменена!\n\n"
        f"📦 Заказ: {order_num}\n"
        f"📝 Товар: {old_name}\n"
        f"💰 {old_price} руб. → {new_price} руб.\n"
        f"💳 Новая сумма заказа: {new_total + delivery_price} руб.\n\n"
        f"Клиент получил уведомление."
    )
    
    safe_del_user_data(ctx, 'admin_change_price_order')
    safe_del_user_data(ctx, 'admin_change_price_parts')
    safe_del_user_data(ctx, 'admin_change_price_idx')
    
    return ConversationHandler.END

# ========== ТРЕК-НОМЕР ==========

@safe_async
async def track_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода трек-номера менеджера"""
    if not upd or not upd.effective_user or upd.effective_user.id != MANAGER_ID:
        return
    
    if not upd.message or not upd.message.reply_to_message:
        return
    
    tracking = upd.message.text.strip()
    if len(tracking) < 3:
        return
    
    order_num = None
    
    # Проверяем сохраненный заказ
    order_num = safe_get_user_data(ctx, 'track_for')
    
    # Если нет, ищем в тексте сообщения
    if not order_num and upd.message.reply_to_message:
        match = re.search(r'заказа (RVN-[A-Z0-9]{6})', upd.message.reply_to_message.text or '')
        if match:
            order_num = match.group(1)
    
    # Если все еще нет, ищем в истории
    if not order_num:
        try:
            async for msg in upd.message.chat.iter_history(limit=20):
                match = re.search(r'(RVN-[A-Z0-9]{6})', msg.text or '')
                if match:
                    order_num = match.group(1)
                    break
        except Exception as e:
            logger.error(f"History search error: {e}")
    
    if not order_num:
        await upd.message.reply_text("❌ Не удалось определить заказ")
        return
    
    order_num = clean_order_number(order_num)
    if not order_num:
        await upd.message.reply_text("❌ Неверный формат заказа")
        return
    
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text(f"❌ Заказ {order_num} не найден")
        return
    
    if update_order(order_num, tracking_number=tracking, status='shipped'):
        await ctx.bot.send_message(
            order.get('user_id'),
            text=f"📦 Заказ {order_num} отправлен!\n\n📮 Трек-номер: {tracking}"
        )
        
        await upd.message.reply_text(
            f"✅ Трек-номер добавлен!\n\n📦 {order_num}\n📮 {tracking}"
        )
    else:
        await upd.message.reply_text(f"❌ Ошибка при обновлении заказа")
    
    safe_del_user_data(ctx, 'track_for')

# ========== ОТВЕТ МЕНЕДЖЕРА (ПОДБОР ЗАПЧАСТЕЙ) ==========

@require_manager
@safe_async
async def manager_reply(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ответ менеджера на заказ (подбор запчастей)"""
    if not upd or not upd.message or not upd.message.reply_to_message:
        return
    
    reply_text = upd.message.reply_to_message.text or ""
    match = re.search(r"НОВЫЙ ЗАКАЗ #(RVN-\w{6})", reply_text) or re.search(r"(RVN-\w{6})", reply_text)
    
    if not match:
        await upd.message.reply_text("❌ Не удалось определить номер заказа.")
        return
    
    order_num = match.group(1)
    order = get_order(order_num)
    
    if not order:
        await upd.message.reply_text(f"❌ Заказ {order_num} не найден.")
        return
    
    if not upd.message.text:
        await upd.message.reply_text("❌ Введите подбор запчастей.")
        return
    
    products = parse_products(upd.message.text)
    if not products:
        await upd.message.reply_text("❌ Не распознано. Формат:\nНазвание = 1000 руб")
        return
    
    update_order(order_num, selected_products=upd.message.text, status='waiting_selection')
    
    kb = [[InlineKeyboardButton(f"⬜ {p['name'][:25]} — {p['price']} руб.", callback_data=f"sel_{order_num}_{i}")] for i, p in enumerate(products)]
    kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ВЫБОР", callback_data=f"fin_{order_num}")])
    
    await ctx.bot.send_message(
        order.get('user_id'),
        text=f"🛒 ПОДБОР ЗАПЧАСТЕЙ ДЛЯ ЗАКАЗА #{order_num}\n\n"
             f"Менеджер подобрал для вас следующие позиции:\n\n"
             f"Выберите нужные запчасти (можно отметить несколько):",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    
    await upd.message.reply_text(f"✅ Подбор запчастей для заказа {order_num} отправлен клиенту!")

# ========== ВЫБОР ЗАПЧАСТЕЙ КЛИЕНТОМ ==========

@safe_async
async def select_cb(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выбор запчасти клиентом"""
    query = upd.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    if len(parts) < 3:
        await query.edit_message_text("❌ Ошибка формата данных")
        return
    
    order_num = parts[1]
    idx = safe_int(parts[2])
    uid = query.from_user.id
    
    order = get_order(order_num)
    if not order or not order.get('selected_products'):
        await query.edit_message_text("❌ Ошибка: подбор запчастей не найден")
        return
    
    if order.get('user_id') != uid:
        await query.answer("❌ Это не ваш заказ!", show_alert=True)
        return
    
    products = parse_products(order.get('selected_products', ''))
    
    if uid not in user_selections:
        user_selections[uid] = {}
    if order_num not in user_selections[uid]:
        user_selections[uid][order_num] = set()
    
    s = user_selections[uid][order_num]
    
    if idx in s:
        s.remove(idx)
    else:
        s.add(idx)
    
    kb = []
    for i, p in enumerate(products):
        cb = "✅" if i in s else "⬜"
        display_name = p['name'][:25] + ".." if len(p['name']) > 25 else p['name']
        kb.append([InlineKeyboardButton(f"{cb} {display_name} — {p['price']} руб.", callback_data=f"sel_{order_num}_{i}")])
    kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ВЫБОР", callback_data=f"fin_{order_num}")])
    
    await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

@safe_async
async def finalize_cb(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение выбора запчастей клиентом"""
    query = upd.callback_query
    await query.answer("⏳ Оформление заказа...", show_alert=False)
    
    order_num = query.data.split('_')[1]
    uid = query.from_user.id
    
    order = get_order(order_num)
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    if order.get('user_id') != uid:
        await query.answer("❌ Это не ваш заказ!", show_alert=True)
        return
    
    if uid not in user_selections or order_num not in user_selections[uid] or not user_selections[uid][order_num]:
        await query.edit_message_text("❌ Ничего не выбрано. Пожалуйста, выберите хотя бы одну запчасть.")
        return
    
    products = parse_products(order.get('selected_products', ''))
    selected = []
    total = 0
    
    for idx in user_selections[uid][order_num]:
        if idx < safe_len(products):
            selected.append(products[idx])
            total += products[idx]['price']
    
    if not selected:
        await query.edit_message_text("❌ Вы не выбрали ни одной запчасти.")
        return
    
    delivery_price = order.get('delivery_price', 0)
    delivery_disc = delivery_discount(total)
    
    if delivery_disc >= 100:
        delivery_final = 0
        delivery_text = "🚚 Доставка: БЕСПЛАТНО!"
    elif delivery_disc > 0:
        discount_amount = int(delivery_price * delivery_disc / 100)
        delivery_final = delivery_price - discount_amount
        delivery_text = f"🚚 Доставка: {delivery_price} руб. → скидка {delivery_disc}% = {delivery_final} руб."
    else:
        delivery_final = delivery_price
        delivery_text = f"🚚 Доставка: {delivery_price} руб."
    
    final_total = total + delivery_final
    update_order(order_num, total_price=total, final_order=str(selected), status='waiting_payment')
    
    result = f"✅ ЗАКАЗ #{order_num} ПОДТВЕРЖДЁН!\n\n"
    
    for p in selected:
        wrapped_name = wrap_text(p['name'], 25)
        ravenol_mark = " (Ravenol, бонусы не начислены)" if is_ravenol_product(p['name']) else ""
        result += f"• {wrapped_name}\n   → {p['price']} руб.{ravenol_mark}\n"
    
    result += f"\n{delivery_text}\n\n"
    result += f"💰 ИТОГО К ОПЛАТЕ: {final_total} руб."
    result += "\n\n📞 Менеджер свяжется с вами для уточнения оплаты."
    
    await query.edit_message_text(result)
    await ctx.bot.send_message(
        MANAGER_ID,
        f"✅ ЗАКАЗ {order_num} ПОДТВЕРЖДЁН КЛИЕНТОМ!\n\n"
        f"👤 Клиент: {order.get('user_name', '')}\n"
        f"💰 Сумма: {final_total} руб."
    )
    
    if uid in user_selections:
        del user_selections[uid][order_num]

# ========== view_order (уже есть выше) и другие обработчики ==========

@require_order_owner
@safe_async
async def view_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Просмотр деталей заказа (уже реализован выше)"""
    # Эта функция уже определена выше
    pass

# ========== ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ==========

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    error_str = str(context.error)
    logger.error(f"Exception: {error_str}")
    
    # Игнорируем известные не критичные ошибки
    ignore_patterns = [
        "Message is not modified",
        "Inline keyboard expected",
        "object of type 'NoneType' has no len()",
        "'NoneType' object has no attribute",
        "timed out",
        "Conflict",
        "Query is too old",
        "Bot was blocked"
    ]
    
    for pattern in ignore_patterns:
        if pattern in error_str:
            logger.info(f"Ignored error: {pattern}")
            return
    
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text(
                "⚠️ Произошла ошибка\n\n"
                "Пожалуйста, попробуйте позже или обратитесь к администратору.\n\n"
                f"Ошибка: {error_str[:100]}"
            )
        except Exception as e:
            logger.error(f"Error sending error message: {e}")

# ========== ЗАПУСК ==========

def main():
    """Запуск бота"""
    try:
        init_db()
        logger.info("Database initialized successfully")
    except Exception as e:
        logger.error(f"DB init error: {e}")
    
    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(backup_db, 'cron', hour=3, minute=0)
        scheduler.start()
        logger.info("Scheduler started - daily backup at 03:00")
    except Exception as e:
        logger.error(f"Scheduler error: {e}")
    
    # Создаем приложение
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Команды бота
    commands = [
        ("start", "Главное меню"),
        ("my_orders", "Мои заказы"),
        ("bonus", "Бонусы"),
        ("referral", "Рефералы"),
        ("delivery", "Доставка"),
        ("help", "Помощь"),
        ("menu", "Панель управления"),
    ]
    
    async def set_commands(application):
        try:
            await application.bot.set_my_commands(commands)
            logger.info("Commands set successfully")
        except Exception as e:
            logger.error(f"Set commands error: {e}")
    
    app.post_init = set_commands
    
    # ========== CONVERSATION HANDLERS ==========
    
    # Order ConversationHandler
    order_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(🛒 Новый заказ)$"), new_order)],
        states={
            OrderStates.VIN: [
                CallbackQueryHandler(order_auto_callback, pattern="^order_"),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_vin)
            ],
            OrderStates.MILEAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mileage)],
            OrderStates.STYLE_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_city)],
            OrderStates.STYLE_HIGHWAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_highway)],
            OrderStates.DELIVERY_TYPE: [MessageHandler(filters.Regex("^(Курьером|Самовывоз|Сторонняя фирма)$"), get_delivery_type)],
            OrderStates.ADDRESS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_address),
                CallbackQueryHandler(pickup_callback, pattern="^pickup_")
            ],
            OrderStates.PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            OrderStates.PART_NODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_part_node)],
            OrderStates.AXLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_axle)],
            OrderStates.PARTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_parts)],
            OrderStates.CONFIRM: [
                MessageHandler(filters.Regex("^(✅ Готово|✏️ Редактировать)$"), confirm_order),
                CallbackQueryHandler(confirm_edit_callback, pattern="^(confirm_edit|cancel_edit)$"),
                CallbackQueryHandler(continue_order_callback, pattern="^continue_order$"),
                CallbackQueryHandler(cancel_order_callback, pattern="^cancel_order$"),
                CallbackQueryHandler(confirm_cancel_order_callback, pattern="^confirm_cancel_order$")
            ],
        },
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=3600
    )
    
    # Remove Items ConversationHandler
    remove_items_conv = ConversationHandler(
        entry_points=[],
        states={RemoveStates.COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_comment_input)]},
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # Admin Add Item ConversationHandler
    admin_add_item_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_add_item_")],
        states={
            AdminAddItemStates.NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_item_name_input)],
            AdminAddItemStates.PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_item_price_input)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # Admin Change Price ConversationHandler
    admin_change_price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_change_price_")],
        states={AdminChangePriceStates.NEW_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_change_price_input)]},
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # Garage ConversationHandler
    garage_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(garage_add_start, pattern="^garage_add$")],
        states={
            GarageStates.VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_get_vin)],
            GarageStates.DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_get_description)]
        },
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # Garage Comment ConversationHandler
    garage_comment_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(garage_comment_start, pattern="^garage_comment_")],
        states={GarageStates.DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_comment_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # Save VIN ConversationHandler
    save_vin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(save_vin_callback, pattern="^save_vin_")],
        states={SaveStates.COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_vin_comment_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # Spend Bonus ConversationHandler
    spend_bonus_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(spend_bonus_custom_callback, pattern="^spend_bonus_custom_")],
        states={BonusStates.SPEND: [MessageHandler(filters.TEXT & ~filters.COMMAND, spend_bonus_custom_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # ========== РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ ==========
    
    # Команды
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("my_orders", my_orders))
    app.add_handler(CommandHandler("bonus", bonus_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("delivery", delivery_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", admin_menu))
    
    # Conversation handlers
    app.add_handler(order_conv)
    app.add_handler(garage_conv)
    app.add_handler(garage_comment_conv)
    app.add_handler(save_vin_conv)
    app.add_handler(spend_bonus_conv)
    app.add_handler(remove_items_conv)
    app.add_handler(admin_add_item_conv)
    app.add_handler(admin_change_price_conv)
    
    # Кнопочные обработчики
    app.add_handler(MessageHandler(filters.Regex("^(🚗 Мой гараж)$"), garage_menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🛒 Новый заказ)$"), new_order))
    
    # Callback обработчики
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|pay_|ordered_|arrived_|ready_|ship_|del_|issued_|cancel_|edit_delivery_|set_delivery_|detail_|order_changes_|admin_edit_items_|admin_remove_items_|admin_toggle_|admin_remove_confirm_|admin_add_item_|admin_change_price_|admin_select_price_item_)"))
    app.add_handler(CallbackQueryHandler(view_order, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^back_orders_list$"))
    app.add_handler(CallbackQueryHandler(start, pattern="^main_menu_back$"))
    app.add_handler(CallbackQueryHandler(garage_menu, pattern="^garage_"))
    app.add_handler(CallbackQueryHandler(bonus_cmd, pattern="^bonus_"))
    app.add_handler(CallbackQueryHandler(apply_bonus_callback, pattern="^apply_bonus_"))
    app.add_handler(CallbackQueryHandler(spend_bonus_percent_callback, pattern="^spend_bonus_percent_"))
    app.add_handler(CallbackQueryHandler(confirm_spend_callback, pattern="^confirm_spend_"))
    app.add_handler(CallbackQueryHandler(remove_items_callback, pattern="^remove_items_"))
    app.add_handler(CallbackQueryHandler(client_toggle_callback, pattern="^client_toggle_"))
    app.add_handler(CallbackQueryHandler(client_confirm_remove_callback, pattern="^client_confirm_remove_"))
    app.add_handler(CallbackQueryHandler(cancel_by_user_callback, pattern="^cancel_by_user_"))
    app.add_handler(CallbackQueryHandler(confirm_user_cancel_callback, pattern="^confirm_user_cancel_"))
    app.add_handler(CallbackQueryHandler(select_cb, pattern="^sel_"))
    app.add_handler(CallbackQueryHandler(finalize_cb, pattern="^fin_"))
    
    # Ответы менеджера
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), track_input))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), manager_reply))
    
    # Error handler
    app.add_error_handler(error_handler)
    
    logger.info(f"🤖 Бот запущен! Админ ID: {MANAGER_ID}")
    logger.info(f"Bot username: @{(app.bot.username) if hasattr(app, 'bot') else 'unknown'}")
    
    # Запуск бота
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
