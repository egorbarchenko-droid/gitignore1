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
from datetime import datetime, timedelta
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, filters
from telegram.warnings import PTBUserWarning
from apscheduler.schedulers.asyncio import AsyncIOScheduler

# Подавление предупреждений
warnings.filterwarnings("ignore", message=r".*CallbackQueryHandler", category=PTBUserWarning)
warnings.filterwarnings("ignore", message=r".*per_*", category=PTBUserWarning)

# Настройка логов
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

# Состояния
VIN, MILEAGE, STYLE_CITY, STYLE_HIGHWAY, DELIVERY_TYPE, ADDRESS, PHONE, PART_NODE, AXLE, PARTS, CONFIRM = range(11)
GARAGE_VIN, GARAGE_DESCRIPTION = range(11, 13)

# Узлы для оси
AXLE_REQUIRED_NODES = ["Подвеска", "Тормозная система", "Рулевое управление"]

# Глобальная переменная для выбора запчастей
user_selections = {}

# ========== ФУНКЦИИ РЕЗЕРВНОГО КОПИРОВАНИЯ ==========
def create_backup():
    if not os.path.exists(DB_PATH):
        return None
    timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
    backup_file = os.path.join(BACKUP_DIR, f"shop_bot_backup_{timestamp}.db")
    import shutil
    shutil.copy2(DB_PATH, backup_file)
    # Очищаем старые бэкапы (старше 30 дней)
    for f in os.listdir(BACKUP_DIR):
        f_path = os.path.join(BACKUP_DIR, f)
        if os.path.isfile(f_path) and f.endswith('.db'):
            if datetime.fromtimestamp(os.path.getmtime(f_path)) < datetime.now() - timedelta(days=30):
                os.remove(f_path)
    return backup_file

async def auto_backup_job():
    """Автоматическое создание бэкапа каждые 24 часа"""
    backup_file = create_backup()
    if backup_file:
        print(f"✅ Автоматический бэкап создан: {backup_file}")

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE,
        user_id INTEGER,
        user_name TEXT,
        phone TEXT,
        vin TEXT,
        mileage TEXT,
        style_city TEXT,
        style_highway TEXT,
        city TEXT,
        distance INTEGER DEFAULT 0,
        delivery_type TEXT,
        delivery_price INTEGER DEFAULT 500,
        delivery_address TEXT,
        part_node TEXT,
        axle TEXT,
        needed_parts TEXT,
        selected_products TEXT,
        final_order TEXT,
        status TEXT,
        status_text TEXT,
        tracking_number TEXT,
        total_price INTEGER DEFAULT 0,
        our_cost INTEGER DEFAULT 0,
        created_at TEXT,
        viewed INTEGER DEFAULT 0
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS garage (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        vin TEXT,
        description TEXT,
        created_at TEXT,
        UNIQUE(user_id, vin)
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bonuses (
        user_id INTEGER PRIMARY KEY,
        balance REAL DEFAULT 0,
        total_earned REAL DEFAULT 0,
        total_spent REAL DEFAULT 0,
        referrer_id INTEGER DEFAULT NULL
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bonus_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        order_number TEXT,
        amount REAL,
        type TEXT,
        description TEXT,
        created_at TEXT
    )''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        referrer_id INTEGER,
        referred_id INTEGER,
        created_at TEXT
    )''')
    
    try:
        c.execute('ALTER TABLE orders ADD COLUMN viewed INTEGER DEFAULT 0')
    except:
        pass
    
    conn.commit()
    conn.close()
    print(f"✅ База данных: {DB_PATH}")
    
    create_backup()

# ========== ОСНОВНЫЕ ФУНКЦИИ ==========
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
    for key, val in kwargs.items():
        try:
            c.execute(f"UPDATE orders SET {key} = ? WHERE order_number = ?", (val, order_number))
        except Exception as e:
            print(f"Ошибка обновления {key}: {e}")
    conn.commit()
    conn.close()

def safe_int(val, default=0):
    try:
        return int(val) if val else default
    except (ValueError, TypeError):
        return default

def safe_str(val, default=''):
    return str(val) if val else default

def parse_date(date_str):
    if not date_str:
        return datetime.now()
    try:
        return datetime.fromisoformat(date_str)
    except:
        return datetime.now()

def get_order(order_number):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,))
    row = c.fetchone()
    conn.close()
    
    if not row:
        return None
    
    order = {
        'id': safe_int(row[0]),
        'order_number': safe_str(row[1]),
        'user_id': safe_int(row[2]),
        'user_name': safe_str(row[3]),
        'phone': safe_str(row[4] if len(row) > 4 else ''),
        'vin': safe_str(row[5] if len(row) > 5 else ''),
        'mileage': safe_str(row[6] if len(row) > 6 else ''),
        'style_city': safe_str(row[7] if len(row) > 7 else ''),
        'style_highway': safe_str(row[8] if len(row) > 8 else ''),
        'city': safe_str(row[9] if len(row) > 9 else ''),
        'distance': safe_int(row[10] if len(row) > 10 else 0),
        'delivery_type': safe_str(row[11] if len(row) > 11 else ''),
        'delivery_price': safe_int(row[12] if len(row) > 12 else 500),
        'delivery_address': safe_str(row[13] if len(row) > 13 else ''),
        'part_node': safe_str(row[14] if len(row) > 14 else ''),
        'axle': safe_str(row[15] if len(row) > 15 else ''),
        'needed_parts': safe_str(row[16] if len(row) > 16 else ''),
        'selected_products': row[17] if len(row) > 17 else None,
        'final_order': row[18] if len(row) > 18 else None,
        'status': row[19] if len(row) > 19 else 'pending',
        'status_text': row[20] if len(row) > 20 else '🆕 Ожидает подбора',
        'tracking_number': row[21] if len(row) > 21 else None,
        'total_price': safe_int(row[22] if len(row) > 22 else 0),
        'our_cost': safe_int(row[23] if len(row) > 23 else 0),
        'created_at': safe_str(row[24] if len(row) > 24 else ''),
        'viewed': safe_int(row[25] if len(row) > 25 else 0),
    }
    return order

def get_all_orders():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT order_number, user_name, status_text, created_at, viewed FROM orders ORDER BY id DESC')
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
    if r:
        return {'balance': r[0] or 0, 'total_earned': r[1] or 0, 'total_spent': r[2] or 0}
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

def get_bonus_history(user_id, limit=20):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''SELECT order_number, amount, type, description, created_at 
                 FROM bonus_history 
                 WHERE user_id = ? 
                 ORDER BY created_at DESC 
                 LIMIT ?''', (user_id, limit))
    rows = c.fetchall()
    conn.close()
    return rows

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

def calc_delivery_price(km):
    if km <= 0: return DELIVERY_BASE
    if km <= 50: return DELIVERY_BASE + km * 25
    if km <= 100: return DELIVERY_BASE + km * 35
    return DELIVERY_BASE + km * 50

