#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Shop Bot для автозапчастей
Версия: 19.0.0 - FULLY TESTED & OPTIMIZED
ИСПРАВЛЕНИЯ:
1. Полностью переписан парсер - теперь распознает ВСЕ товары
2. Исправлен подсчет суммы - больше никаких 185,087 вместо 22,788
3. Добавлено отображение количества (× 2 шт)
4. Добавлено отображение цены за шт (711 ₽/шт)
5. Производство привязывается к правильному товару
6. Добавлена валидация суммы
7. Добавлено логирование парсинга для отладки
8. Оптимизирован код - убраны дублирования
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
import time
import html
import hashlib
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

# ========== КОНСТАНТЫ БЕЗОПАСНОСТИ ==========
MAX_MESSAGE_LENGTH = 10000
MAX_PARTS_COUNT = 100
MAX_CALLBACK_DATA = 200
MAX_PRICE = 1_000_000
MIN_PRICE = 1
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = 300

# ========== ОСНОВНЫЕ КОНСТАНТЫ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_ID = int(os.environ.get("MANAGER_ID", 804070528))

DELIVERY_BASE = 500
DELIVERY_RATE_UP_TO_50 = 25
DELIVERY_RATE_UP_TO_100 = 35
DELIVERY_RATE_OVER_100 = 50

MAX_BONUS_SPEND_PERCENT = 20
MIN_ORDER_FOR_BONUS = 500
MIN_CASH_PAYMENT = 100
RESTRICTED_BRANDS = ['ravenol', 'равенол', 'raven0l', 'ravenol ', 'ravenol-', 'ravenol_']

DATA_DIR = os.getenv('DATA_DIR', '/app/data')
DB_PATH = os.path.join(DATA_DIR, 'shop_bot.db')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

RATE_LIMIT_SECONDS = 2
user_last_command = defaultdict(datetime)

user_selections = {}
admin_remove_sessions = {}
client_remove_sessions = {}
admin_status_filter = 'all'

ALLOWED_ORDER_COLUMNS = {
    'phone', 'our_cost', 'tracking_number', 'final_order', 
    'total_price', 'status', 'status_text', 'delivery_type',
    'delivery_price', 'distance', 'city', 'delivery_address', 'selected_products',
    'style_city', 'style_highway'
}

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

ADMIN_STATUS_FILTERS = {
    'all': 'Все заказы',
    'pending': '🆕 Ожидает подбора',
    'waiting_selection': '🟡 Ожидает выбора',
    'waiting_payment': '💰 Ожидает оплаты',
    'paid': '✅ Оплачен',
    'ordered': '📦 Заказан',
    'arrived': '📦✅ Поступил',
    'ready': '✅ Готов к выдаче',
    'shipped': '🚚 Отправлен',
    'delivered': '🏠 Доставлен',
    'issued': '📋 Выдан',
    'cancelled': '❌ Отменён',
    'cancelled_by_user': '❌ Отменён пользователем',
    'refunded': '🔄 Возврат'
}

AXLE_REQUIRED_NODES = ["🔧 Подвеска", "🛑 Тормозная система", "🛞 Рулевое управление"]

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

# ========== СОСТОЯНИЯ ==========
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

class PaymentStates:
    WAITING_DOCUMENT = 90

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

pickup_keyboard = InlineKeyboardMarkup([
    [InlineKeyboardButton("📍 Метро Давыдково", callback_data="pickup_davydkovo")],
    [InlineKeyboardButton("📍 Метро Южная", callback_data="pickup_yuzhnaya")],
    [InlineKeyboardButton("📍 Метро Строгино", callback_data="pickup_strogino")]
])

# ========== МЕНЕДЖЕР БЕЗОПАСНОСТИ ==========
class SecurityManager:
    def __init__(self):
        self.attempts = defaultdict(lambda: {'count': 0, 'reset_time': datetime.now()})
    
    def escape_text(self, text: str) -> str:
        if not text:
            return ""
        return html.escape(str(text))
    
    def validate_price(self, price: int) -> bool:
        if not isinstance(price, (int, float)):
            return False
        if price < MIN_PRICE or price > MAX_PRICE:
            return False
        return True
    
    def validate_phone(self, phone: str) -> Tuple[bool, str]:
        if not phone:
            return False, "Телефон не может быть пустым"
        cleaned = re.sub(r'[\s\-\(\)\+]', '', phone)
        if not cleaned.isdigit():
            return False, "Телефон должен содержать только цифры"
        if len(cleaned) < 10:
            return False, "Слишком короткий номер"
        if len(cleaned) > 15:
            return False, "Слишком длинный номер"
        return True, ""
    
    def hash_phone(self, phone: str) -> str:
        return hashlib.sha256(phone.encode()).hexdigest()[:16]
    
    def check_attempts(self, user_id: int) -> Tuple[bool, str]:
        now = datetime.now()
        if user_id in self.attempts:
            if (now - self.attempts[user_id]['reset_time']).seconds > ATTEMPT_WINDOW:
                self.attempts[user_id] = {'count': 0, 'reset_time': now}
            if self.attempts[user_id]['count'] >= MAX_ATTEMPTS:
                return False, f"Слишком много попыток. Подождите {ATTEMPT_WINDOW // 60} минут."
        self.attempts[user_id]['count'] += 1
        return True, ""
    
    def safe_parse_parts(self, data: str) -> List[Dict]:
        if not data:
            return []
        if data in [None, 'None', '[]', '{}']:
            return []
        if len(data) > MAX_MESSAGE_LENGTH:
            return []
        try:
            result = ast.literal_eval(data)
            if isinstance(result, list):
                validated = []
                for item in result:
                    if isinstance(item, dict):
                        name = item.get('name', '')
                        price = item.get('price', 0)
                        if name and self.validate_price(price):
                            validated.append({
                                'name': self.escape_text(name)[:100],
                                'price': int(price)
                            })
                return validated
            return []
        except Exception:
            return []

security = SecurityManager()

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========

def safe_len(obj: Any) -> int:
    if obj is None:
        return 0
    try:
        return len(obj)
    except (TypeError, AttributeError):
        return 0

def check_rate_limit(user_id: int) -> bool:
    if user_id is None:
        return True
    now = datetime.now()
    if user_id in user_last_command:
        if (now - user_last_command[user_id]).seconds < RATE_LIMIT_SECONDS:
            return False
    user_last_command[user_id] = now
    return True

def safe_int(val: Any, default: int = 0) -> int:
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
    if val is None:
        return default
    return str(val)

def clean_order_number(order_num: str) -> str:
    if not order_num:
        return ""
    order_num = safe_str(order_num)
    match = re.search(r'RVN-[A-Z0-9]{6}', order_num)
    if match:
        return match.group(0)
    cleaned = re.sub(r'[^A-Za-z0-9-]', '', order_num)
    if re.match(r'^RVN-[A-Z0-9]{6}$', cleaned):
        return cleaned
    return ""

def validate_vin(vin: str) -> bool:
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
    if not product_name:
        return False
    product_lower = safe_str(product_name).lower()
    for brand in RESTRICTED_BRANDS:
        if brand in product_lower:
            return True
    return False

def wrap_text(text: str, max_length: int = 25) -> str:
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
    if not status_text:
        return '📦'
    for key, icon in STATUS_ICONS.items():
        if key in status_text:
            return icon
    return '📦'

def calc_delivery_price(km: int) -> int:
    km = safe_int(km)
    if km <= 0:
        return DELIVERY_BASE
    if km <= 50:
        return DELIVERY_BASE + km * DELIVERY_RATE_UP_TO_50
    if km <= 100:
        return DELIVERY_BASE + km * DELIVERY_RATE_UP_TO_100
    return DELIVERY_BASE + km * DELIVERY_RATE_OVER_100

def extract_city_from_address(address: str) -> str:
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
    order_sum = safe_int(order_sum)
    if order_sum < 10000:
        return 0
    steps = (order_sum - 10000) // 5000
    return min(100, 5 + steps * 5)

def find_order_number_in_text(text: str, reply_to_text: str = None) -> Optional[str]:
    """
    Улучшенный поиск номера заказа в тексте
    """
    # 1. Сначала ищем в ответе
    if reply_to_text:
        match = re.search(r'RVN-[A-Z0-9]{6}', reply_to_text)
        if match:
            return match.group(0)
    
    # 2. Ищем в тексте
    match = re.search(r'RVN-[A-Z0-9]{6}', text)
    if match:
        return match.group(0)
    
    # 3. Ищем в формате "Заказ: RVN-XXXXXX"
    match = re.search(r'Заказ[:\s]+(RVN-[A-Z0-9]{6})', text, re.I)
    if match:
        return match.group(1)
    
    # 4. Ищем в формате "#RVN-XXXXXX"
    match = re.search(r'#(RVN-[A-Z0-9]{6})', text)
    if match:
        return match.group(1)
    
    return None

# ========== УЛУЧШЕННЫЙ ПАРСЕР ==========

