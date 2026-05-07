#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import os
import re
import sqlite3
import random
import string
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, filters

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_ID = int(os.environ.get("MANAGER_ID", 804070528))
DELIVERY_BASE = 500

DATA_DIR = os.getenv('DATA_DIR', '/app/data')
DB_PATH = os.path.join(DATA_DIR, 'shop_bot.db')

if not BOT_TOKEN:
    raise ValueError("BOT_TOKEN не найден!")
# =================================

# Состояния
VIN, MILEAGE, STYLE_CITY, STYLE_HIGHWAY, CITY, DISTANCE, DELIVERY_TYPE, ADDRESS, PART_NODE, AXLE, PARTS = range(11)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    os.makedirs(DATA_DIR, exist_ok=True)
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE,
        user_id INTEGER, user_name TEXT, vin TEXT,
        mileage TEXT, style_city TEXT, style_highway TEXT,
        city TEXT, distance INTEGER DEFAULT 0,
        delivery_type TEXT, delivery_price INTEGER DEFAULT 500, delivery_address TEXT,
        part_node TEXT, axle TEXT, needed_parts TEXT, selected_products TEXT, final_order TEXT,
        status TEXT, status_text TEXT, tracking_number TEXT,
        total_price REAL DEFAULT 0, created_at TEXT,
        manager_question TEXT, client_answer TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bonuses (
        user_id INTEGER PRIMARY KEY, balance REAL DEFAULT 0,
        total_earned REAL DEFAULT 0, referrer_id INTEGER DEFAULT NULL)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS bonus_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT, user_id INTEGER,
        order_number TEXT, amount REAL, type TEXT, description TEXT, created_at TEXT)''')
    
    c.execute('''CREATE TABLE IF NOT EXISTS referrals (
        id INTEGER PRIMARY KEY AUTOINCREMENT, referrer_id INTEGER,
        referred_id INTEGER, created_at TEXT)''')
    
    conn.commit()
    conn.close()
    print(f"✅ База данных: {DB_PATH}")

def save_order(data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
    c.execute('''INSERT INTO orders (order_number, user_id, user_name, vin,
        mileage, style_city, style_highway, city, distance, delivery_type,
        delivery_price, delivery_address, part_node, axle, needed_parts,
        status, status_text, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (num, data['user_id'], data['user_name'], data.get('vin',''),
         data.get('mileage',''), data.get('style_city',''), data.get('style_highway',''),
         data.get('city',''), data.get('distance',0), data.get('delivery_type',''),
         data.get('delivery_price',500), data.get('delivery_address',''),
         data.get('part_node',''), data.get('axle',''), data.get('needed_parts',''),
         'pending', '🆕 Ожидает подбора', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return num

def update_order(order_number, **kwargs):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    for key, val in kwargs.items():
        c.execute(f"UPDATE orders SET {key} = ? WHERE order_number = ?", (val, order_number))
    conn.commit()
    conn.close()

def get_order(order_number):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,))
    r = c.fetchone()
    conn.close()
    if r:
        return {'order_number': r[1], 'user_id': r[2], 'user_name': r[3], 'vin': r[4],
                'mileage': r[5], 'style_city': r[6], 'style_highway': r[7], 'city': r[8],
                'distance': r[9], 'delivery_type': r[10], 'delivery_price': r[11],
                'delivery_address': r[12], 'part_node': r[13], 'axle': r[14],
                'needed_parts': r[15], 'selected_products': r[16], 'final_order': r[17],
                'status': r[18], 'status_text': r[19], 'tracking_number': r[20],
                'total_price': r[21], 'created_at': r[24], 'manager_question': r[25],
                'client_answer': r[26]}
    return None

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
    c.execute('SELECT order_number, status_text, created_at FROM orders WHERE user_id = ? ORDER BY id DESC', (user_id,))
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
    c.execute('SELECT balance FROM bonuses WHERE user_id = ?', (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else 0

def add_bonus(user_id, order_num, amount, desc):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('INSERT INTO bonuses (user_id, balance, total_earned) VALUES (?,?,?) ON CONFLICT(user_id) DO UPDATE SET balance = balance + ?, total_earned = total_earned + ?',
              (user_id, amount, amount, amount, amount))
    c.execute('INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at) VALUES (?,?,?,?,?,?)',
              (user_id, order_num, amount, 'earned', desc, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
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

def calc_delivery_price(km):
    if km <= 0: return DELIVERY_BASE
    if km <= 50: return DELIVERY_BASE + km * 25
    if km <= 100: return DELIVERY_BASE + km * 35
    return DELIVERY_BASE + km * 50

def delivery_discount(order_sum):
    if order_sum < 10000: return 0
    return min(100, ((order_sum - 10000) // 5000) * 5 + 5)

def parse_products(text):
    products = []
    for line in text.strip().split('\n'):
        line = line.strip()
        if not line: continue
        price = re.search(r'(\d{1,3}(?:[\s\.]?\d{3})*)\s*(?:руб|₽)', line, re.I)
        if not price: continue
        price = float(price.group(1).replace('.', '').replace(' ', ''))
        name = re.sub(r'\d{1,3}(?:[\s\.]\d{3})*\s*(?:руб|₽)', '', line).strip()
        name = re.sub(r'[=•\-–—]|арт\.?\S+|\([^)]*\)', '', name).strip()
        name = name[:40] + ".." if len(name) > 40 else name
        if name:
            products.append({'name': name, 'price': price})
    return products

# ========== КЛАВИАТУРЫ ==========
main_menu = ReplyKeyboardMarkup([
    ["🛒 Новый заказ"],
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
    ["🔧 Двигатель"],
    ["🔩 Подвеска"],
    ["🛑 Тормозная система"],
    ["⚙️ Трансмиссия (КПП)"],
    ["🔋 Электрика"],
    ["❄️ Охлаждение"],
    ["🌡️ Система отопления"],
    ["💨 Выхлопная система"],
    ["🛞 Рулевое управление"],
    ["📦 Другое"]
], resize_keyboard=True)

axle_kb = ReplyKeyboardMarkup([
    ["🔧 Передняя ось"],
    ["🔧 Задняя ось"],
    ["🔧 Передняя + Задняя"]
], resize_keyboard=True)

def admin_kb(filter_status=None):
    orders = get_all_orders()
    if not orders:
        return None
    
    grouped = {'new': [], 'waiting': [], 'selection': [], 'paid': [], 'shipped': [], 'delivered': []}
    
    for o in orders:
        order = get_order(o[0])
        if not order: continue
        if order['status'] == 'pending' or 'Ожидает подбора' in order['status_text']:
            grouped['new'].append(order)
        elif order['status'] == 'waiting_answer':
            grouped['waiting'].append(order)
        elif order['status'] == 'waiting_selection' or 'Ожидает выбора' in order['status_text']:
            grouped['selection'].append(order)
        elif 'Оплачен' in order['status_text']:
            grouped['paid'].append(order)
        elif 'Отправлен' in order['status_text']:
            grouped['shipped'].append(order)
        elif 'Доставлен' in order['status_text']:
            grouped['delivered'].append(order)
    
    kb = []
    filter_buttons = [
        ("🆕 Новые", "filter_new"),
        ("❓ Ожидают ответа", "filter_waiting"),
        ("🟡 Выбор запчастей", "filter_selection"),
        ("💰 Оплачены", "filter_paid"),
        ("🚚 Отправлены", "filter_shipped"),
        ("✅ Доставлены", "filter_delivered"),
        ("📋 Все заказы", "filter_all")
    ]
    
    row = []
    for text, callback in filter_buttons:
        row.append(InlineKeyboardButton(text, callback_data=callback))
        if len(row) == 2:
            kb.append(row)
            row = []
    if row:
        kb.append(row)
    
    if filter_status is None or filter_status == 'all':
        display_orders = orders[:15]
    elif filter_status == 'new':
        display_orders = grouped['new'][:15]
    elif filter_status == 'waiting':
        display_orders = grouped['waiting'][:15]
    elif filter_status == 'selection':
        display_orders = grouped['selection'][:15]
    elif filter_status == 'paid':
        display_orders = grouped['paid'][:15]
    elif filter_status == 'shipped':
        display_orders = grouped['shipped'][:15]
    elif filter_status == 'delivered':
        display_orders = grouped['delivered'][:15]
    else:
        display_orders = orders[:15]
    
    for o in display_orders:
        if isinstance(o, dict):
            order_num = o['order_number']
            user_name = o['user_name'][:12] + ".." if len(o['user_name']) > 12 else o['user_name']
            status_icon = get_status_icon(o)
        else:
            order_num = o[0]
            user_name = o[1][:12] + ".." if len(o[1]) > 12 else o[1]
            status_icon = "📦"
        kb.append([InlineKeyboardButton(f"{status_icon} Заказ {order_num} | {user_name}", callback_data=f"admin_order_{order_num}")])
    
    kb.append([InlineKeyboardButton("📊 Статистика", callback_data="admin_stats")])
    kb.append([InlineKeyboardButton("➕ Тестовый заказ", callback_data="admin_fix")])
    return InlineKeyboardMarkup(kb)

def get_status_icon(order):
    if 'Ожидает подбора' in order['status_text']:
        return "🆕"
    elif order['status'] == 'waiting_answer':
        return "❓"
    elif 'Ожидает выбора' in order['status_text']:
        return "🟡"
    elif 'Оплачен' in order['status_text']:
        return "💰"
    elif 'Отправлен' in order['status_text']:
        return "🚚"
    elif 'Доставлен' in order['status_text']:
        return "✅"
    else:
        return "📦"

def get_orders_keyboard(user_id):
    orders = get_user_orders(user_id)
    if not orders: return None
    kb = [[InlineKeyboardButton(f"📦 Заказ {o[0]} - {o[1][:15]}", callback_data=f"view_{o[0]}")] for o in orders]
    return InlineKeyboardMarkup(kb)

def manager_ask_kb(order_num):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("❓ Задать вопрос клиенту", callback_data=f"ask_{order_num}")],
        [InlineKeyboardButton("📝 Отправить подбор", callback_data=f"send_offer_{order_num}")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"admin_order_{order_num}")]
    ])

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
    await upd.message.reply_text("🏎️ Добро пожаловать!\nНажмите 🛒 Новый заказ", reply_markup=main_menu)

async def new_order(upd, ctx):
    await upd.message.reply_text("🔧 Отправьте VIN номер (17 символов):")
    return VIN

async def get_vin(upd, ctx):
    message = upd.message
    
    # Если прислали фото — сообщаем о ручном вводе
    if message.photo:
        await message.reply_text("📸 Пожалуйста, введите VIN вручную (17 символов):")
        return VIN
    
    # Обработка текста
    vin = message.text.upper().strip()
    if len(vin) != 17:
        await message.reply_text("❌ VIN должен быть 17 символов. Попробуйте ещё раз:")
        return VIN
    ctx.user_data['vin'] = vin
    
    await message.reply_text("📊 Пробег (км):")
    return MILEAGE

async def get_mileage(upd, ctx):
    ctx.user_data['mileage'] = upd.message.text
    await upd.message.reply_text("🏙️ Стиль вождения в городе:", reply_markup=city_style_kb)
    return STYLE_CITY

async def get_style_city(upd, ctx):
    ctx.user_data['style_city'] = upd.message.text
    await upd.message.reply_text("🛣️ Стиль вождения на трассе:", reply_markup=highway_style_kb)
    return STYLE_HIGHWAY

async def get_style_highway(upd, ctx):
    ctx.user_data['style_highway'] = upd.message.text
    await upd.message.reply_text("🏙️ Ваш город:")
    return CITY

async def get_city(upd, ctx):
    ctx.user_data['city'] = upd.message.text
    await upd.message.reply_text("📍 Расстояние от МКАД (км):\n0 - если Москва")
    return DISTANCE

async def get_distance(upd, ctx):
    try:
        distance = int(upd.message.text)
        ctx.user_data['distance'] = distance
        await upd.message.reply_text("🚚 Способ доставки:", reply_markup=delivery_type_kb)
        return DELIVERY_TYPE
    except:
        await upd.message.reply_text("❌ Введите число (километры от МКАД):")
        return DISTANCE

async def get_delivery_type(upd, ctx):
    choice = upd.message.text
    ctx.user_data['delivery_type'] = choice
    
    if choice == "Курьером":
        price = calc_delivery_price(ctx.user_data.get('distance', 0))
        ctx.user_data['delivery_price'] = price
        await upd.message.reply_text(f"🚚 Доставка курьером: {price} руб.\n\n📍 Введите адрес (город, улица, дом):")
        return ADDRESS
    
    elif choice == "Самовывоз":
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("📍 Выберите станцию метро:", reply_markup=pickup_station_kb)
        return PART_NODE
    
    else:
        ctx.user_data['delivery_price'] = 0
        await upd.message.reply_text("🚛 Сторонняя фирма (стоимость рассчитает менеджер)\n\n📍 Введите адрес (город, улица, дом):")
        return ADDRESS

async def get_address(upd, ctx):
    ctx.user_data['delivery_address'] = upd.message.text
    await upd.message.reply_text("🔧 Выберите узел запчасти:", reply_markup=part_node_kb)
    return PART_NODE

async def get_part_node(upd, ctx):
    ctx.user_data['part_node'] = upd.message.text
    await upd.message.reply_text("🔧 Выберите ось:", reply_markup=axle_kb)
    return AXLE

async def get_axle(upd, ctx):
    ctx.user_data['axle'] = upd.message.text
    await upd.message.reply_text("🔧 Какие запчасти нужны? (каждая с новой строки)")
    return PARTS

async def get_pickup_station(upd, ctx):
    ctx.user_data['delivery_address'] = upd.message.text
    await upd.message.reply_text("🔧 Выберите узел запчасти:", reply_markup=part_node_kb)
    return PART_NODE

async def get_parts(upd, ctx):
    data = ctx.user_data
    order_num = save_order({
        'user_id': upd.effective_user.id, 'user_name': upd.effective_user.full_name,
        'vin': data.get('vin',''), 'mileage': data['mileage'],
        'style_city': data['style_city'], 'style_highway': data['style_highway'],
        'city': data['city'], 'distance': data.get('distance',0),
        'delivery_type': data['delivery_type'], 'delivery_price': data.get('delivery_price',500),
        'delivery_address': data.get('delivery_address',''),
        'part_node': data.get('part_node',''), 'axle': data.get('axle',''),
        'needed_parts': upd.message.text
    })
    
    await upd.context.bot.send_message(
        MANAGER_ID,
        f"🆕 НОВЫЙ ЗАКАЗ #{order_num}\n"
        f"👤 Клиент: {upd.effective_user.full_name}\n"
        f"🚗 VIN: {data['vin']}\n"
        f"📊 Пробег: {data['mileage']} км\n"
        f"🏙️ Город: {data['city']} | {data.get('distance',0)} км от МКАД\n"
        f"🏎️ Город: {data['style_city']}\n"
        f"🛣️ Трасса: {data['style_highway']}\n"
        f"🚚 Доставка: {data['delivery_type']} | {data.get('delivery_price',500)} руб.\n"
        f"📍 Адрес: {data.get('delivery_address','не указан')}\n"
        f"🔧 Узел: {data.get('part_node','не указан')}\n"
        f"🔧 Ось: {data.get('axle','не указана')}\n"
        f"📝 Запчасти:\n{upd.message.text}\n\n"
        f"➡️ Для уточнения информации нажмите кнопку ниже:",
        reply_markup=manager_ask_kb(order_num)
    )
    
    await upd.message.reply_text(f"✅ Заказ #{order_num} создан!\n\nДоставка: {data.get('delivery_price',500)} руб.\n\nОжидайте подбора (15-30 мин)", reply_markup=main_menu)
    return ConversationHandler.END

async def my_orders(upd, ctx):
    kbd = get_orders_keyboard(upd.effective_user.id)
    if kbd: await upd.message.reply_text("📦 Ваши заказы:", reply_markup=kbd)
    else: await upd.message.reply_text("📭 Нет заказов", reply_markup=main_menu)

async def view_order(upd, ctx):
    q = upd.callback_query; await q.answer()
    order = get_order(q.data[5:])
    if not order: await q.edit_message_text("❌ Заказ не найден"); return
    text = f"📋 ЗАКАЗ {order['order_number']}\n\n👤 {order['user_name']}\n📅 {order['created_at']}\n🚗 VIN: {order['vin']}\n📊 Пробег: {order['mileage']} км\n🏙️ Город: {order['city']} | {order['distance']} км от МКАД\n🏎️ Город: {order['style_city']}\n🛣️ Трасса: {order['style_highway']}\n🚚 Доставка: {order['delivery_type']} | {order['delivery_price']} руб.\n📍 {order['delivery_address']}\n🔧 Узел: {order['part_node']}\n🔧 Ось: {order['axle']}\n\n💰 Подбор:\n{order['selected_products'] or 'ещё не предложен'}\n\n📦 Статус: {order['status_text']}"
    if order['tracking_number']: text += f"\n📦 Трек: {order['tracking_number']}"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_orders")]]))

async def back_orders(upd, ctx):
    q = upd.callback_query; await q.answer()
    kbd = get_orders_keyboard(q.from_user.id)
    if kbd: await q.edit_message_text("📦 Ваши заказы:", reply_markup=kbd)
    else: await q.edit_message_text("📭 Нет заказов")

async def bonus_cmd(upd, ctx):
    uid = upd.effective_user.id
    bal = get_bonus(uid)
    total = get_user_total(uid)
    percent = get_bonus_percent(uid)
    text = f"""🎁 БОНУСНАЯ ПРОГРАММА

💰 Баланс: {int(bal)} бонусов
📦 Накоплено: {int(total)} руб.
⭐ Начисление: {percent}%

📊 Градация:
1% → до 100 000 руб.
2% → 100 000 - 200 000 руб.
3% → 200 000 - 300 000 руб.
4% → 300 000 - 400 000 руб.
5% → 400 000 - 500 000 руб.
6% → 500 000 - 600 000 руб.
7% → 600 000 - 700 000 руб.
8% → 700 000 - 800 000 руб.
9% → 800 000 - 900 000 руб.
10% → от 900 000 руб."""
    await upd.message.reply_text(text)

async def referral_cmd(upd, ctx):
    link = f"https://t.me/{(await ctx.bot.get_me()).username}?start=ref_{upd.effective_user.id}"
    text = f"""🔗 РЕФЕРАЛЬНАЯ ССЫЛКА

{link}

📊 Вы получаете 0.5% от суммы заказов ваших друзей бонусами!
🎁 Друг получает 500 приветственных бонусов!

💰 Текущий баланс: {int(get_bonus(upd.effective_user.id))} бонусов"""
    await upd.message.reply_text(text)

async def delivery_cmd(upd, ctx):
    text = f"""🚚 РАСЧЁТ ДОСТАВКИ ОТ МКАД

Базовая стоимость: {DELIVERY_BASE} руб.

📌 Тарифы:
0 км (Москва): {DELIVERY_BASE} руб.
1-50 км: {DELIVERY_BASE} + км × 25
51-100 км: {DELIVERY_BASE} + км × 35
101+ км: {DELIVERY_BASE} + км × 50

📌 Самовывоз (бесплатно):
- Метро Давыдково
- Метро Строгино
- Метро Южная

📌 Скидка на доставку от суммы заказа:
от 10 000 руб. → 5%
от 15 000 руб. → 10%
... до 100%"""
    await upd.message.reply_text(text)

async def help_cmd(upd, ctx):
    text = """📖 ПОМОЩЬ

/start - Главное меню
/my_orders - Мои заказы
/bonus - Бонусы
/referral - Рефералы
/delivery - Доставка

👨‍💼 Администратор:
/menu - Панель управления
/fix - Тестовый заказ"""
    await upd.message.reply_text(text, reply_markup=main_menu)

# ========== МЕНЕДЖЕР ==========
user_selections = {}

async def manager_ask_question(upd, ctx):
    q = upd.callback_query
    await q.answer()
    order_num = q.data.split('_')[1]
    ctx.user_data['awaiting_answer_for'] = order_num
    await q.edit_message_text(f"❓ Введите вопрос для клиента по заказу {order_num}:")

async def send_question_to_client(upd, ctx):
    if upd.effective_user.id != MANAGER_ID: return
    if 'awaiting_answer_for' not in ctx.user_data: return
    
    order_num = ctx.user_data['awaiting_answer_for']
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text("❌ Заказ не найден")
        return
    
    question = upd.message.text
    update_order(order_num, manager_question=question, status='waiting_answer', status_text='❓ Ожидает ответа на вопрос')
    
    await ctx.bot.send_message(
        order['user_id'],
        text=f"❓ Уточнение по заказу {order_num}\n\nМенеджер хочет уточнить:\n{question}\n\nПожалуйста, ответьте на это сообщение."
    )
    await upd.message.reply_text(f"✅ Вопрос отправлен клиенту по заказу {order_num}")
    del ctx.user_data['awaiting_answer_for']

async def client_answer(upd, ctx):
    user_id = upd.effective_user.id
    if user_id == MANAGER_ID: return
    
    orders = get_user_orders(user_id)
    for o in orders:
        order = get_order(o[0])
        if order and order['status'] == 'waiting_answer':
            update_order(order['order_number'], client_answer=upd.message.text, status='pending', status_text='🟡 Ожидает подбора')
            await ctx.bot.send_message(
                MANAGER_ID,
                text=f"✅ Клиент ответил по заказу {order['order_number']}\n\n❓ Вопрос менеджера:\n{order['manager_question']}\n\n📝 Ответ клиента:\n{upd.message.text}\n\nТеперь вы можете отправить подбор запчастей."
            )
            await upd.message.reply_text("✅ Спасибо! Ваш ответ передан менеджеру.")
            return

async def send_offer(upd, ctx):
    q = upd.callback_query
    await q.answer()
    order_num = q.data.split('_')[1]
    ctx.user_data['awaiting_offer_for'] = order_num
    await q.edit_message_text(f"📝 Введите подбор запчастей для заказа {order_num} в формате:\n\nНазвание = цена\nПример:\nМасло Ravenol 5W-30 = 3000")

async def process_offer(upd, ctx):
    if upd.effective_user.id != MANAGER_ID: return
    if 'awaiting_offer_for' not in ctx.user_data: return
    
    order_num = ctx.user_data['awaiting_offer_for']
    products = parse_products(upd.message.text)
    
    if not products:
        await upd.message.reply_text("❌ Не распознано. Формат:\nНазвание = цена\nПример:\nМасло Ravenol = 3000")
        return
    
    update_order(order_num, selected_products=upd.message.text, status='waiting_selection', status_text='🟡 Ожидает выбора запчастей')
    order = get_order(order_num)
    
    if order:
        kb = [[InlineKeyboardButton(f"⬜ {p['name']} — {int(p['price'])} руб.", callback_data=f"sel_{order_num}_{i}")] for i,p in enumerate(products)]
        kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"fin_{order_num}")])
        await ctx.bot.send_message(order['user_id'], text=f"🛒 Выберите запчасти для заказа {order_num}", reply_markup=InlineKeyboardMarkup(kb))
        await upd.message.reply_text(f"✅ Подбор отправлен клиенту для заказа {order_num}")
    
    del ctx.user_data['awaiting_offer_for']