def extract_city_from_address(address):
    address_lower = address.lower()
    cities = {
        'москва': 'Москва', 'мск': 'Москва',
        'химки': 'Химки', 'мытищи': 'Мытищи', 'люберцы': 'Люберцы',
        'красногорск': 'Красногорск', 'одинцово': 'Одинцово', 'долгопрудный': 'Долгопрудный',
        'реутов': 'Реутов', 'балашиха': 'Балашиха', 'королёв': 'Королёв', 'видное': 'Видное',
        'подольск': 'Подольск', 'дзержинский': 'Дзержинский', 'котельники': 'Котельники',
        'львовский': 'Львовский', 'троицк': 'Троицк', 'щелково': 'Щелково'
    }
    for key, city_name in cities.items():
        if key in address_lower:
            return city_name
    return 'Неизвестный город'

def extract_distance_from_address(address):
    address_lower = address.lower()
    distances = {
        'москва': 0, 'мск': 0,
        'химки': 5, 'мытищи': 8, 'люберцы': 10,
        'красногорск': 7, 'одинцово': 10, 'долгопрудный': 12,
        'реутов': 8, 'балашиха': 15, 'королёв': 18, 'видное': 10,
        'подольск': 25, 'дзержинский': 15, 'котельники': 12,
        'львовский': 30, 'троицк': 35, 'щелково': 20
    }
    for key, dist in distances.items():
        if key in address_lower:
            return dist
    return 30