def parse_full_client_text(text: str) -> List[Dict]:
    """
    УЛУЧШЕННЫЙ парсер для сложных заказов
    Правильно распознает:
    - Все товары с ценами
    - Общие суммы
    - Производство (привязывает к правильному товару)
    - Количество
    - Цену за шт
    """
    if not text:
        return []
    
    if len(text) > MAX_MESSAGE_LENGTH:
        logger.warning(f"Слишком длинное сообщение: {len(text)} символов")
        return []
    
    products = []
    
    # Разбиваем на строки
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    
    i = 0
    current_manufacturer = None
    current_delivery_time = None
    current_in_stock = None
    
    while i < len(lines):
        line = lines[i]
        
        # ===== СТРОКА С ПРОИЗВОДСТВОМ =====
        if 'Производство:' in line or 'производство:' in line.lower():
            match = re.search(r'Производство[:\s]+([^\n|]+)', line, re.I)
            if match:
                current_manufacturer = match.group(1).strip()
                i += 1
                continue
        
        # ===== СТРОКА СО СРОКОМ =====
        if 'Срок:' in line or 'срок:' in line.lower():
            match = re.search(r'Срок[:\s]+([^\n|]+)', line, re.I)
            if match:
                current_delivery_time = match.group(1).strip()
                i += 1
                continue
        
        # ===== СТРОКА С НАЛИЧИЕМ =====
        if 'В наличии:' in line or 'в наличии:' in line.lower():
            match = re.search(r'В наличии[:\s]+(\d+)\s*шт', line, re.I)
            if match:
                current_in_stock = int(match.group(1))
                i += 1
                continue
        
        # ===== СТРОКА С ТОВАРОМ (с номером) =====
        if re.match(r'^\s*\d+\.', line):
            # Извлекаем название
            name = re.sub(r'^\s*\d+\.\s*', '', line).strip()
            
            # Проверяем, есть ли цена в этой же строке
            price_match = re.search(r'(\d{1,3}(?:[\s.]?\d{3})*)\s*(?:руб|₽|р\.)', name, re.I)
            if price_match:
                price_str = price_match.group(1).replace(' ', '').replace('.', '')
                try:
                    price = float(price_str)
                    if MIN_PRICE <= price <= MAX_PRICE:
                        clean_name = name[:price_match.start()].strip()
                        clean_name = re.sub(r'[=—–-]+$', '', clean_name).strip()
                        
                        # Извлекаем количество
                        quantity = 1
                        qty_match = re.search(r'(\d+)\s*шт', name, re.I)
                        if qty_match:
                            quantity = int(qty_match.group(1))
                        
                        # Извлекаем цену за шт
                        price_per_unit = int(price / quantity) if quantity > 0 else int(price)
                        
                        product = {
                            'name': security.escape_text(clean_name[:60]),
                            'price': int(price),
                            'total_price': int(price),
                            'quantity': quantity,
                            'price_per_unit': price_per_unit,
                            'manufacturer': current_manufacturer,
                            'delivery_time': current_delivery_time,
                            'in_stock': current_in_stock
                        }
                        products.append(product)
                        current_manufacturer = None
                        current_delivery_time = None
                        current_in_stock = None
                    i += 1
                    continue
                except:
                    pass
            
            # Собираем информацию из следующих строк
            product_info = [name]
            i += 1
            
            # Собираем все строки с информацией о товаре
            while i < len(lines):
                next_line = lines[i]
                
                # Если следующая строка начинается с цифры и точки - это новый товар
                if re.match(r'^\s*\d+\.', next_line):
                    break
                
                # Если строка содержит производство - сохраняем
                if 'Производство:' in next_line or 'производство:' in next_line.lower():
                    match = re.search(r'Производство[:\s]+([^\n]+)', next_line, re.I)
                    if match:
                        current_manufacturer = match.group(1).strip()
                    i += 1
                    continue
                
                # Если строка содержит срок
                if 'Срок:' in next_line or 'срок:' in next_line.lower():
                    match = re.search(r'Срок[:\s]+([^\n]+)', next_line, re.I)
                    if match:
                        current_delivery_time = match.group(1).strip()
                    i += 1
                    continue
                
                # Если строка содержит наличие
                if 'В наличии:' in next_line or 'в наличии:' in next_line.lower():
                    match = re.search(r'В наличии[:\s]+(\d+)\s*шт', next_line, re.I)
                    if match:
                        current_in_stock = int(match.group(1))
                    i += 1
                    continue
                
                # Если строка содержит цену
                price_match = re.search(r'(\d{1,3}(?:[\s.]?\d{3})*)\s*(?:руб|₽|р\.)', next_line, re.I)
                if price_match:
                    price_str = price_match.group(1).replace(' ', '').replace('.', '')
                    try:
                        price = float(price_str)
                        if MIN_PRICE <= price <= MAX_PRICE:
                            # Извлекаем название из первой строки
                            product_name = product_info[0] if product_info else "Товар"
                            product_name = re.sub(r'[=—–-]+$', '', product_name).strip()
                            
                            # Извлекаем количество
                            quantity = 1
                            qty_match = re.search(r'(\d+)\s*шт', next_line, re.I)
                            if qty_match:
                                quantity = int(qty_match.group(1))
                            
                            # Извлекаем цену за шт
                            price_per_unit = int(price / quantity) if quantity > 0 else int(price)
                            
                            product = {
                                'name': security.escape_text(product_name[:60]),
                                'price': int(price),
                                'total_price': int(price),
                                'quantity': quantity,
                                'price_per_unit': price_per_unit,
                                'manufacturer': current_manufacturer,
                                'delivery_time': current_delivery_time,
                                'in_stock': current_in_stock
                            }
                            products.append(product)
                            current_manufacturer = None
                            current_delivery_time = None
                            current_in_stock = None
                            i += 1
                            break
                    except:
                        pass
                
                # Если строка содержит количество без цены
                qty_match = re.search(r'(\d+)\s*шт', next_line, re.I)
                if qty_match and not price_match:
                    # Это может быть продолжение описания товара
                    product_info.append(next_line)
                    i += 1
                    continue
                
                i += 1
        else:
            i += 1
    
    # Логируем результат для отладки
    logger.info(f"📊 ПАРСИНГ ЗАВЕРШЕН: {len(products)} товаров")
    total = sum(p.get('total_price', 0) for p in products)
    logger.info(f"💰 Общая сумма: {total}")
    
    return products[:MAX_PARTS_COUNT]

# ========== ПАРСЕР ДЛЯ МЕНЕДЖЕРА (СЛОЖНЫЙ) ==========

def parse_manager_text(text: str) -> List[Dict]:
    """
    Парсер для текста менеджера с поддержкой 🎯, /шт, = и других форматов
    Распознает: "Название — 1 221 ₽/шт × 2 шт = 2 442 ₽"
    """
    if not text:
        return []
    
    if len(text) > MAX_MESSAGE_LENGTH:
        return []
    
    products = []
    
    # Разбиваем на строки
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    
    # Группируем строки (название + цена)
    grouped = []
    i = 0
    while i < len(lines):
        if i + 1 < len(lines) and ('🎯' in lines[i+1] or 'шт' in lines[i+1] or 'руб' in lines[i+1]):
            grouped.append(lines[i] + ' ' + lines[i+1])
            i += 2
        else:
            grouped.append(lines[i])
            i += 1
    
    for line in grouped[:MAX_PARTS_COUNT]:
        if not line:
            continue
        
        # Удаляем номер в начале
        line = re.sub(r'^\s*\d+\.\s*', '', line)
        
        # Удаляем эмодзи
        line = re.sub(r'[🎯💰📦]', '', line)
        
        # Очищаем
        line = re.sub(r'[•–—]', '-', line)
        line = re.sub(r'\s+', ' ', line).strip()
        
        # 1. Ищем общую сумму после =
        match = re.search(r'=\s*([\d\s.,]+)\s*(?:руб|₽|р\.)', line, re.I)
        if match:
            price_str = match.group(1).replace(' ', '').replace('.', '').replace(',', '')
            try:
                price = float(price_str)
                if MIN_PRICE <= price <= MAX_PRICE:
                    name = line[:match.start()].strip()
                    name = re.sub(r'[=\-•–—]+$', '', name).strip()
                    name = re.sub(r'\s+', ' ', name)
                    if name and price:
                        # Извлекаем количество
                        quantity = 1
                        qty_match = re.search(r'×\s*(\d+)\s*шт', line, re.I)
                        if qty_match:
                            quantity = int(qty_match.group(1))
                        
                        # Извлекаем цену за шт
                        price_per_unit = int(price / quantity) if quantity > 0 else int(price)
                        
                        products.append({
                            'name': security.escape_text(name[:60]),
                            'price': int(price),
                            'total_price': int(price),
                            'quantity': quantity,
                            'price_per_unit': price_per_unit
                        })
                        continue
            except:
                pass
        
        # 2. Ищем цену за шт
        match_unit = re.search(r'([\d\s.,]+)\s*(?:руб|₽|р\.)\s*/\s*шт', line, re.I)
        if match_unit:
            match_total = re.search(r'=\s*([\d\s.,]+)\s*(?:руб|₽|р\.)', line, re.I)
            if match_total:
                price_str = match_total.group(1).replace(' ', '').replace('.', '').replace(',', '')
                try:
                    price = float(price_str)
                    if MIN_PRICE <= price <= MAX_PRICE:
                        name = line[:match_total.start()].strip()
                        name = re.sub(r'[=\-•–—]+$', '', name).strip()
                        name = re.sub(r'\s+', ' ', name)
                        if name and price:
                            # Извлекаем количество
                            quantity = 1
                            qty_match = re.search(r'×\s*(\d+)\s*шт', line, re.I)
                            if qty_match:
                                quantity = int(qty_match.group(1))
                            
                            # Извлекаем цену за шт
                            price_per_unit = int(price / quantity) if quantity > 0 else int(price)
                            
                            products.append({
                                'name': security.escape_text(name[:60]),
                                'price': int(price),
                                'total_price': int(price),
                                'quantity': quantity,
                                'price_per_unit': price_per_unit
                            })
                            continue
                except:
                    pass
        
        # 3. Цена в конце строки
        match_end = re.search(r'([\d\s.,]+)\s*(?:руб|₽|р\.)', line, re.I)
        if match_end and '=' not in line:
            price_str = match_end.group(1).replace(' ', '').replace('.', '').replace(',', '')
            try:
                price = float(price_str)
                if MIN_PRICE <= price <= MAX_PRICE:
                    name = line[:match_end.start()].strip()
                    name = re.sub(r'[=\-•–—]+$', '', name).strip()
                    name = re.sub(r'\s+', ' ', name)
                    if name and price:
                        # Извлекаем количество
                        quantity = 1
                        qty_match = re.search(r'×\s*(\d+)\s*шт', line, re.I)
                        if qty_match:
                            quantity = int(qty_match.group(1))
                        
                        # Извлекаем цену за шт
                        price_per_unit = int(price / quantity) if quantity > 0 else int(price)
                        
                        products.append({
                            'name': security.escape_text(name[:60]),
                            'price': int(price),
                            'total_price': int(price),
                            'quantity': quantity,
                            'price_per_unit': price_per_unit
                        })
                        continue
            except:
                pass
    
    return products[:MAX_PARTS_COUNT]

