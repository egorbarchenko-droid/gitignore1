#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Shop Bot для автозапчастей
Версия: 2.0.0 (исправленная)
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
import asyncio
from datetime import datetime, timedelta
from functools import wraps
from typing import Dict, Optional, List, Tuple, Any
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application, CommandHandler, MessageHandler, CallbackQueryHandler, 
    ContextTypes, ConversationHandler, filters
)
from telegram.warnings import PTBUserWarning

# ========== КОНСТАНТЫ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_ID = int(os.environ.get("MANAGER_ID", 804070528))
DELIVERY_BASE = 500

DATA_DIR = os.getenv('DATA_DIR', '/app/data')
DB_PATH = os.path.join(DATA_DIR, 'shop_bot.db')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

# Бонусные ограничения
MAX_BONUS_SPEND_PERCENT = 20  # Максимум 20% от суммы заказа
MIN_ORDER_FOR_BONUS = 500     # Минимальная сумма заказа для списания бонусов
MIN_CASH_PAYMENT = 100        # Минимальная оплата деньгами

# Запрещённые бренды для списания бонусов
RESTRICTED_BRANDS = ['ravenol', 'равенол', 'raven0l']

# Безопасные колонки для UPDATE
ALLOWED_ORDER_COLUMNS = {
    'phone', 'our_cost', 'tracking_number', 'final_order', 
    'total_price', 'status', 'status_text', 'delivery_type',
    'delivery_price', 'distance', 'city', 'delivery_address'
}

# Валидные переходы статусов
STATUS_TRANSITIONS = {
    'pending': ['waiting_selection', 'cancelled'],
    'waiting_selection': ['waiting_payment', 'cancelled'],
    'waiting_payment': ['paid', 'cancelled'],
    'paid': ['ordered', 'cancelled', 'refunded'],
    'ordered': ['arrived', 'cancelled'],
    'arrived': ['ready', 'cancelled'],
    'ready': ['shipped', 'issued', 'cancelled'],
    'shipped': ['delivered', 'cancelled'],
    'delivered': ['issued'],
    'issued': [],
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

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)

# ========== НАСТРОЙКА ЛОГИРОВАНИЯ ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

# ========== СОСТОЯНИЯ ДЛЯ CONVERSATIONHANDLER ==========
class OrderStates:
    """Состояния для оформления заказа"""
    VIN, MILEAGE, STYLE_CITY, STYLE_HIGHWAY, DELIVERY_TYPE, \
    ADDRESS, PHONE, PART_NODE, AXLE, PARTS, CONFIRM = range(11)

class GarageStates:
    """Состояния для гаража"""
    VIN, DESCRIPTION = range(20, 22)

class BonusStates:
    """Состояния для бонусов"""
    SPEND = 30

class SaveStates:
    """Состояния для сохранения VIN"""
    COMMENT = 40

# Узлы, для которых нужна информация об оси
AXLE_REQUIRED_NODES = ["🔧 Подвеска", "🛑 Тормозная система", "🛞 Рулевое управление"]

# ========== БЕЗОПАСНЫЕ ФУНКЦИИ РАБОТЫ С БД ==========

def safe_int(val: Any, default: int = 0) -> int:
    """Безопасное преобразование в int"""
    if val is None:
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
    # Удаляем всё, кроме букв, цифр и дефиса
    cleaned = re.sub(r'[^A-Za-z0-9-]', '', order_num)
    # Проверяем формат RVN-XXXXXX
    if not re.match(r'^RVN-[A-Z0-9]{6}$', cleaned):
        logger.warning(f"Invalid order number format: {order_num}")
        return ""
    return cleaned

def validate_vin(vin: str) -> bool:
    """Полная валидация VIN номера"""
    if not vin or len(vin) != 17:
        return False
    
    vin = vin.upper()
    
    # Запрещённые символы в VIN
    if re.search(r'[IOQ]', vin):
        return False
    
    # Должен содержать только буквы и цифры
    if not vin.isalnum():
        return False
    
    return True