async def manager_reply(upd, ctx):
    if upd.effective_user.id != MANAGER_ID or not upd.message.reply_to_message: return
    match = re.search(r"НОВЫЙ ЗАКАЗ #(RVN-\w{6})", upd.message.reply_to_message.text or "")
    if match:
        order_num = match.group(1)
        products = parse_products(upd.message.text)
        if products:
            update_order(order_num, selected_products=upd.message.text, status='waiting_selection', status_text='🟡 Ожидает выбора запчастей')
            order = get_order(order_num)
            if order:
                kb = [[InlineKeyboardButton(f"⬜ {p['name']} — {int(p['price'])} руб.", callback_data=f"sel_{order_num}_{i}")] for i,p in enumerate(products)]
                kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"fin_{order_num}")])
                await ctx.bot.send_message(order['user_id'], text=f"🛒 Выберите запчасти для заказа {order_num}", reply_markup=InlineKeyboardMarkup(kb))
                await upd.message.reply_text(f"✅ Отправлено клиенту")

async def select_cb(upd, ctx):
    q = upd.callback_query; await q.answer()
    _, order_num, idx = q.data.split('_')
    idx = int(idx); uid = q.from_user.id
    order = get_order(order_num)
    if not order or not order['selected_products']: return
    products = parse_products(order['selected_products'])
    if uid not in user_selections: user_selections[uid] = {}
    if order_num not in user_selections[uid]: user_selections[uid][order_num] = set()
    s = user_selections[uid][order_num]
    if idx in s: s.remove(idx)
    else: s.add(idx)
    kb = []
    for i,p in enumerate(products):
        cb = "✅" if i in s else "⬜"
        kb.append([InlineKeyboardButton(f"{cb} {p['name']} — {int(p['price'])} руб.", callback_data=f"sel_{order_num}_{i}")])
    kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"fin_{order_num}")])
    await q.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))