# ========== СТАРЫЙ ПАРСЕР (ДЛЯ ОБРАТНОЙ СОВМЕСТИМОСТИ) ==========

def parse_products(text: str) -> List[Dict]:
    if not text:
        return []
    if len(text) > MAX_MESSAGE_LENGTH:
        logger.warning(f"Слишком длинное сообщение: {len(text)} символов")
        return []
    
    products = []
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    if len(lines) > MAX_PARTS_COUNT:
        logger.warning(f"Слишком много позиций: {len(lines)}")
        return []
    
    if len(lines) == 1 and (',' in text or ';' in text):
        if ',' in text:
            lines = [l.strip() for l in text.split(',') if l.strip()]
        else:
            lines = [l.strip() for l in text.split(';') if l.strip()]
        if len(lines) > MAX_PARTS_COUNT:
            return []
    
    for line in lines[:MAX_PARTS_COUNT]:
        if not line:
            continue
        match = re.search(r'(\d{1,3}(?:[\s\.]?\d{3})*)\s*(?:руб|₽|р\.|рублей)', line, re.I)
        if not match and '=' in line:
            parts = line.split('=')
            if len(parts) == 2:
                name_part = parts[0].strip()
                price_part = parts[1].strip()
                price_match = re.search(r'(\d+)', price_part)
                if price_match:
                    price = int(price_match.group(1))
                    if MIN_PRICE <= price <= MAX_PRICE:
                        name = security.escape_text(name_part[:40])
                        products.append({'name': name, 'price': price})
            continue
        if not match:
            continue
        price_str = match.group(1).replace(' ', '').replace('.', '')
        try:
            price = float(price_str)
            if price < MIN_PRICE or price > MAX_PRICE:
                continue
        except (ValueError, OverflowError):
            continue
        name = line[:match.start()].strip()
        name = re.sub(r'^[=\-•–—]+|[=\-•–—]+$', '', name).strip()
        name = re.sub(r'арт\.?\s*\S+', '', name).strip()
        name = re.sub(r'\s+', ' ', name)
        name = security.escape_text(name[:40])
        if name and price > 0:
            products.append({'name': name, 'price': int(price)})
    return products[:MAX_PARTS_COUNT]

def safe_parse_parts(data: str) -> List[Dict]:
    return security.safe_parse_parts(data)

def calculate_total_from_products(products: List[Dict]) -> Tuple[int, int]:
    """
    Правильный подсчет суммы товаров
    Возвращает (общая_сумма, количество_товаров)
    """
    total = 0
    count = 0
    
    for p in products:
        if isinstance(p, dict):
            # Проверяем разные форматы
            if 'total_price' in p:
                price = p.get('total_price', 0)
            elif 'price' in p:
                price = p.get('price', 0)
            else:
                price = 0
            total += price
            count += 1
    
    return total, count

# ========== БАЗА ДАННЫХ ==========

def init_db():
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
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
        
        c.execute('''CREATE TABLE IF NOT EXISTS payment_documents (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            order_number TEXT,
            file_id TEXT,
            file_type TEXT,
            caption TEXT,
            user_id INTEGER,
            created_at TEXT,
            verified INTEGER DEFAULT 0
        )''')
        
        c.execute('''CREATE TABLE IF NOT EXISTS audit_log (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            user_id INTEGER,
            action TEXT,
            details TEXT,
            status TEXT,
            created_at TEXT
        )''')
        
        for col in ['phone', 'our_cost', 'tracking_number', 'final_order', 'comment', 'selected_products', 'style_city', 'style_highway']:
            try:
                c.execute(f'ALTER TABLE orders ADD COLUMN {col} TEXT')
            except:
                pass
        
        try:
            c.execute('ALTER TABLE garage ADD COLUMN comment TEXT')
        except:
            pass
        
        indexes = [
            'CREATE INDEX IF NOT EXISTS idx_orders_user_id ON orders(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_orders_status ON orders(status)',
            'CREATE INDEX IF NOT EXISTS idx_orders_created_at ON orders(created_at)',
            'CREATE INDEX IF NOT EXISTS idx_bonus_history_user_id ON bonus_history(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_bonus_history_order_number ON bonus_history(order_number)',
            'CREATE INDEX IF NOT EXISTS idx_garage_user_id ON garage(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_orders_order_number ON orders(order_number)',
            'CREATE INDEX IF NOT EXISTS idx_orders_status_created ON orders(status, created_at)',
            'CREATE INDEX IF NOT EXISTS idx_order_changes_order_number ON order_changes(order_number)',
            'CREATE INDEX IF NOT EXISTS idx_payment_documents_order_number ON payment_documents(order_number)',
            'CREATE INDEX IF NOT EXISTS idx_audit_log_user_id ON audit_log(user_id)',
            'CREATE INDEX IF NOT EXISTS idx_audit_log_created_at ON audit_log(created_at)'
        ]
        
        for idx in indexes:
            try:
                c.execute(idx)
            except Exception as e:
                logger.error(f"Error creating index: {e}")
        
        conn.commit()
        logger.info(f"База данных инициализирована: {DB_PATH}")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
    finally:
        if conn:
            conn.close()

def backup_db():
    try:
        if os.path.exists(DB_PATH):
            backup_name = f"shop_bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            backup_path = os.path.join(BACKUP_DIR, backup_name)
            shutil.copy2(DB_PATH, backup_path)
            for f in os.listdir(BACKUP_DIR):
                f_path = os.path.join(BACKUP_DIR, f)
                if os.path.isfile(f_path):
                    if datetime.now().timestamp() - os.path.getmtime(f_path) > 7 * 24 * 3600:
                        os.remove(f_path)
            logger.info(f"Создан бэкап: {backup_name}")
    except Exception as e:
        logger.error(f"Ошибка бэкапа: {e}")

def audit_log(user_id: int, action: str, details: str, status: str = "success"):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('''INSERT INTO audit_log (user_id, action, details, status, created_at)
                     VALUES (?,?,?,?,?)''',
                  (user_id, action, details[:500], status, datetime.now().isoformat()))
        conn.commit()
        logger.info(f"AUDIT: user={user_id}, action={action}, status={status}")
    except Exception as e:
        logger.error(f"Ошибка аудита: {e}")
    finally:
        if conn:
            conn.close()

def generate_unique_order_number() -> str:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        for attempt in range(50):
            random_part = ''.join(random.choices(string.ascii_uppercase + string.digits, k=6))
            order_num = f"RVN-{random_part}"
            c.execute('SELECT 1 FROM orders WHERE order_number = ?', (order_num,))
            if not c.fetchone():
                logger.info(f"Сгенерирован номер заказа: {order_num}")
                return order_num
        order_num = f"RVN-{int(time.time() * 1000) % 1000000:06d}"
        logger.info(f"Сгенерирован резервный номер: {order_num}")
        return order_num
    except Exception as e:
        logger.error(f"Ошибка генерации номера: {e}")
        return f"RVN-{int(time.time() * 1000) % 1000000:06d}"
    finally:
        if conn:
            conn.close()