def delivery_discount(order_sum):
    if order_sum < 10000: return 0
    return min(100, ((order_sum - 10000) // 5000) * 5 + 5)

def parse_products(text):
    products = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line:
            continue
        price_match = re.search(r'(\d{1,3}(?:[\s\.]?\d{3})*)\s*(?:руб|₽|р\.)', line, re.I)
        if not price_match:
            continue
        price_str = price_match.group(1).replace(' ', '').replace('.', '')
        try:
            price = float(price_str)
        except ValueError:
            continue
        name = re.sub(r'\d{1,3}(?:[\s\.]\d{3})*\s*(?:руб|₽|р\.)', '', line).strip()
        name = re.sub(r'[=•\-–—]|арт\.?\S+|\([^)]*\)', '', name).strip()
        name = name[:40] + ".." if len(name) > 40 else name
        if name:
            products.append({'name': name, 'price': price})
    return products

def get_status_bar(status_text):
    statuses = [
        ("Ожидает подбора", 1),
        ("Ожидает выбора", 2),
        ("Оплачен", 3),
        ("Отправлен", 4),
        ("Доставлен", 5)
    ]
    current = 0
    for s, step in statuses:
        if s in status_text:
            current = step
            break
    
    bar = ""
    for i in range(1, 6):
        if i < current:
            bar += "✅ "
        elif i == current:
            bar += "🟢 "
        else:
            bar += "⚪ "
    return bar

def get_order_progress(step):
    steps = ["VIN", "Пробег", "Стиль", "Доставка", "Адрес", "Телефон", "Узел", "Запчасти", "Подтверждение"]
    progress = ""
    for i, s in enumerate(steps):
        if i < step:
            progress += "✅ "
        elif i == step:
            progress += "🟢 "
        else:
            progress += "◻️ "
    return progress

# ========== ФУНКЦИИ ГАРАЖА ==========
def save_car(user_id, vin, description):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT OR REPLACE INTO garage (user_id, vin, description, created_at) VALUES (?,?,?,?)',
              (user_id, vin, description, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()

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

# ========== КЛАВИАТУРЫ ==========
main_menu = ReplyKeyboardMarkup([
    ["🛒 Новый заказ", "🚗 Мой гараж"],
    ["📦 Мои заказы", "🎁 Бонусы"],
    ["🔗 Рефералы", "🚚 Доставка"],
    ["ℹ️ Помощь"]
], resize_keyboard=True)

city_style_kb = ReplyKeyboardMarkup([
    ["Спокойный (до 60 км/ч)"],
    ["Умеренный (60-90 км/ч)"],
    ["Активный (90-120 км/ч)"],
    ["Спортивный (120+ км/ч)"]
], resize_keyboard=True)

highway_style_kb = ReplyKeyboardMarkup([
    ["Спокойный (80-100 км/ч)"],
    ["Умеренный (100-120 км/ч)"],
    ["Активный (120-140 км/ч)"],
    ["Спортивный (140+ км/ч)"]
], resize_keyboard=True)

delivery_type_kb = ReplyKeyboardMarkup([
    ["Курьером"],
    ["Самовывоз"],
    ["Сторонняя фирма"]
], resize_keyboard=True)

pickup_station_kb = ReplyKeyboardMarkup([
    ["Метро Давыдково"],
    ["Метро Строгино"],
    ["Метро Южная"]
], resize_keyboard=True)

part_node_kb = ReplyKeyboardMarkup([
    ["Двигатель", "Подвеска"],
    ["Тормозная система", "Трансмиссия"],
    ["Электрика", "Охлаждение"],
    ["Отопление", "Выхлопная система"],
    ["Рулевое управление", "Другое"]
], resize_keyboard=True)

axle_kb = ReplyKeyboardMarkup([
    ["Передняя ось"],
    ["Задняя ось"],
    ["Передняя + Задняя"]
], resize_keyboard=True)

confirm_order_kb = ReplyKeyboardMarkup([
    ["✅ Готово", "✏️ Редактировать"]
], resize_keyboard=True)

# ========== КЛИЕНТЫ ==========
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
            "🛒 Новый заказ - подбор запчастей по вашему авто\n"
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
            vin = car[0]
            desc = car[1][:20] if car[1] else "без описания"
            keyboard.append([InlineKeyboardButton(f"🚗 {vin} ({desc})", callback_data=f"order_auto_{vin}")])
        keyboard.append([InlineKeyboardButton("🔄 Повторить последний заказ", callback_data="order_repeat")])
        
        await upd.message.reply_text(
            "🔧 ВЫБЕРИТЕ АВТОМОБИЛЬ\n\n"
            "У вас есть автомобили в гараже. Выберите один из них\n"
            "или введите VIN вручную:",
            reply_markup=InlineKeyboardMarkup(keyboard)
        )
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
    elif q.data == "order_repeat":
        orders = get_user_orders(q.from_user.id)
        if orders:
            last_order = get_order(orders[0][0])
            if last_order:
                ctx.user_data['vin'] = last_order['vin']
                ctx.user_data['mileage'] = last_order['mileage']
                ctx.user_data['style_city'] = last_order['style_city']
                ctx.user_data['style_highway'] = last_order['style_highway']
                await q.edit_message_text(
                    f"🔄 Данные скопированы из последнего заказа!\n"
                    f"🚗 VIN: {last_order['vin']}\n"
                    f"📊 Пробег: {last_order['mileage']} км\n\n"
                    f"Вы можете изменить пробег или отправить '=' чтобы оставить как есть:"
                )
                return MILEAGE
        await q.edit_message_text("❌ Нет предыдущих заказов для повтора. Введите VIN вручную:")
        return VIN
    elif q.data.startswith("order_auto_"):
        vin = q.data[11:]
        ctx.user_data['vin'] = vin
        await q.edit_message_text(f"🚗 Выбран автомобиль: {vin}\n\n📊 Теперь введите пробег (км):")
        return MILEAGE

async def get_vin(upd, ctx):
    message = upd.message
    if message.photo:
        await message.reply_text("📸 Пожалуйста, введите VIN вручную (17 символов):")
        return VIN
    vin = message.text.upper().strip()
    if len(vin) != 17:
        await message.reply_text("❌ VIN должен быть 17 символов. Попробуйте ещё раз:")
        return VIN
    ctx.user_data['vin'] = vin
    progress = get_order_progress(0)
    await message.reply_text(f"{progress}\n\n📊 Введите пробег (км):")
    return MILEAGE

async def get_mileage(upd, ctx):
    if upd.message.text == "=" and ctx.user_data.get('mileage'):
        pass
    else:
        try:
            mileage = int(upd.message.text)
            if mileage < 0:
                await upd.message.reply_text("❌ Пробег не может быть отрицательным. Введите корректный пробег:")
                return MILEAGE
            ctx.user_data['mileage'] = str(mileage)
        except ValueError:
            await upd.message.reply_text("❌ Пожалуйста, введите число (пробег в км):")
            return MILEAGE
    
    progress = get_order_progress(1)
    await upd.message.reply_text(f"{progress}\n\n🏙️ Стиль вождения в городе:", reply_markup=city_style_kb)
    return STYLE_CITY

async def get_style_city(upd, ctx):
    ctx.user_data['style_city'] = upd.message.text
    progress = get_order_progress(2)
    await upd.message.reply_text(f"{progress}\n\n🛣️ Стиль вождения на трассе:", reply_markup=highway_style_kb)
    return STYLE_HIGHWAY

async def get_style_highway(upd, ctx):
    ctx.user_data['style_highway'] = upd.message.text
    progress = get_order_progress(3)
    await upd.message.reply_text(f"{progress}\n\n🚚 Способ доставки:", reply_markup=delivery_type_kb)
    return DELIVERY_TYPE

async def get_delivery_type(upd, ctx):
    choice = upd.message.text
    ctx.user_data['delivery_type'] = choice
    
    if choice == "Курьером":
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text(
            "📍 Введите ПОЛНЫЙ АДРЕС доставки\n\n"
            "Пример: г. Москва, ул. Тверская, д. 15, кв. 78\n\n"
            "💡 Стоимость доставки рассчитает менеджер после подтверждения заказа"
        )
        return ADDRESS
    elif choice == "Самовывоз":
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text(
            "📍 Самовывоз\n\nДоступные станции:\n"
            "- Метро Давыдково\n- Метро Строгино\n- Метро Южная\n\n"
            "Введите адрес самовывоза:"
        )
        return ADDRESS
    else:
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("🚛 Сторонняя фирма (стоимость рассчитает менеджер)\n\n📍 Введите адрес доставки:")
        return ADDRESS

async def get_address(upd, ctx):
    full_address = upd.message.text.strip()
    ctx.user_data['delivery_address'] = full_address
    
    city = extract_city_from_address(full_address)
    distance = extract_distance_from_address(full_address)
    
    ctx.user_data['city'] = city
    ctx.user_data['distance'] = distance
    
    progress = get_order_progress(4)
    await upd.message.reply_text(
        f"{progress}\n\n📍 Адрес: {full_address}\n"
        f"🏙️ Город: {city}\n📏 Расстояние от МКАД: ~{distance} км\n\n"
        f"💡 Стоимость доставки будет рассчитана менеджером\n\n"
        f"📞 Введите ваш контактный телефон:"
    )
    return PHONE

async def get_phone(upd, ctx):
    phone = upd.message.text.strip()
    if len(phone) < 5:
        await upd.message.reply_text("❌ Пожалуйста, введите корректный номер телефона:")
        return PHONE
    ctx.user_data['phone'] = phone
    progress = get_order_progress(5)
    await upd.message.reply_text(f"{progress}\n\n🔧 Выберите узел запчасти:", reply_markup=part_node_kb)
    return PART_NODE

async def get_part_node(upd, ctx):
    ctx.user_data['part_node'] = upd.message.text
    progress = get_order_progress(6)
    
    if upd.message.text in AXLE_REQUIRED_NODES:
        await upd.message.reply_text(f"{progress}\n\n🔧 Выберите ось:", reply_markup=axle_kb)
        return AXLE
    else:
        ctx.user_data['axle'] = "Не требуется"
        await upd.message.reply_text(
            f"{progress}\n\n🔧 Какие запчасти нужны? (каждая с новой строки)\n\n"
            "Пример:\nКолодки тормозные\nДиски тормозные"
        )
        return PARTS

async def get_axle(upd, ctx):
    ctx.user_data['axle'] = upd.message.text
    progress = get_order_progress(6)
    await upd.message.reply_text(
        f"{progress}\n\n🔧 Какие запчасти нужны? (каждая с новой строки)\n\n"
        "Пример:\nКолодки тормозные\nДиски тормозные"
    )
    return PARTS

async def get_parts(upd, ctx):
    if not upd.message.text.strip():
        await upd.message.reply_text("❌ Вы не ввели запчасти. Пожалуйста, введите список запчастей:")
        return PARTS
    ctx.user_data['needed_parts'] = upd.message.text
    
    data = ctx.user_data
    progress = get_order_progress(7)
    summary = (f"{progress}\n\n📋 ПРОВЕРЬТЕ ЗАКАЗ\n\n"
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
               f"📝 Запчасти: {data.get('needed_parts', 'не указаны')}\n\n"
               f"💰 Доставка: {data.get('delivery_price', 500)} руб.\n\n"
               "✅ Всё верно? Нажмите «Готово» или «Редактировать»")
    
    await upd.message.reply_text(summary, reply_markup=confirm_order_kb)
    return CONFIRM

async def confirm_order(upd, ctx):
    if upd.message.text == "✅ Готово":
        data = ctx.user_data
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
        
        await upd.context.bot.send_message(
            MANAGER_ID,
            f"🆕 НОВЫЙ ЗАКАЗ #{order_num}\n\n"
            f"👤 Клиент: {upd.effective_user.full_name}\n"
            f"📞 Телефон: {data.get('phone','')}\n"
            f"🚗 VIN: {data.get('vin','')}\n"
            f"📊 Пробег: {data.get('mileage','')} км\n"
            f"🏙️ Город: {data.get('city','')}\n"
            f"🚚 Доставка: {data.get('delivery_type','')}\n"
            f"📍 Адрес: {data.get('delivery_address','не указан')}\n"
            f"📏 Расстояние от МКАД: {data.get('distance', 0)} км\n\n"
            f"📝 Запчасти:\n{data.get('needed_parts','')}\n\n"
            f"➡️ Для подбора запчастей ответьте на это сообщение"
        )
        
        await upd.message.reply_text(
            f"✅ ЗАКАЗ #{order_num} ПРИНЯТ!\n\n"
            f"🚚 Доставка: будет рассчитана менеджером\n"
            f"📍 Адрес: {data.get('delivery_address','не указан')}\n\n"
            f"🔧 Менеджер скоро свяжется с вами.\n\n"
            f"Вы можете вернуться в главное меню:",
            reply_markup=main_menu
        )
        return ConversationHandler.END
    
    elif upd.message.text == "✏️ Редактировать":
        await upd.message.reply_text("✏️ Давайте начнём заказ заново. Нажмите 🛒 Новый заказ", reply_markup=main_menu)
        return ConversationHandler.END

async def my_orders(upd, ctx):
    orders = get_user_orders(upd.effective_user.id)
    if not orders:
        await upd.message.reply_text("📭 У вас пока нет заказов", reply_markup=main_menu)
        return
    
    text = "📦 ВАШИ ЗАКАЗЫ:\n\n"
    kb = []
    
    for o in orders:
        order_num = o[0]
        status_text = o[1]
        created = o[2][:10] if o[2] else "дата неизвестна"
        total_price = int(o[3]) if o[3] else 0
        delivery_price = int(o[4]) if o[4] else 0
        total = total_price + delivery_price
        
        if 'Ожидает подбора' in status_text:
            icon = "🆕"
        elif 'Ожидает ответа' in status_text or 'Ожидает выбора' in status_text:
            icon = "🟡"
        elif 'Оплачен' in status_text:
            icon = "💰"
        elif 'Отправлен' in status_text:
            icon = "🚚"
        elif 'Доставлен' in status_text:
            icon = "✅"
        else:
            icon = "📦"
        
        text += f"{icon} {order_num} — {created} — {total} руб.\n"
        kb.append([InlineKeyboardButton(f"🔍 Заказ {order_num}", callback_data=f"view_{order_num}")])
    
    kb.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="back_to_menu")])
    
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def view_order(upd, ctx):
    q = upd.callback_query
    await q.answer()
    order_num = q.data[5:]
    order = get_order(order_num)
    if not order:
        await q.edit_message_text("❌ Заказ не найден")
        return
    
    total_sum = int(order.get('total_price', 0)) + int(order.get('delivery_price', 0))
    status_bar = get_status_bar(order.get('status_text', ''))
    
    text = (f"📋 ЗАКАЗ {order['order_number']}\n\n"
            f"{status_bar}\n\n"
            f"👤 {order['user_name']}\n"
            f"📅 {order['created_at']}\n"
            f"🚗 VIN: {order.get('vin', 'не указан')}\n"
            f"📊 Пробег: {order.get('mileage', 'не указан')} км\n"
            f"🏙️ Город: {order.get('city', 'не указан')}\n"
            f"🚚 Доставка: {order.get('delivery_type', 'не указана')} | {order.get('delivery_price', 0)} руб.\n"
            f"💰 Общая сумма: {total_sum} руб.\n"
            f"📦 Статус: {order.get('status_text', 'неизвестен')}")
    
    if order.get('tracking_number'):
        text += f"\n📮 Трек: {order.get('tracking_number')}"
    
    if order.get('final_order') and order['final_order'] not in [None, 'None', '[]']:
        text += "\n\n📦 ЗАКАЗАННЫЕ ЗАПЧАСТИ:\n"
        try:
            selected_parts = ast.literal_eval(order['final_order'])
            if isinstance(selected_parts, list) and len(selected_parts) > 0:
                for part in selected_parts[:10]:
                    if isinstance(part, dict):
                        name = part.get('name', 'неизвестно')
                        price = part.get('price', 0)
                        text += f"• {name} — {int(price)} руб.\n"
            else:
                text += f"{order['final_order'][:300]}"
        except:
            text += f"{order['final_order'][:300]}"
    elif order.get('needed_parts'):
        text += f"\n\n📝 ИЗНАЧАЛЬНЫЙ ЗАПРОС:\n{order.get('needed_parts', 'не указан')[:300]}"
    
    kb = [[InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders")]]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def back_orders(upd, ctx):
    q = upd.callback_query
    await q.answer()
    await my_orders(upd, ctx)

async def back_to_menu(upd, ctx):
    q = upd.callback_query
    await q.answer()
    await start(upd, ctx)

async def bonus_cmd(upd, ctx):
    uid = upd.effective_user.id
    bonus_data = get_bonus(uid)
    bal = bonus_data['balance']
    total_earned = bonus_data['total_earned']
    total_spent = bonus_data['total_spent']
    total_purchases = get_user_total(uid)
    percent = get_bonus_percent(uid)
    
    history = get_bonus_history(uid)
    
    text = (f"🎁 БОНУСНАЯ ПРОГРАММА\n\n"
            f"💰 Баланс: {int(bal)} бонусов\n"
            f"📈 Всего начислено: {int(total_earned)} бонусов\n"
            f"📉 Всего потрачено: {int(total_spent)} бонусов\n"
            f"🛒 Накоплено по покупкам: {int(total_purchases)} руб.\n"
            f"⭐ Текущий процент: {percent}%\n\n"
            "📊 Градация:\n"
            "1% -> до 100 000 руб.\n"
            "2% -> 100-200 т.р.\n"
            "3% -> 200-300 т.р.\n"
            "4% -> 300-400 т.р.\n"
            "5% -> 400-500 т.р.\n"
            "6% -> 500-600 т.р.\n"
            "7% -> 600-700 т.р.\n"
            "8% -> 700-800 т.р.\n"
            "9% -> 800-900 т.р.\n"
            "10% -> от 900 т.р.\n\n"
            "📜 История операций:")
    
    await upd.message.reply_text(text)
    
    if history:
        history_text = ""
        for h in history[:10]:
            order_num = h[0] if h[0] else "—"
            amount = int(h[1])
            h_type = "Начисление" if h[2] == 'earned' else "Списание"
            desc = h[3][:40] if h[3] else ""
            created = h[4][:16] if h[4] else ""
            history_text += f"\n• {amount} руб. | {h_type} | {desc[:30]}\n  {created}\n"
        
        if len(history_text) > 4000:
            for i in range(0, len(history_text), 4000):
                await upd.message.reply_text(history_text[i:i+4000])
        else:
            await upd.message.reply_text(history_text)
    else:
        await upd.message.reply_text("📭 История операций пуста")

async def referral_cmd(upd, ctx):
    bot_username = (await ctx.bot.get_me()).username
    link = f"https://t.me/{bot_username}?start=ref_{upd.effective_user.id}"
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM referrals WHERE referrer_id = ?', (upd.effective_user.id,))
    referrals_count = c.fetchone()[0]
    conn.close()
    
    text = (f"🔗 РЕФЕРАЛЬНАЯ ССЫЛКА\n\n{link}\n\n"
            f"👥 Приглашено: {referrals_count}\n"
            "📊 Вы получаете 0.5% от суммы заказов друзей бонусами!\n"
            "🎁 Друг получает 500 бонусов!\n\n"
            f"💰 Баланс: {int(get_bonus(upd.effective_user.id)['balance'])} бонусов")
    
    await upd.message.reply_text(text)

async def delivery_cmd(upd, ctx):
    text = (f"🚚 РАСЧЁТ ДОСТАВКИ ОТ МКАД\n\n"
            f"Базовая стоимость: {DELIVERY_BASE} руб.\n\n"
            "📌 Тарифы:\n"
            f"0 км: {DELIVERY_BASE} руб.\n"
            f"1-50 км: {DELIVERY_BASE} + км×25\n"
            f"51-100 км: {DELIVERY_BASE} + км×35\n"
            f"101+ км: {DELIVERY_BASE} + км×50\n\n"
            "📌 Самовывоз: бесплатно\n"
            "- Метро Давыдково, Строгино, Южная\n\n"
            "📌 Скидка на доставку от суммы заказа:\n"
            "от 10 000 руб. -> 5%\nот 15 000 руб. -> 10%\n... до 100%")
    
    await upd.message.reply_text(text)

async def help_cmd(upd, ctx):
    text = ("📖 ПОМОЩЬ\n\n"
            "/start - Главное меню\n"
            "/my_orders - Мои заказы\n"
            "/bonus - Бонусы\n"
            "/referral - Рефералы\n"
            "/delivery - Доставка\n\n"
            "Администратор:\n"
            "/menu - Панель управления\n"
            "/fix - Тестовый заказ\n\n"
            "По всем вопросам - менеджеру")
    
    await upd.message.reply_text(text, reply_markup=main_menu)

# ========== МЕНЕДЖЕР ==========
async def manager_reply(upd, ctx):
    if upd.effective_user.id != MANAGER_ID or not upd.message.reply_to_message:
        return
    
    match = re.search(r"НОВЫЙ ЗАКАЗ #(RVN-\w{6})", upd.message.reply_to_message.text or "")
    if not match:
        await upd.message.reply_text("❌ Не удалось определить номер заказа. Убедитесь, что вы отвечаете на сообщение о заказе.")
        return
    
    order_num = match.group(1)
    if not upd.message.text:
        await upd.message.reply_text("❌ Вы не ввели подбор запчастей. Введите список с ценами:")
        return
    
    products = parse_products(upd.message.text)
    
    if not products:
        await upd.message.reply_text(
            "❌ Не распознано. Формат:\nНазвание = 1000 руб\n\n"
            "Пример:\nМасло Ravenol 5W-40 = 3500 руб\nФильтр = 500 руб"
        )
        return
    
    total_cost = sum(p['price'] for p in products) * 0.7
    update_order(order_num, selected_products=upd.message.text, our_cost=total_cost, 
                 status='waiting_selection', status_text='🟡 Ожидает выбора')
    
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text(f"❌ Заказ {order_num} не найден!")
        return
    
    kb = [[InlineKeyboardButton(f"⬜ {p['name']} — {int(p['price'])} руб.", callback_data=f"sel_{order_num}_{i}")] for i, p in enumerate(products)]
    kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ВЫБОР", callback_data=f"fin_{order_num}")])
    
    await ctx.bot.send_message(
        order['user_id'],
        text=f"🛒 ПОДБОР ЗАПЧАСТЕЙ ДЛЯ ЗАКАЗА #{order_num}\n\n"
             f"Менеджер подобрал позиции. Выберите нужные:",
        reply_markup=InlineKeyboardMarkup(kb)
    )
    
    await upd.message.reply_text(f"✅ Подбор для заказа {order_num} отправлен клиенту!")

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
        kb.append([InlineKeyboardButton(f"{cb} {p['name']} — {int(p['price'])} руб.", callback_data=f"sel_{order_num}_{i}")])
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
    
    delivery_disc = delivery_discount(total)
    delivery_price = order['delivery_price']
    if delivery_disc >= 100:
        delivery_final = 0
        delivery_text = "🚚 Доставка: БЕСПЛАТНО!"
    elif delivery_disc > 0:
        discount_amount = int(delivery_price * delivery_disc / 100)
        delivery_final = delivery_price - discount_amount
        delivery_text = f"🚚 Доставка: {delivery_price} руб. -> скидка {delivery_disc}% = {delivery_final} руб."
    else:
        delivery_final = delivery_price
        delivery_text = f"🚚 Доставка: {delivery_price} руб."
    
    final_total = total + delivery_final
    update_order(order_num, total_price=total, final_order=str(selected), 
                 status='confirmed', status_text='💰 Ожидает оплаты')
    
    bonus_percent = get_bonus_percent(uid)
    bonus = int(total * bonus_percent / 100)
    add_bonus(uid, order_num, bonus, f"Заказ {order_num} ({bonus_percent}%)")
    
    result = (f"✅ ЗАКАЗ #{order_num} ПОДТВЕРЖДЕН!\n\n" + 
              "\n".join([f"• {p['name']} — {int(p['price'])} руб." for p in selected]) + 
              f"\n\n{delivery_text}\n\n"
              f"💰 ИТОГО: {int(final_total)} руб.")
    if bonus > 0:
        result += f"\n\n🎁 +{bonus} бонусов ({bonus_percent}%)"
    result += "\n\n📞 Менеджер свяжется с вами."
    
    await q.edit_message_text(result)
    
    await ctx.bot.send_message(
        MANAGER_ID,
        f"✅ ЗАКАЗ {order_num} ПОДТВЕРЖДЕН КЛИЕНТОМ!\n\n"
        f"👤 {order['user_name']}\n💰 {int(final_total)} руб.\n🎁 Бонусы: +{bonus}"
    )
    
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
    update_order(order_num, status='waiting_answer', status_text='❓ Ожидает ответа')
    
    await ctx.bot.send_message(
        order['user_id'],
        text=f"❓ Уточнение по заказу {order_num}\n\n{question}\n\nПожалуйста, ответьте."
    )
    await upd.message.reply_text(f"✅ Вопрос отправлен клиенту по заказу {order_num}")
    del ctx.user_data['awaiting_answer_for']

async def client_answer(upd, ctx):
    user_id = upd.effective_user.id
    if user_id == MANAGER_ID:
        return
    
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT order_number FROM orders WHERE user_id = ? AND status = "waiting_answer" LIMIT 1', (user_id,))
    row = c.fetchone()
    conn.close()
    
    if row:
        order_num = row[0]
        update_order(order_num, status='pending', status_text='🆕 Ожидает подбора')
        await ctx.bot.send_message(
            MANAGER_ID,
            text=f"✅ Клиент ответил по заказу {order_num}\n\n📝 {upd.message.text}"
        )
        await upd.message.reply_text("✅ Спасибо! Ответ передан менеджеру.")

# ========== АДМИН ПАНЕЛЬ ==========
async def admin_menu(upd, ctx, callback_query=None):
    if upd.effective_user.id != MANAGER_ID:
        if callback_query:
            await callback_query.edit_message_text("⛔ Доступ запрещён")
        else:
            await upd.message.reply_text("⛔ Доступ запрещён")
        return
    
    orders = get_all_orders()
    
    keyboard = []
    
    keyboard.append([
        InlineKeyboardButton("🆕 Новые", callback_data="filter_🆕"),
        InlineKeyboardButton("🟡 В работе", callback_data="filter_🟡"),
        InlineKeyboardButton("💰 Оплаченные", callback_data="filter_💰"),
        InlineKeyboardButton("🚚 Отправленные", callback_data="filter_🚚"),
        InlineKeyboardButton("✅ Доставленные", callback_data="filter_✅"),
        InlineKeyboardButton("🔄 Все", callback_data="filter_all")
    ])
    
    keyboard.append([
        InlineKeyboardButton("📊 Дашборд", callback_data="admin_dashboard"),
        InlineKeyboardButton("💾 Бэкап", callback_data="admin_backup"),
        InlineKeyboardButton("🔍 Поиск", callback_data="admin_search"),
        InlineKeyboardButton("🔄 Обновить", callback_data="admin_refresh")
    ])
    
    if orders:
        unread_count = sum(1 for o in orders if len(o) > 4 and o[4] == 0)
        if unread_count > 0:
            keyboard.append([InlineKeyboardButton(f"📬 Непрочитанные ({unread_count})", callback_data="filter_unread")])
        
        for o in orders[:20]:
            order_num = o[0]
            user_name = o[1][:12] if len(o[1]) > 12 else o[1]
            status_text = o[2] if len(o) > 2 else ''
            viewed = o[4] if len(o) > 4 else 1
            
            if 'Ожидает подбора' in status_text:
                icon = "🆕"
            elif 'Ожидает ответа' in status_text or 'Ожидает выбора' in status_text:
                icon = "🟡"
            elif 'Оплачен' in status_text:
                icon = "💰"
            elif 'Отправлен' in status_text:
                icon = "🚚"
            elif 'Доставлен' in status_text:
                icon = "✅"
            else:
                icon = "📦"
            
            if viewed == 0:
                icon = "🔴" + icon
            
            keyboard.append([InlineKeyboardButton(f"{icon} {order_num} | {user_name}", callback_data=f"admin_order_{order_num}")])
    
    text = f"👨‍💼 АДМИН ПАНЕЛЬ\n\n📊 Всего заказов: {len(orders)}\n"
    if orders:
        text += f"🆕 Новых: {sum(1 for o in orders if 'Ожидает подбора' in o[2])}\n"
        text += f"🟡 В работе: {sum(1 for o in orders if 'Ожидает ответа' in o[2] or 'Ожидает выбора' in o[2])}\n"
        text += f"💰 Оплаченных: {sum(1 for o in orders if 'Оплачен' in o[2])}\n"
        text += f"🚚 Отправленных: {sum(1 for o in orders if 'Отправлен' in o[2])}\n"
        text += f"✅ Доставленных: {sum(1 for o in orders if 'Доставлен' in o[2])}\n"
    text += "\nВыберите заказ:"
    
    if callback_query:
        await callback_query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(keyboard))
    else:
        await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_refresh(upd, ctx):
    q = upd.callback_query
    await q.answer()
    await admin_menu(upd, ctx, callback_query=q)

async def admin_dashboard(upd, ctx):
    q = upd.callback_query
    await q.answer()
    
    orders = get_all_orders()
    if not orders:
        await q.edit_message_text("📭 Нет данных")
        return
    
    now = datetime.now()
    week_ago = now - timedelta(days=7)
    month_ago = now - timedelta(days=30)
    
    stats = {'total': len(orders), 'week': 0, 'month': 0, 'total_sum': 0, 'week_sum': 0, 'month_sum': 0, 'by_status': {}}
    
    for o in orders:
        order = get_order(o[0])
        if order:
            created = parse_date(order['created_at'])
            total = int(order.get('total_price', 0)) + int(order.get('delivery_price', 0))
            stats['total_sum'] += total
            if created >= week_ago:
                stats['week'] += 1
                stats['week_sum'] += total
            if created >= month_ago:
                stats['month'] += 1
                stats['month_sum'] += total
            status = order.get('status_text', 'Неизвестно')
            stats['by_status'][status] = stats['by_status'].get(status, 0) + 1
    
    text = (f"📊 ДАШБОРД\n\n"
            f"📦 Всего: {stats['total']}\n💰 Сумма: {int(stats['total_sum'])} руб.\n\n"
            f"📅 За неделю: {stats['week']} заказов ({int(stats['week_sum'])} руб.)\n"
            f"📅 За месяц: {stats['month']} заказов ({int(stats['month_sum'])} руб.)\n\n"
            f"📌 По статусам:\n" + "\n".join([f"• {s}: {c}" for s, c in stats['by_status'].items()]))
    
    await q.edit_message_text(text)

async def admin_backup(upd, ctx):
    q = upd.callback_query
    await q.answer()
    backup_file = create_backup()
    if backup_file:
        await q.edit_message_text(f"✅ Бэкап создан: {os.path.basename(backup_file)}")
    else:
        await q.edit_message_text("❌ Ошибка создания бэкапа")

async def admin_search(upd, ctx):
    q = upd.callback_query
    await q.answer()
    ctx.user_data['search_mode'] = 'order'
    await q.edit_message_text("🔍 Введите номер заказа, VIN, телефон или имя:")

async def admin_search_input(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        return
    if 'search_mode' not in ctx.user_data:
        return
    
    search_term = upd.message.text.strip().upper()
    orders = get_all_orders()
    
    results = []
    for o in orders:
        order = get_order(o[0])
        if order:
            if (search_term in order['order_number'] or 
                search_term in order['vin'].upper() or 
                search_term in order['phone'] or 
                search_term in order['user_name'].upper()):
                results.append(order)
    
    if not results:
        await upd.message.reply_text("❌ Заказы не найдены")
        del ctx.user_data['search_mode']
        return
    
    keyboard = []
    for order in results[:10]:
        icon = "📦"
        if 'Оплачен' in order['status_text']:
            icon = "💰"
        elif 'Отправлен' in order['status_text']:
            icon = "🚚"
        elif 'Доставлен' in order['status_text']:
            icon = "✅"
        keyboard.append([InlineKeyboardButton(f"{icon} {order['order_number']} | {order['user_name']}", callback_data=f"admin_order_{order['order_number']}")])
    keyboard.append([InlineKeyboardButton("◀️ Назад", callback_data="admin_back")])
    
    await upd.message.reply_text(f"🔍 Результаты по '{search_term}':", reply_markup=InlineKeyboardMarkup(keyboard))
    del ctx.user_data['search_mode']

async def admin_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data
    print(f"🔍 Callback: {data}")
    
    # Фильтры
    if data.startswith("filter_"):
        await admin_menu(upd, ctx, callback_query=q)
        return
    
    if data == "admin_refresh":
        await admin_refresh(upd, ctx)
        return
    
    if data == "admin_dashboard":
        await admin_dashboard(upd, ctx)
        return
    
    if data == "admin_backup":
        await admin_backup(upd, ctx)
        return
    
    if data == "admin_search":
        await admin_search(upd, ctx)
        return
    
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
        await admin_menu(upd, ctx, callback_query=q)
        return
    
    if data.startswith("admin_order_"):
        order_num = data[12:]
        order = get_order(order_num)
        if not order:
            await q.edit_message_text("❌ Заказ не найден")
            return
        
        update_order(order_num, viewed=1)
        
        total_sum = int(order.get('total_price', 0)) + int(order.get('delivery_price', 0))
        text = (f"📋 ЗАКАЗ {order['order_number']}\n\n"
                f"👤 {order['user_name']}\n"
                f"📞 {order.get('phone', '-')}\n"
                f"🏙️ {order.get('city', '-')}\n"
                f"📍 {order.get('delivery_address', '-')}\n"
                f"🚚 {order.get('delivery_type', '-')} | {order.get('delivery_price', 0)} руб.\n"
                f"💰 Сумма: {total_sum} руб.\n"
                f"📦 Статус: {order.get('status_text', '-')}")
        
        kb = [
            [InlineKeyboardButton("💰 Оплачен", callback_data=f"pay_{order_num}")],
            [InlineKeyboardButton("🚚 Отправлен", callback_data=f"ship_{order_num}")],
            [InlineKeyboardButton("🏠 Доставлен", callback_data=f"del_{order_num}")],
            [InlineKeyboardButton("✏️ Изменить доставку", callback_data=f"edit_delivery_{order_num}")],
            [InlineKeyboardButton("💰 Установить стоимость", callback_data=f"set_delivery_price_{order_num}")],
            [InlineKeyboardButton("❓ Уточнить", callback_data=f"ask_{order_num}")],
            [InlineKeyboardButton("🔍 Детали", callback_data=f"detail_{order_num}")],
            [InlineKeyboardButton("🗑️ Удалить", callback_data=f"delete_{order_num}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data.startswith("edit_delivery_"):
        order_num = data[14:]
        kb = [
            [InlineKeyboardButton("🚚 Курьером", callback_data=f"set_delivery_{order_num}_Курьером")],
            [InlineKeyboardButton("📦 Самовывоз", callback_data=f"set_delivery_{order_num}_Самовывоз")],
            [InlineKeyboardButton("🚛 Сторонняя фирма", callback_data=f"set_delivery_{order_num}_Сторонняя фирма")],
            [InlineKeyboardButton("◀️ Назад", callback_data=f"admin_order_{order_num}")]
        ]
        await q.edit_message_text("✏️ ВЫБЕРИТЕ ДОСТАВКУ:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data.startswith("set_delivery_price_"):
        order_num = data[18:]
        ctx.user_data['set_price_for'] = order_num
        await q.edit_message_text(f"💰 Введите стоимость доставки для {order_num}:")
        return
    
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
                desc = "Сторонняя фирма"
            update_order(order_num, delivery_type=new_delivery, delivery_price=price)
            await ctx.bot.send_message(order['user_id'], text=f"✏️ Доставка изменена: {new_delivery}\n{desc}")
            await q.edit_message_text(f"✅ Доставка изменена!\n\n{desc}")
        return
    
    if data.startswith("pay_"):
        order_num = data[4:]
        update_order(order_num, status='paid', status_text='💰 Оплачен')
        order = get_order(order_num)
        if order:
            await ctx.bot.send_message(order['user_id'], text=f"✅ Заказ {order_num} оплачен!")
        await q.edit_message_text(q.message.text + "\n\n✅ ОПЛАЧЕН")
        return
    
    if data.startswith("ship_"):
        order_num = data[5:]
        ctx.user_data['track_for'] = order_num
        await q.edit_message_text("📦 Введите трек-номер:")
        return
    
    if data.startswith("del_"):
        order_num = data[4:]
        update_order(order_num, status='delivered', status_text='✅ Доставлен')
        order = get_order(order_num)
        if order:
            await ctx.bot.send_message(order['user_id'], text=f"🏠 Заказ {order_num} доставлен! Спасибо!")
        await q.edit_message_text(q.message.text + "\n\n✅ ДОСТАВЛЕН")
        return
    
    if data.startswith("ask_"):
        order_num = data[4:]
        ctx.user_data['awaiting_answer_for'] = order_num
        await q.edit_message_text(f"❓ Введите вопрос:")
        return
    
    if data.startswith("detail_"):
        order_num = data[7:]
        order = get_order(order_num)
        if not order:
            await q.edit_message_text("❌ Заказ не найден")
            return
        text = (f"🔍 ПОЛНАЯ ИНФОРМАЦИЯ\n\n"
                f"📦 Заказ: {order['order_number']}\n"
                f"👤 Клиент: {order['user_name']}\n"
                f"📞 Телефон: {order.get('phone', '-')}\n"
                f"🚗 VIN: {order.get('vin', '-')}\n"
                f"📊 Пробег: {order.get('mileage', '-')} км\n"
                f"🏙️ Город: {order.get('city', '-')}\n"
                f"📍 Адрес: {order.get('delivery_address', '-')}\n"
                f"🚚 Доставка: {order.get('delivery_type', '-')} | {order.get('delivery_price', 0)} руб.\n"
                f"💰 Сумма: {int(order.get('total_price', 0)) + int(order.get('delivery_price', 0))} руб.\n"
                f"💰 Себестоимость: {int(order.get('our_cost', 0))} руб.\n"
                f"📦 Статус: {order.get('status_text', '-')}\n"
                f"📅 Создан: {order.get('created_at', '-')}")
        if order.get('tracking_number'):
            text += f"\n📮 Трек: {order.get('tracking_number')}"
        if order.get('selected_products'):
            text += f"\n\n📦 Подбор менеджера:\n{order.get('selected_products')}"
        if order.get('needed_parts'):
            text += f"\n\n📝 Запчасти клиента:\n{order.get('needed_parts')}"
        await q.edit_message_text(text)
        return
    
    if data.startswith("delete_"):
        order_num = data[7:]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ДА", callback_data=f"confirm_delete_{order_num}")],
            [InlineKeyboardButton("❌ НЕТ", callback_data=f"admin_order_{order_num}")]
        ])
        await q.edit_message_text(f"⚠️ Удалить заказ {order_num}?", reply_markup=kb)
        return
    
    if data.startswith("confirm_delete_"):
        order_num = data[14:]
        order = get_order(order_num)
        
        if order:
            conn = sqlite3.connect(DB_PATH)
            c = conn.cursor()
            c.execute('DELETE FROM orders WHERE order_number = ?', (order_num,))
            conn.commit()
            conn.close()
            
            try:
                await ctx.bot.send_message(order['user_id'], text=f"🗑️ Заказ {order_num} удалён.")
            except:
                pass
        
        await admin_menu(upd, ctx, callback_query=q)
        return
    
    if data == "admin_back":
        await admin_menu(upd, ctx, callback_query=q)
        return

async def set_delivery_price_input(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        return
    if 'set_price_for' not in ctx.user_data:
        return
    
    order_num = ctx.user_data['set_price_for']
    try:
        new_price = int(upd.message.text)
        if new_price < 0:
            await upd.message.reply_text("❌ Цена не может быть отрицательной")
            return
        
        update_order(order_num, delivery_price=new_price)
        order = get_order(order_num)
        
        await ctx.bot.send_message(
            order['user_id'],
            text=f"💰 Стоимость доставки для {order_num}: {new_price} руб."
        )
        await upd.message.reply_text(f"✅ Цена доставки установлена: {new_price} руб.")
        
    except ValueError:
        await upd.message.reply_text("❌ Введите целое число")
    
    del ctx.user_data['set_price_for']

async def track_input(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        return
    if 'track_for' in ctx.user_data:
        order_num = ctx.user_data['track_for']
        update_order(order_num, tracking_number=upd.message.text, status='shipped', status_text='🚚 Отправлен')
        order = get_order(order_num)
        if order:
            await ctx.bot.send_message(order['user_id'], text=f"📦 Заказ {order_num} отправлен!\nТрек: {upd.message.text}")
        await upd.message.reply_text(f"✅ Трек добавлен к заказу {order_num}")
        del ctx.user_data['track_for']

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

async def error_handler(update, context):
    """Логирование ошибок"""
    print(f"Ошибка: {context.error}")
    if update and update.effective_chat:
        await context.bot.send_message(
            chat_id=update.effective_chat.id,
            text="❌ Произошла ошибка. Пожалуйста, попробуйте позже."
        )

async def set_commands(application):
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
    await application.bot.set_my_commands(commands)

# ========== ГАРАЖ ФУНКЦИИ ==========
async def garage_menu(upd, ctx):
    cars = get_cars(upd.effective_user.id)
    
    if not cars:
        await upd.message.reply_text(
            "🚗 МОЙ ГАРАЖ\n\nУ вас пока нет автомобилей.\n\n"
            "➕ Добавить: отправьте VIN (17 символов)\n"
            "или нажмите кнопку:",
            reply_markup=InlineKeyboardMarkup([
                [InlineKeyboardButton("➕ Добавить авто", callback_data="garage_add")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="garage_back")]
            ])
        )
        return
    
    text = "🚗 МОЙ ГАРАЖ\n\n"
    for car in cars:
        vin = car[0]
        desc = car[1][:30] if car[1] else "без описания"
        created = car[2][:10] if car[2] else "дата неизвестна"
        text += f"🔹 {vin}\n   {desc}\n   📅 {created}\n\n"
    
    keyboard = []
    for car in cars:
        vin = car[0]
        keyboard.append([InlineKeyboardButton(f"🗑️ Удалить {vin}", callback_data=f"garage_del_{vin}")])
    keyboard.append([InlineKeyboardButton("➕ Добавить авто", callback_data="garage_add")])
    keyboard.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="garage_back")])
    
    await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard))

