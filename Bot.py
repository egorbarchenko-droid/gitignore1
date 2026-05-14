#!/usr/bin/env python3
# -*- coding: utf-8 -*-

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
from apscheduler.schedulers.background import BackgroundScheduler
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, filters
from telegram.warnings import PTBUserWarning

# --- Настройка логов и предупреждений ---
warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)
logging.basicConfig(level=logging.INFO)
logging.getLogger("httpx").setLevel(logging.WARNING)

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_ID = int(os.environ.get("MANAGER_ID", 804070528))
DELIVERY_BASE = 500

DATA_DIR = os.getenv('DATA_DIR', '/app/data')
DB_PATH = os.path.join(DATA_DIR, 'shop_bot.db')
BACKUP_DIR = os.path.join(DATA_DIR, 'backups')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")

os.makedirs(DATA_DIR, exist_ok=True)
os.makedirs(BACKUP_DIR, exist_ok=True)
# =================================

# Состояния для ConversationHandler
VIN, MILEAGE, STYLE_CITY, STYLE_HIGHWAY, DELIVERY_TYPE, ADDRESS, PHONE, PART_NODE, AXLE, PARTS, CONFIRM = range(11)
# Состояния для гаража
GARAGE_VIN, GARAGE_DESCRIPTION = range(11, 13)
# Состояние для списания бонусов
SPEND_BONUS = 13

# Узлы, для которых нужна информация об оси
AXLE_REQUIRED_NODES = ["🔧 Подвеска", "🛑 Тормозная система", "🛞 Рулевое управление"]

# ========== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==========
def clean_order_number(order_num):
    """Очищает номер заказа от лишних символов"""
    if not order_num:
        return ""
    if order_num.startswith('_'):
        order_num = order_num[1:]
    match = re.search(r'(RVN-\w+)', order_num)
    return match.group(1) if match else order_num

def backup_db():
    """Создание резервной копии базы данных"""
    try:
        if os.path.exists(DB_PATH):
            backup_name = f"shop_bot_backup_{datetime.now().strftime('%Y%m%d_%H%M%S')}.db"
            backup_path = os.path.join(BACKUP_DIR, backup_name)
            shutil.copy2(DB_PATH, backup_path)
            # Удаляем бэкапы старше 7 дней
            for f in os.listdir(BACKUP_DIR):
                f_path = os.path.join(BACKUP_DIR, f)
                if os.path.isfile(f_path) and datetime.now().timestamp() - os.path.getmtime(f_path) > 7 * 24 * 3600:
                    os.remove(f_path)
            print(f"✅ Бэкап создан: {backup_name}")
    except Exception as e:
        print(f"❌ Ошибка бэкапа: {e}")

def safe_int(val, default=0):
    """Безопасное преобразование в целое число"""
    try:
        return int(float(val)) if val else default
    except (ValueError, TypeError):
        return default

def safe_str(val, default=''):
    """Безопасное преобразование в строку"""
    return str(val) if val else default

def wrap_text(text, max_length=25):
    """Перенос длинного текста на новую строку"""
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