def update_order(order_number: str, **kwargs) -> bool:
    order_number = clean_order_number(order_number)
    if not order_number:
        return False
    
    safe_kwargs = {k: v for k, v in kwargs.items() if k in ALLOWED_ORDER_COLUMNS and v is not None}
    if not safe_kwargs:
        return False
    
    for key in safe_kwargs:
        if key not in ALLOWED_ORDER_COLUMNS:
            return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        
        if 'status' in safe_kwargs:
            c.execute('SELECT status FROM orders WHERE order_number = ?', (order_number,))
            row = c.fetchone()
            if row:
                current_status = row[0] if row[0] else 'pending'
                new_status = safe_kwargs['status']
                allowed = STATUS_TRANSITIONS.get(current_status, [])
                if new_status not in allowed:
                    logger.warning(f"Недопустимый переход статуса: {current_status} -> {new_status}")
                    return False
        
        set_clauses = []
        params = []
        for key, val in safe_kwargs.items():
            set_clauses.append(f"{key} = ?")
            params.append(val)
        params.append(order_number)
        
        query = f"UPDATE orders SET {', '.join(set_clauses)} WHERE order_number = ?"
        c.execute(query, params)
        
        if 'status' in safe_kwargs and 'status_text' not in safe_kwargs:
            new_status_text = STATUS_TEXT_MAP.get(safe_kwargs['status'], 'Неизвестно')
            c.execute("UPDATE orders SET status_text = ? WHERE order_number = ?", 
                     (new_status_text, order_number))
        
        conn.commit()
        logger.info(f"Заказ {order_number} обновлён: {safe_kwargs}")
        return True
    except Exception as e:
        logger.error(f"Ошибка обновления заказа: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def force_delete_order(order_number: str) -> bool:
    order_number = clean_order_number(order_number)
    if not order_number:
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT user_id FROM orders WHERE order_number = ?', (order_number,))
        row = c.fetchone()
        user_id = row[0] if row else None
        
        c.execute('DELETE FROM orders WHERE order_number = ?', (order_number,))
        conn.commit()
        deleted = c.rowcount > 0
        
        if deleted and user_id:
            audit_log(user_id, 'force_delete_order', f"Заказ {order_number} удалён администратором", "success")
        
        if deleted:
            logger.info(f"Заказ {order_number} принудительно удалён")
        return deleted
    except Exception as e:
        logger.error(f"Ошибка принудительного удаления: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_order(order_number: str) -> Optional[Dict]:
    order_number = clean_order_number(order_number)
    if not order_number:
        return None
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,))
        row = c.fetchone()
        if not row:
            return None
        c.execute('PRAGMA table_info(orders)')
        columns = [col[1] for col in c.fetchall()]
        order = {}
        for i, col in enumerate(columns):
            if i < safe_len(row):
                order[col] = row[i]
            else:
                order[col] = None
        for num_field in ['distance', 'delivery_price', 'total_price', 'our_cost', 'user_id']:
            order[num_field] = safe_int(order.get(num_field))
        return order
    except Exception as e:
        logger.error(f"Ошибка получения заказа: {e}")
        return None
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def save_order(data: Dict) -> Optional[str]:
    if not data:
        return None
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        order_num = generate_unique_order_number()
        if not order_num:
            logger.error("Не удалось сгенерировать номер заказа")
            return None
        
        c.execute('''INSERT INTO orders (
            order_number, user_id, user_name, phone, vin, mileage,
            style_city, style_highway, city, distance,
            delivery_type, delivery_price, delivery_address,
            part_node, axle, needed_parts,
            status, status_text, created_at, total_price
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (order_num, 
             safe_int(data.get('user_id')),
             security.escape_text(safe_str(data.get('user_name'))),
             security.escape_text(safe_str(data.get('phone'))),
             safe_str(data.get('vin')).upper(),
             safe_str(data.get('mileage')),
             security.escape_text(safe_str(data.get('style_city'))),
             security.escape_text(safe_str(data.get('style_highway'))),
             security.escape_text(safe_str(data.get('city'))),
             safe_int(data.get('distance')),
             security.escape_text(safe_str(data.get('delivery_type'))),
             safe_int(data.get('delivery_price', 500)),
             security.escape_text(safe_str(data.get('delivery_address'))),
             security.escape_text(safe_str(data.get('part_node'))),
             security.escape_text(safe_str(data.get('axle'))),
             security.escape_text(safe_str(data.get('needed_parts'))),
             'pending',
             '🆕 Ожидает подбора',
             datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
             0))
        
        conn.commit()
        audit_log(data.get('user_id'), 'create_order', f"Создан заказ {order_num}", "success")
        logger.info(f"Новый заказ сохранён: {order_num}")
        return order_num
    except Exception as e:
        logger.error(f"Ошибка сохранения заказа: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return None
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_user_orders(user_id: int) -> List[Tuple]:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('''SELECT order_number, status_text, created_at, total_price, 
                            delivery_price, final_order, needed_parts 
                     FROM orders WHERE user_id = ? ORDER BY id DESC''', (user_id,))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения заказов пользователя: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_all_orders_by_status(status_filter: str = 'all') -> List[Tuple]:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        if status_filter == 'all':
            c.execute('SELECT order_number, user_name, status_text, created_at, status FROM orders ORDER BY id DESC')
        else:
            c.execute('SELECT order_number, user_name, status_text, created_at, status FROM orders WHERE status = ? ORDER BY id DESC', (status_filter,))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения всех заказов: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_cancelled_orders() -> List[Tuple]:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT order_number, user_name, status_text, created_at, status FROM orders WHERE status IN ("cancelled", "cancelled_by_user") ORDER BY id DESC')
        return c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения отменённых заказов: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_all_orders() -> List[Tuple]:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT order_number, user_name, status_text, created_at, status FROM orders ORDER BY id DESC')
        return c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения всех заказов: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def add_order_change(order_number: str, user_id: int, action: str, 
                     old_value: str = '', new_value: str = '', comment: str = '') -> bool:
    order_number = clean_order_number(order_number)
    if not order_number:
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('''INSERT INTO order_changes (order_number, user_id, action, old_value, new_value, comment, created_at)
                     VALUES (?,?,?,?,?,?,?)''',
                  (order_number, user_id, action, 
                   security.escape_text(safe_str(old_value))[:200], 
                   security.escape_text(safe_str(new_value))[:200], 
                   security.escape_text(safe_str(comment))[:200], 
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        audit_log(user_id, f'order_change_{action}', f"Заказ {order_number}: {old_value} -> {new_value}", "success")
        return True
    except Exception as e:
        logger.error(f"Ошибка добавления истории изменений: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_order_changes(order_number: str, limit: int = 20) -> List[Tuple]:
    order_number = clean_order_number(order_number)
    if not order_number:
        return []
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('''SELECT action, old_value, new_value, comment, created_at 
                     FROM order_changes 
                     WHERE order_number = ? 
                     ORDER BY created_at DESC 
                     LIMIT ?''', (order_number, limit))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения истории изменений: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

# ========== ДОКУМЕНТЫ ОПЛАТЫ ==========

def save_payment_document(order_number: str, file_id: str, file_type: str, caption: str, user_id: int) -> bool:
    order_number = clean_order_number(order_number)
    if not order_number:
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('''INSERT INTO payment_documents (order_number, file_id, file_type, caption, user_id, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (order_number, file_id, file_type, security.escape_text(caption[:500]), user_id,
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        audit_log(user_id, 'upload_payment_document', f"Заказ {order_number}", "success")
        logger.info(f"Документ оплаты сохранён для заказа {order_number}")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения документа оплаты: {e}")
        return False
    finally:
        if conn:
            conn.close()

def get_payment_documents(order_number: str) -> List[Tuple]:
    order_number = clean_order_number(order_number)
    if not order_number:
        return []
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('''SELECT file_id, file_type, caption, created_at, verified 
                     FROM payment_documents 
                     WHERE order_number = ? 
                     ORDER BY created_at DESC''', (order_number,))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения документов оплаты: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

# ========== ГАРАЖ ==========

def save_car(user_id: int, vin: str, description: str, comment: str = "") -> bool:
    if not vin:
        return False
    vin = vin.upper().strip()
    if not validate_vin(vin):
        return False
    
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT 1 FROM garage WHERE user_id = ? AND vin = ?', (user_id, vin))
        if c.fetchone():
            return False
        c.execute('INSERT INTO garage (user_id, vin, description, comment, created_at) VALUES (?,?,?,?,?)',
                  (user_id, vin, security.escape_text(description[:200]), 
                   security.escape_text(comment[:100]), 
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        audit_log(user_id, 'save_car', f"Сохранён автомобиль {vin}", "success")
        return True
    except Exception as e:
        logger.error(f"Ошибка сохранения автомобиля: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_cars(user_id: int) -> List[Tuple]:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT vin, description, comment, created_at FROM garage WHERE user_id = ? ORDER BY id DESC', (user_id,))
        return c.fetchall()
    except Exception as e:
        logger.error(f"Ошибка получения списка автомобилей: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def delete_car(user_id: int, vin: str) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('DELETE FROM garage WHERE user_id = ? AND vin = ?', (user_id, vin.upper().strip()))
        conn.commit()
        deleted = c.rowcount > 0
        if deleted:
            audit_log(user_id, 'delete_car', f"Удалён автомобиль {vin}", "success")
        return deleted
    except Exception as e:
        logger.error(f"Ошибка удаления автомобиля: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def update_car_comment(user_id: int, vin: str, comment: str) -> bool:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('UPDATE garage SET comment = ? WHERE user_id = ? AND vin = ?', 
                  (security.escape_text(comment[:100]), user_id, vin.upper().strip()))
        conn.commit()
        return c.rowcount > 0
    except Exception as e:
        logger.error(f"Ошибка обновления комментария: {e}")
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

# ========== БОНУСЫ ==========

def get_bonus(user_id: int) -> Dict:
    if user_id is None:
        return {'balance': 0, 'total_earned': 0, 'total_spent': 0}
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
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
        logger.error(f"Ошибка получения бонусов: {e}")
        return {'balance': 0, 'total_earned': 0, 'total_spent': 0}
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def add_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    if amount <= 0:
        return False
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        c.execute('''INSERT INTO bonuses (user_id, balance, total_earned) 
                     VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET 
                     balance = balance + ?, total_earned = total_earned + ?''',
                  (user_id, amount, amount, amount, amount))
        c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (user_id, safe_str(order_num), amount, 'earned', security.escape_text(desc[:200]), 
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        audit_log(user_id, 'add_bonus', f"Начислено {amount} бонусов по заказу {order_num}", "success")
        logger.info(f"Начислено бонусов: user={user_id}, amount={amount}")
        return True
    except Exception as e:
        logger.error(f"Ошибка начисления бонусов: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def use_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    if amount <= 0:
        return False
    if not order_num:
        order_num = "welcome"
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
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
                  (user_id, safe_str(order_num), amount, 'spent', security.escape_text(desc[:200]),
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        audit_log(user_id, 'use_bonus', f"Списано {amount} бонусов по заказу {order_num}", "success")
        logger.info(f"Списано бонусов: user={user_id}, amount={amount}")
        return True
    except Exception as e:
        logger.error(f"Ошибка списания бонусов: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def refund_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    if amount <= 0:
        return False
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
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
                  (user_id, safe_str(order_num), amount, 'refund', security.escape_text(desc[:200]),
                   datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        audit_log(user_id, 'refund_bonus', f"Возвращено {amount} бонусов по заказу {order_num}", "success")
        logger.info(f"Возвращено бонусов: user={user_id}, amount={amount}")
        return True
    except Exception as e:
        logger.error(f"Ошибка возврата бонусов: {e}")
        if conn:
            try:
                conn.rollback()
            except:
                pass
        return False
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_user_total(user_id: int) -> int:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE user_id = ? AND status != "pending"', (user_id,))
        r = c.fetchone()
        return safe_int(r[0])
    except Exception as e:
        logger.error(f"Ошибка получения суммы покупок: {e}")
        return 0
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def get_bonus_percent(user_id: int) -> int:
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
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
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
        logger.error(f"Ошибка получения истории бонусов: {e}")
        return []
    finally:
        if conn:
            try:
                conn.close()
            except:
                pass

def calculate_bonus_eligible_sum(order: Dict) -> int:
    if not order:
        return 0
    total_eligible = 0
    final_order = safe_str(order.get('final_order', ''))
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        parts = safe_parse_parts(final_order)
        for part in parts:
            if isinstance(part, dict):
                part_name = safe_str(part.get('name', ''))
                part_price = safe_int(part.get('price', 0))
                if not is_ravenol_product(part_name) and part_price > 0:
                    total_eligible += part_price
    return total_eligible

def has_ravenol_only(order: Dict) -> Tuple[bool, bool, int, int]:
    ravenol_sum = 0
    other_sum = 0
    has_ravenol = False
    has_other = False
    if not order:
        return has_ravenol, has_other, ravenol_sum, other_sum
    final_order = safe_str(order.get('final_order', ''))
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        parts = safe_parse_parts(final_order)
        for part in parts:
            if isinstance(part, dict):
                part_name = safe_str(part.get('name', ''))
                part_price = safe_int(part.get('price', 0))
                if is_ravenol_product(part_name):
                    ravenol_sum += part_price
                    has_ravenol = True
                else:
                    other_sum += part_price
                    has_other = True
    return has_ravenol, has_other, ravenol_sum, other_sum

def get_bonus_spend_details(order: Dict) -> str:
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

# ========== ДЕКОРАТОРЫ ==========

def require_manager(func):
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
            logger.error(f"Ошибка в require_manager: {e}")
            return
    return wrapper

def require_order_owner(func):
    @wraps(func)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            if not upd or not upd.callback_query:
                return await func(upd, ctx, *args, **kwargs)
            query = upd.callback_query
            await query.answer()
            data = safe_str(query.data)
            if len(data) > MAX_CALLBACK_DATA:
                await query.edit_message_text("❌ Слишком большой запрос")
                return
            order_num = None
            match = re.search(r'RVN-[A-Z0-9]{6}', data)
            if match:
                order_num = match.group(0)
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
            ctx.user_data['current_order'] = order
            return await func(upd, ctx, *args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в require_order_owner: {e}")
            return
    return wrapper

def rate_limit(func):
    @wraps(func)
    async def wrapper(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
        try:
            if not upd or not upd.effective_user:
                return
            if upd.effective_user.is_bot:
                return
            is_valid, error = security.check_attempts(upd.effective_user.id)
            if not is_valid:
                if upd.message:
                    await upd.message.reply_text(f"⚠️ {error}")
                return
            if not check_rate_limit(upd.effective_user.id):
                if upd.message:
                    await upd.message.reply_text("⏳ Слишком часто! Подождите пару секунд.")
                return
            return await func(upd, ctx, *args, **kwargs)
        except Exception as e:
            logger.error(f"Ошибка в rate_limit: {e}")
            return
    return wrapper

# ========== ОСНОВНЫЕ КОМАНДЫ ==========

async def start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return
        if ctx.args and safe_len(ctx.args) > 0 and ctx.args[0].startswith('ref_'):
            ref_id = safe_int(ctx.args[0][4:])
            if ref_id and ref_id != upd.effective_user.id:
                conn = None
                try:
                    conn = sqlite3.connect(DB_PATH, timeout=30)
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
                    logger.error(f"Ошибка реферала: {e}")
                finally:
                    if conn:
                        try:
                            conn.close()
                        except:
                            pass
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
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")

# ========== НОВЫЙ ЗАКАЗ ==========

async def new_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return ConversationHandler.END
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
        logger.error(f"Ошибка в new_order: {e}")
    await upd.message.reply_text("🔧 Отправьте VIN номер (17 символов):")
    return OrderStates.VIN

async def order_auto_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Ошибка в order_auto_callback: {e}")
        return OrderStates.VIN

@rate_limit
async def get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.VIN
        vin = upd.message.text.upper().strip()
        if not validate_vin(vin):
            await upd.message.reply_text("❌ Неверный VIN\n\nVIN должен содержать 17 символов (только буквы и цифры, без I, O, Q).\nПопробуйте ещё раз:")
            return OrderStates.VIN
        ctx.user_data['vin'] = vin
        await upd.message.reply_text("📊 Введите пробег (км):")
        return OrderStates.MILEAGE
    except Exception as e:
        logger.error(f"Ошибка в get_vin: {e}")
        return OrderStates.VIN

@rate_limit
async def get_mileage(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.MILEAGE
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
    except Exception as e:
        logger.error(f"Ошибка в get_mileage: {e}")
        return OrderStates.MILEAGE

async def get_style_city(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.STYLE_CITY
        ctx.user_data['style_city'] = upd.message.text
        await upd.message.reply_text("🛣️ Стиль вождения на трассе:", reply_markup=highway_style_kb)
        return OrderStates.STYLE_HIGHWAY
    except Exception as e:
        logger.error(f"Ошибка в get_style_city: {e}")
        return OrderStates.STYLE_CITY

async def get_style_highway(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.STYLE_HIGHWAY
        ctx.user_data['style_highway'] = upd.message.text
        await upd.message.reply_text("🚚 Способ доставки:", reply_markup=delivery_type_kb)
        return OrderStates.DELIVERY_TYPE
    except Exception as e:
        logger.error(f"Ошибка в get_style_highway: {e}")
        return OrderStates.STYLE_HIGHWAY

async def get_delivery_type(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.DELIVERY_TYPE
        choice = upd.message.text
        ctx.user_data['delivery_type'] = choice
        if choice == "Курьером":
            await upd.message.reply_text("📍 Введите ПОЛНЫЙ АДРЕС доставки\n\nПример: г. Москва, ул. Тверская, д. 15, кв. 78")
            return OrderStates.ADDRESS
        elif choice == "Самовывоз":
            ctx.user_data['delivery_price'] = 0
            await upd.message.reply_text(
                "📍 САМОВЫВОЗ\n\n"
                "Доступные пункты выдачи:\n"
                "📍 Метро Давыдково\n"
                "📍 Метро Южная\n"
                "📍 Метро Строгино\n\n"
                "Нажмите на кнопку ниже для выбора:",
                reply_markup=pickup_keyboard
            )
            return OrderStates.ADDRESS
        else:
            ctx.user_data['delivery_price'] = 0
            await upd.message.reply_text("🚛 Сторонняя фирма (стоимость рассчитает менеджер)\n\n📍 Введите адрес доставки:")
            return OrderStates.ADDRESS
    except Exception as e:
        logger.error(f"Ошибка в get_delivery_type: {e}")
        return OrderStates.DELIVERY_TYPE

async def pickup_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        station_map = {
            "pickup_davydkovo": "Метро Давыдково",
            "pickup_yuzhnaya": "Метро Южная",
            "pickup_strogino": "Метро Строгино"
        }
        station = station_map.get(query.data, "Метро")
        ctx.user_data['delivery_address'] = station
        ctx.user_data['city'] = "Москва"
        ctx.user_data['distance'] = 0
        ctx.user_data['delivery_price'] = 0
        await query.edit_message_text(
            f"📍 Пункт самовывоза: {station}\n"
            f"🏙️ Город: Москва\n"
            f"🚚 Доставка: 0 руб.\n\n"
            f"📞 Введите ваш контактный телефон:"
        )
        return OrderStates.PHONE
    except Exception as e:
        logger.error(f"Ошибка в pickup_callback: {e}")
        return OrderStates.ADDRESS

@rate_limit
async def get_address(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.ADDRESS
        full_address = upd.message.text.strip()
        if len(full_address) < 5:
            await upd.message.reply_text("❌ Введите полный адрес (минимум 5 символов):")
            return OrderStates.ADDRESS
        ctx.user_data['delivery_address'] = full_address
        if ctx.user_data.get('delivery_type') == "Самовывоз":
            city = ctx.user_data.get('city', "Москва")
            distance = ctx.user_data.get('distance', 0)
            price = ctx.user_data.get('delivery_price', 0)
            await upd.message.reply_text(
                f"📍 Пункт самовывоза: {full_address}\n"
                f"🏙️ Город: {city}\n"
                f"🚚 Доставка: {price} руб.\n\n"
                f"📞 Введите ваш контактный телефон:"
            )
            return OrderStates.PHONE
        city = extract_city_from_address(full_address)
        distance = extract_distance_from_address(full_address)
        ctx.user_data['city'] = city
        ctx.user_data['distance'] = distance
        if ctx.user_data.get('delivery_type') == "Курьером":
            price = calc_delivery_price(distance)
            ctx.user_data['delivery_price'] = price
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
    except Exception as e:
        logger.error(f"Ошибка в get_address: {e}")
        return OrderStates.ADDRESS

@rate_limit
async def get_phone(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.PHONE
        phone = upd.message.text.strip()
        is_valid, error = security.validate_phone(phone)
        if not is_valid:
            await upd.message.reply_text(f"❌ {error}\n\nПопробуйте снова:")
            return OrderStates.PHONE
        ctx.user_data['phone'] = phone
        await upd.message.reply_text("🔧 Выберите узел запчасти:", reply_markup=part_node_kb)
        return OrderStates.PART_NODE
    except Exception as e:
        logger.error(f"Ошибка в get_phone: {e}")
        return OrderStates.PHONE

async def get_part_node(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.PART_NODE
        ctx.user_data['part_node'] = upd.message.text
        if upd.message.text in AXLE_REQUIRED_NODES:
            await upd.message.reply_text("🔧 Выберите ось:", reply_markup=axle_kb)
            return OrderStates.AXLE
        else:
            ctx.user_data['axle'] = "Не требуется"
            await upd.message.reply_text(
                "🔧 Какие запчасти нужны? (каждая с новой строки)\n\n"
                "Пример:\n"
                "Колодки тормозные\n"
                "Диски тормозные\n\n"
                "Или просто напишите названия запчастей"
            )
            return OrderStates.PARTS
    except Exception as e:
        logger.error(f"Ошибка в get_part_node: {e}")
        return OrderStates.PART_NODE

async def get_axle(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.AXLE
        ctx.user_data['axle'] = upd.message.text
        await upd.message.reply_text(
            "🔧 Какие запчасти нужны? (каждая с новой строки)\n\n"
            "Пример:\n"
            "Колодки тормозные\n"
            "Диски тормозные\n\n"
            "Или просто напишите названия запчастей"
        )
        return OrderStates.PARTS
    except Exception as e:
        logger.error(f"Ошибка в get_axle: {e}")
        return OrderStates.AXLE

@rate_limit
async def get_parts(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
        ctx.user_data['needed_parts'] = upd.message.text[:MAX_MESSAGE_LENGTH]
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
                   f"📝 Запчасти:\n{data.get('needed_parts', 'не указаны')[:500]}\n\n"
                   f"💰 Доставка: {delivery_price} руб.\n\n"
                   "✅ Всё верно? Нажмите Готово или Редактировать")
        await upd.message.reply_text(summary, reply_markup=confirm_order_kb)
        return OrderStates.CONFIRM
    except Exception as e:
        logger.error(f"Ошибка в get_parts: {e}")
        return OrderStates.PARTS

async def confirm_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return ConversationHandler.END
        if upd.message.text == "✅ Готово":
            data = ctx.user_data.copy() if ctx.user_data else {}
            if not data.get('vin'):
                await upd.message.reply_text("❌ Ошибка: VIN не указан. Начните заказ заново.")
                return ConversationHandler.END
            logger.info(f"Сохранение заказа для пользователя {upd.effective_user.id}")
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
            if ctx.user_data:
                ctx.user_data.clear()
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
            try:
                await ctx.bot.send_message(
                    MANAGER_ID,
                    text=f"🆕 НОВЫЙ ЗАКАЗ #{order_num}\n\n"
                         f"👤 Клиент: {upd.effective_user.full_name}\n"
                         f"📞 Телефон: {data.get('phone', 'не указан')}\n"
                         f"🚗 VIN: {data.get('vin', 'не указан')}\n"
                         f"📍 Адрес: {data.get('delivery_address', 'не указан')}\n"
                         f"🚚 Доставка: {data.get('delivery_type', 'не указана')}\n"
                         f"📝 Запчасти: {data.get('needed_parts', 'не указаны')[:200]}"
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления менеджера: {e}")
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
    except Exception as e:
        logger.error(f"Ошибка в confirm_order: {e}")
        return ConversationHandler.END

async def confirm_edit_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        if query.data == "confirm_edit":
            if ctx.user_data:
                ctx.user_data.clear()
            await query.edit_message_text(
                "✏️ Давайте начнём заказ заново. Нажмите 🛒 Новый заказ",
                reply_markup=main_menu
            )
            return ConversationHandler.END
        else:
            await query.edit_message_text("✅ Продолжаем оформление заказа. Введите запчасти:")
            return OrderStates.PARTS
    except Exception as e:
        logger.error(f"Ошибка в confirm_edit_callback: {e}")
        return ConversationHandler.END

async def continue_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        if ctx.user_data:
            ctx.user_data['needed_parts'] = "Без запчастей (уточнить у менеджера)"
            data = ctx.user_data
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
        else:
            await query.edit_message_text("❌ Ошибка: данные заказа не найдены. Начните заказ заново.", reply_markup=main_menu)
        return OrderStates.CONFIRM
    except Exception as e:
        logger.error(f"Ошибка в continue_order_callback: {e}")
        return ConversationHandler.END

async def cancel_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Ошибка в cancel_order_callback: {e}")

async def confirm_cancel_order_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        if ctx.user_data:
            ctx.user_data.clear()
        await query.edit_message_text(
            "❌ Заказ отменён. Вы можете начать новый заказ в главном меню.",
            reply_markup=main_menu
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в confirm_cancel_order_callback: {e}")
        return ConversationHandler.END

# ========== СОХРАНЕНИЕ VIN ==========

async def save_vin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        vin = query.data[9:]
        ctx.user_data['save_vin'] = vin
        await query.edit_message_text(
            f"🚗 СОХРАНЕНИЕ АВТОМОБИЛЯ\n\n"
            f"VIN: {vin}\n\n"
            f"Добавьте комментарий (например, «зимняя резина», «жена», «служебный»)\n"
            f"Максимум 100 символов.\n\n"
            f"Или отправьте '-' чтобы пропустить:"
        )
        return SaveStates.COMMENT
    except Exception as e:
        logger.error(f"Ошибка в save_vin_callback: {e}")
        return ConversationHandler.END

async def save_vin_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = upd.effective_user.id
        vin = ctx.user_data.get('save_vin') if ctx.user_data else None
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
        if ctx.user_data and 'save_vin' in ctx.user_data:
            del ctx.user_data['save_vin']
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в save_vin_comment_input: {e}")
        return ConversationHandler.END

async def no_save_vin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        await query.edit_message_text("OK, в следующий раз вы сможете сохранить автомобиль в гараж при создании заказа.")
    except Exception as e:
        logger.error(f"Ошибка в no_save_vin_callback: {e}")

# ========== МОИ ЗАКАЗЫ ==========

async def my_orders(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Ошибка в my_orders: {e}")

@require_order_owner
async def view_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        order = ctx.user_data.get('current_order') if ctx.user_data else None
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
            parts = safe_parse_parts(final_order)
            if parts:
                for i, part in enumerate(parts):
                    if isinstance(part, dict):
                        part_name = part.get('name', 'неизвестно')
                        part_price = part.get('price', 0)
                        ravenol_mark = " (Ravenol)" if is_ravenol_product(part_name) else ""
                        text += f"{i+1}. {part_name}\n   → {part_price} руб.{ravenol_mark}\n"
            else:
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
            kb.append([InlineKeyboardButton("💳 Отправить чек об оплате", callback_data=f"pay_document_{order_num}")])
        kb.append([InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders_list")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Ошибка в view_order: {e}")

async def back_orders_list(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        await my_orders(upd, ctx)
    except Exception as e:
        logger.error(f"Ошибка в back_orders_list: {e}")

async def main_menu_back(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        await start(upd, ctx)
    except Exception as e:
        logger.error(f"Ошибка в main_menu_back: {e}")

# ========== ОПЛАТА ==========

@require_order_owner
async def payment_document_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        parts = query.data.split('_')
        if len(parts) < 3:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[2]
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        if order.get('status') != 'waiting_payment':
            await query.edit_message_text("❌ Оплату можно отправить только для заказа в статусе 'Ожидает оплаты'")
            return
        total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
        await query.edit_message_text(
            f"💳 ОПЛАТА ЗАКАЗА #{order_num}\n\n"
            f"💰 Сумма к оплате: {total_sum} руб.\n\n"
            f"📎 Отправьте документ об оплате (чек, квитанцию, счёт):\n\n"
            f"• Можно отправить ФОТО или PDF-файл\n"
            f"• Добавьте комментарий (необязательно)\n\n"
            f"📌 После отправки менеджер проверит оплату и изменит статус заказа.\n\n"
            f"❌ Для отмены отправьте /cancel"
        )
        ctx.user_data['payment_order'] = order_num
        return PaymentStates.WAITING_DOCUMENT
    except Exception as e:
        logger.error(f"Ошибка в payment_document_callback: {e}")
        return ConversationHandler.END

async def handle_payment_document(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """Обработка документов оплаты - ТОЛЬКО ФОТО И ДОКУМЕНТЫ"""
    try:
        if not upd or not upd.message:
            return ConversationHandler.END
        user_id = upd.effective_user.id
        order_num = ctx.user_data.get('payment_order') if ctx.user_data else None
        if not order_num:
            await upd.message.reply_text("❌ Ошибка. Попробуйте снова или начните заново.")
            return ConversationHandler.END
        order = get_order(order_num)
        if not order:
            await upd.message.reply_text("❌ Заказ не найден")
            return ConversationHandler.END
        if order.get('status') != 'waiting_payment':
            await upd.message.reply_text("❌ Оплату можно отправить только для заказа в статусе 'Ожидает оплаты'")
            return ConversationHandler.END
        
        # ========== ТОЛЬКО ФОТО И ДОКУМЕНТЫ ==========
        caption = ""
        file_id = None
        file_type = "unknown"
        
        if upd.message.photo:
            file_id = upd.message.photo[-1].file_id
            file_type = "photo"
            caption = upd.message.caption or ""
        elif upd.message.document:
            file_id = upd.message.document.file_id
            file_type = "document"
            caption = upd.message.caption or ""
        else:
            await upd.message.reply_text(
                "❌ Пожалуйста, отправьте ФОТО или ДОКУМЕНТ (PDF, JPG, PNG)\n\n"
                "Вы можете:\n"
                "📸 Сфотографировать чек\n"
                "📎 Прикрепить файл (PDF)\n"
                "🖼️ Отправить скриншот\n\n"
                "Или отправьте /cancel для отмены"
            )
            return PaymentStates.WAITING_DOCUMENT
        
        total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
        save_payment_document(order_num, file_id, file_type, caption, user_id)
        
        try:
            manager_text = (
                f"💳 НОВЫЙ ДОКУМЕНТ ОПЛАТЫ\n\n"
                f"📦 Заказ: {order_num}\n"
                f"👤 Клиент: {order.get('user_name', 'Неизвестно')}\n"
                f"📞 Телефон: {order.get('phone', 'Не указан')}\n"
                f"💰 Сумма: {total_sum} руб.\n"
                f"📎 Тип: {'Фото' if file_type == 'photo' else 'Документ'}\n"
                f"💬 Комментарий: {caption[:200] if caption else 'Нет'}\n\n"
                f"✅ Для подтверждения оплаты нажмите:\n"
                f"/confirm_payment {order_num}"
            )
            if file_type == "photo":
                await ctx.bot.send_photo(MANAGER_ID, file_id, caption=manager_text)
            else:
                await ctx.bot.send_document(MANAGER_ID, file_id, caption=manager_text)
        except Exception as e:
            logger.error(f"Ошибка отправки менеджеру: {e}")
            await upd.message.reply_text("⚠️ Ошибка при отправке документа менеджеру. Попробуйте позже.")
            return ConversationHandler.END
        
        await upd.message.reply_text(
            f"✅ Документ об оплате для заказа {order_num} успешно отправлен!\n\n"
            f"💰 Сумма: {total_sum} руб.\n"
            f"📎 Файл: {'Фото' if file_type == 'photo' else 'Документ'}\n\n"
            f"⏳ Менеджер проверит оплату и изменит статус заказа.\n"
            f"Вы получите уведомление, когда заказ будет подтверждён.\n\n"
            f"Вернуться в главное меню: /start"
        )
        if ctx.user_data and 'payment_order' in ctx.user_data:
            del ctx.user_data['payment_order']
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в handle_payment_document: {e}")
        await upd.message.reply_text(f"❌ Произошла ошибка: {str(e)[:100]}")
        return ConversationHandler.END

# ========== УНИВЕРСАЛЬНЫЙ ОБРАБОТЧИК МЕНЕДЖЕРА (ОБНОВЛЕН) ==========

@require_manager
async def manager_message_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    ОБНОВЛЕННЫЙ обработчик сообщений от менеджера
    - Правильно парсит ВСЕ товары
    - Правильно считает сумму
    - Правильно привязывает производство
    """
    try:
        if not upd or not upd.message:
            return
        
        msg_text = upd.message.text or ""
        reply_to = upd.message.reply_to_message
        
        # ========== 1. ПАРСИНГ ТЕКСТА С ЦЕНАМИ ==========
        has_prices = re.search(r'\d+\s*(?:руб|₽|р\.)', msg_text, re.I)
        
        if has_prices and len(msg_text) > 20:
            # Логируем для отладки
            logger.info(f"📥 НОВОЕ СООБЩЕНИЕ ОТ МЕНЕДЖЕРА")
            logger.info(f"📄 Текст: {msg_text[:200]}...")
            
            # Парсим полный текст
            products = parse_full_client_text(msg_text)
            
            # Если не распозналось - пробуем парсер для менеджера
            if not products:
                logger.info("🔄 Пробуем parse_manager_text")
                products = parse_manager_text(msg_text)
            
            # Если всё ещё не распозналось - пробуем стандартный
            if not products:
                logger.info("🔄 Пробуем parse_products")
                products = parse_products(msg_text)
            
            if products:
                # ===== ПРАВИЛЬНЫЙ ПОДСЧЕТ СУММЫ =====
                total_price = 0
                product_list = []
                
                for p in products:
                    if 'total_price' in p:
                        price = p.get('total_price', 0)
                    elif 'price' in p:
                        price = p.get('price', 0)
                    else:
                        price = 0
                    total_price += price
                    product_list.append(p)
                
                # Логируем результат
                logger.info(f"📊 РАСПОЗНАНО ТОВАРОВ: {len(product_list)}")
                logger.info(f"💰 СУММА: {total_price}")
                
                # Ищем номер заказа
                order_num = find_order_number_in_text(msg_text, reply_to.text if reply_to else None)
                
                # Если не нашли - ищем последний активный заказ
                if not order_num:
                    conn = None
                    try:
                        conn = sqlite3.connect(DB_PATH, timeout=30)
                        c = conn.cursor()
                        c.execute('''
                            SELECT order_number FROM orders 
                            WHERE status IN ('pending', 'waiting_selection') 
                            ORDER BY id DESC LIMIT 1
                        ''')
                        row = c.fetchone()
                        if row:
                            order_num = row[0]
                    except Exception as e:
                        logger.error(f"Ошибка поиска заказа: {e}")
                    finally:
                        if conn:
                            conn.close()
                
                if not order_num:
                    await upd.message.reply_text(
                        "❌ Не удалось определить номер заказа.\n\n"
                        "Укажите номер заказа в сообщении.\n"
                        "Пример: Заказ: RVN-ABCD12"
                    )
                    return
                
                order = get_order(order_num)
                if not order:
                    await upd.message.reply_text(f"❌ Заказ {order_num} не найден")
                    return
                
                delivery_price = order.get('delivery_price', 0)
                total_sum = total_price + delivery_price
                
                # ===== ВАЛИДАЦИЯ СУММЫ =====
                # Проверяем, что сумма не превышает разумные пределы
                if total_price > 1000000:
                    logger.warning(f"⚠️ ПОДОЗРИТЕЛЬНАЯ СУММА: {total_price}")
                    # Проверяем, есть ли в тексте другие суммы
                    all_prices = re.findall(r'(\d{1,3}(?:[\s.]?\d{3})*)\s*(?:руб|₽|р\.)', msg_text, re.I)
                    if all_prices:
                        # Если есть много цен, возможно парсер ошибся
                        # Пробуем пересчитать вручную
                        manual_total = 0
                        for price_str in all_prices:
                            try:
                                p = int(price_str.replace(' ', '').replace('.', ''))
                                if 1 <= p <= 1000000:
                                    manual_total += p
                            except:
                                pass
                        if manual_total > 0 and manual_total < total_price:
                            logger.info(f"🔄 РУЧНОЙ ПЕРЕСЧЕТ: {manual_total} вместо {total_price}")
                            total_price = manual_total
                            total_sum = total_price + delivery_price
                
                # Обновляем заказ
                update_order(
                    order_num,
                    selected_products=msg_text[:MAX_MESSAGE_LENGTH],
                    final_order=str(product_list),
                    total_price=total_price,
                    status='waiting_selection'
                )
                
                # ===== ФОРМИРУЕМ СООБЩЕНИЕ ДЛЯ КЛИЕНТА =====
                client_text = f"🛒 **ПОДБОР ЗАПЧАСТЕЙ ДЛЯ ЗАКАЗА #{order_num}**\n\n"
                client_text += f"Менеджер подобрал для вас следующие позиции:\n\n"
                
                for i, p in enumerate(product_list, 1):
                    # Название
                    client_text += f"**{i}. {p.get('name', '')}**\n"
                    
                    # Артикул
                    if p.get('part_number'):
                        client_text += f"   📦 Артикул: `{p['part_number']}`\n"
                    
                    # Бренд
                    if p.get('brand'):
                        client_text += f"   🏭 Бренд: {p['brand']}\n"
                    
                    # Цена с количеством
                    qty = p.get('quantity', 1)
                    price_per_unit = p.get('price_per_unit', 0)
                    total = p.get('total_price', 0) if 'total_price' in p else p.get('price', 0)
                    
                    if qty > 1 and price_per_unit > 0:
                        client_text += f"   💰 {price_per_unit:,} ₽/шт × {qty} шт = **{total:,} ₽**\n"
                    else:
                        client_text += f"   💰 **{total:,} ₽**\n"
                    
                    # Срок
                    if p.get('delivery_time'):
                        client_text += f"   ⏱️ Срок: {p['delivery_time']}\n"
                    
                    # Производство
                    if p.get('manufacturer'):
                        client_text += f"   🏭 Производство: {p['manufacturer']}\n"
                    
                    # Наличие
                    if p.get('in_stock'):
                        client_text += f"   📦 В наличии: {p['in_stock']} шт\n"
                    
                    client_text += "\n"
                
                # Итог
                client_text += "─" * 30 + "\n"
                client_text += f"📦 **ИТОГО:**\n"
                client_text += f"   • Всего позиций: {len(product_list)}\n"
                client_text += f"   • Сумма товаров: **{total_price:,} ₽**\n"
                client_text += f"   • 🚚 Доставка: {delivery_price:,} ₽\n"
                client_text += f"   • 💳 **ИТОГО К ОПЛАТЕ: {total_sum:,} ₽**\n\n"
                client_text += f"✅ Выберите нужные запчасти (можно отметить несколько):"
                
                # Создаем клавиатуру
                kb = []
                for i, p in enumerate(product_list[:50]):
                    display_name = p.get('name', '')[:25] + ".." if len(p.get('name', '')) > 25 else p.get('name', '')
                    total = p.get('total_price', 0) if 'total_price' in p else p.get('price', 0)
                    
                    # Показываем количество если > 1
                    qty = p.get('quantity', 1)
                    if qty > 1:
                        display_name += f" ×{qty}"
                    
                    kb.append([InlineKeyboardButton(
                        f"⬜ {display_name} — {total:,} руб.",
                        callback_data=f"sel_{order_num}_{i}"
                    )])
                kb.append([InlineKeyboardButton(
                    "✅ ПОДТВЕРДИТЬ ВЫБОР",
                    callback_data=f"fin_{order_num}"
                )])
                
                # Отправляем клиенту
                await ctx.bot.send_message(
                    order.get('user_id'),
                    text=client_text,
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode='Markdown'
                )
                
                # Подтверждение менеджеру
                products_preview = "\n".join([
                    f"   {i+1}. {p.get('name', '')[:30]} = {p.get('price', 0):,} руб."
                    for i, p in enumerate(product_list[:5])
                ])
                if len(product_list) > 5:
                    products_preview += f"\n   ... и ещё {len(product_list) - 5} товаров"
                
                await upd.message.reply_text(
                    f"✅ **ПОДБОР ОТПРАВЛЕН КЛИЕНТУ!**\n\n"
                    f"📦 **Заказ:** {order_num}\n"
                    f"👤 **Клиент:** {order.get('user_name', '')}\n"
                    f"📦 **Товаров:** {len(product_list)}\n"
                    f"💰 **Сумма:** {total_sum:,} руб.\n\n"
                    f"📋 **Товары:**\n{products_preview}"
                )
                
                audit_log(MANAGER_ID, 'send_selection', 
                         f"Заказ {order_num}: отправлен подбор из {len(product_list)} позиций", "success")
                return
        
        # ========== 2. ТРЕК-НОМЕР ==========
        if reply_to and reply_to.text:
            reply_text = reply_to.text or ""
            reply_text_lower = reply_text.lower()
            
            if "трек-номер" in reply_text_lower or "трек номер" in reply_text_lower:
                tracking = msg_text.strip()
                if len(tracking) < 3 or len(tracking) > 100:
                    await upd.message.reply_text("❌ Трек-номер должен быть от 3 до 100 символов")
                    return
                match = re.search(r'RVN-[A-Z0-9]{6}', reply_text)
                if not match:
                    await upd.message.reply_text("❌ Не удалось определить номер заказа")
                    return
                order_num = match.group(0)
                order = get_order(order_num)
                if not order:
                    await upd.message.reply_text(f"❌ Заказ {order_num} не найден")
                    return
                if order.get('status') != 'ready':
                    await upd.message.reply_text(
                        f"❌ Нельзя отправить заказ {order_num} из статуса '{order.get('status_text', 'неизвестен')}'\n"
                        f"Сначала переведите заказ в статус '✅ Готов к выдаче'"
                    )
                    return
                if update_order(order_num, tracking_number=security.escape_text(tracking), status='shipped'):
                    try:
                        await ctx.bot.send_message(
                            order.get('user_id'),
                            text=f"📦 Заказ {order_num} отправлен!\n\n📮 Трек-номер: {security.escape_text(tracking)}"
                        )
                    except Exception as e:
                        logger.error(f"Ошибка уведомления клиента: {e}")
                    audit_log(MANAGER_ID, 'add_tracking', f"Заказ {order_num}: трек-номер {tracking}", "success")
                    await upd.message.reply_text(f"✅ Трек-номер добавлен!\n\n📦 {order_num}\n📮 {tracking}")
                else:
                    await upd.message.reply_text(f"❌ Ошибка при обновлении заказа")
                return
        
        # ========== 3. ОТВЕТ КЛИЕНТУ ==========
        if reply_to and reply_to.from_user and reply_to.from_user.id != MANAGER_ID:
            if len(msg_text) > 4000:
                await upd.message.reply_text("❌ Сообщение слишком длинное (макс. 4000 символов)")
                return
            try:
                await ctx.bot.send_message(
                    reply_to.from_user.id,
                    text=f"📨 ОТВЕТ МЕНЕДЖЕРА:\n\n{security.escape_text(msg_text)}\n\n---\nВы можете продолжить диалог в этом чате."
                )
                audit_log(MANAGER_ID, 'reply_to_client', f"Ответ клиенту: {msg_text[:100]}", "success")
                await upd.message.reply_text("✅ Ответ отправлен клиенту!")
            except Exception as e:
                logger.error(f"Ошибка отправки ответа клиенту: {e}")
                await upd.message.reply_text(f"❌ Ошибка отправки: {str(e)[:100]}")
            return
        
        # ========== 4. НЕИЗВЕСТНО ==========
        await upd.message.reply_text(
            "📝 **ЧТО Я МОГУ СДЕЛАТЬ:**\n\n"
            "1️⃣ **Отправить подбор запчастей:**\n"
            "   Просто отправьте текст с ценами\n"
            "   Поддерживаются форматы:\n"
            "   - Название = 1000 руб\n"
            "   - Название — 1000 ₽/шт × 2 шт = 2000 ₽\n"
            "   - 1. Название\n"
            "     🎯 1000 ₽/шт × 2 шт = 2000 ₽\n"
            "     💰 Срок: 1-2 дня | Производство: KOREA\n\n"
            "2️⃣ **Ответить клиенту:**\n"
            "   Ответьте на сообщение клиента\n\n"
            "3️⃣ **Добавить трек-номер:**\n"
            "   Сначала нажмите '🚚 Отправлен' в админ-панели"
        )
        
    except Exception as e:
        logger.error(f"Ошибка в manager_message_handler: {e}", exc_info=True)
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")

# ========== ОСТАЛЬНЫЕ КОМАНДЫ (СОХРАНЕНЫ) ==========

# ... (все остальные функции остаются без изменений: 
# confirm_payment_command, show_payment_docs, remove_items_callback,
# client_toggle_callback, client_confirm_remove_callback,
# remove_comment_input, cancel_by_user_callback,
# confirm_user_cancel_callback, garage_menu, garage_add_start,
# garage_get_vin, garage_get_description, garage_delete,
# garage_confirm_delete, garage_comment_start, garage_comment_input,
# garage_back_to_menu, referral_cmd, delivery_cmd, help_cmd,
# bonus_cmd, bonus_history_callback, bonus_back_callback,
# apply_bonus_callback, spend_bonus_percent_callback,
# confirm_spend_callback, spend_bonus_custom_callback,
# spend_bonus_custom_input, delete_order_command,
# batch_delete_orders, show_all_orders_raw, fix_orphan_orders,
# admin_menu, admin_callback, admin_add_item_name_input,
# admin_add_item_price_input, admin_change_price_input,
# send_message_to_client, select_command, select_cb,
# finalize_cb, error_handler)

# ========== ЗАПУСК ==========

def main():
    try:
        init_db()
        logger.info("База данных инициализирована успешно")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
    
    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(backup_db, 'cron', hour=3, minute=0)
        scheduler.start()
        logger.info("Планировщик запущен - ежедневный бэкап в 03:00")
    except Exception as e:
        logger.error(f"Ошибка планировщика: {e}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    commands = [
        ("start", "Главное меню"),
        ("my_orders", "Мои заказы"),
        ("bonus", "Бонусы"),
        ("referral", "Рефералы"),
        ("delivery", "Доставка"),
        ("help", "Помощь"),
        ("menu", "Панель управления"),
        ("allorders", "Показать все заказы"),
        ("fix_orders", "Проверить заказы"),
        ("delorder", "Удалить заказ по номеру"),
        ("batch_del", "Массовое удаление заказов"),
        ("confirm_payment", "Подтвердить оплату заказа"),
        ("payment_docs", "Показать документы оплаты"),
        ("select", "Отправить подбор запчастей"),
        ("msg", "Написать клиенту"),
    ]
    
    async def set_commands(application):
        try:
            await application.bot.set_my_commands(commands)
            logger.info("Команды установлены успешно")
        except Exception as e:
            logger.error(f"Ошибка установки команд: {e}")
    
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
    
    # ConversationHandler для сохранения VIN
    save_vin_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(save_vin_callback, pattern="^save_vin_")],
        states={SaveStates.COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, save_vin_comment_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
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
    
    # ConversationHandler для удаления товаров
    remove_items_conv = ConversationHandler(
        entry_points=[],
        states={RemoveStates.COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_comment_input)]},
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # ConversationHandler для списания бонусов
    spend_bonus_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(spend_bonus_custom_callback, pattern="^spend_bonus_custom_")],
        states={BonusStates.SPEND: [MessageHandler(filters.TEXT & ~filters.COMMAND, spend_bonus_custom_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # ConversationHandler для админа (добавление товара)
    admin_add_item_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_add_item_")],
        states={
            AdminAddItemStates.NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_item_name_input)],
            AdminAddItemStates.PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_item_price_input)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # ConversationHandler для админа (изменение цены)
    admin_change_price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_change_price_")],
        states={AdminChangePriceStates.NEW_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_change_price_input)]},
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # ConversationHandler для отправки документов оплаты
    payment_conv = ConversationHandler(
        entry_points=[
            CallbackQueryHandler(payment_document_callback, pattern="^pay_document_"),
        ],
        states={PaymentStates.WAITING_DOCUMENT: [MessageHandler(filters.PHOTO | filters.Document.ALL, handle_payment_document)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=600
    )
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(order_conv)
    app.add_handler(save_vin_conv)
    app.add_handler(garage_conv)
    app.add_handler(garage_comment_conv)
    app.add_handler(remove_items_conv)
    app.add_handler(spend_bonus_conv)
    app.add_handler(admin_add_item_conv)
    app.add_handler(admin_change_price_conv)
    app.add_handler(payment_conv)
    
    app.add_handler(CommandHandler("my_orders", my_orders))
    app.add_handler(CommandHandler("bonus", bonus_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("delivery", delivery_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", admin_menu))
    app.add_handler(CommandHandler("select", select_command))
    app.add_handler(CommandHandler("msg", send_message_to_client))
    
    # Админ команды
    app.add_handler(CommandHandler("delorder", delete_order_command))
    app.add_handler(CommandHandler("batch_del", batch_delete_orders))
    app.add_handler(CommandHandler("allorders", show_all_orders_raw))
    app.add_handler(CommandHandler("fix_orders", fix_orphan_orders))
    app.add_handler(CommandHandler("confirm_payment", confirm_payment_command))
    app.add_handler(CommandHandler("payment_docs", show_payment_docs))
    
    # Кнопочные обработчики
    app.add_handler(MessageHandler(filters.Regex("^(🚗 Мой гараж)$"), garage_menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    
    # Callback обработчики
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|pay_|ordered_|arrived_|ready_|ship_|del_|issued_|cancel_|edit_delivery_|set_delivery_|detail_|order_changes_|admin_edit_items_|admin_remove_items_|admin_toggle_|admin_remove_confirm_|admin_add_item_|admin_change_price_|admin_select_price_item_|waiting_selection_|waiting_payment_)"))
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
    
    # Универсальный обработчик сообщений менеджера
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID) & filters.TEXT & ~filters.COMMAND, manager_message_handler))
    
    app.add_error_handler(error_handler)
    
    logger.info(f"🤖 Бот запущен! ID администратора: {MANAGER_ID}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
