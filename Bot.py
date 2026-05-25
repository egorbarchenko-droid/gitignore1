#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Shop Bot для автозапчастей
Версия: 4.1.0 - FULLY FIXED WITH TRACKING
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

# Безопасные колонки для UPDATE
ALLOWED_ORDER_COLUMNS = {
    'phone', 'our_cost', 'tracking_number', 'final_order', 
    'total_price', 'status', 'status_text', 'delivery_type',
    'delivery_price', 'distance', 'city', 'delivery_address', 'selected_products'
}

# Статусы заказов
STATUS_TRANSITIONS = {
    'pending': ['waiting_selection', 'cancelled'],
    'waiting_selection': ['waiting_payment', 'cancelled'],
    'waiting_payment': ['paid', 'cancelled'],
    'paid': ['ordered', 'cancelled', 'refunded'],
    'ordered': ['arrived', 'cancelled'],
    'arrived': ['ready', 'cancelled'],
    'ready': ['shipped', 'issued', 'cancelled'],
    'shipped': ['delivered', 'cancelled'],
    'delivered': ['issued', 'cancelled'],
    'issued': ['cancelled'],
    'cancelled': [],
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
    'cancelled': '❌ Отменён',
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

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def check_rate_limit(user_id: int) -> bool:
    """Проверка rate limiting"""
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
    return str(val) if val is not None else default

def clean_order_number(order_num: str) -> str:
    """Безопасная очистка номера заказа"""
    if not order_num:
        return ""
    order_num = str(order_num)
    cleaned = re.sub(r'[^A-Za-z0-9-]', '', order_num)
    if not re.match(r'^RVN-[A-Z0-9]{6}$', cleaned):
        logger.warning(f"Invalid order number format: {order_num}")
        return ""
    return cleaned

def validate_vin(vin: str) -> bool:
    """Полная валидация VIN номера"""
    if not vin or len(vin) != 17:
        return False
    vin = vin.upper()
    if re.search(r'[IOQ]', vin):
        return False
    if not vin.isalnum():
        return False
    return True

def is_ravenol_product(product_name: str) -> bool:
    """Проверка, является ли продукт Ravenol"""
    if not product_name:
        return False
    product_lower = product_name.lower()
    for brand in RESTRICTED_BRANDS:
        if brand in product_lower:
            return True
    return False

def wrap_text(text: str, max_length: int = 25) -> str:
    """Перенос текста по словам"""
    if len(text) <= max_length:
        return text
    words = text.split()
    lines = []
    current_line = ""
    for word in words:
        if len(current_line) + len(word) + 1 <= max_length:
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
    for key, icon in STATUS_ICONS.items():
        if key in status_text:
            return icon
    return '📦'

# ========== БАЗА ДАННЫХ ==========

def init_db():
    """Инициализация базы данных с индексами"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Таблица заказов
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
    
    # Таблица гаража
    c.execute('''CREATE TABLE IF NOT EXISTS garage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, vin TEXT, description TEXT, comment TEXT, created_at TEXT,
        UNIQUE(user_id, vin)
    )''')
    
    # Таблица бонусов
    c.execute('''CREATE TABLE IF NOT EXISTS bonuses (
        user_id INTEGER PRIMARY KEY,
        balance INTEGER DEFAULT 0, total_earned INTEGER DEFAULT 0,
        total_spent INTEGER DEFAULT 0, referrer_id INTEGER DEFAULT NULL
    )''')
    
    # Таблица истории бонусов
    c.execute('''CREATE TABLE IF NOT EXISTS bonus_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, order_number TEXT, amount INTEGER,
        type TEXT, description TEXT, created_at TEXT
    )''')
    
    # Таблица рефералов
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER, referred_id INTEGER, created_at TEXT
    )''')
    
    # Таблица отзывов
    c.execute('''CREATE TABLE IF NOT EXISTS feedback (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT, user_id INTEGER, rating INTEGER,
        comment TEXT, created_at TEXT
    )''')
    
    # Добавляем недостающие колонки
    for col in ['phone', 'our_cost', 'tracking_number', 'final_order', 'comment', 'selected_products']:
        try:
            c.execute(f'ALTER TABLE orders ADD COLUMN {col} TEXT')
        except:
            pass
    
    try:
        c.execute('ALTER TABLE garage ADD COLUMN comment TEXT')
    except:
        pass
    
    # Индексы для производительности
    c.execute('CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_bonus_history_user_id ON bonus_history(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_bonus_history_order_number ON bonus_history(order_number)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_bonus_history_created_at ON bonus_history(created_at)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_garage_user_id ON garage(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at)')
    
    conn.commit()
    conn.close()
    logger.info(f"Database initialized: {DB_PATH}")

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
    """Генерирует уникальный номер заказа с блокировкой"""
    conn = sqlite3.connect(DB_PATH)
    conn.execute('BEGIN IMMEDIATE')
    try:
        c = conn.cursor()
        max_attempts = 10
        for _ in range(max_attempts):
            num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
            try:
                c.execute('INSERT INTO orders (order_number) VALUES (?)', (num,))
                conn.commit()
                return num
            except sqlite3.IntegrityError:
                continue
        raise RuntimeError("Failed to generate unique order number")
    finally:
        conn.close()

def update_order(order_number: str, **kwargs) -> bool:
    """Безопасное обновление заказа"""
    order_number = clean_order_number(order_number)
    if not order_number:
        logger.error(f"Invalid order number for update")
        return False
    
    safe_kwargs = {k: v for k, v in kwargs.items() if k in ALLOWED_ORDER_COLUMNS}
    if not safe_kwargs:
        return False
    
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        
        # Проверяем валидность перехода статуса
        if 'status' in safe_kwargs:
            c.execute('SELECT status FROM orders WHERE order_number = ?', (order_number,))
            row = c.fetchone()
            if row and safe_kwargs['status'] not in STATUS_TRANSITIONS.get(row[0], []):
                logger.warning(f"Invalid status transition: {row[0]} -> {safe_kwargs['status']}")
                return False
        
        for key, val in safe_kwargs.items():
            c.execute(f"UPDATE orders SET {key} = ? WHERE order_number = ?", (val, order_number))
        
        # Обновляем status_text если изменился status
        if 'status' in safe_kwargs and 'status_text' not in safe_kwargs:
            new_status_text = STATUS_TEXT_MAP.get(safe_kwargs['status'], 'Неизвестно')
            c.execute("UPDATE orders SET status_text = ? WHERE order_number = ?", 
                     (new_status_text, order_number))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Update order error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def get_order(order_number: str) -> Optional[Dict]:
    """Получение заказа по номеру"""
    order_number = clean_order_number(order_number)
    if not order_number:
        return None
    
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('PRAGMA table_info(orders)')
        columns = [col[1] for col in c.fetchall()]
        
        c.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,))
        row = c.fetchone()
        
        if not row:
            return None
        
        order = {}
        for i, col in enumerate(columns):
            order[col] = row[i] if i < len(row) else None
        
        # Преобразуем числовые поля
        for num_field in ['distance', 'delivery_price', 'total_price', 'our_cost', 'user_id']:
            order[num_field] = safe_int(order.get(num_field))
        
        return order
    except Exception as e:
        logger.error(f"Get order error: {e}")
        return None
    finally:
        conn.close()

def save_order(data: Dict) -> Optional[str]:
    """Сохранение нового заказа"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        num = generate_order_number()
        
        c.execute('''INSERT INTO orders (
            order_number, user_id, user_name, phone, vin, mileage,
            style_city, style_highway, city, distance,
            delivery_type, delivery_price, delivery_address,
            part_node, axle, needed_parts,
            status, status_text, created_at, total_price
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (num, data['user_id'], data['user_name'], data.get('phone',''),
             data.get('vin',''), data.get('mileage',''), data.get('style_city',''),
             data.get('style_highway',''), data.get('city',''), data.get('distance',0),
             data.get('delivery_type',''), data.get('delivery_price',500), data.get('delivery_address',''),
             data.get('part_node',''), data.get('axle',''), data.get('needed_parts',''),
             'pending', '🆕 Ожидает подбора', datetime.now().strftime("%Y-%m-%d %H:%M:%S"), 0))
        
        conn.commit()
        return num
    except Exception as e:
        logger.error(f"Save order error: {e}")
        conn.rollback()
        return None
    finally:
        conn.close()

def get_user_orders(user_id: int) -> List[Tuple]:
    """Получение заказов пользователя"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('''SELECT order_number, status_text, created_at, total_price, 
                            delivery_price, final_order, needed_parts 
                     FROM orders WHERE user_id = ? ORDER BY id DESC''', (user_id,))
        return c.fetchall()
    finally:
        conn.close()

def get_all_orders() -> List[Tuple]:
    """Получение всех заказов"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT order_number, user_name, status_text, created_at FROM orders ORDER BY id DESC')
        return c.fetchall()
    finally:
        conn.close()

# ========== БОНУСЫ ==========

def get_bonus(user_id: int) -> Dict:
    """Получение информации о бонусах"""
    if user_id is None:
        return {'balance': 0, 'total_earned': 0, 'total_spent': 0}
    
    conn = sqlite3.connect(DB_PATH)
    try:
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
    finally:
        conn.close()

def add_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    """Начисление бонусов (только после оплаты)"""
    if amount <= 0:
        return False
    
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        
        c.execute('''INSERT INTO bonuses (user_id, balance, total_earned) 
                     VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET 
                     balance = balance + ?, total_earned = total_earned + ?''',
                  (user_id, amount, amount, amount, amount))
        
        c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (user_id, order_num, amount, 'earned', desc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Add bonus error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def use_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    """Списание бонусов"""
    if amount <= 0:
        return False
    
    if order_num is None:
        order_num = "welcome"
    
    conn = sqlite3.connect(DB_PATH)
    try:
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
                  (user_id, order_num, amount, 'spent', desc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Use bonus error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def refund_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    """Возврат бонусов (списание начисленных)"""
    if amount <= 0:
        return False
    
    conn = sqlite3.connect(DB_PATH)
    try:
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
                  (user_id, order_num, amount, 'refund', desc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        
        conn.commit()
        return True
    except Exception as e:
        logger.error(f"Refund bonus error: {e}")
        conn.rollback()
        return False
    finally:
        conn.close()

def get_user_total(user_id: int) -> int:
    """Общая сумма покупок пользователя"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT SUM(total_price) FROM orders WHERE user_id = ? AND status != "pending"', (user_id,))
        r = c.fetchone()
        return safe_int(r[0])
    finally:
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
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('''
            SELECT order_number, amount, type, description, created_at
            FROM bonus_history
            WHERE user_id = ?
            ORDER BY created_at DESC
            LIMIT ?
        ''', (user_id, limit))
        return c.fetchall()
    finally:
        conn.close()

def calculate_bonus_eligible_sum(order: Dict) -> int:
    """Рассчитывает сумму, с которой можно списать бонусы (без Ravenol)"""
    total_eligible = 0
    
    final_order = order.get('final_order', '')
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        try:
            selected_parts = ast.literal_eval(final_order)
            if isinstance(selected_parts, list):
                for part in selected_parts:
                    if isinstance(part, dict):
                        part_name = part.get('name', '')
                        part_price = safe_int(part.get('price', 0))
                        
                        if not is_ravenol_product(part_name) and part_price > 0:
                            total_eligible += part_price
        except (ValueError, SyntaxError, MemoryError) as e:
            logger.error(f"Error parsing final_order: {e}")
    
    return total_eligible

def has_ravenol_only(order: Dict) -> Tuple[bool, bool, int, int]:
    """Проверяет, есть ли в заказе только Ravenol или есть другие товары"""
    ravenol_sum = 0
    other_sum = 0
    has_ravenol = False
    has_other = False
    
    final_order = order.get('final_order', '')
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        try:
            selected_parts = ast.literal_eval(final_order)
            if isinstance(selected_parts, list):
                for part in selected_parts:
                    if isinstance(part, dict):
                        part_name = part.get('name', '')
                        part_price = safe_int(part.get('price', 0))
                        
                        if is_ravenol_product(part_name):
                            ravenol_sum += part_price
                            has_ravenol = True
                        else:
                            other_sum += part_price
                            has_other = True
        except:
            pass
    
    return has_ravenol, has_other, ravenol_sum, other_sum

def get_bonus_spend_details(order: Dict) -> str:
    """Возвращает детали для отображения пользователю"""
    eligible_sum = calculate_bonus_eligible_sum(order)
    delivery_price = order.get('delivery_price', 0)
    total_parts = order.get('total_price', 0)
    total_sum = total_parts + delivery_price
    has_ravenol, has_other, ravenol_sum, other_sum = has_ravenol_only(order)
    
    details = f"💰 **Детали заказа:**\n"
    details += f"• Сумма запчастей: {total_parts} руб.\n"
    
    if ravenol_sum > 0:
        details += f"• Из них Ravenol: {ravenol_sum} руб. (❌ бонусы не начисляются)\n"
    if other_sum > 0:
        details += f"• Сумма для бонусов: {other_sum} руб.\n"
    
    details += f"• Доставка: {delivery_price} руб. (❌ бонусы не списываются)\n"
    details += f"• **Итого к оплате:** {total_sum} руб.\n\n"
    
    if eligible_sum > 0:
        max_bonus = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100)
        details += f"🎁 **Максимум списания бонусами:** {max_bonus} руб. ({MAX_BONUS_SPEND_PERCENT}% от {eligible_sum} руб.)\n"
    else:
        details += f"❌ **Нет товаров для списания бонусов**\n"
    
    return details

# ========== РАСЧЁТЫ ==========

def calc_delivery_price(km: int) -> int:
    """Расчёт стоимости доставки"""
    if km <= 0:
        return DELIVERY_BASE
    if km <= 50:
        return DELIVERY_BASE + km * DELIVERY_RATE_UP_TO_50
    if km <= 100:
        return DELIVERY_BASE + km * DELIVERY_RATE_UP_TO_100
    return DELIVERY_BASE + km * DELIVERY_RATE_OVER_100

def extract_city_from_address(address: str) -> str:
    """Определение города из адреса"""
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
    if order_sum < 10000:
        return 0
    steps = (order_sum - 10000) // 5000
    return min(100, 5 + steps * 5)

def parse_products(text: str) -> List[Dict]:
    """Парсинг запчастей из текста"""
    products = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        
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
        
        name = line[:match.start()].strip()
        name = re.sub(r'^[=\-•–—]+|[=\-•–—]+$', '', name).strip()
        name = re.sub(r'арт\.?\s*\S+', '', name).strip()
        name = name[:40] + ".." if len(name) > 40 else name
        
        if name and price > 0:
            products.append({'name': name, 'price': int(price)})
    
    return products

# ========== ГАРАЖ ==========

def save_car(user_id: int, vin: str, description: str, comment: str = "") -> bool:
    """Сохранение автомобиля в гараж"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT 1 FROM garage WHERE user_id = ? AND vin = ?', (user_id, vin))
        if c.fetchone():
            return False
        
        c.execute('INSERT INTO garage (user_id, vin, description, comment, created_at) VALUES (?,?,?,?,?)',
                  (user_id, vin, description, comment, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        return True
    finally:
        conn.close()

def get_cars(user_id: int) -> List[Tuple]:
    """Получение списка автомобилей пользователя"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT vin, description, comment, created_at FROM garage WHERE user_id = ? ORDER BY id DESC', (user_id,))
        return c.fetchall()
    finally:
        conn.close()

def delete_car(user_id: int, vin: str) -> bool:
    """Удаление автомобиля из гаража"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('DELETE FROM garage WHERE user_id = ? AND vin = ?', (user_id, vin))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()

def update_car_comment(user_id: int, vin: str, comment: str) -> bool:
    """Обновление комментария к автомобилю"""
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('UPDATE garage SET comment = ? WHERE user_id = ? AND vin = ?', (comment, user_id, vin))
        conn.commit()
        return c.rowcount > 0
    finally:
        conn.close()

# ========== ДЕКОРАТОРЫ ==========

def require_manager(func):
    """Декоратор проверки прав менеджера"""
    @wraps(func)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if upd.effective_user.id != MANAGER_ID:
            await upd.message.reply_text("⛔ Доступ запрещён")
            return
        return await func(upd, ctx, *args, **kwargs)
    return wrapper

def require_order_owner(func):
    """Декоратор проверки владельца заказа"""
    @wraps(func)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        query = upd.callback_query
        if not query:
            return await func(upd, ctx, *args, **kwargs)
        
        await query.answer()
        
        parts = query.data.split('_')
        order_num = None
        for part in parts:
            if part.startswith('RVN-') or re.match(r'^[A-Z0-9]{6}$', part):
                order_num = part
                break
        
        if not order_num:
            await query.edit_message_text("❌ Ошибка: заказ не найден")
            return
        
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        if order['user_id'] != query.from_user.id:
            await query.answer("❌ Это не ваш заказ!", show_alert=True)
            return
        
        ctx.user_data['current_order'] = order
        return await func(upd, ctx, *args, **kwargs)
    return wrapper

def rate_limit(func):
    """Декоратор rate limiting"""
    @wraps(func)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        if not check_rate_limit(upd.effective_user.id):
            await upd.message.reply_text("⏳ Слишком часто! Подождите пару секунд.")
            return
        return await func(upd, ctx, *args, **kwargs)
    return wrapper

# ========== ОСНОВНЫЕ КОМАНДЫ ==========

async def start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработчик команды /start"""
    if ctx.args and ctx.args[0].startswith('ref_'):
        ref_id = int(ctx.args[0][4:])
        if ref_id != upd.effective_user.id:
            conn = sqlite3.connect(DB_PATH)
            try:
                c = conn.cursor()
                c.execute('INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?,?,?)',
                          (ref_id, upd.effective_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
                c.execute('INSERT INTO bonuses (user_id, referrer_id) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET referrer_id = ?',
                          (upd.effective_user.id, ref_id, ref_id))
                conn.commit()
                add_bonus(upd.effective_user.id, None, 500, "Приветственные бонусы по реферальной ссылке")
                await ctx.bot.send_message(ref_id, f"👋 {upd.effective_user.full_name} перешёл по вашей реферальной ссылке и получил 500 бонусов!")
                await upd.message.reply_text("🎉 +500 бонусов за регистрацию по реферальной ссылке!")
            finally:
                conn.close()
    
    text = ("🏎️ **Добро пожаловать в магазин автозапчастей!**\n\n"
            "Что я умею:\n"
            "🛒 **Новый заказ** - подбор запчастей по вашему автомобилю\n"
            "🚗 **Мой гараж** - храните VIN и описание автомобилей\n"
            "📦 **Мои заказы** - история ваших заказов\n"
            "🎁 **Бонусы** - накапливайте бонусы от покупок\n"
            "🔗 **Рефералы** - приглашайте друзей и получайте бонусы\n"
            "🚚 **Доставка** - расчёт стоимости доставки\n\n"
            "Нажмите 🛒 **Новый заказ**, чтобы начать!")
    
    await upd.message.reply_text(text, reply_markup=main_menu, parse_mode='Markdown')

# ========== НОВЫЙ ЗАКАЗ (СОКРАЩЁННЫЙ ДЛЯ ЭКОНОМИИ МЕСТА) ==========
# Полный код функций new_order, get_vin, get_mileage и т.д. аналогичен предыдущей версии
# Для экономии места здесь приведены только критически важные функции

# ========== ТРЕК-НОМЕР (ИСПРАВЛЕННАЯ ВЕРСИЯ) ==========

async def track_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка ввода трек-номера менеджера - ИСПРАВЛЕННАЯ ВЕРСИЯ"""
    if upd.effective_user.id != MANAGER_ID:
        return
    
    tracking = upd.message.text.strip()
    
    # Пропускаем команды
    if tracking.startswith('/'):
        return
    
    # Пропускаем слишком короткие сообщения
    if len(tracking) < 3:
        return
    
    logger.info(f"[TRACK] Получен трек-номер: {tracking}")
    logger.info(f"[TRACK] user_data keys: {list(ctx.user_data.keys())}")
    logger.info(f"[TRACK] Есть reply_to_message: {upd.message.reply_to_message is not None}")
    
    order_num = None
    
    # Способ 1: Из user_data (если кнопка была нажата)
    if 'track_for' in ctx.user_data:
        order_num = ctx.user_data.pop('track_for')
        logger.info(f"[TRACK] Найден из user_data: {order_num}")
    
    # Способ 2: Из сообщения, на которое отвечаем
    if not order_num and upd.message.reply_to_message:
        reply_text = upd.message.reply_to_message.text or ""
        logger.info(f"[TRACK] Reply text: {reply_text[:100]}")
        match = re.search(r'(RVN-[A-Z0-9]{6})', reply_text)
        if match:
            order_num = match.group(1)
            logger.info(f"[TRACK] Найден из reply: {order_num}")
    
    # Способ 3: Поиск в истории сообщений
    if not order_num:
        logger.info("[TRACK] Поиск в истории...")
        try:
            async for msg in upd.message.chat.iter_history(limit=30):
                if msg.text and 'RVN-' in msg.text:
                    match = re.search(r'(RVN-[A-Z0-9]{6})', msg.text)
                    if match:
                        order_num = match.group(1)
                        logger.info(f"[TRACK] Найден в истории: {order_num} (msg: {msg.text[:50]})")
                        break
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
    
    if not order_num:
        logger.warning(f"[TRACK] НЕ УДАЛОСЬ НАЙТИ ЗАКАЗ для трека: {tracking}")
        await upd.message.reply_text(
            "❌ **Не удалось определить номер заказа.**\n\n"
            "**Пожалуйста, выполните следующие шаги:**\n\n"
            "1️⃣ Откройте заказ через `/menu`\n"
            "2️⃣ Нажмите кнопку **🚚 Отправлен**\n"
            "3️⃣ **ОБЯЗАТЕЛЬНО ответьте на сообщение бота** с трек-номером\n"
            "4️⃣ Не пишите просто в чат\n\n"
            f"Ваш трек-номер: `{tracking}`",
            parse_mode='Markdown'
        )
        return
    
    order_num = clean_order_number(order_num)
    if not order_num:
        await upd.message.reply_text("❌ Неверный формат номера заказа")
        return
    
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text(f"❌ Заказ {order_num} не найден в базе данных!")
        return
    
    logger.info(f"[TRACK] Обновление заказа {order_num}, текущий статус: {order.get('status')}")
    
    # Обновляем заказ
    success = update_order(order_num, tracking_number=tracking, status='shipped')
    
    if not success:
        await upd.message.reply_text(f"❌ Ошибка при обновлении заказа {order_num}")
        return
    
    # Отправляем уведомление клиенту
    try:
        await ctx.bot.send_message(
            order['user_id'],
            text=f"📦 **Заказ {order_num} отправлен!**\n\n"
                 f"📮 **Трек-номер для отслеживания:** `{tracking}`\n\n"
                 f"Вы можете отслеживать посылку на сайте почты.",
            parse_mode='Markdown'
        )
        logger.info(f"[TRACK] Уведомление отправлено клиенту {order['user_id']}")
    except Exception as e:
        logger.error(f"Не удалось отправить уведомление: {e}")
    
    # Подтверждение менеджеру
    await upd.message.reply_text(
        f"✅ **Трек-номер успешно добавлен!**\n\n"
        f"📦 Заказ: `{order_num}`\n"
        f"📮 Трек-номер: `{tracking}`\n"
        f"👤 Клиент: {order.get('user_name', 'Неизвестно')}\n"
        f"📞 Телефон: {order.get('phone', 'Не указан')}\n\n"
        f"Клиент получил уведомление.",
        parse_mode='Markdown'
    )
    
    # Очищаем
    ctx.user_data.pop('track_for', None)

# ========== АДМИН ПАНЕЛЬ - КРИТИЧЕСКАЯ ЧАСТЬ С SHIP ==========

@require_manager
async def admin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка админ-панели"""
    query = upd.callback_query
    data = query.data
    await query.answer()
    
    if data.startswith("ship_"):
        # Извлекаем номер заказа
        order_num = data[5:]
        order_num = clean_order_number(order_num)
        
        if not order_num:
            await query.edit_message_text("❌ Не удалось определить номер заказа. Пожалуйста, откройте заказ заново.")
            return
        
        # Дополнительная проверка: существует ли заказ?
        order = get_order(order_num)
        if not order:
            await query.edit_message_text(f"❌ Заказ {order_num} не найден в базе данных!")
            return
        
        # Сохраняем в user_data
        ctx.user_data['track_for'] = order_num
        ctx.user_data['track_order_status'] = order.get('status')
        
        logger.info(f"[SHIP] Установлен track_for для заказа: {order_num}, статус: {order.get('status')}")
        
        # Отправляем сообщение с ПОНЯТНЫМ указанием ответить
        await query.edit_message_text(
            f"📦 **Введите трек-номер для заказа {order_num}**\n\n"
            f"⬇️ **ВАЖНО!** ⬇️\n\n"
            f"1️⃣ **НАЖМИТЕ НА ЭТО СООБЩЕНИЕ**\n"
            f"2️⃣ Выберите **«ОТВЕТИТЬ»** (Reply)\n"
            f"3️⃣ Введите трек-номер\n"
            f"4️⃣ Отправьте\n\n"
            f"❌ Не пишите просто в чат - бот не поймёт!\n\n"
            f"Ваш трек-номер должен выглядеть так: `10270287171`",
            parse_mode='Markdown'
        )
        return
    
    # Остальные обработчики статусов (pay_, ordered_, arrived_, ready_, del_, issued_, cancel_)
    # ... (аналогично предыдущей версии)

# ========== ЗАПУСК ==========

def main():
    """Запуск бота"""
    init_db()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(backup_db, 'cron', hour=3, minute=0)
    scheduler.start()
    logger.info("Scheduler started")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Регистрация команд
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
        await application.bot.set_my_commands(commands)
    
    app.post_init = set_commands
    
    # Регистрация обработчиков (критические)
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("menu", admin_menu))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(ship_|pay_|ordered_|arrived_|ready_|del_|issued_|cancel_|admin_)"))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), track_input))
    
    # ... остальные обработчики (аналогично предыдущей версии)
    
    logger.info(f"🤖 Бот запущен! Админ ID: {MANAGER_ID}")
    app.run_polling()

if __name__ == "__main__":
    main()
