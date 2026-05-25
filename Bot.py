#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Shop Bot для автозапчастей
Версия: 4.2.0 - FULLY FIXED WITH TRACKING
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

# ========== НОВЫЙ ЗАКАЗ ==========

async def new_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Начало оформления заказа"""
    cars = get_cars(upd.effective_user.id)
    if cars:
        keyboard = [[InlineKeyboardButton("🆕 Ввести VIN вручную", callback_data="order_manual")]]
        for car in cars:
            vin, description, comment, _ = car
            desc_short = description[:20] if description else ""
            display_text = f"🚗 {vin}"
            if desc_short:
                display_text += f" ({desc_short})"
            if comment:
                display_text += f" [{comment[:15]}]"
            keyboard.append([InlineKeyboardButton(display_text, callback_data=f"order_auto_{vin}")])
        
        await upd.message.reply_text(
            "🔧 **ВЫБЕРИТЕ АВТОМОБИЛЬ** из гаража или введите VIN вручную:",
            reply_markup=InlineKeyboardMarkup(keyboard),
            parse_mode='Markdown'
        )
        return OrderStates.VIN
    
    await upd.message.reply_text("🔧 Отправьте VIN номер (17 символов):")
    return OrderStates.VIN

async def order_auto_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка выбора авто из гаража"""
    query = upd.callback_query
    await query.answer()
    
    if query.data == "order_manual":
        await query.edit_message_text("🔧 Отправьте VIN номер (17 символов):")
        return OrderStates.VIN
    
    if query.data.startswith("order_auto_"):
        vin = query.data[11:]
        ctx.user_data['vin'] = vin
        await query.edit_message_text(f"🚗 Выбран автомобиль: `{vin}`\n\n📊 Теперь введите пробег (км):", parse_mode='Markdown')
        return OrderStates.MILEAGE
    
    return OrderStates.VIN

@rate_limit
async def get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение VIN номера"""
    vin = upd.message.text.upper().strip()
    
    if not validate_vin(vin):
        await upd.message.reply_text("❌ **Неверный VIN**\n\nVIN должен содержать 17 символов (только буквы и цифры, без I, O, Q).\nПопробуйте ещё раз:")
        return OrderStates.VIN
    
    ctx.user_data['vin'] = vin
    await upd.message.reply_text("📊 Введите пробег (км):")
    return OrderStates.MILEAGE

@rate_limit
async def get_mileage(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение пробега"""
    try:
        mileage = int(upd.message.text)
        if mileage < 0:
            raise ValueError
        ctx.user_data['mileage'] = str(mileage)
    except ValueError:
        await upd.message.reply_text("❌ Пожалуйста, введите число (пробег в км):")
        return OrderStates.MILEAGE
    
    await upd.message.reply_text("🏙️ **Стиль вождения в городе:**", reply_markup=city_style_kb, parse_mode='Markdown')
    return OrderStates.STYLE_CITY