def get_monthly_stats(user_id):
    """Получает статистику заказов по месяцам"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT strftime('%Y-%m', created_at) as month,
               COUNT(*) as orders_count,
               SUM(total_price + delivery_price) as total_sum
        FROM orders
        WHERE user_id = ? AND status != 'pending'
        GROUP BY strftime('%Y-%m', created_at)
        ORDER BY month DESC
        LIMIT 12
    ''', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_months_names():
    """Возвращает названия месяцев на русском"""
    months = {
        '01': 'Янв', '02': 'Фев', '03': 'Мар', '04': 'Апр',
        '05': 'Май', '06': 'Июн', '07': 'Июл', '08': 'Авг',
        '09': 'Сен', '10': 'Окт', '11': 'Ноя', '12': 'Дек'
    }
    return months

def format_monthly_stats(stats):
    """Форматирует статистику в виде графика"""
    if not stats:
        return "📭 Нет данных за последние 12 месяцев"
    
    months_ru = get_months_names()
    result = "📊 **СТАТИСТИКА ПО МЕСЯЦАМ**\n\n"
    
    for month, count, total in stats:
        month_name = months_ru.get(month[5:], month[5:])
        year = month[:4]
        
        # График в виде полосок
        bar_length = min(20, int(total / 1000)) if total > 0 else 0
        bar = "█" * bar_length if bar_length > 0 else "▏"
        
        result += f"**{month_name} {year}**\n"
        result += f"   📦 Заказов: {count}\n"
        result += f"   💰 Сумма: {int(total):,} руб.\n"
        result += f"   {bar}\n\n"
    
    return result

def get_bonus_history_grouped(user_id, limit=50):
    """Получает историю бонусов с группировкой по месяцам"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''
        SELECT order_number, amount, type, description, created_at
        FROM bonus_history
        WHERE user_id = ?
        ORDER BY created_at DESC
        LIMIT ?
    ''', (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return rows

def format_bonus_history_grouped(history):
    """Форматирует историю бонусов с группировкой по месяцам"""
    if not history:
        return "📭 История операций пока пуста"
    
    months_ru = get_months_names()
    grouped = {}
    
    for h in history:
        created = h[4]
        if created:
            month_key = created[:7]
            if month_key not in grouped:
                grouped[month_key] = []
            grouped[month_key].append(h)
    
    result = "📜 **ИСТОРИЯ ОПЕРАЦИЙ**\n\n"
    
    for month_key in sorted(grouped.keys(), reverse=True):
        month_name = months_ru.get(month_key[5:], month_key[5:])
        year = month_key[:4]
        result += f"**{month_name} {year}**\n"
        
        month_total_earned = 0
        month_total_spent = 0
        
        for h in grouped[month_key]:
            amount = int(h[1])
            h_type = h[2]
            desc = h[3][:35] if h[3] else ""
            
            if h_type == 'earned':
                icon = "➕"
                month_total_earned += amount
                result += f"   {icon} +{amount} руб. | {desc}\n"
            elif h_type == 'refund':
                icon = "➖"
                month_total_spent += amount
                result += f"   {icon} -{amount} руб. | {desc}\n"
            else:
                icon = "➖"
                month_total_spent += amount
                result += f"   {icon} -{amount} руб. | {desc}\n"
        
        if month_total_earned > 0 or month_total_spent > 0:
            result += f"   ─────────────────\n"
            if month_total_earned > 0:
                result += f"   📈 Итого начислено: +{month_total_earned} руб.\n"
            if month_total_spent > 0:
                result += f"   📉 Итого списано: -{month_total_spent} руб.\n"
        result += "\n"
    
    return result

# ========== ФУНКЦИИ БОНУСОВ ==========
def use_bonus(user_id, order_num, amount, desc):
    """Списание бонусов при оплате заказа"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    # Проверяем, достаточно ли бонусов
    c.execute('SELECT balance FROM bonuses WHERE user_id = ?', (user_id,))
    balance = c.fetchone()
    if balance and balance[0] >= amount:
        c.execute('UPDATE bonuses SET balance = balance - ?, total_spent = total_spent + ? WHERE user_id = ?',
                  (amount, amount, user_id))
        c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (user_id, order_num, amount, 'spent', desc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        return True
    conn.close()
    return False

# ========== БАЗА ДАННЫХ ==========
def init_db():
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
        user_id INTEGER, vin TEXT, description TEXT, created_at TEXT,
        UNIQUE(user_id, vin)
    )''')
    for col in ['phone', 'our_cost', 'tracking_number', 'final_order']:
        try:
            c.execute(f'ALTER TABLE orders ADD COLUMN {col} TEXT')
        except:
            pass
    c.execute('''CREATE TABLE IF NOT EXISTS bonuses (
        user_id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0, total_earned REAL DEFAULT 0,
        total_spent REAL DEFAULT 0, referrer_id INTEGER DEFAULT NULL
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS bonus_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, order_number TEXT, amount REAL,
        type TEXT, description TEXT, created_at TEXT
    )''')
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER, referred_id INTEGER, created_at TEXT
    )''')
    try:
        c.execute('ALTER TABLE bonuses ADD COLUMN total_spent REAL DEFAULT 0')
    except:
        pass
    conn.commit()
    conn.close()
    print(f"✅ База данных: {DB_PATH}")

def save_order(data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
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
    conn.close()
    return num

def update_order(order_number, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    order_number = clean_order_number(order_number)
    for key, val in kwargs.items():
        try:
            c.execute(f"UPDATE orders SET {key} = ? WHERE order_number = ?", (val, order_number))
        except Exception as e:
            print(f"Ошибка обновления {key}: {e}")
    conn.commit()
    conn.close()

def get_order(order_number):
    order_number = clean_order_number(order_number)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('PRAGMA table_info(orders)')
    columns = [col[1] for col in c.fetchall()]
    c.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,))
    row = c.fetchone()
    conn.close()
    if not row:
        return None
    order = {}
    for i, col in enumerate(columns):
        order[col] = row[i] if i < len(row) else None
    for num_field in ['distance', 'delivery_price', 'total_price', 'our_cost', 'user_id']:
        order[num_field] = safe_int(order.get(num_field))
    return order

def get_all_orders():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT order_number, user_name, status_text, created_at FROM orders ORDER BY id DESC')
    rows = c.fetchall()
    conn.close()
    return rows

def get_user_orders(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT order_number, status_text, created_at, total_price, delivery_price, final_order, needed_parts FROM orders WHERE user_id = ? ORDER BY id DESC', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_user_total(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT SUM(total_price) FROM orders WHERE user_id = ? AND status != "pending"', (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] or 0

def get_bonus(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT balance, total_earned, total_spent FROM bonuses WHERE user_id = ?', (user_id,))
    r = c.fetchone()
    conn.close()
    if r and len(r) >= 3:
        balance = float(r[0]) if r[0] is not None else 0
        total_earned = float(r[1]) if r[1] is not None else 0
        total_spent = float(r[2]) if r[2] is not None else 0
        return {'balance': balance, 'total_earned': total_earned, 'total_spent': total_spent}
    return {'balance': 0, 'total_earned': 0, 'total_spent': 0}

def add_bonus(user_id, order_num, amount, desc):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO bonuses (user_id, balance, total_earned) 
                 VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET 
                 balance = balance + ?, total_earned = total_earned + ?''',
              (user_id, amount, amount, amount, amount))
    c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                 VALUES (?,?,?,?,?,?)''',
              (user_id, order_num, amount, 'earned', desc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def refund_bonus(user_id, order_num, amount, desc):
    """Возврат бонусов при удалении заказа"""
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('UPDATE bonuses SET balance = balance - ?, total_earned = total_earned - ? WHERE user_id = ?',
              (amount, amount, user_id))
    c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                 VALUES (?,?,?,?,?,?)''',
              (user_id, order_num, amount, 'refund', desc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

def get_bonus_percent(user_id):
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

# ========== ГАРАЖ (РАБОТА С БД) ==========
def save_car(user_id, vin, description):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT 1 FROM garage WHERE user_id = ? AND vin = ?', (user_id, vin))
    if c.fetchone():
        conn.close()
        return False
    c.execute('INSERT INTO garage (user_id, vin, description, created_at) VALUES (?,?,?,?)',
              (user_id, vin, description, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return True

def get_cars(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT vin, description, created_at FROM garage WHERE user_id = ? ORDER BY id DESC', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def delete_car(user_id, vin):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('DELETE FROM garage WHERE user_id = ? AND vin = ?', (user_id, vin))
    conn.commit()
    conn.close()
    return c.rowcount > 0

# ========== ФУНКЦИИ ДЛЯ РАСЧЕТОВ ==========
def calc_delivery_price(km):
    if km <= 0: return DELIVERY_BASE
    if km <= 50: return DELIVERY_BASE + km * 25
    if km <= 100: return DELIVERY_BASE + km * 35
    return DELIVERY_BASE + km * 50

def extract_city_from_address(address):
    address_lower = address.lower()
    if 'химки' in address_lower: return 'Химки'
    if 'мытищи' in address_lower: return 'Мытищи'
    if 'люберцы' in address_lower: return 'Люберцы'
    if 'красногорск' in address_lower: return 'Красногорск'
    if 'одинцово' in address_lower: return 'Одинцово'
    if 'подольск' in address_lower: return 'Подольск'
    if 'балашиха' in address_lower: return 'Балашиха'
    if 'москва' in address_lower or 'мск' in address_lower: return 'Москва'
    return 'Москва'

def extract_distance_from_address(address):
    address_lower = address.lower()
    if 'химки' in address_lower: return 5
    if 'мытищи' in address_lower: return 8
    if 'люберцы' in address_lower: return 10
    if 'красногорск' in address_lower: return 7
    if 'одинцово' in address_lower: return 10
    if 'подольск' in address_lower: return 25
    if 'балашиха' in address_lower: return 15
    return 0 if ('москва' in address_lower or 'мск' in address_lower) else 30

def delivery_discount(order_sum):
    if order_sum < 10000: return 0
    return min(100, ((order_sum - 10000) // 5000) * 5 + 5)

def parse_products(text):
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
            if price <= 0:
                continue
        except ValueError:
            continue
        name = line[:match.start()].strip()
        name = re.sub(r'^[=\-•–—]+|[=\-•–—]+$', '', name).strip()
        name = re.sub(r'арт\.?\s*\S+', '', name).strip()
        name = name[:40] + ".." if len(name) > 40 else name
        if name and price > 0:
            products.append({'name': name, 'price': price})
    return products

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

pickup_station_kb = ReplyKeyboardMarkup([
    ["Метро Давыдково"], ["Метро Строгино"], ["Метро Южная"]
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

# ========== ГАРАЖ (UI) ==========
async def garage_menu(upd, ctx):
    cars = get_cars(upd.effective_user.id)
    if not cars:
        await upd.message.reply_text(
            "🚗 МОЙ ГАРАЖ\n\nУ вас пока нет добавленных автомобилей.\n\n➕ Добавить автомобиль:\nПросто отправьте мне VIN номер (17 символов) или нажмите кнопку ниже.",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить авто", callback_data="garage_add")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")]
            ])
        )
        return
    
    text = "🚗 МОЙ ГАРАЖ\n\n"
    keyboard = []
    for car in cars:
        vin, description, created = car
        desc = description if description else "Описание не указано"
        text += f"🔹 {vin}\n   📝 {desc}\n   📅 Добавлен: {created[:10]}\n\n"
        keyboard.append([InlineKeyboardButton(f"🗑️ Удалить {vin}", callback_data=f"garage_del_{vin}")])
    keyboard.append([InlineKeyboardButton("➕ Добавить авто", callback_data="garage_add")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")])
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def garage_add_start(upd, ctx):
    q = upd.callback_query
    await q.answer()
    await q.edit_message_text("🚗 ДОБАВЛЕНИЕ АВТОМОБИЛЯ\n\nШаг 1/2: Отправьте VIN номер автомобиля (17 символов):")
    return 11

async def garage_get_vin(upd, ctx):
    vin = upd.message.text.upper().strip()
    if len(vin) != 17 or not vin.isalnum():
        await upd.message.reply_text("❌ VIN должен быть 17 символов. Попробуйте ещё раз:")
        return 11
    ctx.user_data['new_car_vin'] = vin
    await upd.message.reply_text(f"🚗 VIN: {vin}\n\nШаг 2/2: Введите описание автомобиля\n\nПример: BMW X5 3.0d, 2018, чёрный\n\nИли отправьте '-' чтобы пропустить:")
    return 12

async def garage_get_description(upd, ctx):
    description = upd.message.text.strip()
    if description == "-":
        description = ""
    vin = ctx.user_data.pop('new_car_vin', None)
    if not vin:
        await upd.message.reply_text("❌ Ошибка. Начните добавление заново.")
        return ConversationHandler.END
    if save_car(upd.effective_user.id, vin, description):
        await upd.message.reply_text(f"✅ Автомобиль {vin} успешно добавлен в ваш гараж!")
    else:
        await upd.message.reply_text(f"❌ Автомобиль {vin} уже есть в вашем гараже!")
    await garage_menu(upd, ctx)
    return ConversationHandler.END

async def garage_delete(upd, ctx):
    q = upd.callback_query
    await q.answer()
    vin = q.data[12:]
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"garage_confirm_del_{vin}")],
        [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="main_menu_back")]
    ])
    await q.edit_message_text(f"⚠️ Удалить автомобиль {vin} из гаража?\n\nЭто действие нельзя отменить.", reply_markup=kb)

async def garage_confirm_delete(upd, ctx):
    q = upd.callback_query
    await q.answer()
    vin = q.data[18:]
    if delete_car(q.from_user.id, vin):
        await q.edit_message_text(f"✅ Автомобиль {vin} удалён из гаража!")
    else:
        await q.edit_message_text(f"❌ Автомобиль {vin} не найден в гараже.")
    await garage_menu(upd, ctx)

# ========== ОСНОВНЫЕ КОМАНДЫ КЛИЕНТА ==========
async def start(upd, ctx):
    if ctx.args and ctx.args[0].startswith('ref_'):
        ref_id = int(ctx.args[0][4:])
        if ref_id != upd.effective_user.id:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?,?,?)',
                      (ref_id, upd.effective_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            c.execute('INSERT INTO bonuses (user_id, referrer_id) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET referrer_id = ?',
                      (upd.effective_user.id, ref_id, ref_id))
            conn.commit()
            conn.close()
            add_bonus(upd.effective_user.id, None, 500, "Приветственные бонусы")
            await ctx.bot.send_message(ref_id, f"👋 {upd.effective_user.full_name} перешёл по вашей ссылке!")
            await upd.message.reply_text("🎉 +500 бонусов!")
    
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

async def new_order(upd, ctx):
    cars = get_cars(upd.effective_user.id)
    if cars:
        keyboard = [[InlineKeyboardButton("🆕 Ввести вручную", callback_data="order_manual")]]
        for car in cars:
            vin, desc, _ = car
            desc_short = desc[:20] if desc else ""
            keyboard.append([InlineKeyboardButton(f"🚗 {vin} ({desc_short})", callback_data=f"order_auto_{vin}")])
        await upd.message.reply_text("🔧 ВЫБЕРИТЕ АВТОМОБИЛЬ из гаража или введите VIN вручную:", reply_markup=InlineKeyboardMarkup(keyboard))
        return VIN
    else:
        await upd.message.reply_text("🔧 Отправьте VIN номер (17 символов):")
        return VIN

async def order_auto_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    if q.data == "order_manual":
        await q.edit_message_text("🔧 Отправьте VIN номер (17 символов):")
        return VIN
    elif q.data.startswith("order_auto_"):
        vin = q.data[11:]
        ctx.user_data['vin'] = vin
        await q.edit_message_text(f"🚗 Выбран автомобиль: {vin}\n\n📊 Теперь введите пробег (км):")
        return MILEAGE

async def get_vin(upd, ctx):
    vin = upd.message.text.upper().strip()
    if len(vin) != 17 or not vin.isalnum():
        await upd.message.reply_text("❌ VIN должен быть 17 символов. Попробуйте ещё раз:")
        return VIN
    ctx.user_data['vin'] = vin
    await upd.message.reply_text("📊 Пробег (км):")
    return MILEAGE

async def get_mileage(upd, ctx):
    try:
        mileage = int(upd.message.text)
        if mileage < 0:
            raise ValueError
        ctx.user_data['mileage'] = str(mileage)
    except ValueError:
        await upd.message.reply_text("❌ Пожалуйста, введите число (пробег в км):")
        return MILEAGE
    await upd.message.reply_text("🏙️ Стиль вождения в городе:", reply_markup=city_style_kb)
    return STYLE_CITY

async def get_style_city(upd, ctx):
    ctx.user_data['style_city'] = upd.message.text
    await upd.message.reply_text("🛣️ Стиль вождения на трассе:", reply_markup=highway_style_kb)
    return STYLE_HIGHWAY

async def get_style_highway(upd, ctx):
    ctx.user_data['style_highway'] = upd.message.text
    await upd.message.reply_text("🚚 Способ доставки:", reply_markup=delivery_type_kb)
    return DELIVERY_TYPE

async def get_delivery_type(upd, ctx):
    choice = upd.message.text
    ctx.user_data['delivery_type'] = choice
    if choice == "Курьером":
        await upd.message.reply_text("📍 Введите ПОЛНЫЙ АДРЕС доставки\n\nПример: г. Москва, ул. Тверская, д. 15, кв. 78\n\nБот автоматически определит город и расстояние от МКАД")
        return ADDRESS
    elif choice == "Самовывоз":
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("📍 Самовывоз\n\nДоступные станции:\n- Метро Давыдково\n- Метро Строгино\n- Метро Южная\n\nВведите адрес самовывоза:")
        return ADDRESS
    else:
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("🚛 Сторонняя фирма (стоимость рассчитает менеджер)\n\n📍 Введите адрес доставки:")
        return ADDRESS

async def get_address(upd, ctx):
    full_address = upd.message.text
    ctx.user_data['delivery_address'] = full_address
    city = extract_city_from_address(full_address)
    distance = extract_distance_from_address(full_address)
    ctx.user_data['city'] = city
    ctx.user_data['distance'] = distance
    if ctx.user_data.get('delivery_type') == "Курьером":
        price = calc_delivery_price(distance)
        ctx.user_data['delivery_price'] = price
        await upd.message.reply_text(f"📍 Адрес: {full_address}\n🏙️ Город: {city}\n📏 Расстояние от МКАД: {distance} км\n🚚 Стоимость доставки: {price} руб.\n\n📞 Введите ваш контактный телефон:")
    else:
        await upd.message.reply_text(f"📍 Адрес: {full_address}\n\n📞 Введите ваш контактный телефон:")
    return PHONE

async def get_phone(upd, ctx):
    phone = upd.message.text.strip()
    if len(phone) < 5:
        await upd.message.reply_text("❌ Пожалуйста, введите корректный номер телефона:")
        return PHONE
    ctx.user_data['phone'] = phone
    await upd.message.reply_text("🔧 Выберите узел запчасти:", reply_markup=part_node_kb)
    return PART_NODE

async def get_part_node(upd, ctx):
    ctx.user_data['part_node'] = upd.message.text
    if upd.message.text in AXLE_REQUIRED_NODES:
        await upd.message.reply_text("🔧 Выберите ось:", reply_markup=axle_kb)
        return AXLE
    else:
        ctx.user_data['axle'] = "Не требуется"
        await upd.message.reply_text("🔧 Какие запчасти нужны? (каждая с новой строки)\n\nПример:\nКолодки тормозные\nДиски тормозные")
        return PARTS

async def get_axle(upd, ctx):
    ctx.user_data['axle'] = upd.message.text
    await upd.message.reply_text("🔧 Какие запчасти нужны? (каждая с новой строки)\n\nПример:\nКолодки тормозные\nДиски тормозные")
    return PARTS

async def get_parts(upd, ctx):
    if not upd.message.text.strip():
        await upd.message.reply_text("❌ Вы не ввели запчасти. Пожалуйста, введите список запчастей:")
        return PARTS
    ctx.user_data['needed_parts'] = upd.message.text
    
    data = ctx.user_data
    summary = (f"📋 ПРОВЕРЬТЕ ЗАКАЗ\n\n"
               f"🚗 VIN: {data.get('vin', 'не указан')}\n📊 Пробег: {data.get('mileage', 'не указан')} км\n"
               f"🏎️ Стиль город: {data.get('style_city', 'не указан')}\n🛣️ Стиль трасса: {data.get('style_highway', 'не указан')}\n"
               f"🏙️ Город: {data.get('city', 'не указан')}\n🚚 Доставка: {data.get('delivery_type', 'не указана')}\n"
               f"📍 Адрес: {data.get('delivery_address', 'не указан')}\n📞 Телефон: {data.get('phone', 'не указан')}\n"
               f"🔧 Узел: {data.get('part_node', 'не указан')}\n🔧 Ось: {data.get('axle', 'не указана')}\n"
               f"📝 Запчасти: {data.get('needed_parts', 'не указаны')}\n\n💰 Доставка: {data.get('delivery_price', 500)} руб.\n\n"
               "✅ Всё верно? Нажмите «Готово» или «Редактировать»")
    await upd.message.reply_text(summary, reply_markup=confirm_order_kb)
    return CONFIRM

async def confirm_order(upd, ctx):
    if upd.message.text == "✅ Готово":
        data = ctx.user_data.copy()
        order_num = save_order({
            'user_id': upd.effective_user.id,
            'user_name': upd.effective_user.full_name,
            'phone': data.get('phone',''),
            'vin': data.get('vin',''),
            'mileage': data.get('mileage',''),
            'style_city': data.get('style_city',''),
            'style_highway': data.get('style_highway',''),
            'city': data.get('city',''),
            'distance': data.get('distance',0),
            'delivery_type': data.get('delivery_type',''),
            'delivery_price': data.get('delivery_price',500),
            'delivery_address': data.get('delivery_address',''),
            'part_node': data.get('part_node',''),
            'axle': data.get('axle',''),
            'needed_parts': data.get('needed_parts','')
        })
        ctx.user_data.clear()
        
        await upd.context.bot.send_message(
            MANAGER_ID,
            f"🆕 НОВЫЙ ЗАКАЗ #{order_num}\n\n👤 Клиент: {upd.effective_user.full_name}\n📞 Телефон: {data.get('phone','')}\n🚗 VIN: {data.get('vin','')}\n📊 Пробег: {data.get('mileage','')} км\n🏙️ Город: {data.get('city','')}\n🚚 Доставка: {data.get('delivery_type','')} | {data.get('delivery_price',500)} руб.\n📍 Адрес: {data.get('delivery_address','не указан')}\n🔧 Узел: {data.get('part_node','не указан')}\n📝 Запчасти:\n{data.get('needed_parts','')}\n\n➡️ Для подбора запчастей ответьте на это сообщение"
        )
        
        await upd.message.reply_text(
            f"✅ ЗАКАЗ #{order_num} ПРИНЯТ!\n\n📋 Детали заказа:\n🚚 Доставка: {data.get('delivery_price',500)} руб.\n📍 Адрес: {data.get('delivery_address','не указан')}\n\n🔧 Менеджер скоро свяжется с вами для уточнения деталей.\n\nВы можете вернуться в главное меню:",
            reply_markup=main_menu
        )
        return ConversationHandler.END
    elif upd.message.text == "✏️ Редактировать":
        ctx.user_data.clear()
        await upd.message.reply_text("✏️ Давайте начнём заказ заново. Нажмите 🛒 Новый заказ", reply_markup=main_menu)
        return ConversationHandler.END

async def my_orders(upd, ctx):
    orders = get_user_orders(upd.effective_user.id)
    if not orders:
        await upd.message.reply_text("📭 У вас пока нет заказов", reply_markup=main_menu)
        return
    
    text = "📦 ВАШИ ЗАКАЗЫ:\n\n"
    kb = []
    for order in orders:
        order_num, status_text, created, tp, dp, final, needed = order
        total = safe_int(tp) + safe_int(dp)
        
        if 'Ожидает подбора' in status_text:
            icon = "🆕"
        elif 'Ожидает ответа' in status_text:
            icon = "❓"
        elif 'Ожидает выбора' in status_text:
            icon = "🟡"
        elif 'Заказан' in status_text:
            icon = "📦"
        elif 'Готов к выдаче' in status_text:
            icon = "✅"
        elif 'Оплачен' in status_text:
            icon = "💰"
        elif 'Отправлен' in status_text:
            icon = "🚚"
        elif 'Доставлен' in status_text:
            icon = "🏠"
        else:
            icon = "📦"
        
        text += f"{icon} {order_num} — {created[:10]} — {total} руб.\n"
        kb.append([InlineKeyboardButton(f"🔍 Заказ {order_num}", callback_data=f"view_{order_num}")])
    kb.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")])
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def view_order(upd, ctx):
    q = upd.callback_query
    await q.answer()
    order_num = q.data[5:]
    order = get_order(order_num)
    if not order:
        await q.edit_message_text("❌ Заказ не найден")
        return
    
    total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
    status = order.get('status', '')
    
    text = (f"📋 ЗАКАЗ {order.get('order_number', '')}\n\n"
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
            if isinstance(selected_parts, list) and len(selected_parts) > 0:
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
    
    # Кнопка для списания бонусов (только для заказов в статусе "Ожидает оплаты")
    if status == 'confirmed':
        bonus_data = get_bonus(order['user_id'])
        if bonus_data['balance'] > 0:
            kb = [
                [InlineKeyboardButton("🎁 Списать бонусы", callback_data=f"apply_bonus_{order_num}")],
                [InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders_list")]
            ]
        else:
            kb = [[InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders_list")]]
    else:
        kb = [[InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders_list")]]
    
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def back_orders_list(upd, ctx):
    q = upd.callback_query
    await q.answer()
    await my_orders(upd, ctx)

async def main_menu_back(upd, ctx):
    q = upd.callback_query
    await q.answer()
    await start(upd, ctx)

async def apply_bonus_callback(upd, ctx):
    """Применение бонусов к заказу"""
    q = upd.callback_query
    await q.answer()
    order_num = q.data[12:]  # убираем "apply_bonus_"
    uid = q.from_user.id
    
    order = get_order(order_num)
    if not order:
        await q.edit_message_text("❌ Заказ не найден")
        return
    
    if order.get('status') != 'confirmed':
        await q.edit_message_text("❌ Бонусы можно применить только к неподтверждённому заказу")
        return
    
    bonus_data = get_bonus(uid)
    balance = bonus_data['balance']
    total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
    
    if balance <= 0:
        await q.edit_message_text("❌ У вас нет бонусов для списания")
        return
    
    # Предлагаем списать бонусы
    kb = InlineKeyboardMarkup([
        [InlineKeyboardButton(f"✅ Списать все ({int(balance)} руб.)", callback_data=f"spend_all_bonus_{order_num}")],
        [InlineKeyboardButton("✏️ Ввести сумму", callback_data=f"spend_custom_bonus_{order_num}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"view_{order_num}")]
    ])
    
    await q.edit_message_text(
        f"🎁 **СПИСАНИЕ БОНУСОВ**\n\n"
        f"📦 Заказ: {order_num}\n"
        f"💰 Сумма заказа: {total_sum} руб.\n"
        f"🎁 Доступно бонусов: {int(balance)} руб.\n\n"
        f"Бонусы можно списать в размере до 100% суммы заказа.\n\n"
        f"Выберите действие:",
        reply_markup=kb,
        parse_mode='Markdown'
    )

async def spend_all_bonus_callback(upd, ctx):
    """Списать все доступные бонусы"""
    q = upd.callback_query
    await q.answer()
    order_num = q.data[16:]  # убираем "spend_all_bonus_"
    uid = q.from_user.id
    
    order = get_order(order_num)
    if not order:
        await q.edit_message_text("❌ Заказ не найден")
        return
    
    total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
    bonus_data = get_bonus(uid)
    balance = bonus_data['balance']
    
    spend_amount = min(int(balance), int(total_sum))
    
    if spend_amount <= 0:
        await q.edit_message_text("❌ Недостаточно бонусов для списания")
        return
    
    # Списываем бонусы
    if use_bonus(uid, order_num, spend_amount, f"Списание бонусов по заказу {order_num}"):
        # Обновляем сумму заказа
        new_total = total_sum - spend_amount
        update_order(order_num, total_price=order.get('total_price', 0) - spend_amount)
        
        await q.edit_message_text(
            f"✅ **БОНУСЫ СПИСАНЫ!**\n\n"
            f"📦 Заказ: {order_num}\n"
            f"🎁 Списано: {spend_amount} руб.\n"
            f"💰 Сумма к оплате: {new_total} руб.\n\n"
            f"Остаток бонусов: {int(balance - spend_amount)} руб.",
            parse_mode='Markdown'
        )
    else:
        await q.edit_message_text("❌ Ошибка при списании бонусов")

async def spend_custom_bonus_callback(upd, ctx):
    """Списать определённую сумму бонусов"""
    q = upd.callback_query
    await q.answer()
    order_num = q.data[18:]  # убираем "spend_custom_bonus_"
    ctx.user_data['bonus_order'] = order_num
    await q.edit_message_text("✏️ Введите сумму бонусов для списания (целое число):")
    return SPEND_BONUS

async def spend_custom_bonus_input(upd, ctx):
    """Обработка ввода суммы для списания"""
    user_id = upd.effective_user.id
    order_num = ctx.user_data.get('bonus_order')
    
    if not order_num:
        await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
        return ConversationHandler.END
    
    try:
        amount = int(upd.message.text)
        if amount <= 0:
            raise ValueError
    except ValueError:
        await upd.message.reply_text("❌ Введите целое положительное число.")
        return SPEND_BONUS
    
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text("❌ Заказ не найден")
        return ConversationHandler.END
    
    total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
    bonus_data = get_bonus(user_id)
    balance = bonus_data['balance']
    
    if amount > balance:
        await upd.message.reply_text(f"❌ У вас только {int(balance)} бонусов. Попробуйте снова.")
        return SPEND_BONUS
    
    if amount > total_sum:
        await upd.message.reply_text(f"❌ Сумма списания не может превышать сумму заказа ({total_sum} руб.).")
        return SPEND_BONUS
    
    # Списываем бонусы
    if use_bonus(user_id, order_num, amount, f"Списание бонусов по заказу {order_num}"):
        new_total = total_sum - amount
        update_order(order_num, total_price=order.get('total_price', 0) - amount)
        
        await upd.message.reply_text(
            f"✅ **БОНУСЫ СПИСАНЫ!**\n\n"
            f"📦 Заказ: {order_num}\n"
            f"🎁 Списано: {amount} руб.\n"
            f"💰 Сумма к оплате: {new_total} руб.\n\n"
            f"Остаток бонусов: {int(balance - amount)} руб.",
            parse_mode='Markdown'
        )
    else:
        await upd.message.reply_text("❌ Ошибка при списании бонусов")
    
    del ctx.user_data['bonus_order']
    return ConversationHandler.END

async def bonus_cmd(upd, ctx):
    uid = upd.effective_user.id
    bonus_data = get_bonus(uid)
    bal = bonus_data['balance']
    total_earned = bonus_data['total_earned']
    total_spent = bonus_data['total_spent']
    total_purchases = get_user_total(uid)
    percent = get_bonus_percent(uid)
    
    text = (f"🎁 **БОНУСНАЯ ПРОГРАММА**\n\n"
            f"💰 Текущий баланс: **{int(bal)}** бонусов\n"
            f"📈 Всего начислено: **{int(total_earned)}** бонусов\n"
            f"📉 Всего потрачено: **{int(total_spent)}** бонусов\n"
            f"🛒 Накоплено по покупкам: **{int(total_purchases)}** руб.\n"
            f"⭐ Текущий процент: **{percent}%**\n\n"
            "📊 **Градация начисления:**\n"
            "1% → до 100 000 руб.\n2% → 100 000 - 200 000 руб.\n3% → 200 000 - 300 000 руб.\n"
            "4% → 300 000 - 400 000 руб.\n5% → 400 000 - 500 000 руб.\n6% → 500 000 - 600 000 руб.\n"
            "7% → 600 000 - 700 000 руб.\n8% → 700 000 - 800 000 руб.\n9% → 800 000 - 900 000 руб.\n10% → от 900 000 руб.\n\n")
    
    await upd.message.reply_text(text, parse_mode='Markdown')
    
    # Кнопки для дополнительной статистики
    monthly_stats = get_monthly_stats(uid)
    if monthly_stats:
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("📊 Статистика по месяцам", callback_data="bonus_stats")],
            [InlineKeyboardButton("📜 Полная история", callback_data="bonus_history_full")]
        ])
        await upd.message.reply_text("📊 **Дополнительная статистика:**", reply_markup=kb, parse_mode='Markdown')
    else:
        history = get_bonus_history_grouped(uid)
        if history:
            history_text = format_bonus_history_grouped(history)
            if len(history_text) > 4000:
                for i in range(0, len(history_text), 4000):
                    await upd.message.reply_text(history_text[i:i+4000], parse_mode='Markdown')
            else:
                await upd.message.reply_text(history_text, parse_mode='Markdown')
        else:
            await upd.message.reply_text("📭 История операций пока пуста")

async def bonus_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data
    uid = q.from_user.id
    
    if data == "bonus_stats":
        monthly_stats = get_monthly_stats(uid)
        stats_text = format_monthly_stats(monthly_stats)
        await q.edit_message_text(stats_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="bonus_back")]]))
    
    elif data == "bonus_history_full":
        history = get_bonus_history_grouped(uid)
        history_text = format_bonus_history_grouped(history)
        if len(history_text) > 4000:
            parts = [history_text[i:i+4000] for i in range(0, len(history_text), 4000)]
            await q.edit_message_text(parts[0], parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="bonus_back")]]))
            for part in parts[1:]:
                await q.message.reply_text(part, parse_mode='Markdown')
        else:
            await q.edit_message_text(history_text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="bonus_back")]]))
    
    elif data == "bonus_back":
        await bonus_cmd(upd, ctx)