async def garage_add_start(upd, ctx):
    q = upd.callback_query
    await q.answer()
    await q.edit_message_text(
        "🚗 ДОБАВЛЕНИЕ АВТОМОБИЛЯ\n\n"
        "Шаг 1/2: Отправьте VIN номер (17 символов):"
    )
    return GARAGE_VIN

async def garage_get_vin(upd, ctx):
    vin = upd.message.text.upper().strip()
    if len(vin) != 17:
        await upd.message.reply_text("❌ VIN должен быть 17 символов. Попробуйте ещё раз:")
        return GARAGE_VIN
    
    ctx.user_data['garage_vin'] = vin
    await upd.message.reply_text(
        f"🚗 VIN: {vin}\n\nШаг 2/2: Введите описание авто\n"
        "Пример: BMW X5 3.0d, 2018\nИли '-' чтобы пропустить:"
    )
    return GARAGE_DESCRIPTION

async def garage_get_description(upd, ctx):
    description = upd.message.text.strip()
    if description == "-":
        description = ""
    
    vin = ctx.user_data['garage_vin']
    save_car(upd.effective_user.id, vin, description)
    
    await upd.message.reply_text(f"✅ Автомобиль {vin} добавлен!")
    await garage_menu(upd, ctx)
    return ConversationHandler.END