async def get_style_city(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение стиля вождения в городе"""
    ctx.user_data['style_city'] = upd.message.text
    await upd.message.reply_text("🛣️ **Стиль вождения на трассе:**", reply_markup=highway_style_kb, parse_mode='Markdown')
    return OrderStates.STYLE_HIGHWAY

async def get_style_highway(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение стиля вождения на трассе"""
    ctx.user_data['style_highway'] = upd.message.text
    await upd.message.reply_text("🚚 **Способ доставки:**", reply_markup=delivery_type_kb, parse_mode='Markdown')
    return OrderStates.DELIVERY_TYPE

async def get_delivery_type(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение типа доставки"""
    choice = upd.message.text
    ctx.user_data['delivery_type'] = choice
    
    if choice == "Курьером":
        await upd.message.reply_text("📍 Введите **ПОЛНЫЙ АДРЕС** доставки\n\nПример: г. Москва, ул. Тверская, д. 15, кв. 78", parse_mode='Markdown')
        return OrderStates.ADDRESS
    elif choice == "Самовывоз":
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("📍 **Самовывоз**\n\nДоступные станции:\n• Метро Давыдково\n• Метро Строгино\n• Метро Южная\n\nВведите адрес самовывоза:", parse_mode='Markdown')
        return OrderStates.ADDRESS
    else:
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("🚛 **Сторонняя фирма** (стоимость рассчитает менеджер)\n\n📍 Введите адрес доставки:", parse_mode='Markdown')
        return OrderStates.ADDRESS

@rate_limit
async def get_address(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение адреса доставки"""
    full_address = upd.message.text.strip()
    if len(full_address) < 10:
        await upd.message.reply_text("❌ Введите полный адрес (минимум 10 символов):")
        return OrderStates.ADDRESS
    
    ctx.user_data['delivery_address'] = full_address
    city = extract_city_from_address(full_address)
    distance = extract_distance_from_address(full_address)
    ctx.user_data['city'] = city
    ctx.user_data['distance'] = distance
    
    if ctx.user_data.get('delivery_type') == "Курьером":
        price = calc_delivery_price(distance)
        ctx.user_data['delivery_price'] = price
        await upd.message.reply_text(
            f"📍 **Адрес:** {full_address}\n"
            f"🏙️ **Город:** {city}\n"
            f"📏 **Расстояние от МКАД:** {distance} км\n"
            f"🚚 **Стоимость доставки:** {price} руб.\n\n"
            f"📞 Введите ваш контактный телефон:",
            parse_mode='Markdown'
        )
    else:
        await upd.message.reply_text(f"📍 **Адрес:** {full_address}\n\n📞 Введите ваш контактный телефон:", parse_mode='Markdown')
    
    return OrderStates.PHONE

@rate_limit
async def get_phone(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение номера телефона"""
    phone = upd.message.text.strip()
    if len(phone) < 5:
        await upd.message.reply_text("❌ Пожалуйста, введите корректный номер телефона:")
        return OrderStates.PHONE
    
    ctx.user_data['phone'] = phone
    await upd.message.reply_text("🔧 **Выберите узел запчасти:**", reply_markup=part_node_kb, parse_mode='Markdown')
    return OrderStates.PART_NODE

async def get_part_node(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение узла запчасти"""
    ctx.user_data['part_node'] = upd.message.text
    
    if upd.message.text in AXLE_REQUIRED_NODES:
        await upd.message.reply_text("🔧 **Выберите ось:**", reply_markup=axle_kb, parse_mode='Markdown')
        return OrderStates.AXLE
    else:
        ctx.user_data['axle'] = "Не требуется"
        await upd.message.reply_text(
            "🔧 **Какие запчасти нужны?** (каждая с новой строки)\n\n"
            "Пример:\n"
            "Колодки тормозные\n"
            "Диски тормозные",
            parse_mode='Markdown'
        )
        return OrderStates.PARTS

async def get_axle(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение оси"""
    ctx.user_data['axle'] = upd.message.text
    await upd.message.reply_text(
        "🔧 **Какие запчасти нужны?** (каждая с новой строки)\n\n"
        "Пример:\n"
        "Колодки тормозные передние\n"
        "Диски тормозные задние",
        parse_mode='Markdown'
    )
    return OrderStates.PARTS

@rate_limit
async def get_parts(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение списка запчастей"""
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
    
    ctx.user_data['needed_parts'] = upd.message.text
    
    data = ctx.user_data
    delivery_price = data.get('delivery_price', 500)
    
    summary = (f"📋 **ПРОВЕРЬТЕ ЗАКАЗ**\n\n"
               f"🚗 VIN: `{data.get('vin', 'не указан')}`\n"
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
               f"💰 **Доставка: {delivery_price} руб.**\n\n"
               "✅ Всё верно? Нажмите **Готово** или **Редактировать**")
    
    await upd.message.reply_text(summary, reply_markup=confirm_order_kb, parse_mode='Markdown')
    return OrderStates.CONFIRM

async def confirm_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение заказа"""
    if upd.message.text == "✅ Готово":
        data = ctx.user_data.copy()
        
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
        
        ctx.user_data.clear()
        
        if not order_num:
            await upd.message.reply_text("❌ Ошибка при создании заказа. Попробуйте позже.")
            return ConversationHandler.END
        
        vin = data.get('vin', '')
        if vin and validate_vin(vin):
            cars = get_cars(upd.effective_user.id)
            vin_exists = any(car[0] == vin for car in cars)
            
            if not vin_exists:
                kb = InlineKeyboardMarkup([
                    [InlineKeyboardButton("✅ Да, сохранить в гараж", callback_data=f"save_vin_{vin}")],
                    [InlineKeyboardButton("❌ Нет, спасибо", callback_data="no_save_vin")]
                ])
                await upd.message.reply_text(
                    f"🚗 Хотите сохранить автомобиль с VIN `{vin}` в ваш гараж?\n\n"
                    f"В следующий раз вам не придётся вводить VIN заново!",
                    reply_markup=kb,
                    parse_mode='Markdown'
                )
        
        await upd.message.reply_text(
            f"✅ **ЗАКАЗ #{order_num} ПРИНЯТ!**\n\n"
            f"📋 Детали заказа:\n"
            f"🚚 Доставка: {data.get('delivery_price', 500)} руб.\n"
            f"📍 Адрес: {data.get('delivery_address', 'не указан')}\n\n"
            f"🔧 Менеджер скоро свяжется с вами для уточнения деталей.\n\n"
            f"Вы можете вернуться в главное меню:",
            reply_markup=main_menu,
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    
    elif upd.message.text == "✏️ Редактировать":
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, начать заново", callback_data="confirm_edit")],
            [InlineKeyboardButton("❌ Нет, продолжить", callback_data="cancel_edit")]
        ])
        await upd.message.reply_text(
            "⚠️ **Внимание!**\n\n"
            "При редактировании все введённые данные будут потеряны.\n"
            "Вы уверены, что хотите начать заказ заново?",
            reply_markup=kb,
            parse_mode='Markdown'
        )
        return OrderStates.CONFIRM
    
    return ConversationHandler.END

async def confirm_edit_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение редактирования заказа"""
    query = upd.callback_query
    await query.answer()
    
    if query.data == "confirm_edit":
        ctx.user_data.clear()
        await query.edit_message_text(
            "✏️ Давайте начнём заказ заново. Нажмите 🛒 **Новый заказ**",
            reply_markup=main_menu,
            parse_mode='Markdown'
        )
        return ConversationHandler.END
    else:
        await query.edit_message_text("✅ Продолжаем оформление заказа. Введите запчасти:")
        return OrderStates.PARTS

async def continue_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Продолжить заказ без запчастей"""
    query = upd.callback_query
    await query.answer()
    
    ctx.user_data['needed_parts'] = "Без запчастей (уточнить у менеджера)"
    
    data = ctx.user_data
    delivery_price = data.get('delivery_price', 500)
    
    summary = (f"📋 **ПРОВЕРЬТЕ ЗАКАЗ**\n\n"
               f"🚗 VIN: `{data.get('vin', 'не указан')}`\n"
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
               f"💰 **Доставка: {delivery_price} руб.**\n\n"
               "✅ Всё верно? Нажмите **Готово** или **Редактировать**")
    
    await query.edit_message_text(summary, reply_markup=confirm_order_kb, parse_mode='Markdown')
    return OrderStates.CONFIRM

async def cancel_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отмена заказа"""
    query = upd.callback_query
    await query.answer()
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ Да, отменить", callback_data="confirm_cancel_order")],
        [InlineKeyboardButton("❌ Нет, продолжить", callback_data="continue_order")]
    ])
    await query.edit_message_text(
        "⚠️ **Вы уверены, что хотите отменить создание заказа?**",
        reply_markup=kb,
        parse_mode='Markdown'
    )

async def confirm_cancel_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение отмены заказа"""
    query = upd.callback_query
    await query.answer()
    
    uid = query.from_user.id
    if uid in user_selections:
        del user_selections[uid]
    
    ctx.user_data.clear()
    await query.edit_message_text(
        "❌ Заказ отменён. Вы можете начать новый заказ в главном меню.",
        reply_markup=main_menu
    )
    return ConversationHandler.END

# ========== СОХРАНЕНИЕ VIN ==========

async def save_vin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Сохранение VIN в гараж"""
    query = upd.callback_query
    await query.answer()
    vin = query.data[9:]
    user_id = query.from_user.id
    
    ctx.user_data['save_vin'] = vin
    await query.edit_message_text(
        f"🚗 **СОХРАНЕНИЕ АВТОМОБИЛЯ**\n\n"
        f"VIN: `{vin}`\n\n"
        f"Добавьте комментарий (например, «зимняя резина», «жена», «служебный»)\n"
        f"Максимум 100 символов.\n\n"
        f"Или отправьте '-' чтобы пропустить:",
        parse_mode='Markdown'
    )
    return SaveStates.COMMENT

async def save_vin_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение комментария для сохранения VIN"""
    user_id = upd.effective_user.id
    vin = ctx.user_data.get('save_vin')
    
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
            await upd.message.reply_text(f"✅ Автомобиль `{vin}` сохранён в гараж!\n💬 Комментарий: {comment}", parse_mode='Markdown')
        else:
            await upd.message.reply_text(f"✅ Автомобиль `{vin}` сохранён в гараж!", parse_mode='Markdown')
    else:
        await upd.message.reply_text(f"❌ Автомобиль `{vin}` уже есть в вашем гараже!", parse_mode='Markdown')
    
    del ctx.user_data['save_vin']
    return ConversationHandler.END

async def no_save_vin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Отказ от сохранения VIN"""
    query = upd.callback_query
    await query.answer()
    await query.edit_message_text("OK, в следующий раз вы сможете сохранить автомобиль в гараж при создании заказа.")

# ========== БОНУСЫ ПОЛЬЗОВАТЕЛЯ ==========

async def bonus_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /bonus - информация о бонусах"""
    uid = upd.effective_user.id
    bonus_data = get_bonus(uid)
    bal = bonus_data['balance']
    total_earned = bonus_data['total_earned']
    total_spent = bonus_data['total_spent']
    total_purchases = get_user_total(uid)
    percent = get_bonus_percent(uid)
    
    text = (f"🎁 **БОНУСНАЯ ПРОГРАММА**\n\n"
            f"💰 Текущий баланс: **{bal}** бонусов\n"
            f"📈 Всего начислено: **{total_earned}** бонусов\n"
            f"📉 Всего потрачено: **{total_spent}** бонусов\n"
            f"🛒 Накоплено по покупкам: **{total_purchases}** руб.\n"
            f"⭐ Текущий процент начисления: **{percent}%**\n\n"
            f"**📋 ПРАВИЛА ИСПОЛЬЗОВАНИЯ:**\n"
            f"• ✅ Можно списать **до {MAX_BONUS_SPEND_PERCENT}%** от суммы запчастей\n"
            f"• ❌ **Не действует на продукцию RAVENOL**\n"
            f"• ❌ **Не действует на доставку**\n"
            f"• 💳 Минимальная оплата деньгами: **{MIN_CASH_PAYMENT} руб.**\n\n"
            f"**📊 ГРАДАЦИЯ НАЧИСЛЕНИЯ:**\n"
            f"1% → до 100 000 руб.\n2% → 100 000 - 200 000 руб.\n3% → 200 000 - 300 000 руб.\n"
            f"4% → 300 000 - 400 000 руб.\n5% → 400 000 - 500 000 руб.\n6% → 500 000 - 600 000 руб.\n"
            f"7% → 600 000 - 700 000 руб.\n8% → 700 000 - 800 000 руб.\n"
            f"9% → 800 000 - 900 000 руб.\n10% → от 900 000 руб.\n")
    
    await upd.message.reply_text(text, parse_mode='Markdown')
    
    history = get_bonus_history(uid, 10)
    if history:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📜 Показать историю", callback_data="bonus_history")]
        ])
        await upd.message.reply_text("📊 **Дополнительная информация:**", reply_markup=kb, parse_mode='Markdown')

async def bonus_history_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать историю бонусов"""
    query = upd.callback_query
    await query.answer()
    
    uid = query.from_user.id
    history = get_bonus_history(uid, 20)
    
    if not history:
        await query.edit_message_text("📭 История операций пока пуста")
        return
    
    text = "📜 **ИСТОРИЯ БОНУСОВ**\n\n"
    for h in history:
        order_num, amount, h_type, desc, created = h
        sign = "➕" if h_type == 'earned' else "➖" if h_type == 'spent' else "🔄"
        text += f"{sign} **{amount} руб.** - {desc}\n"
        text += f"   📅 {created[:10]}\n\n"
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("◀️ Назад", callback_data="bonus_back")]
    ])
    await query.edit_message_text(text, parse_mode='Markdown', reply_markup=kb)