async def referral_cmd(upd, ctx):
    bot_username = (await ctx.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{upd.effective_user.id}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', (upd.effective_user.id,))
    referrals_count = c.fetchone()[0]
    conn.close()
    text = (f"🔗 **РЕФЕРАЛЬНАЯ ССЫЛКА**\n\n"
            f"{link}\n\n"
            f"👥 Приглашено друзей: **{referrals_count}**\n"
            "📊 Вы получаете **0.5%** от суммы заказов ваших друзей бонусами!\n"
            "🎁 Друг получает **500** приветственных бонусов!\n\n"
            f"💰 Текущий баланс: **{int(get_bonus(upd.effective_user.id)['balance'])}** бонусов")
    await upd.message.reply_text(text, parse_mode='Markdown')

async def delivery_cmd(upd, ctx):
    text = (f"🚚 **РАСЧЁТ ДОСТАВКИ ОТ МКАД**\n\n"
            f"Базовая стоимость: **{DELIVERY_BASE}** руб.\n\n"
            "📌 **Тарифы:**\n"
            "0 км (Москва): 500 руб.\n"
            "1-50 км: 500 + км × 25\n"
            "51-100 км: 500 + км × 35\n"
            "101+ км: 500 + км × 50\n\n"
            "📌 **Самовывоз (бесплатно):**\n"
            "- Метро Давыдково\n"
            "- Метро Строгино\n"
            "- Метро Южная\n\n"
            "📌 **Скидка на доставку от суммы заказа:**\n"
            "от 10 000 руб. → 5%\n"
            "от 15 000 руб. → 10%\n"
            "от 20 000 руб. → 15%\n"
            "... до 100% бесплатно")
    await upd.message.reply_text(text, parse_mode='Markdown')

async def help_cmd(upd, ctx):
    text = ("📖 **ПОМОЩЬ**\n\n"
            "**Основные команды:**\n"
            "/start - Главное меню\n"
            "/my_orders - Мои заказы\n"
            "/bonus - Бонусы\n"
            "/referral - Рефералы\n"
            "/delivery - Доставка\n\n"
            "**👨‍💼 Администратор:**\n"
            "/menu - Панель управления\n"
            "/fix - Создать тестовый заказ\n"
            "/resend - Повторно отправить подбор\n\n"
            "**🚗 Мой гараж:**\n"
            "Храните VIN и описание автомобилей\n"
            "Быстрый выбор при создании заказа\n\n"
            "**❓ Вопросы:**\n"
            "По всем вопросам обращайтесь к менеджеру")
    await upd.message.reply_text(text, reply_markup=main_menu, parse_mode='Markdown')

# ========== МЕНЕДЖЕР (АДМИН) ==========
user_selections = {}

async def manager_reply(upd, ctx):
    if upd.effective_user.id != MANAGER_ID or not upd.message.reply_to_message:
        return
    reply_text = upd.message.reply_to_message.text or ""
    match = re.search(r"НОВЫЙ ЗАКАЗ #(RVN-\w{6})", reply_text) or re.search(r"(RVN-\w{6})", reply_text)
    if not match:
        await upd.message.reply_text("❌ Не удалось определить номер заказа.")
        return
    order_num = match.group(1)
    
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text(f"❌ Заказ {order_num} не найден в базе данных. Возможно, он был удалён.")
        return
    
    if not upd.message.text:
        await upd.message.reply_text("❌ Вы не ввели подбор запчастей.")
        return
    products = parse_products(upd.message.text)
    if not products:
        await upd.message.reply_text("❌ Не распознано. Формат:\nНазвание запчасти = 1000 руб\n\nПример:\nМасло моторное Ravenol 5W-40 = 3500 руб")
        return
    total_cost = sum(p['price'] for p in products) * 0.7
    update_order(order_num, selected_products=upd.message.text, our_cost=total_cost, status='waiting_selection', status_text='🟡 Ожидает выбора')
    
    kb = []
    for i, p in enumerate(products):
        display_name = p['name'][:25] + ".." if len(p['name']) > 25 else p['name']
        kb.append([InlineKeyboardButton(f"⬜ {display_name} — {int(p['price'])} руб.", callback_data=f"sel_{order_num}_{i}")])
    kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ВЫБОР", callback_data=f"fin_{order_num}")])
    await ctx.bot.send_message(order['user_id'], text=f"🛒 **ПОДБОР ЗАПЧАСТЕЙ ДЛЯ ЗАКАЗА #{order_num}**\n\nМенеджер подобрал для вас следующие позиции:\n\nВыберите нужные запчасти (можно отметить несколько):", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    await upd.message.reply_text(f"✅ Подбор запчастей для заказа {order_num} отправлен клиенту!")

async def resend_selection(upd, ctx):
    """Повторная отправка подбора запчастей (если клиент не ответил)"""
    if upd.effective_user.id != MANAGER_ID:
        await upd.message.reply_text("⛔ Доступ запрещён")
        return
    
    args = ctx.args
    if not args:
        await upd.message.reply_text("📦 Использование: /resend RVN-XXXXXX")
        return
    
    order_num = clean_order_number(args[0])
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text(f"❌ Заказ {order_num} не найден!")
        return
    
    if not order.get('selected_products'):
        await upd.message.reply_text(f"❌ Для заказа {order_num} ещё нет подбора запчастей.")
        return
    
    products = parse_products(order['selected_products'])
    if not products:
        await upd.message.reply_text(f"❌ Не удалось распарсить подбор для заказа {order_num}.")
        return
    
    kb = [[InlineKeyboardButton(f"⬜ {p['name'][:25]} — {int(p['price'])} руб.", callback_data=f"sel_{order_num}_{i}")] for i, p in enumerate(products)]
    kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ВЫБОР", callback_data=f"fin_{order_num}")])
    
    await ctx.bot.send_message(order['user_id'], text=f"🛒 **ПОВТОРНЫЙ ПОДБОР ЗАПЧАСТЕЙ ДЛЯ ЗАКАЗА #{order_num}**\n\nМенеджер повторно отправляет вам подбор:\n\nВыберите нужные запчасти (можно отметить несколько):", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    await upd.message.reply_text(f"✅ Подбор для заказа {order_num} отправлен повторно!")

async def select_cb(upd, ctx):
    q = upd.callback_query
    await q.answer()
    _, order_num, idx = q.data.split('_')
    idx = int(idx)
    uid = q.from_user.id
    order = get_order(order_num)
    if not order or not order.get('selected_products'):
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
        kb.append([InlineKeyboardButton(f"{cb} {display_name} — {int(p['price'])} руб.", callback_data=f"sel_{order_num}_{i}")])
    kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ВЫБОР", callback_data=f"fin_{order_num}")])
    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

async def finalize_cb(upd, ctx):
    q = upd.callback_query
    await q.answer()
    order_num = q.data.split('_')[1]
    uid = q.from_user.id
    if uid not in user_selections or order_num not in user_selections[uid] or not user_selections[uid][order_num]:
        await q.edit_message_text("❌ Ничего не выбрано.")
        return
    order = get_order(order_num)
    if not order:
        await q.edit_message_text("❌ Заказ не найден.")
        return
    products = parse_products(order['selected_products'])
    selected = []
    total = 0
    for idx in user_selections[uid][order_num]:
        if idx < len(products):
            selected.append(products[idx])
            total += products[idx]['price']
    
    if not selected:
        await q.edit_message_text("❌ Вы не выбрали ни одной запчасти. Пожалуйста, выберите хотя бы одну позицию.")
        return
    
    delivery_disc = delivery_discount(total)
    delivery_price = order['delivery_price']
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
    update_order(order_num, total_price=total, final_order=str(selected), status='confirmed', status_text='💰 Ожидает оплаты')
    
    bonus_percent = get_bonus_percent(uid)
    bonus = int(total * bonus_percent / 100)
    add_bonus(uid, order_num, bonus, f"Заказ {order_num} ({bonus_percent}%)")
    
    result = f"✅ **ЗАКАЗ #{order_num} ПОДТВЕРЖДЁН!**\n\n"
    for p in selected:
        wrapped_name = wrap_text(p['name'], 25)
        result += f"• {wrapped_name}\n   → {int(p['price'])} руб.\n"
    result += f"\n{delivery_text}\n\n"
    result += f"💰 **ИТОГО К ОПЛАТЕ: {int(final_total)} руб.**"
    if bonus > 0:
        result += f"\n\n🎁 +{bonus} бонусов начислено ({bonus_percent}%)"
    result += "\n\n📞 Менеджер свяжется с вами для уточнения оплаты."
    
    await q.edit_message_text(result, parse_mode='Markdown')
    await ctx.bot.send_message(MANAGER_ID, f"✅ **ЗАКАЗ {order_num} ПОДТВЕРЖДЁН КЛИЕНТОМ!**\n\n👤 Клиент: {order['user_name']}\n💰 Сумма: {int(final_total)} руб.\n🎁 Бонусы: +{bonus} ({bonus_percent}%)", parse_mode='Markdown')
    del user_selections[uid][order_num]

async def ask_client(upd, ctx):
    q = upd.callback_query
    await q.answer()
    order_num = q.data.split('_')[1]
    ctx.user_data['awaiting_answer_for'] = order_num
    await q.edit_message_text(f"❓ Введите вопрос для клиента по заказу {order_num}:")

async def send_question_to_client(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        return
    if 'awaiting_answer_for' not in ctx.user_data:
        return
    order_num = ctx.user_data['awaiting_answer_for']
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text("❌ Заказ не найден")
        return
    question = upd.message.text
    update_order(order_num, status='waiting_answer', status_text='❓ Ожидает ответа на вопрос')
    await ctx.bot.send_message(order['user_id'], text=f"❓ **Уточнение по заказу {order_num}**\n\n{question}\n\nПожалуйста, ответьте на это сообщение.", parse_mode='Markdown')
    await upd.message.reply_text(f"✅ Вопрос отправлен клиенту по заказу {order_num}")
    del ctx.user_data['awaiting_answer_for']

async def client_answer(upd, ctx):
    user_id = upd.effective_user.id
    if user_id == MANAGER_ID:
        return
    if not upd.message.text or not upd.message.text.strip():
        await upd.message.reply_text("❌ Пожалуйста, напишите ответ на вопрос менеджера.")
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT order_number FROM orders WHERE user_id = ? AND status = "waiting_answer" LIMIT 1', (user_id,))
    row = c.fetchone()
    conn.close()
    if row:
        order_num = row[0]
        update_order(order_num, status='pending', status_text='🆕 Ожидает подбора')
        await ctx.bot.send_message(MANAGER_ID, f"✅ **Клиент ответил по заказу {order_num}**\n\n📝 {upd.message.text}", parse_mode='Markdown')
        await upd.message.reply_text("✅ Спасибо! Ваш ответ передан менеджеру.")

# ========== АДМИН ПАНЕЛЬ ==========
async def admin_menu(upd, ctx, message=None):
    if upd.effective_user.id != MANAGER_ID:
        if message:
            await message.reply_text("⛔ Доступ запрещён")
        else:
            await upd.message.reply_text("⛔ Доступ запрещён")
        return
    
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
        
        if 'Ожидает подбора' in status_text:
            icon = "🆕"
        elif 'Ожидает выбора' in status_text or 'Ожидает ответа' in status_text:
            icon = "🟡"
        elif 'Заказан' in status_text:
            icon = "📦"
        elif 'Готов к выдаче' in status_text:
            icon = "✅"
        elif 'Оплачен' in status_text:
            icon = "💰"
        elif 'Отправлен' in status_text:
            icon = "🚚"
        elif 'Доставлен' in status_text:
            icon = "🏠"
        else:
            icon = "📦"
        
        keyboard.append([InlineKeyboardButton(f"{icon} {order_num} | {user_name}", callback_data=f"admin_order_{order_num}")])
    
    text = "👨‍💼 **АДМИН ПАНЕЛЬ**\n\nВыберите заказ для управления:"
    if message:
        try:
            await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
        except Exception as e:
            if "Message is not modified" not in str(e):
                print(f"Ошибка: {e}")
    else:
        await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')

async def admin_callback(upd, ctx):
    q = upd.callback_query
    data = q.data
    await q.answer()
    print(f"🔍 Callback: {data}")
    
    # --- КНОПКИ "НАЗАД" И "ОБНОВИТЬ" ---
    if data in ["admin_back", "admin_dashboard", "admin_refresh"]:
        await admin_menu(upd, ctx, q.message)
        return
    
    # --- СТАТИСТИКА ---
    if data == "admin_stats":
        orders = get_all_orders()
        if not orders:
            await q.edit_message_text("📭 Нет данных")
            return
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
        await q.edit_message_text(
            f"📊 **СТАТИСТИКА**\n\n📦 Заказов: {total_orders}\n💰 Сумма: {int(total_sum)} руб.\n\n**По статусам:**\n{status_text}",
            reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]),
            parse_mode='Markdown'
        )
        return
    
    # --- ТЕСТОВЫЙ ЗАКАЗ ---
    if data == "admin_fix":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
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
        conn.close()
        await q.edit_message_text(f"✅ Тестовый заказ {num} создан!")
        await admin_menu(upd, ctx, q.message)
        return
    
    # --- ОТКРЫТЬ КАРТОЧКУ ЗАКАЗА ---
    if data.startswith("admin_order_"):
        order_num = data[12:]
        order = get_order(order_num)
        if not order:
            await q.edit_message_text("❌ Заказ не найден")
            return
        
        total_sum = order.get('total_price', 0) + order.get('delivery_price', 0)
        text = (f"📋 **ЗАКАЗ {order.get('order_number', '')}**\n\n"
                f"👤 {order.get('user_name', '')}\n📞 {order.get('phone', 'не указан')}\n"
                f"🏙️ Город: {order.get('city', 'не указан')}\n📍 Адрес: {order.get('delivery_address', 'не указан')}\n"
                f"🚚 Доставка: {order.get('delivery_type', 'не указана')} | {order.get('delivery_price', 0)} руб.\n"
                f"💰 Сумма: {total_sum} руб.\n📦 Статус: {order.get('status_text', 'неизвестен')}")
        
        kb = [
            [InlineKeyboardButton("💰 Оплачен", callback_data=f"pay_{order_num}")],
            [InlineKeyboardButton("📦 Заказан", callback_data=f"ordered_{order_num}")],
            [InlineKeyboardButton("✅ Готов к выдаче", callback_data=f"ready_{order_num}")],
            [InlineKeyboardButton("🚚 Отправлен", callback_data=f"ship_{order_num}")],
            [InlineKeyboardButton("🏠 Доставлен", callback_data=f"del_{order_num}")],
            [InlineKeyboardButton("✏️ Изменить доставку", callback_data=f"edit_delivery_{order_num}")],
            [InlineKeyboardButton("❓ Уточнить", callback_data=f"ask_{order_num}")],
            [InlineKeyboardButton("🔍 Детали", callback_data=f"detail_{order_num}")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{order_num}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return
    
    # --- ОПЛАЧЕН ---
    if data.startswith("pay_"):
        order_num = data[4:]
        order = get_order(order_num)
        if not order:
            await q.edit_message_text("❌ Заказ не найден")
            return
        
        update_order(order_num, status='paid', status_text='💰 Оплачен')
        
        await ctx.bot.send_message(
            order['user_id'],
            text=f"✅ Заказ {order_num} оплачен!\n\n"
                 f"💰 Сумма: {order.get('total_price', 0) + order.get('delivery_price', 0)} руб.\n\n"
                 f"Спасибо за покупку!"
        )
        await q.edit_message_text(q.message.text + "\n\n✅ **СТАТУС: ОПЛАЧЕН**", parse_mode='Markdown')
        return
    
    # --- ЗАКАЗАН ---
    if data.startswith("ordered_"):
        order_num = data[8:]
        update_order(order_num, status='ordered', status_text='📦 Заказан')
        order = get_order(order_num)
        if order:
            await ctx.bot.send_message(order['user_id'], text=f"📦 Заказ {order_num} заказан у поставщика! Ожидайте поступления.")
        await q.edit_message_text(q.message.text + "\n\n✅ **СТАТУС: ЗАКАЗАН**", parse_mode='Markdown')
        return
    
    # --- ГОТОВ К ВЫДАЧЕ ---
    if data.startswith("ready_"):
        order_num = data[6:]
        update_order(order_num, status='ready', status_text='✅ Готов к выдаче')
        order = get_order(order_num)
        if order:
            await ctx.bot.send_message(order['user_id'], text=f"✅ Заказ {order_num} готов к выдаче! Можете забрать.")
        await q.edit_message_text(q.message.text + "\n\n✅ **СТАТУС: ГОТОВ К ВЫДАЧЕ**", parse_mode='Markdown')
        return
    
    # --- ОТПРАВЛЕН ---
    if data.startswith("ship_"):
        order_num = data[5:]
        ctx.user_data['track_for'] = order_num
        await q.edit_message_text("📦 Введите трек-номер для отправления:")
        return
    
    # --- ДОСТАВЛЕН ---
    if data.startswith("del_"):
        order_num = data[4:]
        update_order(order_num, status='delivered', status_text='🏠 Доставлен')
        order = get_order(order_num)
        if order:
            await ctx.bot.send_message(order['user_id'], text=f"🏠 Заказ {order_num} доставлен! Спасибо за покупку!")
        await q.edit_message_text(q.message.text + "\n\n✅ **СТАТУС: ДОСТАВЛЕН**", parse_mode='Markdown')
        return
    
    # --- ИЗМЕНИТЬ ДОСТАВКУ (показать варианты) ---
    if data.startswith("edit_delivery_"):
        order_num = data[14:]
        kb = [
            [InlineKeyboardButton("🚚 Курьером", callback_data=f"set_delivery_{order_num}_Курьером")],
            [InlineKeyboardButton("📦 Самовывоз", callback_data=f"set_delivery_{order_num}_Самовывоз")],
            [InlineKeyboardButton("🚛 Сторонняя фирма", callback_data=f"set_delivery_{order_num}_Сторонняя фирма")],
            [InlineKeyboardButton("◀️ Назад", callback_data=f"admin_order_{order_num}")]
        ]
        await q.edit_message_text("✏️ **Выберите способ доставки:**", reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return
    
    # --- УСТАНОВИТЬ НОВУЮ ДОСТАВКУ ---
    if data.startswith("set_delivery_"):
        parts = data.split('_')
        order_num = parts[2]
        new_delivery = parts[3]
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
            await ctx.bot.send_message(order['user_id'], text=f"✏️ Доставка изменена: {new_delivery}\n{desc}")
            await q.edit_message_text(f"✅ Доставка изменена!\n\n{desc}")
        return
    
    # --- УТОЧНИТЬ У КЛИЕНТА ---
    if data.startswith("ask_"):
        order_num = data[4:]
        ctx.user_data['awaiting_answer_for'] = order_num
        await q.edit_message_text("❓ Введите вопрос для клиента:")
        return
    
    # --- ПОЛНАЯ ИНФОРМАЦИЯ ---
    if data.startswith("detail_"):
        order_num = data[7:]
        order = get_order(order_num)
        if not order:
            await q.edit_message_text("❌ Заказ не найден")
            return
        text = (f"🔍 **ПОЛНАЯ ИНФОРМАЦИЯ**\n\n"
                f"📦 Заказ: {order.get('order_number', '')}\n"
                f"👤 Клиент: {order.get('user_name', '')}\n"
                f"📞 Телефон: {order.get('phone', '-')}\n"
                f"🚗 VIN: {order.get('vin', '-')}\n"
                f"📊 Пробег: {order.get('mileage', '-')} км\n"
                f"🏙️ Город: {order.get('city', '-')}\n"
                f"📍 Адрес: {order.get('delivery_address', '-')}\n"
                f"🚚 Доставка: {order.get('delivery_type', '-')} | {order.get('delivery_price', 0)} руб.\n"
                f"💰 Сумма запчастей: {order.get('total_price', 0)} руб.\n"
                f"💰 Себестоимость: {order.get('our_cost', 0)} руб.\n"
                f"💵 Маржа: {order.get('total_price', 0) - order.get('our_cost', 0)} руб.\n"
                f"📦 Статус: {order.get('status_text', '-')}\n"
                f"📅 Создан: {order.get('created_at', '-')}")
        if order.get('tracking_number'):
            text += f"\n📮 Трек-номер: {order.get('tracking_number')}"
        if order.get('selected_products'):
            text += f"\n\n📦 **Подбор менеджера:**\n{order.get('selected_products')}"
        if order.get('needed_parts'):
            text += f"\n\n📝 **Запчасти клиента:**\n{order.get('needed_parts')}"
        await q.edit_message_text(text, parse_mode='Markdown')
        return
    
    # --- УДАЛИТЬ ЗАКАЗ (запрос подтверждения) ---
    if data.startswith("delete_"):
        order_num = data[7:]
        order_num = clean_order_number(order_num)
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"confirm_delete_{order_num}")],
            [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data=f"admin_order_{order_num}")]
        ])
        await q.edit_message_text(f"⚠️ **Удалить заказ {order_num}?**\n\nЭто действие нельзя отменить.", reply_markup=kb, parse_mode='Markdown')
        return
    
    # --- ПОДТВЕРЖДЕНИЕ УДАЛЕНИЯ (С ВОЗВРАТОМ БОНУСОВ) ---
    if data.startswith("confirm_delete_"):
        order_num = data[14:]
        order_num = clean_order_number(order_num)
        print(f"🔍 Удаляем заказ: {order_num}")
        order = get_order(order_num)
        
        if order:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('SELECT amount FROM bonus_history WHERE order_number = ? AND type = "earned"', (order_num,))
            bonus_row = c.fetchone()
            
            c.execute('DELETE FROM orders WHERE order_number = ?', (order_num,))
            conn.commit()
            conn.close()
            
            if bonus_row and bonus_row[0] > 0:
                bonus_amount = bonus_row[0]
                refund_bonus(order['user_id'], order_num, bonus_amount, f"Возврат бонусов при удалении заказа {order_num}")
                print(f"💰 Возвращено бонусов: {bonus_amount}")
                await ctx.bot.send_message(order['user_id'], text=f"🗑️ Заказ {order_num} удалён менеджером.\n\n💰 Бонусы в размере {int(bonus_amount)} руб. были списаны с вашего счета.")
            else:
                await ctx.bot.send_message(order['user_id'], text=f"🗑️ Заказ {order_num} удалён менеджером.")
        
        await admin_menu(upd, ctx, q.message)
        return