def has_restricted_brands(order: Dict) -> bool:
    """Проверяет, есть ли в заказе запрещённые бренды"""
    # Проверяем final_order (выбранные запчасти)
    final_order = order.get('final_order', '')
    if final_order and final_order not in [None, 'None', '[]']:
        try:
            selected_parts = ast.literal_eval(final_order)
            if isinstance(selected_parts, list):
                for part in selected_parts:
                    if isinstance(part, dict):
                        part_name = part.get('name', '').lower()
                        for brand in RESTRICTED_BRANDS:
                            if brand in part_name:
                                return True
        except:
            pass
    
    # Проверяем needed_parts (изначальный запрос)
    needed_parts = order.get('needed_parts', '').lower()
    for brand in RESTRICTED_BRANDS:
        if brand in needed_parts:
            return True
    
    # Проверяем selected_products (подбор менеджера)
    selected_products = order.get('selected_products', '').lower()
    for brand in RESTRICTED_BRANDS:
        if brand in selected_products:
            return True
    
    return False

def validate_status_transition(old_status: str, new_status: str) -> bool:
    """Проверяет валидность перехода статуса"""
    return new_status in STATUS_TRANSITIONS.get(old_status, [])

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
    for col in ['phone', 'our_cost', 'tracking_number', 'final_order', 'comment']:
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
    c.execute('CREATE INDEX IF NOT EXISTS idx_garage_user_id ON garage(user_id)')
    c.execute('CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number)')
    
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
    conn.execute('BEGIN IMMEDIATE')  # Блокировка таблицы
    try:
        c = conn.cursor()
        max_attempts = 10
        for _ in range(max_attempts):
            num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
            c.execute('SELECT 1 FROM orders WHERE order_number = ?', (num,))
            if not c.fetchone():
                conn.commit()
                return num
        raise RuntimeError("Failed to generate unique order number")
    finally:
        conn.close()

def update_order(order_number: str, **kwargs) -> bool:
    """Безопасное обновление заказа"""
    order_number = clean_order_number(order_number)
    if not order_number:
        logger.error(f"Invalid order number for update")
        return False
    
    # Фильтруем только разрешённые поля
    safe_kwargs = {k: v for k, v in kwargs.items() if k in ALLOWED_ORDER_COLUMNS}
    if not safe_kwargs:
        return False
    
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        
        # Проверяем валидность перехода статуса если он меняется
        if 'status' in safe_kwargs:
            c.execute('SELECT status FROM orders WHERE order_number = ?', (order_number,))
            row = c.fetchone()
            if row and not validate_status_transition(row[0], safe_kwargs['status']):
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
    """Начисление бонусов"""
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
    
    conn = sqlite3.connect(DB_PATH)
    try:
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        
        c.execute('SELECT balance FROM bonuses WHERE user_id = ?', (user_id,))
        balance = c.fetchone()
        
        if not balance or balance[0] < amount:
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

# ========== РАСЧЁТЫ ==========

def calc_delivery_price(km: int) -> int:
    """Расчёт стоимости доставки"""
    if km <= 0:
        return DELIVERY_BASE
    if km <= 50:
        return DELIVERY_BASE + km * 25
    if km <= 100:
        return DELIVERY_BASE + km * 35
    return DELIVERY_BASE + km * 50

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
        await query.answer()
        
        # Извлекаем order_num из callback data
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
                add_bonus(upd.effective_user.id, None, 500, "Приветственные бонусы")
                await ctx.bot.send_message(ref_id, f"👋 {upd.effective_user.full_name} перешёл по вашей ссылке!")
                await upd.message.reply_text("🎉 +500 бонусов!")
            finally:
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

async def new_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Начало оформления заказа"""
    cars = get_cars(upd.effective_user.id)
    if cars:
        keyboard = [[InlineKeyboardButton("🆕 Ввести вручную", callback_data="order_manual")]]
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
            "🔧 ВЫБЕРИТЕ АВТОМОБИЛЬ из гаража или введите VIN вручную:",
            reply_markup=InlineKeyboardMarkup(keyboard)
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
        await query.edit_message_text(f"🚗 Выбран автомобиль: {vin}\n\n📊 Теперь введите пробег (км):")
        return OrderStates.MILEAGE
    
    return OrderStates.VIN

async def get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение VIN номера"""
    vin = upd.message.text.upper().strip()
    
    if not validate_vin(vin):
        await upd.message.reply_text("❌ Неверный VIN. Он должен содержать 17 символов (только буквы и цифры, без I, O, Q). Попробуйте ещё раз:")
        return OrderStates.VIN
    
    ctx.user_data['vin'] = vin
    await upd.message.reply_text("📊 Пробег (км):")
    return OrderStates.MILEAGE

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
    
    await upd.message.reply_text("🏙️ Стиль вождения в городе:", reply_markup=city_style_kb)
    return OrderStates.STYLE_CITY