async def bonus_back_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Назад к бонусам"""
    query = upd.callback_query
    await query.answer()
    await bonus_cmd(upd, ctx)

# ========== ПРИМЕНЕНИЕ БОНУСОВ ==========

@require_order_owner
async def apply_bonus_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Применение бонусов к заказу (только на non-Ravenol товары)"""
    query = upd.callback_query
    order = ctx.user_data['current_order']
    order_num = order['order_number']
    uid = query.from_user.id
    
    if order.get('status') != 'waiting_payment':
        await query.edit_message_text("❌ Бонусы можно применить только к заказу в статусе 'Ожидает оплаты'")
        return
    
    bonus_data = get_bonus(uid)
    balance = bonus_data['balance']
    eligible_sum = calculate_bonus_eligible_sum(order)
    
    if eligible_sum <= 0:
        await query.edit_message_text(
            f"❌ **НЕВОЗМОЖНО СПИСАТЬ БОНУСЫ**\n\n"
            f"{get_bonus_spend_details(order)}\n\n"
            f"Причина: в заказе нет товаров, подходящих для списания бонусов.",
            parse_mode='Markdown'
        )
        return
    
    max_spend = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100)
    max_spend = min(max_spend, balance)
    
    if max_spend <= 0:
        await query.edit_message_text(
            f"❌ **НЕВОЗМОЖНО СПИСАТЬ БОНУСЫ**\n\n"
            f"{get_bonus_spend_details(order)}\n"
            f"🎁 Ваш баланс: {balance} руб.\n\n"
            f"Недостаточно бонусов или сумма слишком мала.",
            parse_mode='Markdown'
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
        f"🎁 **СПИСАНИЕ БОНУСОВ**\n\n"
        f"{details}\n"
        f"🎁 Ваш баланс: {balance} руб.\n"
        f"📊 **Максимум списания: {max_spend} руб.**\n\n"
        f"Выберите действие:",
        reply_markup=kb,
        parse_mode='Markdown'
    )