async def finalize_cb(upd, ctx):
    q = upd.callback_query; await q.answer()
    order_num = q.data.split('_')[1]; uid = q.from_user.id
    if uid not in user_selections or order_num not in user_selections[uid] or not user_selections[uid][order_num]:
        await q.edit_message_text("❌ Ничего не выбрано"); return
    order = get_order(order_num)
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
        delivery_text = f"🚚 Доставка: {delivery_price} руб. → скидка {delivery_disc}% = {delivery_final} руб."
    else:
        delivery_final = delivery_price
        delivery_text = f"🚚 Доставка: {delivery_price} руб."
    
    final_total = total + delivery_final
    
    update_order(order_num, final_order=f"✅ ЗАКАЗ #{order_num}\n\n" + "\n".join([f"• {p['name']} — {int(p['price'])} руб." for p in selected]) + f"\n\n{delivery_text}\n\n💰 ИТОГО К ОПЛАТЕ: {int(final_total)} руб.", total_price=total)
    
    bonus_percent = get_bonus_percent(uid)
    bonus = int(total * bonus_percent / 100)
    add_bonus(uid, order_num, bonus, f"Заказ {order_num} ({bonus_percent}%)")
    
    result = f"✅ ЗАКАЗ #{order_num} ПОДТВЕРЖДЕН!\n\n" + "\n".join([f"• {p['name']} — {int(p['price'])} руб." for p in selected]) + f"\n\n{delivery_text}\n\n💰 ИТОГО К ОПЛАТЕ: {int(final_total)} руб."
    if bonus > 0: result += f"\n\n🎁 Начислено бонусов: +{bonus} ({bonus_percent}%)"
    await q.edit_message_text(result)
    
    await ctx.bot.send_message(MANAGER_ID, f"✅ ЗАКАЗ {order_num} ПОДТВЕРЖДЕН КЛИЕНТОМ!\n\n👤 Клиент: {order['user_name']}\n💰 Товары: {int(total)} руб.\n🚚 Доставка: {delivery_final} руб.\n💎 ИТОГО: {int(final_total)} руб.")
    del user_selections[uid][order_num]