async def get_style_city(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение стиля вождения в городе"""
    ctx.user_data['style_city'] = upd.message.text
    await upd.message.reply_text("🛣️ Стиль вождения на трассе:", reply_markup=highway_style_kb)
    return OrderStates.STYLE_HIGHWAY

async def get_style_highway(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение стиля вождения на трассе"""
    ctx.user_data['style_highway'] = upd.message.text
    await upd.message.reply_text("🚚 Способ доставки:", reply_markup=delivery_type_kb)
    return OrderStates.DELIVERY_TYPE

async def get_delivery_type(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение типа доставки"""
    choice = upd.message.text
    ctx.user_data['delivery_type'] = choice
    
    if choice == "Курьером":
        await upd.message.reply_text("📍 Введите ПОЛНЫЙ АДРЕС доставки\n\nПример: г. Москва, ул. Тверская, д. 15, кв. 78")
        return OrderStates.ADDRESS
    elif choice == "Самовывоз":
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("📍 Самовывоз\n\nДоступные станции:\n- Метро Давыдково\n- Метро Строгино\n- Метро Южная\n\nВведите адрес самовывоза:")
        return OrderStates.ADDRESS
    else:
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("🚛 Сторонняя фирма (стоимость рассчитает менеджер)\n\n📍 Введите адрес доставки:")
        return OrderStates.ADDRESS

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
            f"📍 Адрес: {full_address}\n🏙️ Город: {city}\n📏 Расстояние от МКАД: {distance} км\n"
            f"🚚 Стоимость доставки: {price} руб.\n\n📞 Введите ваш контактный телефон:"
        )
    else:
        await upd.message.reply_text(f"📍 Адрес: {full_address}\n\n📞 Введите ваш контактный телефон:")
    
    return OrderStates.PHONE

async def get_phone(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение номера телефона"""
    phone = upd.message.text.strip()
    if len(phone) < 5:
        await upd.message.reply_text("❌ Пожалуйста, введите корректный номер телефона:")
        return OrderStates.PHONE
    
    ctx.user_data['phone'] = phone
    await upd.message.reply_text("🔧 Выберите узел запчасти:", reply_markup=part_node_kb)
    return OrderStates.PART_NODE

async def get_part_node(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение узла запчасти"""
    ctx.user_data['part_node'] = upd.message.text
    
    if upd.message.text in AXLE_REQUIRED_NODES:
        await upd.message.reply_text("🔧 Выберите ось:", reply_markup=axle_kb)
        return OrderStates.AXLE
    else:
        ctx.user_data['axle'] = "Не требуется"
        await upd.message.reply_text("🔧 Какие запчасти нужны? (каждая с новой строки)\n\nПример:\nКолодки тормозные\nДиски тормозные")
        return OrderStates.PARTS

async def get_axle(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение оси"""
    ctx.user_data['axle'] = upd.message.text
    await upd.message.reply_text("🔧 Какие запчасти нужны? (каждая с новой строки)\n\nПример:\nКолодки тормозные\nДиски тормозные")
    return OrderStates.PARTS

async def get_parts(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение списка запчастей"""
    if not upd.message.text.strip():
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Продолжить", callback_data="continue_order")],
            [InlineKeyboardButton("❌ Отменить заказ", callback_data="cancel_order")]
        ])
        await upd.message.reply_text(
            "❌ Вы не ввели запчасти.\n\nХотите продолжить оформление заказа или отменить его?",
            reply_markup=kb
        )
        return OrderStates.PARTS
    
    ctx.user_data['needed_parts'] = upd.message.text
    
    data = ctx.user_data
    summary = (f"📋 ПРОВЕРЬТЕ ЗАКАЗ\n\n"
               f"🚗 VIN: {data.get('vin', 'не указан')}\n📊 Пробег: {data.get('mileage', 'не указан')} км\n"
               f"🏎️ Стиль город: {data.get('style_city', 'не указан')}\n🛣️ Стиль трасса: {data.get('style_highway', 'не указан')}\n"
               f"🏙️ Город: {data.get('city', 'не указан')}\n🚚 Доставка: {data.get('delivery_type', 'не указана')}\n"
               f"📍 Адрес: {data.get('delivery_address', 'не указан')}\n📞 Телефон: {data.get('phone', 'не указан')}\n"
               f"🔧 Узел: {data.get('part_node', 'не указан')}\n🔧 Ось: {data.get('axle', 'не указана')}\n"
               f"📝 Запчасти: {data.get('needed_parts', 'не указаны')}\n\n"
               f"💰 Доставка: {data.get('delivery_price', 500)} руб.\n\n"
               "✅ Всё верно? Нажмите «Готово» или «Редактировать»")
    
    await upd.message.reply_text(summary, reply_markup=confirm_order_kb)
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
                    [InlineKeyboardButton("✅ Да, сохранить", callback_data=f"save_vin_{vin}")],
                    [InlineKeyboardButton("❌ Нет, спасибо", callback_data="no_save_vin")]
                ])
                await upd.message.reply_text(
                    f"🚗 Хотите сохранить автомобиль с VIN `{vin}` в ваш гараж?\n\n"
                    f"В следующий раз вам не придётся вводить VIN заново!",
                    reply_markup=kb,
                    parse_mode='Markdown'
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
        ctx.user_data.clear()
        await upd.message.reply_text("✏️ Давайте начнём заказ заново. Нажмите 🛒 Новый заказ", reply_markup=main_menu)
        return ConversationHandler.END
    
    return ConversationHandler.END

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
            f"⭐ Текущий процент: **{percent}%**\n\n"
            f"**ПРАВИЛА ИСПОЛЬЗОВАНИЯ:**\n"
            f"• Можно списать не более **{MAX_BONUS_SPEND_PERCENT}%** от суммы заказа\n"
            f"• ❌ **Не действует на продукцию RAVENOL**\n"
            f"• Минимальная оплата деньгами: {MIN_CASH_PAYMENT} руб.\n\n"
            "📊 **Градация начисления:**\n"
            "1% → до 100 000 руб.\n2% → 100 000 - 200 000 руб.\n3% → 200 000 - 300 000 руб.\n"
            "4% → 300 000 - 400 000 руб.\n5% → 400 000 - 500 000 руб.\n6% → 500 000 - 600 000 руб.\n"
            "7% → 600 000 - 700 000 руб.\n8% → 700 000 - 800 000 руб.\n9% → 800 000 - 900 000 руб.\n10% → от 900 000 руб.\n\n")
    
    await upd.message.reply_text(text, parse_mode='Markdown')

@require_order_owner
async def apply_bonus_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Применение бонусов к заказу"""
    query = upd.callback_query
    order = ctx.user_data['current_order']
    order_num = order['order_number']
    uid = query.from_user.id
    
    if order.get('status') != 'waiting_payment':
        await query.edit_message_text("❌ Бонусы можно применить только к заказу в статусе 'Ожидает оплаты'")
        return
    
    # Проверка на запрещённые бренды
    if has_restricted_brands(order):
        await query.edit_message_text(
            "❌ **НЕВОЗМОЖНО СПИСАТЬ БОНУСЫ**\n\n"
            "В вашем заказе присутствует продукция **RAVENOL**.\n"
            "По условиям акции, на продукцию Ravenol списание бонусов не действует.\n\n"
            "Вы можете:\n"
            "• Удалить продукцию Ravenol из заказа\n"
            "• Оплатить заказ полностью без списания бонусов\n\n"
            "По всем вопросам обратитесь к менеджеру.",
            parse_mode='Markdown'
        )
        return
    
    bonus_data = get_bonus(uid)
    balance = bonus_data['balance']
    total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
    
    # Расчёт максимального списания (20%)
    max_bonus_spend = int(total_sum * MAX_BONUS_SPEND_PERCENT / 100)
    
    if balance <= 0 or max_bonus_spend <= 0 or total_sum < MIN_ORDER_FOR_BONUS:
        await query.edit_message_text(
            f"❌ Невозможно списать бонусы.\n\n"
            f"• Доступно бонусов: {balance} руб.\n"
            f"• Максимум для списания (20%): {max_bonus_spend} руб.\n"
            f"• Сумма заказа: {total_sum} руб.\n\n"
            f"Минимальная сумма заказа для списания: {MIN_ORDER_FOR_BONUS} руб."
        )
        return
    
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Списать {MAX_BONUS_SPEND_PERCENT}% ({max_bonus_spend} руб.)", 
                            callback_data=f"spend_max_percent_{order_num}")],
        [InlineKeyboardButton("✏️ Ввести сумму", callback_data=f"spend_custom_bonus_{order_num}")],
        [InlineKeyboardButton("◀️ Назад к заказу", callback_data=f"view_{order_num}")]
    ])
    
    await query.edit_message_text(
        f"🎁 **СПИСАНИЕ БОНУСОВ**\n\n"
        f"📦 Заказ: {order_num}\n"
        f"💰 Сумма заказа: {total_sum} руб.\n"
        f"🎁 Доступно бонусов: {balance} руб.\n"
        f"📊 **Максимум для списания: {max_bonus_spend} руб. ({MAX_BONUS_SPEND_PERCENT}%)**\n\n"
        f"Выберите действие:",
        reply_markup=kb,
        parse_mode='Markdown'
    )