@rate_limit
async def spend_bonus_percent_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Списание максимального процента бонусов"""
    query = upd.callback_query
    await query.answer("⏳ Списание бонусов...", show_alert=False)
    
    order_num = query.data[19:]
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
        f"⚠️ **ПОДТВЕРЖДЕНИЕ СПИСАНИЯ**\n\n"
        f"📦 Заказ: {order_num}\n"
        f"💰 Сумма запчастей: {current_total_parts} руб.\n"
        f"🎁 Будет списано: {spend_amount} руб.\n"
        f"💳 К оплате после списания: {new_total} руб.\n\n"
        f"Вы уверены?",
        reply_markup=kb,
        parse_mode='Markdown'
    )

async def confirm_spend_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение списания бонусов"""
    query = upd.callback_query
    await query.answer()
    
    parts = query.data.split('_')
    order_num = parts[2]
    spend_amount = int(parts[3])
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
            ravenol_info = f"\n\n📊 **Состав заказа:**\n• Ravenol: {ravenol_sum} руб. (❌ без изменений)\n• Остальные: {other_sum} → {other_sum - spend_amount} руб."
        
        bonus_data = get_bonus(uid)
        
        await query.edit_message_text(
            f"✅ **БОНУСЫ УСПЕШНО СПИСАНЫ!**\n\n"
            f"📦 Заказ: {order_num}\n"
            f"💰 Сумма запчастей: {current_total_parts} → {new_parts_total} руб.\n"
            f"🚚 Доставка: {delivery_price} руб. (❌ без изменений)\n"
            f"🎁 Списано бонусов: {spend_amount} руб.\n"
            f"💳 **ИТОГО К ОПЛАТЕ: {new_total} руб.**{ravenol_info}\n\n"
            f"🎁 Остаток бонусов: {bonus_data['balance']} руб.",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("❌ Ошибка при списании бонусов")

async def spend_bonus_custom_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ввод произвольной суммы списания"""
    query = upd.callback_query
    await query.answer()
    
    order_num = query.data[18:]
    ctx.user_data['bonus_order'] = order_num
    
    order = get_order(order_num)
    if order:
        eligible_sum = calculate_bonus_eligible_sum(order)
        max_allowed = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100)
        balance = get_bonus(query.from_user.id)['balance']
        max_possible = min(max_allowed, balance)
        
        await query.edit_message_text(
            f"✏️ **ВВЕДИТЕ СУММУ СПИСАНИЯ**\n\n"
            f"📦 Заказ: {order_num}\n"
            f"💰 Сумма для бонусов: {eligible_sum} руб.\n"
            f"📊 Максимум списания (20%): {max_allowed} руб.\n"
            f"🎁 Ваш баланс: {balance} руб.\n"
            f"✅ Доступно для списания: {max_possible} руб.\n\n"
            f"Введите целое число (рублей):",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("❌ Заказ не найден")
    
    return BonusStates.SPEND

async def spend_bonus_custom_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка введённой суммы списания"""
    user_id = upd.effective_user.id
    order_num = ctx.user_data.get('bonus_order')
    
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
        f"⚠️ **ПОДТВЕРЖДЕНИЕ СПИСАНИЯ**\n\n"
        f"📦 Заказ: {order_num}\n"
        f"💰 Сумма запчастей: {current_parts} руб.\n"
        f"🎁 Будет списано: {spend_amount} руб.\n"
        f"💳 К оплате после списания: {new_total} руб.\n\n"
        f"Вы уверены?",
        reply_markup=kb,
        parse_mode='Markdown'
    )
    
    del ctx.user_data['bonus_order']
    return ConversationHandler.END

# ========== МОИ ЗАКАЗЫ ==========