async def track_input(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        return
    if 'track_for' in ctx.user_data:
        order_num = ctx.user_data.pop('track_for')
        update_order(order_num, tracking_number=upd.message.text, status='shipped', status_text='🚚 Отправлен')
        order = get_order(order_num)
        if order:
            await ctx.bot.send_message(order['user_id'], text=f"📦 Заказ {order_num} отправлен!\nТрек-номер: {upd.message.text}")
        await upd.message.reply_text(f"✅ Трек-номер добавлен к заказу {order_num}")

async def fix_orders(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        await upd.message.reply_text("⛔ Нет доступа")
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
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
    conn.close()
    await upd.message.reply_text(f"✅ Тестовый заказ {num} создан!\n\n/menu")

async def set_commands(application):
    await application.bot.set_my_commands([
        ("start", "Главное меню"),
        ("my_orders", "Мои заказы"),
        ("bonus", "Бонусы"),
        ("referral", "Рефералы"),
        ("delivery", "Доставка"),
        ("help", "Помощь"),
        ("menu", "Панель управления"),
        ("fix", "Тестовый заказ"),
        ("resend", "Повторно отправить подбор"),
    ])

# ========== ЗАПУСК ==========
def main():
    init_db()
    
    # Запуск планировщика бэкапов
    scheduler = BackgroundScheduler()
    scheduler.add_job(backup_db, 'cron', hour=3, minute=0)
    scheduler.start()
    print("⏰ Автобэкап базы данных в 03:00")
    
    app = Application.builder().token(BOT_TOKEN).build()
    app.post_init = set_commands
    
    # ConversationHandler для заказа
    order_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(🛒 Новый заказ)$"), new_order)],
        states={
            VIN: [CallbackQueryHandler(order_auto_callback, pattern="^order_"), MessageHandler(filters.TEXT & ~filters.COMMAND, get_vin)],
            MILEAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mileage)],
            STYLE_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_city)],
            STYLE_HIGHWAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_highway)],
            DELIVERY_TYPE: [MessageHandler(filters.Regex("^(Курьером|Самовывоз|Сторонняя фирма)$"), get_delivery_type)],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            PART_NODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_part_node)],
            AXLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_axle)],
            PARTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_parts)],
            CONFIRM: [MessageHandler(filters.Regex("^(✅ Готово|✏️ Редактировать)$"), confirm_order)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # ConversationHandler для гаража
    garage_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(garage_add_start, pattern="^garage_add$")],
        states={11: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_get_vin)], 12: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_get_description)]},
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # ConversationHandler для списания бонусов
    spend_bonus_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(spend_custom_bonus_callback, pattern="^spend_custom_bonus_")],
        states={SPEND_BONUS: [MessageHandler(filters.TEXT & ~filters.COMMAND, spend_custom_bonus_input)]},
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # Регистрация обработчиков
    app.add_handler(CommandHandler("start", start))
    app.add_handler(order_conv)
    app.add_handler(garage_conv)
    app.add_handler(spend_bonus_conv)
    app.add_handler(CommandHandler("my_orders", my_orders))
    app.add_handler(CommandHandler("bonus", bonus_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("delivery", delivery_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", admin_menu))
    app.add_handler(CommandHandler("fix", fix_orders))
    app.add_handler(CommandHandler("resend", resend_selection))
    
    # Обработчики кнопок меню
    app.add_handler(MessageHandler(filters.Regex("^(🚗 Мой гараж)$"), garage_menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    
    # Административные обработчики
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), manager_reply))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), track_input))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), send_question_to_client))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, client_answer))
    
    # Callback-обработчики (порядок ВАЖЕН!)
    app.add_handler(CallbackQueryHandler(select_cb, pattern="^sel_"))
    app.add_handler(CallbackQueryHandler(finalize_cb, pattern="^fin_"))
    app.add_handler(CallbackQueryHandler(view_order, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(back_orders_list, pattern="^back_orders_list$"))
    app.add_handler(CallbackQueryHandler(main_menu_back, pattern="^main_menu_back$"))
    app.add_handler(CallbackQueryHandler(bonus_callback, pattern="^bonus_"))
    app.add_handler(CallbackQueryHandler(apply_bonus_callback, pattern="^apply_bonus_"))
    app.add_handler(CallbackQueryHandler(spend_all_bonus_callback, pattern="^spend_all_bonus_"))
    app.add_handler(CallbackQueryHandler(garage_delete, pattern="^garage_del_"))
    app.add_handler(CallbackQueryHandler(garage_confirm_delete, pattern="^garage_confirm_del_"))
    app.add_handler(CallbackQueryHandler(admin_callback))
    app.add_handler(CallbackQueryHandler(ask_client, pattern="^ask_"))
    
    print("🤖 БОТ ЗАПУЩЕН!")
    print(f"👨‍💼 Админ ID: {MANAGER_ID}")
    print(f"💾 База данных: {DB_PATH}")
    app.run_polling()

if __name__ == "__main__":
    main()