async def garage_delete(upd, ctx):
    q = upd.callback_query
    await q.answer()
    vin = q.data[12:]
    
    if delete_car(q.from_user.id, vin):
        await q.edit_message_text(f"✅ Автомобиль {vin} удалён!")
    else:
        await q.edit_message_text(f"❌ Автомобиль {vin} не найден")
    
    await garage_menu(upd, ctx)

async def garage_back(upd, ctx):
    q = upd.callback_query
    await q.answer()
    await start(upd, ctx)

# ========== ЗАПУСК ==========
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Настройка автоматического бэкапа каждый день в 03:00
    scheduler = AsyncIOScheduler()
    scheduler.add_job(auto_backup_job, 'cron', hour=3, minute=0)
    scheduler.start()
    
    app.post_init = set_commands
    app.add_error_handler(error_handler)
    
    order_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(🛒 Новый заказ)$"), new_order), 
                      CallbackQueryHandler(order_auto_callback, pattern="^order_")],
        states={
            VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_vin), MessageHandler(filters.PHOTO, get_vin), 
                  CallbackQueryHandler(order_auto_callback, pattern="^order_")],
            MILEAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mileage)],
            STYLE_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_city)],
            STYLE_HIGHWAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_highway)],
            DELIVERY_TYPE: [
                MessageHandler(filters.Regex("^(Курьером)$"), get_delivery_type),
                MessageHandler(filters.Regex("^(Самовывоз)$"), get_delivery_type),
                MessageHandler(filters.Regex("^(Сторонняя фирма)$"), get_delivery_type),
            ],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
            PHONE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_phone)],
            PART_NODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_part_node)],
            AXLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_axle)],
            PARTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_parts)],
            CONFIRM: [MessageHandler(filters.Regex("^(✅ Готово|✏️ Редактировать)$"), confirm_order)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    garage_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(garage_add_start, pattern="^garage_add$")],
        states={
            GARAGE_VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_get_vin)],
            GARAGE_DESCRIPTION: [MessageHandler(filters.TEXT & ~filters.COMMAND, garage_get_description)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(order_conv)
    app.add_handler(garage_conv)
    app.add_handler(CommandHandler("my_orders", my_orders))
    app.add_handler(CommandHandler("bonus", bonus_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("delivery", delivery_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", admin_menu))
    app.add_handler(CommandHandler("fix", fix_orders))
    
    app.add_handler(MessageHandler(filters.Regex("^(🚗 Мой гараж)$"), garage_menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), manager_reply))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), track_input))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), send_question_to_client))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), set_delivery_price_input))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), admin_search_input))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, client_answer))
    
    app.add_handler(CallbackQueryHandler(select_cb, pattern="^sel_"))
    app.add_handler(CallbackQueryHandler(finalize_cb, pattern="^fin_"))
    app.add_handler(CallbackQueryHandler(view_order, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(back_orders, pattern="^back_orders$"))
    app.add_handler(CallbackQueryHandler(back_to_menu, pattern="^back_to_menu$"))
    app.add_handler(CallbackQueryHandler(admin_callback))
    app.add_handler(CallbackQueryHandler(ask_client, pattern="^ask_"))
    app.add_handler(CallbackQueryHandler(garage_delete, pattern="^garage_del_"))
    app.add_handler(CallbackQueryHandler(garage_back, pattern="^garage_back$"))
    
    print("🤖 БОТ ЗАПУЩЕН!")
    print(f"👨‍💼 Админ ID: {MANAGER_ID}")
    print(f"💾 База данных: {DB_PATH}")
    print("⏰ Автоматический бэкап БД каждый день в 03:00")
    app.run_polling()

if __name__ == "__main__":
    main()