async def my_orders(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать заказы пользователя"""
    await upd.effective_chat.send_action(action="typing")
    
    orders = get_user_orders(upd.effective_user.id)
    
    if not orders:
        await upd.message.reply_text("📭 У вас пока нет заказов", reply_markup=main_menu)
        return
    
    text = "📦 **ВАШИ ЗАКАЗЫ:**\n\n"
    kb = []
    
    for order in orders:
        order_num, status_text, created, tp, dp, final, needed = order
        total = safe_int(tp) + safe_int(dp)
        icon = get_status_icon(status_text)
        
        text += f"{icon} `{order_num}` — {created[:10]} — {total} руб.\n"
        kb.append([InlineKeyboardButton(f"🔍 Заказ {order_num}", callback_data=f"view_{order_num}")])
    
    kb.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")])
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

@require_order_owner
async def view_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Просмотр деталей заказа"""
    query = upd.callback_query
    order = ctx.user_data['current_order']
    order_num = order['order_number']
    
    total_parts = order.get('total_price', 0)
    delivery_price = order.get('delivery_price', 0)
    total_sum = total_parts + delivery_price
    
    eligible_sum = calculate_bonus_eligible_sum(order)
    eligible_percent = int(eligible_sum * MAX_BONUS_SPEND_PERCENT / 100) if eligible_sum > 0 else 0
    
    text = (f"📋 **ЗАКАЗ {order_num}**\n\n"
            f"🚗 VIN: `{order.get('vin', 'не указан')}`\n"
            f"📊 Пробег: {order.get('mileage', 'не указан')} км\n"
            f"📍 Адрес: {order.get('delivery_address', 'не указан')}\n"
            f"📞 Телефон: {order.get('phone', 'не указан')}\n"
            f"💰 Запчасти: {total_parts} руб.\n"
            f"🚚 Доставка: {delivery_price} руб.\n"
            f"💳 **ИТОГО: {total_sum} руб.**\n"
            f"📦 Статус: {order.get('status_text', 'неизвестен')}")
    
    if order.get('tracking_number'):
        text += f"\n📮 Трек-номер: `{order.get('tracking_number')}`"
    
    if eligible_sum > 0:
        text += f"\n\n🎁 **Доступно для бонусов:** {eligible_sum} руб.\n"
        text += f"   (можно списать до {eligible_percent} руб.)"
    
    final_order = order.get('final_order')
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        text += "\n\n📦 **ЗАКАЗАННЫЕ ЗАПЧАСТИ:**\n"
        try:
            selected_parts = ast.literal_eval(final_order)
            if isinstance(selected_parts, list) and selected_parts:
                for part in selected_parts:
                    if isinstance(part, dict):
                        part_name = part.get('name', 'неизвестно')
                        part_price = part.get('price', 0)
                        ravenol_mark = " ❌ (Ravenol)" if is_ravenol_product(part_name) else ""
                        wrapped_name = wrap_text(part_name, 25)
                        text += f"• {wrapped_name}\n   → {part_price} руб.{ravenol_mark}\n"
                    else:
                        text += f"• {part}\n"
            else:
                text += f"{final_order[:300]}"
        except:
            text += f"{final_order[:300]}"
    elif order.get('needed_parts'):
        text += f"\n\n📝 **ИЗНАЧАЛЬНЫЙ ЗАПРОС:**\n{order.get('needed_parts', 'не указан')[:300]}"
    
    status = order.get('status', '')
    kb = [[InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders_list")]]
    
    if status == 'waiting_payment':
        bonus_data = get_bonus(order['user_id'])
        eligible_sum = calculate_bonus_eligible_sum(order)
        if bonus_data['balance'] > 0 and eligible_sum > 0:
            kb.insert(0, [InlineKeyboardButton("🎁 Списать бонусы", callback_data=f"apply_bonus_{order_num}")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')

async def back_orders_list(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Назад к списку заказов"""
    query = upd.callback_query
    await query.answer()
    await my_orders(upd, ctx)

async def main_menu_back(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Назад в главное меню"""
    query = upd.callback_query
    await query.answer()
    await start(upd, ctx)

# ========== ГАРАЖ ==========

async def garage_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Меню гаража"""
    cars = get_cars(upd.effective_user.id)
    
    if not cars:
        await upd.message.reply_text(
            "🚗 **МОЙ ГАРАЖ**\n\nУ вас пока нет добавленных автомобилей.\n\n"
            "➕ **Добавить автомобиль:**\n"
            "Просто отправьте мне VIN номер (17 символов) или нажмите кнопку ниже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить авто", callback_data="garage_add")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="garage_back_to_menu")]
            ]),
            parse_mode='Markdown'
        )
        return
    
    text = "🚗 **МОЙ ГАРАЖ**\n\n"
    keyboard = []
    
    for car in cars:
        vin, description, comment, created = car
        desc = description if description else "Описание не указано"
        text += f"🔹 **{vin}**\n"
        text += f"   📝 {desc}\n"
        if comment:
            text += f"   💬 *{comment}*\n"
        text += f"   📅 Добавлен: {created[:10]}\n\n"
        
        keyboard.append([InlineKeyboardButton(f"✏️ Комментарий", callback_data=f"garage_comment_{vin}")])
        keyboard.append([InlineKeyboardButton(f"🗑️ Удалить", callback_data=f"garage_del_{vin}")])
    
    keyboard.append([InlineKeyboardButton("➕ Добавить авто", callback_data="garage_add")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="garage_back_to_menu")])
    
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def garage_add_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Начало добавления автомобиля"""
    query = upd.callback_query
    await query.answer()
    await query.edit_message_text(
        "🚗 **ДОБАВЛЕНИЕ АВТОМОБИЛЯ**\n\n"
        "Шаг 1/2: Отправьте VIN номер автомобиля (17 символов):",
        parse_mode='Markdown'
    )
    return GarageStates.VIN

async def garage_get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение VIN для гаража"""
    vin = upd.message.text.upper().strip()
    
    if not validate_vin(vin):
        await upd.message.reply_text(
            "❌ **Неверный VIN**\n\n"
            "VIN должен содержать 17 символов (только буквы и цифры, без I, O, Q).\n"
            "Попробуйте ещё раз:",
            parse_mode='Markdown'
        )
        return GarageStates.VIN
    
    ctx.user_data['new_car_vin'] = vin
    await upd.message.reply_text(
        f"🚗 VIN: `{vin}`\n\n"
        f"**Шаг 2/2:** Введите описание автомобиля\n\n"
        f"Пример: BMW X5 3.0d, 2018, чёрный\n\n"
        f"Или отправьте '-' чтобы пропустить:",
        parse_mode='Markdown'
    )
    return GarageStates.DESCRIPTION

async def garage_get_description(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение описания для гаража"""
    description = upd.message.text.strip()
    if description == "-":
        description = ""
    
    vin = ctx.user_data.pop('new_car_vin', None)
    if not vin:
        await upd.message.reply_text("❌ Ошибка. Начните добавление заново.")
        return ConversationHandler.END
    
    if save_car(upd.effective_user.id, vin, description, ""):
        await upd.message.reply_text(f"✅ Автомобиль `{vin}` успешно добавлен в ваш гараж!", parse_mode='Markdown')
    else:
        await upd.message.reply_text(f"❌ Автомобиль `{vin}` уже есть в вашем гараже!", parse_mode='Markdown')
    
    await garage_menu(upd, ctx)
    return ConversationHandler.END

async def garage_delete(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Удаление автомобиля из гаража"""
    query = upd.callback_query
    await query.answer()
    vin = query.data[12:]
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"garage_confirm_del_{vin}")],
        [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="garage_back_to_menu")]
    ])
    await query.edit_message_text(
        f"⚠️ Удалить автомобиль `{vin}` из гаража?\n\nЭто действие нельзя отменить.",
        reply_markup=kb,
        parse_mode='Markdown'
    )

async def garage_confirm_delete(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Подтверждение удаления автомобиля"""
    query = upd.callback_query
    await query.answer()
    vin = query.data[18:]
    
    if delete_car(query.from_user.id, vin):
        await query.edit_message_text(f"✅ Автомобиль `{vin}` удалён из гаража!", parse_mode='Markdown')
    else:
        await query.edit_message_text(f"❌ Автомобиль `{vin}` не найден в гараже.", parse_mode='Markdown')
    
    await garage_menu(upd, ctx)

async def garage_comment_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Начало добавления комментария к автомобилю"""
    query = upd.callback_query
    await query.answer()
    vin = query.data[16:]
    ctx.user_data['comment_vin'] = vin
    
    await query.edit_message_text(
        f"✏️ **КОММЕНТАРИЙ К АВТОМОБИЛЮ**\n\n"
        f"Автомобиль: `{vin}`\n\n"
        f"Введите комментарий для этого автомобиля.\n"
        f"Например: «зимняя резина», «жена», «служебный»\n\n"
        f"Или отправьте '-' чтобы удалить комментарий.\n\n"
        f"Максимум 100 символов.",
        parse_mode='Markdown'
    )
    return GarageStates.DESCRIPTION

async def garage_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение комментария для автомобиля"""
    user_id = upd.effective_user.id
    vin = ctx.user_data.get('comment_vin')
    
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
        await upd.message.reply_text(f"✅ Комментарий «{comment}» добавлен к автомобилю `{vin}`!", parse_mode='Markdown')
    else:
        await upd.message.reply_text(f"✅ Комментарий к автомобилю `{vin}` удалён!", parse_mode='Markdown')
    
    del ctx.user_data['comment_vin']
    await garage_menu(upd, ctx)
    return ConversationHandler.END

async def garage_back_to_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Назад в главное меню из гаража"""
    query = upd.callback_query
    await query.answer()
    await start(upd, ctx)

# ========== РЕФЕРАЛЫ ==========

async def referral_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /referral - реферальная система"""
    bot_username = (await ctx.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{upd.effective_user.id}"
    
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', (upd.effective_user.id,))
        referrals_count = c.fetchone()[0]
    finally:
        conn.close()
    
    text = (f"🔗 **РЕФЕРАЛЬНАЯ ПРОГРАММА**\n\n"
            f"**Ваша реферальная ссылка:**\n"
            f"`{link}`\n\n"
            f"👥 Приглашено друзей: **{referrals_count}**\n"
            f"📊 Вы получаете **0.5%** от суммы заказов ваших друзей бонусами!\n"
            f"🎁 Друг получает **500** приветственных бонусов!\n\n"
            f"💰 Текущий баланс: **{get_bonus(upd.effective_user.id)['balance']}** бонусов")
    
    await upd.message.reply_text(text, parse_mode='Markdown')

# ========== ДОСТАВКА И ПОМОЩЬ ==========

async def delivery_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /delivery - информация о доставке"""
    text = (f"🚚 **РАСЧЁТ ДОСТАВКИ ОТ МКАД**\n\n"
            f"Базовая стоимость: **{DELIVERY_BASE}** руб.\n\n"
            f"📌 **Тарифы:**\n"
            f"• 0 км (Москва): {DELIVERY_BASE} руб.\n"
            f"• 1-50 км: {DELIVERY_BASE} + км × {DELIVERY_RATE_UP_TO_50}\n"
            f"• 51-100 км: {DELIVERY_BASE} + км × {DELIVERY_RATE_UP_TO_100}\n"
            f"• 101+ км: {DELIVERY_BASE} + км × {DELIVERY_RATE_OVER_100}\n\n"
            f"📌 **Самовывоз (бесплатно):**\n"
            f"• Метро Давыдково\n"
            f"• Метро Строгино\n"
            f"• Метро Южная\n\n"
            f"📌 **Скидка на доставку от суммы заказа:**\n"
            f"• от 10 000 руб. → 5%\n"
            f"• от 15 000 руб. → 10%\n"
            f"• от 20 000 руб. → 15%\n"
            f"... до 100% бесплатно")
    
    await upd.message.reply_text(text, parse_mode='Markdown')

async def help_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Команда /help - помощь"""
    text = ("📖 **ПОМОЩЬ**\n\n"
            "**Основные команды:**\n"
            "/start - Главное меню\n"
            "/my_orders - Мои заказы\n"
            "/bonus - Бонусы\n"
            "/referral - Рефералы\n"
            "/delivery - Доставка\n"
            "/help - Эта справка\n\n"
            "**Мой гараж:**\n"
            "Храните VIN и описание автомобилей\n"
            "Быстрый выбор при создании заказа\n\n"
            "**Бонусная система:**\n"
            f"• Начисление: от 1% до 10% от суммы заказа\n"
            f"• Списание: до {MAX_BONUS_SPEND_PERCENT}% от суммы запчастей\n"
            f"• ❌ Не действует на RAVENOL и доставку\n"
            f"• Минимальная оплата деньгами: {MIN_CASH_PAYMENT} руб.\n\n"
            "**Вопросы:**\n"
            "По всем вопросам обращайтесь к менеджеру")
    
    await upd.message.reply_text(text, parse_mode='Markdown')

# ========== АДМИН ПАНЕЛЬ ==========

@require_manager
async def admin_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, message=None):
    """Панель управления менеджера"""
    orders = get_all_orders()
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("➕ Тестовый заказ", callback_data="admin_fix")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="admin_refresh")]
    ]
    
    for o in orders[:10]:
        order_num = clean_order_number(o[0])
        user_name = o[1][:15] if len(o[1]) > 15 else o[1]
        status_text = o[2] if len(o) > 2 else ''
        icon = get_status_icon(status_text)
        
        keyboard.append([InlineKeyboardButton(f"{icon} {order_num} | {user_name}", callback_data=f"admin_order_{order_num}")])
    
    text = "👨‍💼 **АДМИН ПАНЕЛЬ**\n\nВыберите заказ для управления:"
    
    if message:
        try:
            await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Admin menu edit error: {e}")
    else:
        await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

@require_manager
async def admin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка админ-панели"""
    query = upd.callback_query
    data = query.data
    await query.answer()
    
    if data == "admin_refresh":
        await admin_menu(upd, ctx, query.message)
        return
    
    if data == "admin_stats":
        orders = get_all_orders()
        total_orders = len(orders)
        total_sum = 0
        status_count = {}
        
        for o in orders:
            order = get_order(o[0])
            if order:
                total_sum += order.get('total_price', 0) + order.get('delivery_price', 0)
            status = o[2] if len(o) > 2 else 'Неизвестно'
            status_count[status] = status_count.get(status, 0) + 1
        
        status_text = "\n".join([f"• {s}: {c}" for s, c in status_count.items()])
        
        await query.edit_message_text(
            f"📊 **СТАТИСТИКА**\n\n"
            f"📦 Заказов: {total_orders}\n"
            f"💰 Сумма: {total_sum:,} руб.\n\n"
            f"**По статусам:**\n{status_text}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]),
            parse_mode='Markdown'
        )
        return
    
    if data == "admin_fix":
        conn = sqlite3.connect(DB_PATH)
        try:
            c = conn.cursor()
            num = generate_order_number()
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
        finally:
            conn.close()
        await admin_menu(upd, ctx, query.message)
        return
    
    if data == "admin_back":
        await admin_menu(upd, ctx, query.message)
        return
    
    if data.startswith("admin_order_"):
        order_num = data[12:]
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
        text = (f"📋 **ЗАКАЗ {order.get('order_number', '')}**\n\n"
                f"👤 {order.get('user_name', '')}\n"
                f"📞 {order.get('phone', 'не указан')}\n"
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
            [InlineKeyboardButton("❌ Отменить", callback_data=f"cancel_{order_num}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
        ]
        
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return
    
    # ========== КРИТИЧЕСКАЯ ЧАСТЬ: ОТПРАВЛЕН (SHIP) ==========
    if data.startswith("ship_"):
        order_num = data[5:]
        order_num = clean_order_number(order_num)
        
        if not order_num:
            await query.edit_message_text("❌ Не удалось определить номер заказа. Пожалуйста, откройте заказ заново.")
            return
        
        # Проверяем существование заказа
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
            f"📦 **Введите трек-номер для заказа {order_num}:**\n\n"
            f"⬇️ **ВАЖНО!** ⬇️\n\n"
            f"1️⃣ **НАЖМИТЕ НА ЭТО СООБЩЕНИЕ**\n"
            f"2️⃣ Выберите **«ОТВЕТИТЬ»** (Reply)\n"
            f"3️⃣ Введите трек-номер\n"
            f"4️⃣ Отправьте\n\n"
            f"❌ Не пишите просто в чат - бот не поймёт!\n\n"
            f"Пример трек-номера: `10270287171`",
            parse_mode='Markdown'
        )
        return
    
    # ========== ОСТАЛЬНЫЕ СТАТУСЫ ==========
    if data.startswith("pay_"):
        order_num = data[4:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='paid'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order['user_id'], text=f"✅ Заказ {order_num} оплачен! Спасибо за покупку!")
                
                # Начисляем бонусы ПОСЛЕ оплаты
                final_order = order.get('final_order', '')
                if final_order and final_order not in [None, 'None', '[]', '{}']:
                    try:
                        selected_parts = ast.literal_eval(final_order)
                        if isinstance(selected_parts, list):
                            bonus_percent = get_bonus_percent(order['user_id'])
                            eligible_for_bonus = sum(p.get('price', 0) for p in selected_parts if isinstance(p, dict) and not is_ravenol_product(p.get('name', '')))
                            bonus = int(eligible_for_bonus * bonus_percent / 100)
                            if bonus > 0:
                                add_bonus(order['user_id'], order_num, bonus, f"Заказ {order_num} ({bonus_percent}% от {eligible_for_bonus} руб.)")
                    except:
                        pass
            await query.edit_message_text(query.message.text + "\n\n✅ **СТАТУС: ОПЛАЧЕН**", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("ordered_"):
        order_num = data[8:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='ordered'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order['user_id'], text=f"📦 Заказ {order_num} заказан у поставщика! Ожидайте поступления.")
            await query.edit_message_text(query.message.text + "\n\n✅ **СТАТУС: ЗАКАЗАН**", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("arrived_"):
        order_num = data[8:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='arrived'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order['user_id'], text=f"📦✅ Заказ {order_num}\n\nТовар поступил на склад! Скоро он будет готов к выдаче или отправке.")
            await query.edit_message_text(query.message.text + "\n\n✅ **СТАТУС: ТОВАР ПОСТУПИЛ**", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("ready_"):
        order_num = data[6:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='ready'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order['user_id'], text=f"✅ Заказ {order_num} готов к выдаче! Можете забрать.")
            await query.edit_message_text(query.message.text + "\n\n✅ **СТАТУС: ГОТОВ К ВЫДАЧЕ**", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("del_"):
        order_num = data[4:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='delivered'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order['user_id'], text=f"🏠 Заказ {order_num} доставлен! Спасибо за покупку!")
            await query.edit_message_text(query.message.text + "\n\n✅ **СТАТУС: ДОСТАВЛЕН**", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("issued_"):
        order_num = data[7:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='issued'):
            order = get_order(order_num)
            if order:
                await ctx.bot.send_message(order['user_id'], text=f"📋 Заказ {order_num} ВЫДАН!\n\nСпасибо, что воспользовались нашими услугами! Ждём вас снова! 🏎️")
            await query.edit_message_text(query.message.text + "\n\n✅ **СТАТУС: ВЫДАН**", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("cancel_"):
        order_num = data[7:]
        order_num = clean_order_number(order_num)
        
        order = get_order(order_num)
        if order:
            conn = sqlite3.connect(DB_PATH)
            try:
                c = conn.cursor()
                c.execute('SELECT amount FROM bonus_history WHERE order_number = ? AND type = "earned"', (order_num,))
                bonus_row = c.fetchone()
                if bonus_row and bonus_row[0] > 0:
                    refund_bonus(order['user_id'], order_num, bonus_row[0], f"Возврат бонусов при отмене заказа {order_num}")
                    await ctx.bot.send_message(order['user_id'], text=f"❌ Заказ {order_num} отменён менеджером.\n\n💰 Бонусы в размере {bonus_row[0]} руб. были списаны с вашего счета.")
                else:
                    await ctx.bot.send_message(order['user_id'], text=f"❌ Заказ {order_num} отменён менеджером.")
            finally:
                conn.close()
        
        if update_order(order_num, status='cancelled'):
            await query.edit_message_text(query.message.text + "\n\n✅ **СТАТУС: ОТМЕНЁН**", parse_mode='Markdown')
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return

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
        logger.info(f"[TRACK] Reply текст: {reply_text[:100]}")
        match = re.search(r'(RVN-[A-Z0-9]{6})', reply_text)
        if match:
            order_num = match.group(1)
            logger.info(f"[TRACK] Найден из reply: {order_num}")
    
    # Способ 3: Поиск в истории сообщений
    if not order_num:
        logger.info("[TRACK] Поиск в истории сообщений...")
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
            "3️⃣ **НАЖМИТЕ НА СООБЩЕНИЕ БОТА** и выберите «Ответить»\n"
            "4️⃣ Введите трек-номер\n"
            "5️⃣ Отправьте\n\n"
            "❌ Не пишите просто в чат - бот не поймёт!\n\n"
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

# ========== ОТВЕТ МЕНЕДЖЕРА ==========

@require_manager
async def manager_reply(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ответ менеджера на заказ (подбор запчастей)"""
    if not upd.message.reply_to_message:
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
        order['user_id'],
        text=f"🛒 **ПОДБОР ЗАПЧАСТЕЙ ДЛЯ ЗАКАЗА #{order_num}**\n\n"
             f"Менеджер подобрал для вас следующие позиции:\n\n"
             f"Выберите нужные запчасти (можно отметить несколько):",
        reply_markup=InlineKeyboardMarkup(kb),
        parse_mode='Markdown'
    )
    
    await upd.message.reply_text(f"✅ Подбор запчастей для заказа {order_num} отправлен клиенту!")

# ========== ВЫБОР ЗАПЧАСТЕЙ КЛИЕНТОМ ==========

async def select_cb(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Выбор запчасти клиентом"""
    query = upd.callback_query
    await query.answer()
    
    _, order_num, idx = query.data.split('_')
    idx = int(idx)
    uid = query.from_user.id
    
    order = get_order(order_num)
    if not order or not order.get('selected_products'):
        await query.edit_message_text("❌ Ошибка: подбор запчастей не найден")
        return
    
    if order['user_id'] != uid:
        await query.answer("❌ Это не ваш заказ!", show_alert=True)
        return
    
    products = parse_products(order['selected_products'])
    
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
    
    if order['user_id'] != uid:
        await query.answer("❌ Это не ваш заказ!", show_alert=True)
        return
    
    if uid not in user_selections or order_num not in user_selections[uid] or not user_selections[uid][order_num]:
        await query.edit_message_text("❌ Ничего не выбрано. Пожалуйста, выберите хотя бы одну запчасть.")
        return
    
    products = parse_products(order['selected_products'])
    selected = []
    total = 0
    
    for idx in user_selections[uid][order_num]:
        if idx < len(products):
            selected.append(products[idx])
            total += products[idx]['price']
    
    if not selected:
        await query.edit_message_text("❌ Вы не выбрали ни одной запчасти.")
        return
    
    # Расчёт доставки
    delivery_price = order['delivery_price']
    delivery_disc = delivery_discount(total)
    
    if delivery_disc >= 100:
        delivery_final = 0
        delivery_text = "🚚 Доставка: **БЕСПЛАТНО!**"
    elif delivery_disc > 0:
        discount_amount = int(delivery_price * delivery_disc / 100)
        delivery_final = delivery_price - discount_amount
        delivery_text = f"🚚 Доставка: {delivery_price} руб. → скидка {delivery_disc}% = {delivery_final} руб."
    else:
        delivery_final = delivery_price
        delivery_text = f"🚚 Доставка: {delivery_price} руб."
    
    final_total = total + delivery_final
    update_order(order_num, total_price=total, final_order=str(selected), status='waiting_payment')
    
    # Бонусы НЕ начисляем здесь! Они начисляются после оплаты в admin_callback
    
    # Формируем результат
    result = f"✅ **ЗАКАЗ #{order_num} ПОДТВЕРЖДЁН!**\n\n"
    
    for p in selected:
        wrapped_name = wrap_text(p['name'], 25)
        ravenol_mark = " ❌ (Ravenol, бонусы не начислены)" if is_ravenol_product(p['name']) else ""
        result += f"• {wrapped_name}\n   → {p['price']} руб.{ravenol_mark}\n"
    
    result += f"\n{delivery_text}\n\n"
    result += f"💰 **ИТОГО К ОПЛАТЕ: {final_total} руб.**"
    result += "\n\n📞 Менеджер свяжется с вами для уточнения оплаты."
    
    await query.edit_message_text(result, parse_mode='Markdown')
    await ctx.bot.send_message(
        MANAGER_ID,
        f"✅ **ЗАКАЗ {order_num} ПОДТВЕРЖДЁН КЛИЕНТОМ!**\n\n"
        f"👤 Клиент: {order['user_name']}\n"
        f"💰 Сумма: {final_total} руб.",
        parse_mode='Markdown'
    )
    
    # Очищаем выбор
    if uid in user_selections:
        del user_selections[uid][order_num]

# ========== ГЛОБАЛЬНЫЙ ОБРАБОТЧИК ОШИБОК ==========

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Глобальный обработчик ошибок"""
    logger.error(f"Exception: {context.error}")
    if update and update.effective_message:
        await update.effective_message.reply_text(
            "⚠️ **Произошла ошибка**\n\n"
            "Пожалуйста, попробуйте позже или обратитесь к администратору.",
            parse_mode='Markdown'
        )

# ========== ЗАПУСК ==========

def main():
    """Запуск бота"""
    init_db()
    
    scheduler = BackgroundScheduler()
    scheduler.add_job(backup_db, 'cron', hour=3, minute=0)
    scheduler.start()
    logger.info("Scheduler started - daily backup at 03:00")
    
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
    
    # ConversationHandler для заказов
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
            OrderStates.ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
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
    
    # ConversationHandler для гаража
    garage_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(garage_add_start, pattern="^garage_add$")],
        states={
            GarageStates.VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_get_vin)],
            GarageStates.DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_get_description)]
        },
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # ConversationHandler для комментариев гаража
    garage_comment_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(garage_comment_start, pattern="^garage_comment_")],
        states={GarageStates.DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_comment_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # ConversationHandler для сохранения VIN
    save_vin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(save_vin_callback, pattern="^save_vin_")],
        states={SaveStates.COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_vin_comment_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # ConversationHandler для списания бонусов
    spend_bonus_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(spend_bonus_custom_callback, pattern="^spend_bonus_custom_")],
        states={BonusStates.SPEND: [MessageHandler(filters.TEXT & ~filters.COMMAND, spend_bonus_custom_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(order_conv)
    app.add_handler(garage_conv)
    app.add_handler(garage_comment_conv)
    app.add_handler(save_vin_conv)
    app.add_handler(spend_bonus_conv)
    app.add_handler(CommandHandler("my_orders", my_orders))
    app.add_handler(CommandHandler("bonus", bonus_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("delivery", delivery_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", admin_menu))
    
    # Кнопочные обработчики
    app.add_handler(MessageHandler(filters.Regex("^(🚗 Мой гараж)$"), garage_menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    
    # Callback обработчики
    app.add_handler(CallbackQueryHandler(select_cb, pattern="^sel_"))
    app.add_handler(CallbackQueryHandler(finalize_cb, pattern="^fin_"))
    app.add_handler(CallbackQueryHandler(view_order, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(back_orders_list, pattern="^back_orders_list$"))
    app.add_handler(CallbackQueryHandler(main_menu_back, pattern="^main_menu_back$"))
    app.add_handler(CallbackQueryHandler(bonus_history_callback, pattern="^bonus_history$"))
    app.add_handler(CallbackQueryHandler(bonus_back_callback, pattern="^bonus_back$"))
    app.add_handler(CallbackQueryHandler(apply_bonus_callback, pattern="^apply_bonus_"))
    app.add_handler(CallbackQueryHandler(spend_bonus_percent_callback, pattern="^spend_bonus_percent_"))
    app.add_handler(CallbackQueryHandler(confirm_spend_callback, pattern="^confirm_spend_"))
    app.add_handler(CallbackQueryHandler(garage_delete, pattern="^garage_del_"))
    app.add_handler(CallbackQueryHandler(garage_confirm_delete, pattern="^garage_confirm_del_"))
    app.add_handler(CallbackQueryHandler(garage_back_to_menu, pattern="^garage_back_to_menu$"))
    app.add_handler(CallbackQueryHandler(no_save_vin_callback, pattern="^no_save_vin$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(ship_|pay_|ordered_|arrived_|ready_|del_|issued_|cancel_|admin_)"))
    
    # Ответы менеджера
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), track_input))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), manager_reply))
    
    # Глобальный обработчик ошибок
    app.add_error_handler(error_handler)
    
    logger.info(f"🤖 Бот запущен! Админ ID: {MANAGER_ID}")
    app.run_polling()

if __name__ == "__main__":
    main()