async def spend_max_percent_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Списание максимального процента бонусов"""
    query = upd.callback_query
    await query.answer()
    
    order_num = query.data[18:]
    uid = query.from_user.id
    
    order = get_order(order_num)
    if not order:
        await query.edit_message_text("❌ Заказ не найден")
        return
    
    # Проверка на Ravenol
    if has_restricted_brands(order):
        await query.edit_message_text("❌ Списание бонусов недоступно для заказов с продукцией Ravenol")
        return
    
    total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
    bonus_data = get_bonus(uid)
    balance = bonus_data['balance']
    
    max_allowed = int(total_sum * MAX_BONUS_SPEND_PERCENT / 100)
    spend_amount = min(balance, max_allowed)
    
    # Проверка на минимальную оплату деньгами
    new_total = total_sum - spend_amount
    if new_total < MIN_CASH_PAYMENT and new_total > 0:
        spend_amount = total_sum - MIN_CASH_PAYMENT
        new_total = MIN_CASH_PAYMENT
    
    if spend_amount <= 0:
        await query.edit_message_text("❌ Недостаточно бонусов или сумма заказа слишком мала")
        return
    
    if use_bonus(uid, order_num, spend_amount, f"Списание {MAX_BONUS_SPEND_PERCENT}% по заказу {order_num}"):
        update_order(order_num, total_price=order.get('total_price', 0) - spend_amount)
        
        await query.edit_message_text(
            f"✅ **СПИСАНО {MAX_BONUS_SPEND_PERCENT}% БОНУСАМИ!**\n\n"
            f"📦 Заказ: {order_num}\n"
            f"💰 Сумма: {total_sum} руб.\n"
            f"🎁 Списано: {spend_amount} руб. ({int(spend_amount/total_sum*100)}%)\n"
            f"💳 К оплате: {new_total} руб.\n\n"
            f"Остаток бонусов: {balance - spend_amount} руб.",
            parse_mode='Markdown'
        )
    else:
        await query.edit_message_text("❌ Ошибка при списании бонусов")

# ========== МОИ ЗАКАЗЫ ==========

async def my_orders(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Показать заказы пользователя"""
    orders = get_user_orders(upd.effective_user.id)
    
    if not orders:
        await upd.message.reply_text("📭 У вас пока нет заказов", reply_markup=main_menu)
        return
    
    text = "📦 ВАШИ ЗАКАЗЫ:\n\n"
    kb = []
    
    for order in orders:
        order_num, status_text, created, tp, dp, final, needed = order
        total = safe_int(tp) + safe_int(dp)
        
        icon_map = {
            'Ожидает подбора': '🆕', 'Ожидает выбора': '🟡', 'Ожидает оплаты': '💰',
            'Оплачен': '✅', 'Заказан': '📦', 'Товар поступил': '📦✅',
            'Готов к выдаче': '✅', 'Отправлен': '🚚', 'Доставлен': '🏠',
            'Выдан': '📋', 'Отменён': '❌'
        }
        
        icon = '📦'
        for key, ic in icon_map.items():
            if key in status_text:
                icon = ic
                break
        
        text += f"{icon} {order_num} — {created[:10]} — {total} руб.\n"
        kb.append([InlineKeyboardButton(f"🔍 Заказ {order_num}", callback_data=f"view_{order_num}")])
    
    kb.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")])
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

