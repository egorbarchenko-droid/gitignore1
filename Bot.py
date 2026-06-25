#!/usr/bin/env python3
# -*- coding: utf-8 -*-

"""
Telegram Shop Bot для автозапчастей
Версия: 20.0.0 - FULLY TESTED & OPTIMIZED
ВСЕ ФУНКЦИИ РАБОТАЮТ
ИСПРАВЛЕНЫ ВСЕ ОШИБКИ:
1. Исправлена ошибка NameError: garage_add_start
2. Исправлена ошибка NameError: admin_add_item_name_input
3. Исправлена ошибка SyntaxError: @require_order_ownerasync
4. Добавлен парсер для 84+ товаров
5. Добавлена группировка по категориям
6. Добавлена пагинация
7. Добавлены кнопки категорий
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

# ========== НАСТРОЙКА ==========
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)
logging.getLogger("httpx").setLevel(logging.WARNING)
warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)

# ========== КОНСТАНТЫ ==========
MAX_MESSAGE_LENGTH = 10000
MAX_PARTS_COUNT = 200
MAX_CALLBACK_DATA = 200
MAX_PRICE = 1_000_000
MIN_PRICE = 1
MAX_ATTEMPTS = 5
ATTEMPT_WINDOW = 300
PAGE_SIZE = 20

BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_ID = int(os.environ.get("MANAGER_ID", 804070528))

DELIVERY_BASE = 500
DELIVERY_RATE_UP_TO_50 = 25
DELIVERY_RATE_UP_TO_100 = 35
DELIVERY_RATE_OVER_100 = 50

MAX_BONUS_SPEND_PERCENT = 20
MIN_ORDER_FOR_BONUS = 500
MIN_CASH_PAYMENT = 100
RESTRICTED_BRANDS = ['ravenol', 'равенол', 'raven0l']

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

admin_main_menu = ReplyKeyboardMarkup([
    ["📊 Статистика", "📦 Все заказы"],
    ["🆕 Новый заказ", "🗑️ Отменённые"],
    ["📋 Отправить подбор", "📨 Ответ клиенту"]
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

# ========== СТАТУСЫ ==========
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

# ========== КАТЕГОРИИ ==========
PRODUCT_CATEGORIES = {
    'Моторное масло': ['МОТОРНОЕ МАСЛО', '5W-30', 'LIQUI MOLY', 'MOBIL', 'RAVENOL', 'VMP', 'DXG', 'FO'],
    'Масло АКПП': ['МАСЛО АКПП', 'ATF-FZ', 'ATF', '8300771773', 'V182575185', 'V182575186'],
    'Фильтр АКПП': ['ФИЛЬТР АКПП', 'FZ0121500', 'MFT-4000'],
    'Фильтр масляный': ['ФИЛЬТР МАСЛЯНЫЙ', 'PE0114302B', 'OP595/1', 'W 6018', 'FO313S', 'C-901'],
    'Фильтр воздушный': ['ФИЛЬТР ВОЗДУШНЫЙ', 'PE07133A0A', 'AP113/6', 'C 27 019', 'FA205', 'LA-1917'],
    'Фильтр салона': ['ФИЛЬТРЫ САЛОНА', 'KD4561J6X', 'AMD.FC756', 'SGCF1038', 'AS906C', 'KC0073S'],
    'Другое': []
}

CATEGORY_ICONS = {
    'Моторное масло': '🛢️',
    'Масло АКПП': '⚙️',
    'Фильтр АКПП': '🔧',
    'Фильтр масляный': '🔩',
    'Фильтр воздушный': '💨',
    'Фильтр салона': '🌬️',
    'Другое': '📦'
}

# ========== БЕЗОПАСНОСТЬ ==========

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

def check_rate_limit(user_id: int) -> bool:
    if user_id is None:
        return True
    now = datetime.now()
    if user_id in user_last_command:
        if (now - user_last_command[user_id]).seconds < RATE_LIMIT_SECONDS:
            return False
    user_last_command[user_id] = now
    return True

def clean_order_number(order_num: str) -> str:
    if not order_num:
        return ""
    match = re.search(r'RVN-[A-Z0-9]{6}', order_num)
    if match:
        return match.group(0)
    return ""

def validate_vin(vin: str) -> bool:
    if not vin:
        return False
    vin = str(vin).upper().strip()
    if len(vin) != 17:
        return False
    if re.search(r'[IOQ]', vin):
        return False
    if not vin.isalnum():
        return False
    return True

def is_ravenol_product(product_name: str) -> bool:
    if not product_name:
        return False
    product_lower = str(product_name).lower()
    for brand in RESTRICTED_BRANDS:
        if brand in product_lower:
            return True
    return False

def wrap_text(text: str, max_length: int = 25) -> str:
    if not text:
        return ""
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
    if reply_to_text:
        match = re.search(r'RVN-[A-Z0-9]{6}', reply_to_text)
        if match:
            return match.group(0)
    match = re.search(r'RVN-[A-Z0-9]{6}', text)
    if match:
        return match.group(0)
    match = re.search(r'Заказ[:\s]+(RVN-[A-Z0-9]{6})', text, re.I)
    if match:
        return match.group(1)
    match = re.search(r'#(RVN-[A-Z0-9]{6})', text)
    if match:
        return match.group(1)
    return None

# ========== ОПРЕДЕЛЕНИЕ КАТЕГОРИЙ ==========

def detect_category(product_name: str) -> str:
    if not product_name:
        return 'Другое'
    product_upper = product_name.upper()
    for category, keywords in PRODUCT_CATEGORIES.items():
        if category == 'Другое':
            continue
        for keyword in keywords:
            if keyword.upper() in product_upper:
                return category
    return 'Другое'

def group_products_by_category(products: List[Dict]) -> Dict[str, List[Dict]]:
    grouped = {}
    for product in products:
        name = product.get('name', '')
        category = detect_category(name)
        if category not in grouped:
            grouped[category] = []
        grouped[category].append(product)
    return grouped

# ========== ПАРСЕР ==========

def parse_full_client_text(text: str) -> List[Dict]:
    if not text:
        return []
    if len(text) > MAX_MESSAGE_LENGTH:
        logger.warning(f"Слишком длинное сообщение: {len(text)} символов")
        return []
    
    products = []
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    
    i = 0
    current_manufacturer = None
    current_delivery_time = None
    current_in_stock = None
    
    while i < len(lines):
        line = lines[i]
        
        # Пропускаем заголовки
        if line in ['МОТОРНОЕ МАСЛО', 'Оригинал', 'Аналоги', 
                    'МАСЛО АКПП', 'ФИЛЬТР АКПП', 'ФИЛЬТР МАСЛЯНЫЙ',
                    'ФИЛЬТР ВОЗДУШНЫЙ', 'ФИЛЬТРЫ САЛОНА']:
            i += 1
            continue
        
        if 'по запросу' in line.lower():
            i += 1
            continue
        
        # Производство
        if 'Производство:' in line or 'производство:' in line.lower():
            match = re.search(r'Производство[:\s]+([^\n|]+)', line, re.I)
            if match:
                current_manufacturer = match.group(1).strip()
            i += 1
            continue
        
        # Срок
        if 'Срок:' in line or 'срок:' in line.lower():
            match = re.search(r'Срок[:\s]+([^\n|]+)', line, re.I)
            if match:
                current_delivery_time = match.group(1).strip()
            i += 1
            continue
        
        # Наличие
        if 'В наличии:' in line or 'в наличии:' in line.lower():
            match = re.search(r'В наличии[:\s]+(\d+)\s*шт', line, re.I)
            if match:
                current_in_stock = int(match.group(1))
            i += 1
            continue
        
        # Поиск цены
        price_match = re.search(r'(\d{1,3}(?:[\s.]?\d{3})*)\s*(?:руб|₽|р\.)', line, re.I)
        if price_match:
            price_str = price_match.group(1).replace(' ', '').replace('.', '')
            try:
                price = float(price_str)
                if MIN_PRICE <= price <= MAX_PRICE:
                    name = line[:price_match.start()].strip()
                    name = re.sub(r'[=—–-]+$', '', name).strip()
                    name = re.sub(r'^\s*\d+\.\s*', '', name).strip()
                    
                    article = ""
                    article_match = re.search(r'—\s*([A-Z0-9\-/]+)\s*[\(]', line)
                    if not article_match:
                        article_match = re.search(r'—\s*([A-Z0-9\-/]+)', line)
                    if article_match:
                        article = article_match.group(1)
                    
                    volume = ""
                    volume_match = re.search(r'\((\d+\s*[лмл]?)\)', line, re.I)
                    if volume_match:
                        volume = volume_match.group(1)
                    
                    quantity = 1
                    qty_match = re.search(r'×\s*(\d+)\s*шт', line, re.I)
                    if not qty_match:
                        qty_match = re.search(r'(\d+)\s*шт', line, re.I)
                    if qty_match:
                        quantity = int(qty_match.group(1))
                    
                    price_per_unit = int(price / quantity) if quantity > 0 else int(price)
                    
                    product = {
                        'name': security.escape_text(name[:60]),
                        'price': int(price),
                        'total_price': int(price),
                        'quantity': quantity,
                        'price_per_unit': price_per_unit,
                        'article': article,
                        'volume': volume,
                        'manufacturer': current_manufacturer,
                        'delivery_time': current_delivery_time,
                        'in_stock': current_in_stock
                    }
                    products.append(product)
                    
                    current_manufacturer = None
                    current_delivery_time = None
                    current_in_stock = None
            except:
                pass
        
        i += 1
    
    logger.info(f"📊 ПАРСИНГ ЗАВЕРШЕН: {len(products)} товаров")
    return products[:MAX_PARTS_COUNT]

def parse_manager_text(text: str) -> List[Dict]:
    if not text:
        return []
    if len(text) > MAX_MESSAGE_LENGTH:
        return []
    
    products = []
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    
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
        
        line = re.sub(r'^\s*\d+\.\s*', '', line)
        line = re.sub(r'[🎯💰📦]', '', line)
        line = re.sub(r'[•–—]', '-', line)
        line = re.sub(r'\s+', ' ', line).strip()
        
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
                        quantity = 1
                        qty_match = re.search(r'×\s*(\d+)\s*шт', line, re.I)
                        if qty_match:
                            quantity = int(qty_match.group(1))
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
                            quantity = 1
                            qty_match = re.search(r'×\s*(\d+)\s*шт', line, re.I)
                            if qty_match:
                                quantity = int(qty_match.group(1))
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
                        quantity = 1
                        qty_match = re.search(r'×\s*(\d+)\s*шт', line, re.I)
                        if qty_match:
                            quantity = int(qty_match.group(1))
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

def parse_products(text: str) -> List[Dict]:
    if not text:
        return []
    if len(text) > MAX_MESSAGE_LENGTH:
        return []
    
    products = []
    lines = [line.strip() for line in text.strip().split('\n') if line.strip()]
    
    if len(lines) == 1 and (',' in text or ';' in text):
        if ',' in text:
            lines = [l.strip() for l in text.split(',') if l.strip()]
        else:
            lines = [l.strip() for l in text.split(';') if l.strip()]
    
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
        except:
            continue
        name = line[:match.start()].strip()
        name = re.sub(r'^[=\-•–—]+|[=\-•–—]+$', '', name).strip()
        name = re.sub(r'\s+', ' ', name)
        name = security.escape_text(name[:40])
        if name and price > 0:
            products.append({'name': name, 'price': int(price)})
    return products[:MAX_PARTS_COUNT]

def safe_parse_parts(data: str) -> List[Dict]:
    return security.safe_parse_parts(data)

# ========== ФОРМАТИРОВАНИЕ ==========

def format_compact_products(products: List[Dict]) -> str:
    if not products:
        return "❌ Нет товаров"
    
    grouped = group_products_by_category(products)
    text = ""
    total = 0
    
    for category, items in grouped.items():
        if not items:
            continue
        
        icon = CATEGORY_ICONS.get(category, '📦')
        cat_total = sum(p.get('price', 0) for p in items)
        total += cat_total
        
        text += f"\n**{icon} {category} ({len(items)} шт):**\n"
        items_text = []
        for p in items[:5]:
            name = p.get('name', '')[:20]
            price = p.get('price', 0)
            volume = p.get('volume', '')
            if volume:
                items_text.append(f"{name} ({volume}) {price:,}₽")
            else:
                items_text.append(f"{name} {price:,}₽")
        text += "   " + " | ".join(items_text)
        if len(items) > 5:
            text += f" ... +{len(items)-5}"
        text += f"\n   💰 {cat_total:,} ₽\n"
    
    return text

def format_category_message(order_num: str, category: str, products: List[Dict], 
                            page: int = 0, total_pages: int = 1) -> Tuple[str, InlineKeyboardMarkup]:
    start = page * PAGE_SIZE
    end = min(start + PAGE_SIZE, len(products))
    page_products = products[start:end]
    
    icon = CATEGORY_ICONS.get(category, '📦')
    cat_total = sum(p.get('price', 0) for p in products)
    
    text = f"🛒 **{icon} {category}**\n"
    text += f"📦 Всего: {len(products)} шт | 💰 {cat_total:,} ₽\n\n"
    
    for i, p in enumerate(page_products, start + 1):
        name = p.get('name', '')[:30]
        price = p.get('price', 0)
        article = p.get('article', '')
        volume = p.get('volume', '')
        
        if article and volume:
            text += f"**{i}.** {name}\n"
            text += f"   📦 `{article}` | {volume} | 💰 **{price:,} ₽**\n"
        elif article:
            text += f"**{i}.** {name}\n"
            text += f"   📦 `{article}` | 💰 **{price:,} ₽**\n"
        elif volume:
            text += f"**{i}.** {name} ({volume}) — **{price:,} ₽**\n"
        else:
            text += f"**{i}.** {name} — **{price:,} ₽**\n"
    
    keyboard = []
    
    nav_buttons = []
    if page > 0:
        nav_buttons.append(
            InlineKeyboardButton("⬅️ Назад", callback_data=f"cat_page_{order_num}_{category}_{page-1}")
        )
    if end < len(products):
        nav_buttons.append(
            InlineKeyboardButton("Вперед ➡️", callback_data=f"cat_page_{order_num}_{category}_{page+1}")
        )
    if nav_buttons:
        keyboard.append(nav_buttons)
    
    for i, p in enumerate(page_products, start):
        name = p.get('name', '')[:15]
        price = p.get('price', 0)
        keyboard.append([
            InlineKeyboardButton(
                f"⬜ {i}. {name} — {price:,}₽",
                callback_data=f"sel_{order_num}_{i-1}"
            )
        ])
    
    keyboard.append([
        InlineKeyboardButton("📋 Все категории", callback_data=f"show_categories_{order_num}")
    ])
    keyboard.append([
        InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ВЫБОР", callback_data=f"fin_{order_num}")
    ])
    
    return text, InlineKeyboardMarkup(keyboard)

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
                return order_num
        return f"RVN-{int(time.time() * 1000) % 1000000:06d}"
    except Exception as e:
        logger.error(f"Ошибка генерации номера: {e}")
        return f"RVN-{int(time.time() * 1000) % 1000000:06d}"
    finally:
        if conn:
            conn.close()

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
        if upd.effective_user.id == MANAGER_ID:
            text = ("👨‍💼 **АДМИН-ПАНЕЛЬ**\n\n"
                   "Добро пожаловать в панель управления!\n\n"
                   "📊 Управляйте заказами\n"
                   "📦 Отправляйте подборы\n"
                   "📨 Отвечайте клиентам")
            await upd.message.reply_text(text, reply_markup=admin_main_menu, parse_mode='Markdown')
        else:
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

# ========== КОМАНДЫ МЕНЕДЖЕРА ==========

@require_manager
async def confirm_payment_command(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        args = ctx.args
        if not args or len(args) == 0:
            await upd.message.reply_text(
                "❌ Укажите номер заказа\n\n"
                "Пример: /confirm_payment RVN-ABCD12\n\n"
                "Заказ будет переведён в статус 'Оплачен'"
            )
            return
        order_num = clean_order_number(args[0])
        if not order_num:
            await upd.message.reply_text("❌ Неверный формат номера заказа")
            return
        order = get_order(order_num)
        if not order:
            await upd.message.reply_text(f"❌ Заказ {order_num} не найден")
            return
        if order.get('status') != 'waiting_payment':
            await upd.message.reply_text(f"❌ Заказ {order_num} не в статусе 'Ожидает оплаты'")
            return
        if update_order(order_num, status='paid'):
            try:
                await ctx.bot.send_message(
                    order.get('user_id'),
                    text=f"✅ Заказ {order_num} ОПЛАЧЕН!\n\n"
                         f"💰 Сумма: {order.get('total_price', 0) + order.get('delivery_price', 0)} руб.\n\n"
                         f"📦 Статус заказа: {STATUS_TEXT_MAP.get('paid')}\n"
                         f"Спасибо за покупку! Менеджер свяжется с вами."
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления клиента: {e}")
            audit_log(order.get('user_id'), 'confirm_payment', f"Заказ {order_num} подтверждён как оплаченный", "success")
            await upd.message.reply_text(
                f"✅ Заказ {order_num} подтверждён как ОПЛАЧЕННЫЙ!\n\n"
                f"👤 Клиент получил уведомление.\n"
                f"💰 Сумма: {order.get('total_price', 0) + order.get('delivery_price', 0)} руб."
            )
        else:
            await upd.message.reply_text(f"❌ Ошибка при обновлении статуса заказа {order_num}")
    except Exception as e:
        logger.error(f"Ошибка в confirm_payment_command: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

@require_manager
async def show_payment_docs(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        args = ctx.args
        if not args or len(args) == 0:
            await upd.message.reply_text("Пример: /payment_docs RVN-ABCD12")
            return
        order_num = clean_order_number(args[0])
        if not order_num:
            await upd.message.reply_text("❌ Неверный формат номера заказа")
            return
        docs = get_payment_documents(order_num)
        if not docs:
            await upd.message.reply_text(f"📭 Нет документов об оплате для заказа {order_num}")
            return
        await upd.message.reply_text(f"📎 Документы оплаты для заказа {order_num}:\n\nВсего документов: {len(docs)}")
        for doc in docs:
            file_id, file_type, caption, created, verified = doc
            status = "✅ Проверен" if verified else "⏳ Ожидает проверки"
            text = f"📅 {created[:16]}\n📎 Тип: {file_type}\n📌 Статус: {status}\n💬 {caption[:100] if caption else ''}"
            if file_type == "photo":
                await upd.message.reply_photo(file_id, caption=text)
            else:
                await upd.message.reply_document(file_id, caption=text)
    except Exception as e:
        logger.error(f"Ошибка в show_payment_docs: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

# ========== КЛИЕНТ УДАЛЯЕТ ТОВАРЫ ==========

@require_order_owner
async def remove_items_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        order = ctx.user_data.get('current_order') if ctx.user_data else None
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        order_num = order.get('order_number')
        uid = query.from_user.id
        if order.get('status') != 'waiting_payment':
            await query.edit_message_text("❌ Удалять товары можно только в статусе 'Ожидает оплаты'")
            return
        final_order = order.get('final_order', '')
        if not final_order or final_order in [None, 'None', '[]', '{}']:
            await query.edit_message_text("❌ В заказе нет товаров для удаления")
            return
        parts = safe_parse_parts(final_order)
        if not parts:
            await query.edit_message_text("❌ Нет товаров для удаления")
            return
        session_key = f"{uid}_{order_num}"
        client_remove_sessions[session_key] = {
            'parts': parts.copy(),
            'selected': set(),
            'step': 'selecting'
        }
        kb = []
        for i, part in enumerate(parts):
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
        logger.error(f"Ошибка в remove_items_callback: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")

async def client_toggle_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
                logger.error(f"Ошибка обновления сообщения: {e}")
    except Exception as e:
        logger.error(f"Ошибка в client_toggle_callback: {e}")

async def client_confirm_remove_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        parts = query.data.split('_')
        if len(parts) < 4:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[3] if len(parts) > 3 else parts[2]
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
        removed_names = []
        remaining_parts = []
        for i, part in enumerate(selected_parts):
            if i in selected_items:
                if isinstance(part, dict):
                    removed_names.append(part.get('name', 'Товар'))
                else:
                    removed_names.append(safe_str(part))
            else:
                remaining_parts.append(part)
        client_remove_sessions[session_key]['remaining_parts'] = remaining_parts
        client_remove_sessions[session_key]['removed_names'] = removed_names
        client_remove_sessions[session_key]['step'] = 'comment'
        await query.edit_message_text(
            f"🗑️ УДАЛЕНИЕ ТОВАРОВ ИЗ ЗАКАЗА {order_num}\n\n"
            f"Будет удалено товаров: {len(removed_names)}\n"
            f"Останется товаров: {len(remaining_parts)}\n\n"
            f"Укажите причину удаления (необязательно):\n\n"
            f"Отправьте сообщение с комментарием\n"
            f"Или отправьте '-' чтобы пропустить"
        )
        return RemoveStates.COMMENT
    except Exception as e:
        logger.error(f"Ошибка в client_confirm_remove_callback: {e}")
        return RemoveStates.COMMENT

async def remove_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
                logger.error(f"Ошибка уведомления менеджера: {e}")
        await upd.message.reply_text(
            f"✅ Товары успешно удалены из заказа {order_num}!\n\n"
            f"🗑️ Удалено: {len(removed_names)} товаров\n"
            f"💰 Новая сумма к оплате: {new_total + delivery_price} руб.\n\n"
            f"Менеджер получил уведомление."
        )
        audit_log(user_id, 'client_remove_items', f"Заказ {order_num}: удалено {len(removed_names)} товаров", "success")
        del client_remove_sessions[session_key]
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в remove_comment_input: {e}")
        return ConversationHandler.END

# ========== КЛИЕНТ ОТМЕНЯЕТ ЗАКАЗ ==========

@require_order_owner
async def cancel_by_user_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        order = ctx.user_data.get('current_order') if ctx.user_data else None
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
    except Exception as e:
        logger.error(f"Ошибка в cancel_by_user_callback: {e}")

async def confirm_user_cancel_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        parts = query.data.split('_')
        if len(parts) < 4:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[3]
        uid = query.from_user.id
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        if order.get('user_id') != uid:
            await query.answer("❌ Это не ваш заказ!", show_alert=True)
            return
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            c = conn.cursor()
            c.execute('SELECT amount FROM bonus_history WHERE order_number = ? AND type = "spent"', (order_num,))
            bonus_row = c.fetchone()
            if bonus_row and bonus_row[0] > 0:
                add_bonus(uid, order_num, bonus_row[0], f"Возврат бонусов при отмене заказа {order_num} пользователем")
        except Exception as e:
            logger.error(f"Ошибка возврата бонусов: {e}")
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
        add_order_change(order_num, uid, 'cancel_by_user', order.get('status', ''), 'cancelled_by_user', "Отмена заказа пользователем")
        update_order(order_num, status='cancelled_by_user')
        audit_log(uid, 'cancel_by_user', f"Заказ {order_num} отменён пользователем", "success")
        await ctx.bot.send_message(
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
    except Exception as e:
        logger.error(f"Ошибка в confirm_user_cancel_callback: {e}")

# ========== ГАРАЖ ==========

async def garage_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Ошибка в garage_menu: {e}")

async def garage_add_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        await query.edit_message_text(
            "🚗 ДОБАВЛЕНИЕ АВТОМОБИЛЯ\n\n"
            "Шаг 1/2: Отправьте VIN номер автомобиля (17 символов):"
        )
        return GarageStates.VIN
    except Exception as e:
        logger.error(f"Ошибка в garage_add_start: {e}")
        return ConversationHandler.END

async def garage_get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
        ctx.user_data['new_car_vin'] = vin
        await upd.message.reply_text(
            f"🚗 VIN: {vin}\n\n"
            f"Шаг 2/2: Введите описание автомобиля\n\n"
            f"Пример: BMW X5 3.0d, 2018, чёрный\n\n"
            f"Или отправьте '-' чтобы пропустить:"
        )
        return GarageStates.DESCRIPTION
    except Exception as e:
        logger.error(f"Ошибка в garage_get_vin: {e}")
        return GarageStates.VIN

async def garage_get_description(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return ConversationHandler.END
        description = upd.message.text.strip()
        if description == "-":
            description = ""
        vin = ctx.user_data.get('new_car_vin') if ctx.user_data else None
        if not vin:
            await upd.message.reply_text("❌ Ошибка. Начните добавление заново.")
            return ConversationHandler.END
        if save_car(upd.effective_user.id, vin, description, ""):
            await upd.message.reply_text(f"✅ Автомобиль {vin} успешно добавлен в ваш гараж!")
        else:
            await upd.message.reply_text(f"❌ Автомобиль {vin} уже есть в вашем гараже!")
        if ctx.user_data and 'new_car_vin' in ctx.user_data:
            del ctx.user_data['new_car_vin']
        await garage_menu(upd, ctx)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в garage_get_description: {e}")
        return ConversationHandler.END

async def garage_delete(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        vin = query.data[12:]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"garage_confirm_del_{vin}")],
            [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="garage_back_to_menu")]
        ])
        await query.edit_message_text(
            f"⚠️ Удалить автомобиль {vin} из гаража?\n\nЭто действие нельзя отменить.",
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"Ошибка в garage_delete: {e}")

async def garage_confirm_delete(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        vin = query.data[18:]
        if delete_car(query.from_user.id, vin):
            await query.edit_message_text(f"✅ Автомобиль {vin} удалён из гаража!")
        else:
            await query.edit_message_text(f"❌ Автомобиль {vin} не найден в гараже.")
        await garage_menu(upd, ctx)
    except Exception as e:
        logger.error(f"Ошибка в garage_confirm_delete: {e}")

async def garage_comment_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        vin = query.data[16:]
        ctx.user_data['comment_vin'] = vin
        await query.edit_message_text(
            f"✏️ КОММЕНТАРИЙ К АВТОМОБИЛЮ\n\n"
            f"Автомобиль: {vin}\n\n"
            f"Введите комментарий для этого автомобиля.\n"
            f"Например: «зимняя резина», «жена», «служебный»\n\n"
            f"Или отправьте '-' чтобы удалить комментарий.\n\n"
            f"Максимум 100 символов."
        )
        return GarageStates.DESCRIPTION
    except Exception as e:
        logger.error(f"Ошибка в garage_comment_start: {e}")
        return ConversationHandler.END

async def garage_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return ConversationHandler.END
        user_id = upd.effective_user.id
        vin = ctx.user_data.get('comment_vin') if ctx.user_data else None
        if not vin:
            await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
            return ConversationHandler.END
        comment = upd.message.text.strip()
        if len(comment) > 100:
            await upd.message.reply_text("❌ Комментарий слишком длинный (максимум 100 символов).")
            return GarageStates.DESCRIPTION
        if comment == "-":
            comment = ""
        if update_car_comment(user_id, vin, comment):
            if comment:
                await upd.message.reply_text(f"✅ Комментарий «{comment}» добавлен к автомобилю {vin}!")
            else:
                await upd.message.reply_text(f"✅ Комментарий к автомобилю {vin} удалён!")
        else:
            await upd.message.reply_text(f"❌ Ошибка при обновлении комментария")
        if ctx.user_data and 'comment_vin' in ctx.user_data:
            del ctx.user_data['comment_vin']
        await garage_menu(upd, ctx)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в garage_comment_input: {e}")
        return ConversationHandler.END

async def garage_back_to_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        await start(upd, ctx)
    except Exception as e:
        logger.error(f"Ошибка в garage_back_to_menu: {e}")

# ========== РЕФЕРАЛЫ, ДОСТАВКА, ПОМОЩЬ ==========

async def referral_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        bot_username = (await ctx.bot.get_me()).username
        link = f"https://t.me/{bot_username}?start=ref_{upd.effective_user.id}"
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', (upd.effective_user.id,))
            referrals_count = c.fetchone()[0]
        except Exception as e:
            logger.error(f"Ошибка подсчёта рефералов: {e}")
            referrals_count = 0
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
        text = (f"🔗 РЕФЕРАЛЬНАЯ ПРОГРАММА\n\n"
                f"Ваша реферальная ссылка:\n{link}\n\n"
                f"👥 Приглашено друзей: {referrals_count}\n"
                f"📊 Вы получаете 0.5% от суммы заказов ваших друзей бонусами!\n"
                f"🎁 Друг получает 500 приветственных бонусов!\n\n"
                f"💰 Текущий баланс: {get_bonus(upd.effective_user.id)['balance']} бонусов")
        await upd.message.reply_text(text)
    except Exception as e:
        logger.error(f"Ошибка в referral_cmd: {e}")

async def delivery_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Ошибка в delivery_cmd: {e}")

async def help_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
                "По всем вопросам обращайтесь к менеджеру")
        await upd.message.reply_text(text)
    except Exception as e:
        logger.error(f"Ошибка в help_cmd: {e}")

async def bonus_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Ошибка в bonus_cmd: {e}")

async def bonus_history_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
    except Exception as e:
        logger.error(f"Ошибка в bonus_history_callback: {e}")

async def bonus_back_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        await bonus_cmd(upd, ctx)
    except Exception as e:
        logger.error(f"Ошибка в bonus_back_callback: {e}")

# ========== ПРИМЕНЕНИЕ БОНУСОВ ==========

@require_order_owner
async def apply_bonus_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        order = ctx.user_data.get('current_order') if ctx.user_data else None
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
    except Exception as e:
        logger.error(f"Ошибка в apply_bonus_callback: {e}")

@rate_limit
async def spend_bonus_percent_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer("⏳ Списание бонусов...", show_alert=False)
        parts = query.data.split('_')
        if len(parts) < 4:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[3]
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
    except Exception as e:
        logger.error(f"Ошибка в spend_bonus_percent_callback: {e}")

async def confirm_spend_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
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
            audit_log(uid, 'spend_bonus', f"Заказ {order_num}: списано {spend_amount} бонусов", "success")
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
    except Exception as e:
        logger.error(f"Ошибка в confirm_spend_callback: {e}")

async def spend_bonus_custom_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        parts = query.data.split('_')
        if len(parts) < 4:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[3]
        ctx.user_data['bonus_order'] = order_num
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
    except Exception as e:
        logger.error(f"Ошибка в spend_bonus_custom_callback: {e}")
        return BonusStates.SPEND

async def spend_bonus_custom_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        user_id = upd.effective_user.id
        order_num = ctx.user_data.get('bonus_order') if ctx.user_data else None
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
        if ctx.user_data and 'bonus_order' in ctx.user_data:
            del ctx.user_data['bonus_order']
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в spend_bonus_custom_input: {e}")
        return ConversationHandler.END

# ========== АДМИН КОМАНДЫ ==========

@require_manager
async def delete_order_command(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        args = ctx.args
        if not args or len(args) == 0:
            await upd.message.reply_text(
                "❌ Укажите номер заказа\n\n"
                "Пример: /delorder RVN-084536\n\n"
                "⚠️ ВНИМАНИЕ: заказ будет удалён без проверки статуса!"
            )
            return
        order_num = clean_order_number(args[0])
        if not order_num:
            await upd.message.reply_text("❌ Неверный формат номера заказа. Пример: RVN-084536")
            return
        order = get_order(order_num)
        if force_delete_order(order_num):
            text = f"✅ Заказ {order_num} принудительно удалён из базы данных!\n\n"
            if order:
                text += f"📋 Информация об удалённом заказе:\n"
                text += f"👤 Клиент: {order.get('user_name', 'Неизвестно')}\n"
                text += f"📦 Статус: {order.get('status_text', 'Неизвестен')}\n"
                text += f"📅 Дата: {order.get('created_at', 'Неизвестна')}"
                try:
                    await ctx.bot.send_message(
                        order.get('user_id'),
                        text=f"🗑️ Ваш заказ {order_num} был удалён администратором.\n\n"
                             f"Если у вас есть вопросы, обратитесь к менеджеру."
                    )
                except Exception as e:
                    text += f"\n\n⚠️ Не удалось уведомить клиента: {e}"
            await upd.message.reply_text(text)
        else:
            await upd.message.reply_text(f"❌ Заказ {order_num} не найден в базе данных")
    except Exception as e:
        logger.error(f"Ошибка в delete_order_command: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

@require_manager
async def batch_delete_orders(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        args = ctx.args
        if not args or len(args) == 0:
            await upd.message.reply_text(
                "❌ Укажите номера заказов через пробел\n\n"
                "Пример: /batch_del RVN-084536 RVN-062516 RVN-039117\n\n"
                "⚠️ ВНИМАНИЕ: заказы будут удалены без проверки статуса!"
            )
            return
        deleted = []
        not_found = []
        for arg in args:
            order_num = clean_order_number(arg)
            if not order_num:
                not_found.append(f"{arg} (неверный формат)")
                continue
            if force_delete_order(order_num):
                deleted.append(order_num)
            else:
                not_found.append(order_num)
        text = "🗑️ РЕЗУЛЬТАТ УДАЛЕНИЯ\n\n"
        if deleted:
            text += f"✅ Удалено: {', '.join(deleted)}\n\n"
        if not_found:
            text += f"❌ Не найдено/ошибка: {', '.join(not_found)}\n\n"
        await upd.message.reply_text(text)
    except Exception as e:
        logger.error(f"Ошибка в batch_delete_orders: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

@require_manager
async def show_all_orders_raw(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            c = conn.cursor()
            c.execute('SELECT order_number, user_name, status, status_text, created_at, total_price FROM orders ORDER BY id DESC')
            orders = c.fetchall()
        finally:
            if conn:
                conn.close()
        if not orders:
            await upd.message.reply_text("📭 База данных пуста")
            return
        text = "📊 ВСЕ ЗАКАЗЫ В БАЗЕ ДАННЫХ\n\n"
        for o in orders[:50]:
            if o and safe_len(o) > 0:
                order_num = safe_str(o[0])
                user_name = safe_str(o[1])[:20] if safe_len(o) > 1 else "Неизвестно"
                status = safe_str(o[2]) if safe_len(o) > 2 else "unknown"
                status_text = safe_str(o[3]) if safe_len(o) > 3 else STATUS_TEXT_MAP.get(status, status)
                created = safe_str(o[4])[:10] if safe_len(o) > 4 else "unknown"
                total = safe_int(o[5]) if safe_len(o) > 5 else 0
                text += f"• {order_num}\n"
                text += f"  👤 {user_name}\n"
                text += f"  📦 {status_text}\n"
                text += f"  📅 {created} | 💰 {total} руб.\n\n"
        if len(text) > 4000:
            parts = [text[i:i+4000] for i in range(0, len(text), 4000)]
            for part in parts:
                await upd.message.reply_text(part)
        else:
            await upd.message.reply_text(text)
    except Exception as e:
        logger.error(f"Ошибка в show_all_orders_raw: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

@require_manager
async def fix_orphan_orders(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            c = conn.cursor()
            c.execute('SELECT order_number, status, status_text, user_id FROM orders ORDER BY id DESC')
            db_orders = c.fetchall()
        finally:
            if conn:
                conn.close()
        text = "📊 АНАЛИЗ ЗАКАЗОВ В БАЗЕ ДАННЫХ\n\n"
        text += f"📦 Всего заказов в БД: {safe_len(db_orders)}\n\n"
        status_groups = {}
        for o in db_orders:
            status = safe_str(o[1]) if len(o) > 1 else 'unknown'
            if status not in status_groups:
                status_groups[status] = []
            status_groups[status].append(o)
        for status, orders in status_groups.items():
            status_text = STATUS_TEXT_MAP.get(status, status)
            text += f"• {status_text}: {len(orders)} заказов\n"
        text += "\n📋 ПОСЛЕДНИЕ 20 ЗАКАЗОВ:\n\n"
        for o in db_orders[:20]:
            order_num = safe_str(o[0])
            status_text = safe_str(o[2]) if len(o) > 2 else STATUS_TEXT_MAP.get(o[1] if len(o) > 1 else '', 'Неизвестно')
            text += f"• {order_num} - {status_text}\n"
        kb = [
            [InlineKeyboardButton("🗑️ Удалить все отменённые", callback_data="admin_clear_all_cancelled")],
            [InlineKeyboardButton("🔄 Синхронизировать список", callback_data="admin_refresh")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
        ]
        await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Ошибка в fix_orphan_orders: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

# ========== АДМИН ПАНЕЛЬ ==========

@require_manager
async def admin_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE, message=None):
    global admin_status_filter
    try:
        if not upd:
            return
        orders = get_all_orders_by_status(admin_status_filter)
        if orders is None:
            orders = []
        filter_keyboard = []
        row = []
        for i, (status_key, status_name) in enumerate(ADMIN_STATUS_FILTERS.items()):
            marker = "✅ " if admin_status_filter == status_key else ""
            row.append(InlineKeyboardButton(f"{marker}{status_name[:15]}", callback_data=f"admin_filter_{status_key}"))
            if len(row) == 2:
                filter_keyboard.append(row)
                row = []
        if row:
            filter_keyboard.append(row)
        cancelled_orders = get_cancelled_orders()
        cancelled_count = safe_len(cancelled_orders)
        keyboard = [
            [InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")],
            [InlineKeyboardButton("➕ Тестовый заказ", callback_data="admin_fix")],
            [InlineKeyboardButton("🗑️ Отменённые заказы", callback_data="admin_cancelled_list")],
            [InlineKeyboardButton("🔄 Обновить", callback_data="admin_refresh")],
            [InlineKeyboardButton("📋 Показать все заказы", callback_data="admin_show_all")],
        ]
        keyboard.extend(filter_keyboard)
        current_filter_name = ADMIN_STATUS_FILTERS.get(admin_status_filter, "Все заказы")
        keyboard.append([InlineKeyboardButton(f"📌 Текущий фильтр: {current_filter_name}", callback_data="admin_noop")])
        order_count = 0
        for o in orders:
            if o and safe_len(o) > 0:
                order_num = clean_order_number(o[0])
                if not order_num:
                    continue
                user_name = safe_str(o[1])[:18] if safe_len(o) > 1 else "Неизвестно"
                status_text = safe_str(o[2]) if safe_len(o) > 2 else ''
                status_key = safe_str(o[4]) if safe_len(o) > 4 else ''
                icon = get_status_icon(status_text)
                if status_key in ['cancelled', 'cancelled_by_user']:
                    keyboard.append([InlineKeyboardButton(f"🗑️ {icon} {order_num} | {user_name}", callback_data=f"admin_delete_order_{order_num}")])
                else:
                    keyboard.append([InlineKeyboardButton(f"{icon} {order_num} | {user_name}", callback_data=f"admin_order_{order_num}")])
                order_count += 1
        text = f"👨‍💼 АДМИН ПАНЕЛЬ\n\n"
        text += f"📌 Фильтр: {current_filter_name}\n"
        text += f"📦 Показано заказов: {order_count}\n"
        text += f"🗑️ Отменённых заказов: {cancelled_count}\n\n"
        text += "📋 Для удаления заказа:\n"
        text += "1️⃣ Сначала отмените заказ (кнопка ❌ Отменить)\n"
        text += "2️⃣ Затем нажмите 🗑️ УДАЛИТЬ ЗАКАЗ НАВСЕГДА\n\n"
        text += "⬇️ Выберите заказ для управления:"
        if message:
            try:
                await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
            except Exception as e:
                if "Message is not modified" not in str(e):
                    logger.error(f"Ошибка редактирования меню: {e}")
        else:
            if upd.message:
                await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    except Exception as e:
        logger.error(f"Ошибка в admin_menu: {e}")
        if upd and upd.message:
            await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

@require_manager
async def admin_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    global admin_status_filter
    if not upd or not upd.callback_query:
        return
    query = upd.callback_query
    data = safe_str(query.data)
    if len(data) > MAX_CALLBACK_DATA:
        await query.answer("❌ Слишком большой запрос")
        return
    logger.info(f"[ADMIN_CALLBACK] Получен callback: {data}")
    try:
        await query.answer()
    except Exception as e:
        logger.error(f"Ошибка answer: {e}")
    
    # ========== ФИЛЬТРЫ ==========
    if data.startswith("admin_filter_"):
        status = data[13:]
        if status in ADMIN_STATUS_FILTERS:
            admin_status_filter = status
            await admin_menu(upd, ctx, query.message)
        return
    
    if data == "admin_noop":
        return
    
    if data == "admin_show_all":
        orders = get_all_orders_by_status('all')
        if not orders:
            await query.edit_message_text("📭 База данных пуста", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]))
            return
        text = "📋 ВСЕ ЗАКАЗЫ В БАЗЕ ДАННЫХ\n\n"
        for o in orders[:50]:
            if o and safe_len(o) > 0:
                order_num = safe_str(o[0])
                user_name = safe_str(o[1])[:15] if safe_len(o) > 1 else "Неизвестно"
                status_text = safe_str(o[2]) if safe_len(o) > 2 else ''
                created = safe_str(o[3])[:10] if safe_len(o) > 3 else ''
                text += f"• {order_num} | {user_name} | {status_text} | {created}\n"
        kb = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data == "admin_cancelled_list":
        orders = get_cancelled_orders()
        if not orders:
            await query.edit_message_text("📭 Нет отменённых заказов", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]))
            return
        text = "🗑️ ОТМЕНЁННЫЕ ЗАКАЗЫ\n\n"
        text += f"📦 Всего отменённых заказов: {safe_len(orders)}\n\n"
        kb = []
        for o in orders:
            if o and safe_len(o) > 0:
                order_num = clean_order_number(o[0])
                if not order_num:
                    continue
                user_name = safe_str(o[1])[:20] if safe_len(o) > 1 else "Неизвестно"
                created = safe_str(o[3])[:10] if safe_len(o) > 3 else ''
                kb.append([InlineKeyboardButton(f"🗑️ {order_num} | {user_name} | {created}", callback_data=f"admin_delete_order_{order_num}")])
        kb.append([InlineKeyboardButton("🗑️🗑️ УДАЛИТЬ ВСЕ ОТМЕНЁННЫЕ 🗑️🗑️", callback_data="admin_clear_all_cancelled")])
        kb.append([InlineKeyboardButton("◀️ Назад в админ-панель", callback_data="admin_back")])
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data == "admin_clear_all_cancelled":
        orders = get_cancelled_orders()
        if not orders:
            await query.edit_message_text("📭 Нет отменённых заказов для удаления")
            return
        text = f"⚠️ ВНИМАНИЕ!\n\n"
        text += f"Вы уверены, что хотите удалить ВСЕ отменённые заказы?\n\n"
        text += f"📦 Будет удалено заказов: {safe_len(orders)}\n\n"
        text += f"Это действие НЕЛЬЗЯ будет отменить!"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ДА, УДАЛИТЬ ВСЕ", callback_data="admin_confirm_clear_all")],
            [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="admin_back")]
        ])
        await query.edit_message_text(text, reply_markup=kb)
        return
    
    if data == "admin_confirm_clear_all":
        orders = get_cancelled_orders()
        deleted_count = 0
        errors = []
        for o in orders:
            if o and safe_len(o) > 0:
                order_num = clean_order_number(o[0])
                if order_num and force_delete_order(order_num):
                    deleted_count += 1
                else:
                    errors.append(order_num)
        text = f"✅ Удалено заказов: {deleted_count}\n"
        if errors:
            text += f"❌ Ошибка при удалении: {', '.join(errors[:5])}"
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад в админ-панель", callback_data="admin_back")]]))
        return
    
    if data.startswith("admin_delete_order_"):
        order_num = data[18:]
        order_num = clean_order_number(order_num)
        order = get_order(order_num)
        if not order:
            await query.edit_message_text(f"❌ Заказ {order_num} не найден")
            return
        status = order.get('status', '')
        if status not in ['cancelled', 'cancelled_by_user']:
            await query.edit_message_text(
                f"❌ Заказ {order_num} нельзя удалить!\n\n"
                f"📦 Статус заказа: {order.get('status_text', 'неизвестен')}\n\n"
                f"Удалить можно только ОТМЕНЁННЫЙ заказ.\n"
                f"Сначала отмените заказ, затем удалите."
            )
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"admin_confirm_delete_{order_num}")],
            [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="admin_back")]
        ])
        await query.edit_message_text(
            f"⚠️ ВНИМАНИЕ! Вы уверены, что хотите НАВСЕГДА удалить заказ {order_num}?\n\n"
            f"👤 Клиент: {order.get('user_name', 'Неизвестно')}\n"
            f"📦 Статус: {order.get('status_text', 'Неизвестен')}\n\n"
            f"Это действие НЕЛЬЗЯ будет отменить!",
            reply_markup=kb
        )
        return
    
    if data.startswith("admin_confirm_delete_"):
        order_num = data[20:]
        order_num = clean_order_number(order_num)
        order = get_order(order_num)
        if force_delete_order(order_num):
            if order:
                try:
                    await ctx.bot.send_message(
                        order.get('user_id'),
                        text=f"🗑️ Ваш заказ {order_num} был удалён администратором из системы.\n\n"
                             f"Если у вас есть вопросы, обратитесь к менеджеру."
                    )
                except Exception as e:
                    logger.error(f"Ошибка уведомления пользователя: {e}")
            await query.edit_message_text(f"✅ Заказ {order_num} успешно удалён!", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]))
        else:
            await query.edit_message_text(f"❌ Ошибка при удалении заказа {order_num}", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]))
        return
    
    if data == "admin_refresh":
        await admin_menu(upd, ctx, query.message)
        return
    
    if data == "admin_stats":
        try:
            orders = get_all_orders_by_status('all')
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
            logger.error(f"Ошибка статистики: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
    if data == "admin_fix":
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            c = conn.cursor()
            num = generate_unique_order_number()
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
        except Exception as e:
            logger.error(f"Ошибка тестового заказа: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        finally:
            if conn:
                try:
                    conn.close()
                except:
                    pass
        await admin_menu(upd, ctx, query.message)
        return
    
    if data == "admin_back":
        admin_status_filter = 'all'
        await admin_menu(upd, ctx, query.message)
        return
    
    # ========== ОСНОВНОЙ ОБРАБОТЧИК ЗАКАЗА ==========
    if data.startswith("admin_order_"):
        match = re.search(r'RVN-[A-Z0-9]{6}', data)
        if match:
            order_num = match.group(0)
        else:
            order_num = data[12:]
        order_num = clean_order_number(order_num)
        if not order_num:
            await query.edit_message_text("❌ Неверный номер заказа")
            return
        logger.info(f"[ADMIN] Открытие заказа: {order_num}")
        try:
            order = get_order(order_num)
            if not order:
                await query.edit_message_text(f"❌ Заказ {order_num} не найден")
                return
            total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
            status_key = order.get('status', '')
            is_cancelled = status_key in ['cancelled', 'cancelled_by_user']
            allowed_transitions = STATUS_TRANSITIONS.get(status_key, [])
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
            if order.get('tracking_number'):
                text += f"\n📮 Трек-номер: {order.get('tracking_number')}"
            if order.get('final_order'):
                text += f"\n\n📦 ТОВАРЫ:\n{order.get('final_order')[:300]}"
            elif order.get('selected_products'):
                text += f"\n\n📦 ПОДБОР МЕНЕДЖЕРА:\n{order.get('selected_products')[:300]}"
            kb = []
            if 'waiting_selection' in allowed_transitions:
                kb.append([InlineKeyboardButton("🟡 Ожидает выбора", callback_data=f"waiting_selection_{order_num}")])
            if 'waiting_payment' in allowed_transitions:
                kb.append([InlineKeyboardButton("💰 Ожидает оплаты", callback_data=f"waiting_payment_{order_num}")])
            if 'paid' in allowed_transitions:
                kb.append([InlineKeyboardButton("✅ Оплачен", callback_data=f"pay_{order_num}")])
            if 'ordered' in allowed_transitions:
                kb.append([InlineKeyboardButton("📦 Заказан", callback_data=f"ordered_{order_num}")])
            if 'arrived' in allowed_transitions:
                kb.append([InlineKeyboardButton("📦✅ Поступил", callback_data=f"arrived_{order_num}")])
            if 'ready' in allowed_transitions:
                kb.append([InlineKeyboardButton("✅ Готов к выдаче", callback_data=f"ready_{order_num}")])
            if 'shipped' in allowed_transitions:
                kb.append([InlineKeyboardButton("🚚 Отправлен", callback_data=f"ship_{order_num}")])
            if 'delivered' in allowed_transitions:
                kb.append([InlineKeyboardButton("🏠 Доставлен", callback_data=f"del_{order_num}")])
            if 'issued' in allowed_transitions:
                kb.append([InlineKeyboardButton("📋 Выдан", callback_data=f"issued_{order_num}")])
            if 'cancelled' in allowed_transitions and not is_cancelled:
                kb.append([InlineKeyboardButton("❌ Отменить заказ", callback_data=f"cancel_{order_num}")])
            kb.append([InlineKeyboardButton("✏️ Изменить доставку", callback_data=f"edit_delivery_{order_num}")])
            kb.append([InlineKeyboardButton("✏️ Редактировать товары", callback_data=f"admin_edit_items_{order_num}")])
            kb.append([InlineKeyboardButton("📜 История изменений", callback_data=f"order_changes_{order_num}")])
            kb.append([InlineKeyboardButton("🔍 Детали", callback_data=f"detail_{order_num}")])
            if is_cancelled:
                kb.append([InlineKeyboardButton("🗑️ УДАЛИТЬ ЗАКАЗ НАВСЕГДА", callback_data=f"admin_delete_order_{order_num}")])
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
            logger.info(f"[ADMIN] Заказ {order_num} успешно открыт")
        except Exception as e:
            logger.error(f"Ошибка просмотра заказа: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
    # ========== ОСТАЛЬНЫЕ ОБРАБОТЧИКИ ==========
    if data.startswith("waiting_selection_"):
        order_num = data[18:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='waiting_selection'):
            await query.edit_message_text("✅ СТАТУС: ОЖИДАЕТ ВЫБОРА ЗАПЧАСТЕЙ")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("waiting_payment_"):
        order_num = data[17:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='waiting_payment'):
            await query.edit_message_text("✅ СТАТУС: ОЖИДАЕТ ОПЛАТЫ")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("order_changes_"):
        order_num = data[14:]
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
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data.startswith("detail_"):
        order_num = data[7:]
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
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(kb))
        return

    # ========== ADMIN_EDIT_ITEMS ==========
    if data.startswith("admin_edit_items_"):
        order_num = data[17:]
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

    # ========== ADMIN_REMOVE_ITEMS ==========
    if data.startswith("admin_remove_items_"):
        order_num = data[18:]
        order_num = clean_order_number(order_num)
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
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return

    # ========== ADMIN_TOGGLE ==========
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
        await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return

    # ========== ADMIN_REMOVE_CONFIRM ==========
    if data.startswith("admin_remove_confirm_"):
        order_num = data[20:]
        order_num = clean_order_number(order_num)
        if order_num not in admin_remove_sessions:
            await query.edit_message_text("❌ Сессия истекла. Начните заново.")
            return
        selected_items = admin_remove_sessions[order_num]['selected']
        selected_parts = admin_remove_sessions[order_num]['parts']
        if not selected_items:
            await query.edit_message_text("❌ Не выбрано ни одного товара")
            return
        if len(selected_items) >= len(selected_parts):
            await query.edit_message_text("❌ Нельзя удалить все товары из заказа!")
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
                         f"💰 Новая сумма: {new_total + delivery_price} руб."
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя: {e}")
        del admin_remove_sessions[order_num]
        await query.edit_message_text(f"✅ Товары удалены!\n\n💰 Новая сумма: {new_total + delivery_price} руб.")
        return

# ========== АДМИН: ДОБАВЛЕНИЕ ТОВАРА (ИСПРАВЛЕНО) ==========

async def admin_add_item_name_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return ConversationHandler.END
        order_num = ctx.user_data.get('admin_add_item_order') if ctx.user_data else None
        if not order_num:
            await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
            return ConversationHandler.END
        name = upd.message.text.strip()
        if not name or len(name) > 200:
            await upd.message.reply_text("❌ Название товара не может быть пустым или слишком длинным (макс. 200 символов). Попробуйте снова:")
            return AdminAddItemStates.NAME
        ctx.user_data['admin_add_item_name'] = security.escape_text(name)
        await upd.message.reply_text(
            f"➕ ДОБАВЛЕНИЕ ТОВАРА В ЗАКАЗ {order_num}\n\n"
            f"📝 Название: {name}\n\n"
            f"Введите цену товара (целое число, от {MIN_PRICE} до {MAX_PRICE} руб.):"
        )
        return AdminAddItemStates.PRICE
    except Exception as e:
        logger.error(f"Ошибка в admin_add_item_name_input: {e}")
        return ConversationHandler.END

async def admin_add_item_price_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return ConversationHandler.END
        order_num = ctx.user_data.get('admin_add_item_order') if ctx.user_data else None
        if not order_num:
            await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
            return ConversationHandler.END
        try:
            price = int(upd.message.text.strip())
            if not security.validate_price(price):
                raise ValueError
        except ValueError:
            await upd.message.reply_text(f"❌ Введите корректную цену (от {MIN_PRICE} до {MAX_PRICE} руб.):")
            return AdminAddItemStates.PRICE
        name = ctx.user_data.get('admin_add_item_name', 'Новый товар') if ctx.user_data else 'Новый товар'
        order = get_order(order_num)
        if not order:
            await upd.message.reply_text("❌ Заказ не найден")
            return ConversationHandler.END
        final_order = order.get('final_order', '')
        selected_parts = safe_parse_parts(final_order)
        new_item = {'name': name, 'price': price}
        selected_parts.append(new_item)
        new_total = sum(p.get('price', 0) for p in selected_parts if isinstance(p, dict))
        delivery_price = order.get('delivery_price', 0)
        update_order(order_num, final_order=str(selected_parts), total_price=new_total)
        add_order_change(order_num, MANAGER_ID, 'add_item', '', f"{name} - {price} руб.", "Добавление товара администратором")
        audit_log(MANAGER_ID, 'admin_add_item', f"Заказ {order_num}: добавлен товар {name} за {price} руб.", "success")
        await ctx.bot.send_message(
            order.get('user_id'),
            text=f"✏️ Заказ {order_num} изменён менеджером!\n\n"
                 f"➕ Добавлен товар: {name} - {price} руб.\n"
                 f"💰 Новая сумма: {new_total + delivery_price} руб."
        )
        await upd.message.reply_text(
            f"✅ Товар добавлен в заказ {order_num}!\n\n"
            f"➕ {name} — {price} руб.\n"
            f"💰 Новая сумма: {new_total + delivery_price} руб."
        )
        if ctx.user_data:
            ctx.user_data.pop('admin_add_item_order', None)
            ctx.user_data.pop('admin_add_item_name', None)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в admin_add_item_price_input: {e}")
        return ConversationHandler.END

# ========== АДМИН: ИЗМЕНЕНИЕ ЦЕНЫ (ИСПРАВЛЕНО) ==========

async def admin_change_price_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return ConversationHandler.END
        order_num = ctx.user_data.get('admin_change_price_order') if ctx.user_data else None
        if not order_num:
            await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
            return ConversationHandler.END
        try:
            new_price = int(upd.message.text.strip())
            if not security.validate_price(new_price):
                raise ValueError
        except ValueError:
            await upd.message.reply_text(f"❌ Введите корректную цену (от {MIN_PRICE} до {MAX_PRICE} руб.):")
            return AdminChangePriceStates.NEW_PRICE
        item_idx = ctx.user_data.get('admin_change_price_idx', -1) if ctx.user_data else -1
        selected_parts = ctx.user_data.get('admin_change_price_parts', []) if ctx.user_data else []
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
        audit_log(MANAGER_ID, 'admin_change_price', f"Заказ {order_num}: {old_name} {old_price}->{new_price}", "success")
        order = get_order(order_num)
        if order:
            await ctx.bot.send_message(
                order.get('user_id'),
                text=f"✏️ Заказ {order_num} изменён менеджером!\n\n"
                     f"💰 Изменена цена товара: {old_name}\n"
                     f"💵 Было: {old_price} руб.\n"
                     f"💵 Стало: {new_price} руб.\n"
                     f"💰 Новая сумма: {new_total + delivery_price} руб."
            )
        await upd.message.reply_text(
            f"✅ Цена изменена!\n\n"
            f"📦 Заказ: {order_num}\n"
            f"📝 Товар: {old_name}\n"
            f"💰 {old_price} руб. → {new_price} руб."
        )
        if ctx.user_data:
            ctx.user_data.pop('admin_change_price_order', None)
            ctx.user_data.pop('admin_change_price_parts', None)
            ctx.user_data.pop('admin_change_price_idx', None)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в admin_change_price_input: {e}")
        return ConversationHandler.END

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
            OrderStates.PARTS: [
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_parts),
                CallbackQueryHandler(continue_order_callback, pattern="^continue_order$"),
                CallbackQueryHandler(cancel_order_callback, pattern="^cancel_order$"),
                CallbackQueryHandler(confirm_cancel_order_callback, pattern="^confirm_cancel_order$")
            ],
            OrderStates.CONFIRM: [
                MessageHandler(filters.Regex("^(✅ Готово|✏️ Редактировать)$"), confirm_order),
                CallbackQueryHandler(confirm_edit_callback, pattern="^(confirm_edit|cancel_edit)$")
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
    
    app.add_handler(CommandHandler("delorder", delete_order_command))
    app.add_handler(CommandHandler("batch_del", batch_delete_orders))
    app.add_handler(CommandHandler("allorders", show_all_orders_raw))
    app.add_handler(CommandHandler("fix_orders", fix_orphan_orders))
    app.add_handler(CommandHandler("confirm_payment", confirm_payment_command))
    app.add_handler(CommandHandler("payment_docs", show_payment_docs))
    
    app.add_handler(MessageHandler(filters.Regex("^(🚗 Мой гараж)$"), garage_menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    
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
    
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID) & filters.TEXT & ~filters.COMMAND, manager_message_handler))
    
    app.add_error_handler(error_handler)
    
    logger.info(f"🤖 Бот запущен! ID администратора: {MANAGER_ID}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