# ========== АДМИН ==========
async def admin_menu(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        await upd.message.reply_text("⛔ Доступ запрещён")
        return
    kb = admin_kb('all')
    if not kb:
        await upd.message.reply_text("📭 Нет заказов\n/fix - создать тестовый")
        return
    await upd.message.reply_text("👨‍💼 ПАНЕЛЬ УПРАВЛЕНИЯ\n\nВыберите фильтр и заказ:", reply_markup=kb)

async def admin_filter_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    filter_type = q.data.split('_')[1]
    
    kb = admin_kb(filter_type)
    if not kb:
        await q.edit_message_text("📭 Нет заказов")
        return
    
    titles = {
        'new': "🆕 НОВЫЕ ЗАКАЗЫ (ожидают подбора)",
        'waiting': "❓ ОЖИДАЮТ ОТВЕТА",
        'selection': "🟡 ОЖИДАЮТ ВЫБОРА ЗАПЧАСТЕЙ",
        'paid': "💰 ОПЛАЧЕНЫ",
        'shipped': "🚚 ОТПРАВЛЕНЫ",
        'delivered': "✅ ДОСТАВЛЕНЫ",
        'all': "📋 ВСЕ ЗАКАЗЫ"
    }
    
    title = titles.get(filter_type, "👨‍💼 ПАНЕЛЬ УПРАВЛЕНИЯ")
    await q.edit_message_text(f"{title}\n\nВыберите заказ:", reply_markup=kb)

async def admin_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data
    
    if data == "admin_fix":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
        c.execute('''INSERT INTO orders (order_number, user_id, user_name, vin, mileage,
            style_city, style_highway, city, distance, delivery_type, delivery_price, needed_parts,
            status, status_text, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (num, MANAGER_ID, 'Тестовый Клиент', 'TEST123', '50000',
             'Спокойный', 'Спокойный', 'Москва', 0, 'Курьером', 500,
             'Тестовый заказ', 'pending', '🆕 Ожидает подбора',
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        await q.edit_message_text(f"✅ Тестовый заказ {num} создан!\n/menu")
        return
    
    if data.startswith("admin_order_"):
        order_num = data[12:]
        order = get_order(order_num)
        if not order:
            await q.edit_message_text("❌ Заказ не найден")
            return
        text = f"📋 ЗАКАЗ {order['order_number']}\n\n👤 {order['user_name']}\n🏙️ {order['city']} | {order['distance']} км от МКАД\n🚚 Доставка: {order['delivery_type']} | {order['delivery_price']} руб.\n💰 Сумма: {int(order['total_price'])} руб.\n📦 Статус: {order['status_text']}"
        
        kb = [
            [InlineKeyboardButton("💰 Оплачен", callback_data=f"pay_{order_num}")],
            [InlineKeyboardButton("🚚 Отправлен", callback_data=f"ship_{order_num}")],
            [InlineKeyboardButton("🏠 Доставлен", callback_data=f"del_{order_num}")],
            [InlineKeyboardButton("❓ Задать вопрос", callback_data=f"ask_{order_num}")],
            [InlineKeyboardButton("📝 Отправить подбор", callback_data=f"send_offer_{order_num}")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data == "admin_back":
        kb = admin_kb('all')
        if kb:
            await q.edit_message_text("👨‍💼 ПАНЕЛЬ УПРАВЛЕНИЯ\n\nВыберите фильтр и заказ:", reply_markup=kb)
        else:
            await q.edit_message_text("📭 Нет заказов")
        return
    
    if data.startswith("pay_"):
        order_num = data[4:]
        update_order(order_num, status='paid', status_text='💰 Оплачен')
        order = get_order(order_num)
        if order: await ctx.bot.send_message(order['user_id'], text=f"✅ Заказ {order_num} оплачен!")
        await q.edit_message_text(q.message.text + "\n\n✅ Статус: ОПЛАЧЕН")
    
    elif data.startswith("ship_"):
        order_num = data[5:]
        ctx.user_data['track_for'] = order_num
        await q.edit_message_text("📦 Введите трек-номер:")
    
    elif data.startswith("del_"):
        order_num = data[4:]
        update_order(order_num, status='delivered', status_text='✅ Доставлен')
        order = get_order(order_num)
        if order: await ctx.bot.send_message(order['user_id'], text=f"🏠 Заказ {order_num} доставлен! Спасибо!")
        await q.edit_message_text(q.message.text + "\n\n✅ Статус: ДОСТАВЛЕН")
    
    elif data == "admin_stats":
        orders = get_all_orders()
        if not orders: await q.edit_message_text("📭 Нет данных"); return
        total_orders = len(orders)
        total_sum = 0
        for o in orders:
            order = get_order(o[0])
            if order: total_sum += order['total_price']
        await q.edit_message_text(f"📊 СТАТИСТИКА\n\n📦 Заказов: {total_orders}\n💰 Общая сумма: {int(total_sum)} руб.", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]))

async def track_input(upd, ctx):
    if upd.effective_user.id != MANAGER_ID: return
    if 'track_for' in ctx.user_data:
        order_num = ctx.user_data['track_for']
        update_order(order_num, tracking_number=upd.message.text, status='shipped', status_text='🚚 Отправлен')
        order = get_order(order_num)
        if order: await ctx.bot.send_message(order['user_id'], text=f"📦 Заказ {order_num} отправлен!\nТрек: {upd.message.text}")
        await upd.message.reply_text(f"✅ Трек добавлен к заказу {order_num}")
        del ctx.user_data['track_for']

async def fix_orders(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        await upd.message.reply_text("⛔ Нет доступа")
        return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM orders')
    if c.fetchone()[0] == 0:
        num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
        c.execute('''INSERT INTO orders (order_number, user_id, user_name, vin, mileage,
            style_city, style_highway, city, distance, delivery_type, delivery_price, needed_parts,
            status, status_text, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (num, MANAGER_ID, 'Тестовый Клиент', 'TEST123', '50000',
             'Спокойный', 'Спокойный', 'Москва', 0, 'Курьером', 500,
             'Тестовый заказ', 'pending', '🆕 Ожидает подбора',
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        await upd.message.reply_text(f"✅ Тестовый заказ {num} создан!\n/menu")
    else:
        await upd.message.reply_text(f"📊 В базе уже есть заказы\n/menu")
    conn.close()

async def set_commands(application):
    commands = [
        ("start", "Главное меню"),
        ("my_orders", "Мои заказы"),
        ("bonus", "Бонусы"),
        ("referral", "Рефералы"),
        ("delivery", "Доставка"),
        ("help", "Помощь"),
        ("menu", "Панель управления (админ)"),
        ("fix", "Тестовый заказ (админ)"),
    ]
    await application.bot.set_my_commands(commands)

# ========== ЗАПУСК ==========
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    app.post_init = set_commands
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(🛒 Новый заказ)$"), new_order)],
        states={
            VIN: [MessageHandler(filters.PHOTO, get_vin), MessageHandler(filters.TEXT & ~filters.COMMAND, get_vin)],
            MILEAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mileage)],
            STYLE_CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_city)],
            STYLE_HIGHWAY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style_highway)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_city)],
            DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_distance)],
            DELIVERY_TYPE: [
                MessageHandler(filters.Regex("^(Курьером)$"), get_delivery_type),
                MessageHandler(filters.Regex("^(Самовывоз)$"), get_delivery_type),
                MessageHandler(filters.Regex("^(Сторонняя фирма)$"), get_delivery_type),
            ],
            ADDRESS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_address)],
            PART_NODE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_part_node)],
            AXLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_axle)],
            PARTS: [
                MessageHandler(filters.Regex("^(Метро Давыдково)$"), get_pickup_station),
                MessageHandler(filters.Regex("^(Метро Строгино)$"), get_pickup_station),
                MessageHandler(filters.Regex("^(Метро Южная)$"), get_pickup_station),
                MessageHandler(filters.TEXT & ~filters.COMMAND, get_parts)
            ],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(CommandHandler("my_orders", my_orders))
    app.add_handler(CommandHandler("bonus", bonus_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("delivery", delivery_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("menu", admin_menu))
    app.add_handler(CommandHandler("fix", fix_orders))
    
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), manager_reply))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), track_input))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), send_question_to_client))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), process_offer))
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, client_answer))
    
    app.add_handler(CallbackQueryHandler(select_cb, pattern="^sel_"))
    app.add_handler(CallbackQueryHandler(finalize_cb, pattern="^fin_"))
    app.add_handler(CallbackQueryHandler(view_order, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(back_orders, pattern="^back_orders$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^pay_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^ship_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_stats$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_fix$"))
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^admin_back$"))
    app.add_handler(CallbackQueryHandler(manager_ask_question, pattern="^ask_"))
    app.add_handler(CallbackQueryHandler(send_offer, pattern="^send_offer_"))
    app.add_handler(CallbackQueryHandler(admin_filter_callback, pattern="^filter_"))
    
    print("🤖 БОТ ЗАПУЩЕН!")
    print(f"👨‍💼 Админ ID: {MANAGER_ID}")
    print(f"💾 База данных: {DB_PATH}")
    print("📊 Сортировка заказов по статусам включена")
    app.run_polling()

if __name__ == "__main__":
    main()