@require_order_owner
async def view_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Просмотр деталей заказа"""
    query = upd.callback_query
    order = ctx.user_data['current_order']
    order_num = order['order_number']
    
    total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
    
    text = (f"📋 ЗАКАЗ {order_num}\n\n"
            f"👤 {order.get('user_name', '')}\n📅 {order.get('created_at', '')}\n"
            f"🚗 VIN: {order.get('vin', 'не указан')}\n📊 Пробег: {order.get('mileage', 'не указан')} км\n"
            f"🏙️ Город: {order.get('city', 'не указан')}\n🚚 Доставка: {order.get('delivery_type', 'не указана')} | {order.get('delivery_price', 0)} руб.\n"
            f"📞 Телефон: {order.get('phone', 'не указан')}\n💰 Общая сумма: {total_sum} руб.\n"
            f"📦 Статус: {order.get('status_text', 'неизвестен')}")
    
    if order.get('tracking_number'):
        text += f"\n📮 Трек-номер: {order.get('tracking_number')}"
    
    final_order = order.get('final_order')
    if final_order and final_order not in [None, 'None', '[]']:
        text += "\n\n📦 ЗАКАЗАННЫЕ ЗАПЧАСТИ:\n"
        try:
            selected_parts = ast.literal_eval(final_order)
            if isinstance(selected_parts, list) and selected_parts:
                for part in selected_parts:
                    if isinstance(part, dict):
                        wrapped_name = wrap_text(part.get('name', 'неизвестно'), 22)
                        text += f"• {wrapped_name}\n   → {int(part.get('price', 0))} руб.\n"
                    else:
                        text += f"• {part}\n"
            else:
                text += f"{final_order[:500]}"
        except:
            text += f"{final_order[:500]}"
    elif order.get('needed_parts'):
        text += f"\n\n📝 ИЗНАЧАЛЬНЫЙ ЗАПРОС:\n{order.get('needed_parts', 'не указан')[:500]}"
    
    status = order.get('status', '')
    kb = [[InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders_list")]]
    
    if status == 'waiting_payment':
        bonus_data = get_bonus(order['user_id'])
        if bonus_data['balance'] > 0 and not has_restricted_brands(order):
            kb.insert(0, [InlineKeyboardButton("🎁 Списать бонусы", callback_data=f"apply_bonus_{order_num}")])
    
    await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

# ========== ГАРАЖ ==========

async def garage_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Меню гаража"""
    cars = get_cars(upd.effective_user.id)
    
    if not cars:
        await upd.message.reply_text(
            "🚗 МОЙ ГАРАЖ\n\nУ вас пока нет добавленных автомобилей.\n\n➕ Добавить автомобиль:\n"
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
    await query.edit_message_text("🚗 ДОБАВЛЕНИЕ АВТОМОБИЛЯ\n\nШаг 1/2: Отправьте VIN номер автомобиля (17 символов):")
    return GarageStates.VIN

async def garage_get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Получение VIN для гаража"""
    vin = upd.message.text.upper().strip()
    
    if not validate_vin(vin):
        await upd.message.reply_text("❌ Неверный VIN. Он должен содержать 17 символов (только буквы и цифры, без I, O, Q). Попробуйте ещё раз:")
        return GarageStates.VIN
    
    ctx.user_data['new_car_vin'] = vin
    await upd.message.reply_text(f"🚗 VIN: {vin}\n\nШаг 2/2: Введите описание автомобиля\n\nПример: BMW X5 3.0d, 2018, чёрный\n\nИли отправьте '-' чтобы пропустить:")
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
        await upd.message.reply_text(f"✅ Автомобиль {vin} успешно добавлен в ваш гараж!")
    else:
        await upd.message.reply_text(f"❌ Автомобиль {vin} уже есть в вашем гараже!")
    
    await garage_menu(upd, ctx)
    return ConversationHandler.END

# ========== АДМИН ПАНЕЛЬ ==========

@require_manager
async def admin_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, message=None):
    """Панель управления менеджера"""
    orders = get_all_orders()
    keyboard = [
        [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
        [InlineKeyboardButton("➕ Тестовый заказ", callback_data="admin_fix")],
        [InlineKeyboardButton("📊 Экспорт отчёта", callback_data="admin_export")],
        [InlineKeyboardButton("🔄 Обновить", callback_data="admin_refresh")]
    ]
    
    for o in orders[:10]:
        order_num = clean_order_number(o[0])
        user_name = o[1][:15] if len(o[1]) > 15 else o[1]
        status_text = o[2] if len(o) > 2 else ''
        
        icon_map = {
            'Ожидает подбора': '🆕', 'Ожидает выбора': '🟡', 'Ожидает оплаты': '💰',
            'Оплачен': '✅', 'Заказан': '📦', 'Товар поступил': '📦✅',
            'Готов к выдаче': '✅', 'Отправлен': '🚚', 'Доставлен': '🏠',
            'Выдан': '📋', 'Отменён': '❌'
        }
        
        icon = '📦'
        for key, ic in icon_map.items():
            if key in status_text:
                icon = ic
                break
        
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

# ========== МЕНЕДЖЕР (АДМИН) ОБРАБОТЧИКИ ==========

async def manager_reply(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Ответ менеджера на заказ"""
    if upd.effective_user.id != MANAGER_ID:
        return
    
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
        await upd.message.reply_text(f"❌ Заказ {order_num} не найден в базе данных.")
        return
    
    if not upd.message.text:
        await upd.message.reply_text("❌ Вы не ввели подбор запчастей.")
        return
    
    products = parse_products(upd.message.text)
    if not products:
        await upd.message.reply_text("❌ Не распознано. Формат:\nНазвание запчасти = 1000 руб\n\nПример:\nМасло моторное Ravenol 5W-40 = 3500 руб")
        return
    
    update_order(order_num, selected_products=upd.message.text, status='waiting_selection')
    
    kb = []
    for i, p in enumerate(products):
        display_name = p['name'][:25] + ".." if len(p['name']) > 25 else p['name']
        kb.append([InlineKeyboardButton(f"⬜ {display_name} — {p['price']} руб.", callback_data=f"sel_{order_num}_{i}")])
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

@require_manager
async def fix_orders(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Создание тестового заказа"""
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
        await upd.message.reply_text(f"✅ Тестовый заказ {num} создан!\n\n/menu")
    finally:
        conn.close()

# ========== ЗАПУСК ==========

def main():
    """Запуск бота"""
    init_db()
    
    # Запуск планировщика бэкапов
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
        ("fix", "Тестовый заказ"),
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
            OrderStates.CONFIRM: [MessageHandler(filters.Regex("^(✅ Готово|✏️ Редактировать)$"), confirm_order)],
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
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(order_conv)
    app.add_handler(garage_conv)
    app.add_handler(CommandHandler("my_orders", my_orders))
    app.add_handler(CommandHandler("bonus", bonus_cmd))
    app.add_handler(CommandHandler("menu", admin_menu))
    app.add_handler(CommandHandler("fix", fix_orders))
    
    # Кнопочные обработчики
    app.add_handler(MessageHandler(filters.Regex("^(🚗 Мой гараж)$"), garage_menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    
    # Callback обработчики
    app.add_handler(CallbackQueryHandler(view_order, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(apply_bonus_callback, pattern="^apply_bonus_"))
    app.add_handler(CallbackQueryHandler(spend_max_percent_callback, pattern="^spend_max_percent_"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^back_orders_list$"))
    app.add_handler(CallbackQueryHandler(start, pattern="^main_menu_back$"))
    app.add_handler(CallbackQueryHandler(garage_menu, pattern="^garage_back_to_menu$"))
    app.add_handler(CallbackQueryHandler(admin_menu, pattern="^admin_back$"))
    
    # Ответы менеджера
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), manager_reply))
    
    logger.info(f"🤖 Бот запущен! Админ ID: {MANAGER_ID}")
    app.run_polling()

if __name__ == "__main__":
    main()
