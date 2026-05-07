#!/usr/bin/env python3
# -*- coding: utf-8 -*-

import sqlite3
import random
import string
import re
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, filters

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.environ.get("BOT_TOKEN")
MANAGER_ID = 804070528
DELIVERY_BASE = 500
# =================================

# Состояния
VIN, MILEAGE, DRIVING_STYLE, SPEED_CITY, SPEED_HIGHWAY, CITY, DISTANCE, DELIVERY_TYPE, PARTS, AXLE_TYPE = range(10)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE,
        user_id INTEGER, user_name TEXT, vin TEXT, mileage TEXT,
        driving_style TEXT, speed_city INTEGER, speed_highway INTEGER,
        city TEXT, distance INTEGER DEFAULT 0,
        delivery_type TEXT, delivery_price INTEGER DEFAULT 0, delivery_desc TEXT,
        axle_type TEXT, needed_parts TEXT, selected_products TEXT, final_order TEXT,
        status TEXT, status_text TEXT, tracking_number TEXT,
        total_price REAL DEFAULT 0, client_messages TEXT, manager_messages TEXT,
        created_at TEXT)''')
    
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

def save_order(data):
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
    c.execute('''INSERT INTO orders (order_number, user_id, user_name, vin, mileage,
        driving_style, speed_city, speed_highway, city, distance,
        delivery_type, delivery_price, delivery_desc, axle_type, needed_parts,
        status, status_text, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (num, data['user_id'], data['user_name'], data['vin'], data['mileage'],
         data['driving_style'], data['speed_city'], data['speed_highway'],
         data['city'], data.get('distance',0), data.get('delivery_type',''),
         data.get('delivery_price',0), data.get('delivery_desc',''),
         data.get('axle_type',''), data['needed_parts'],
         'pending', '🟡 Ожидает подбора', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    return num

def update_order(order_number, **kwargs):
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    for key, val in kwargs.items():
        c.execute(f"UPDATE orders SET {key} = ? WHERE order_number = ?", (val, order_number))
    conn.commit()
    conn.close()

def get_order(order_number):
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    c.execute('SELECT * FROM orders WHERE order_number = ?', (order_number,))
    r = c.fetchone()
    conn.close()
    if r:
        return {'order_number': r[1], 'user_id': r[2], 'user_name': r[3], 'vin': r[4],
                'mileage': r[5], 'driving_style': r[6], 'speed_city': r[7],
                'speed_highway': r[8], 'city': r[9], 'distance': r[10],
                'delivery_type': r[11], 'delivery_price': r[12], 'delivery_desc': r[13],
                'axle_type': r[14], 'needed_parts': r[15], 'selected_products': r[16],
                'final_order': r[17], 'status': r[18], 'status_text': r[19],
                'tracking_number': r[20], 'total_price': r[21], 'client_messages': r[22],
                'manager_messages': r[23], 'created_at': r[24]}
    return None

def get_user_orders(user_id):
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    c.execute('SELECT order_number, status_text, created_at FROM orders WHERE user_id = ? ORDER BY id DESC', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_orders():
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    c.execute('SELECT order_number, user_name, status_text, created_at FROM orders ORDER BY id DESC')
    rows = c.fetchall()
    conn.close()
    return rows

def get_user_total(user_id):
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    c.execute('SELECT SUM(total_price) FROM orders WHERE user_id = ? AND status != "pending"', (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] or 0

def get_bonus(user_id):
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    c.execute('SELECT balance FROM bonuses WHERE user_id = ?', (user_id,))
    r = c.fetchone()
    conn.close()
    return r[0] if r else 0

def add_bonus(user_id, order_num, amount, desc):
    conn = sqlite3.connect('shop_bot.db')
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
main_menu = ReplyKeyboardMarkup([["🛒 Новый заказ"], ["📦 Мои заказы", "🎁 Бонусы"], ["🔗 Рефералы", "🚚 Доставка"], ["ℹ️ Помощь"]], resize_keyboard=True)
style_kb = ReplyKeyboardMarkup([["🚗 Спокойный", "⚡ Умеренный"], ["🏎️ Активный", "🔥 Спортивный"]], resize_keyboard=True)
delivery_kb = ReplyKeyboardMarkup([["🚚 Курьером"], ["📦 Самовывоз"], ["🚛 Сторонняя фирма"]], resize_keyboard=True)
axle_kb = ReplyKeyboardMarkup([["🔧 Передняя ось"], ["🔧 Задняя ось"], ["🔧 Передняя + Задняя"]], resize_keyboard=True)

def speed_city_kb():
    kb = []
    for s in range(0, 151, 10):
        kb.append([InlineKeyboardButton(f"{s} км/ч", callback_data=f"sc_{s}")])
    return InlineKeyboardMarkup(kb)

def speed_highway_kb():
    kb = []
    for s in range(50, 201, 25):
        kb.append([InlineKeyboardButton(f"{s} км/ч", callback_data=f"sh_{s}")])
    return InlineKeyboardMarkup(kb)

def admin_kb():
    orders = get_all_orders()
    if not orders: return None
    kb = [[InlineKeyboardButton(f"{o[0]} | {o[1][:10]} | {o[2][:10]}", callback_data=f"adm_{o[0]}")] for o in orders[:10]]
    kb.append([InlineKeyboardButton("📊 Статистика", callback_data="adm_stats")])
    return InlineKeyboardMarkup(kb)

def edit_delivery_kb(order_num):
    return InlineKeyboardMarkup([
        [InlineKeyboardButton("🚚 Курьером", callback_data=f"edit_delivery_{order_num}_Курьером")],
        [InlineKeyboardButton("📦 Самовывоз", callback_data=f"edit_delivery_{order_num}_Самовывоз")],
        [InlineKeyboardButton("🚛 Сторонняя фирма", callback_data=f"edit_delivery_{order_num}_Сторонняя")],
        [InlineKeyboardButton("◀️ Назад", callback_data=f"adm_{order_num}")]
    ])

# ========== КЛИЕНТЫ ==========
async def start(upd, ctx):
    if ctx.args and ctx.args[0].startswith('ref_'):
        ref_id = int(ctx.args[0][4:])
        if ref_id != upd.effective_user.id:
            conn = sqlite3.connect('shop_bot.db')
            c = conn.cursor()
            c.execute('INSERT INTO referrals (referrer_id, referred_id, created_at) VALUES (?,?,?)',
                      (ref_id, upd.effective_user.id, datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
            c.execute('INSERT INTO bonuses (user_id, referrer_id) VALUES (?,?) ON CONFLICT(user_id) DO UPDATE SET referrer_id = ?',
                      (upd.effective_user.id, ref_id, ref_id))
            conn.commit()
            conn.close()
            add_bonus(upd.effective_user.id, None, 500, "Приветственные бонусы")
            await ctx.bot.send_message(ref_id, f"👋 {upd.effective_user.full_name} перешёл по вашей ссылке!")
            await upd.message.reply_text("🎉 +500 бонусов за регистрацию!")
    await upd.message.reply_text("🏎️ Добро пожаловать!\nНажмите 🛒 Новый заказ", reply_markup=main_menu)

async def new_order(upd, ctx): await upd.message.reply_text("🔧 VIN номер:"); return VIN
async def get_vin(upd, ctx): ctx.user_data['vin'] = upd.message.text; await upd.message.reply_text("📊 Пробег (км):"); return MILEAGE
async def get_mileage(upd, ctx): ctx.user_data['mileage'] = upd.message.text; await upd.message.reply_text("🏎️ Стиль езды:", reply_markup=style_kb); return DRIVING_STYLE
async def get_style(upd, ctx): ctx.user_data['driving_style'] = upd.message.text; await upd.message.reply_text("🏙️ **Введите город**"); return CITY
async def get_city(upd, ctx): ctx.user_data['city'] = upd.message.text; await upd.message.reply_text("🌆 **Скорость в городе** (км/ч, шаг 10):", reply_markup=speed_city_kb()); return SPEED_CITY
async def get_speed_city(upd, ctx):
    q = upd.callback_query; await q.answer()
    ctx.user_data['speed_city'] = int(q.data.split('_')[1])
    await q.edit_message_text(f"✅ Скорость в городе: {ctx.user_data['speed_city']} км/ч\n\n🛣️ **Скорость на трассе** (км/ч, шаг 25):", reply_markup=speed_highway_kb())
    return SPEED_HIGHWAY
async def get_speed_highway(upd, ctx):
    q = upd.callback_query; await q.answer()
    ctx.user_data['speed_highway'] = int(q.data.split('_')[1])
    await q.edit_message_text(f"✅ Скорость в городе: {ctx.user_data['speed_city']} км/ч\n✅ Скорость на трассе: {ctx.user_data['speed_highway']} км/ч\n\n📍 **Расстояние от МКАД (км):**\n0 - если Москва")
    return DISTANCE
async def get_dist(upd, ctx):
    try:
        ctx.user_data['distance'] = int(upd.message.text)
        await upd.message.reply_text("🚚 **Выберите способ доставки:**", reply_markup=delivery_kb)
        return DELIVERY_TYPE
    except: await upd.message.reply_text("❌ Введите число"); return DISTANCE
async def get_delivery(upd, ctx):
    choice = upd.message.text
    ctx.user_data['delivery_type'] = choice
    km = ctx.user_data.get('distance', 0)
    if "Курьером" in choice:
        price = calc_delivery_price(km)
        desc = f"🚚 Курьер: {km} км от МКАД = {price} ₽"
        ctx.user_data['delivery_price'] = price
    elif "Самовывоз" in choice:
        price = 0
        desc = "📦 Самовывоз (бесплатно)"
        ctx.user_data['delivery_price'] = price
    else:
        price = None
        desc = "🚛 Сторонняя фирма (стоимость уточнит менеджер)"
        ctx.user_data['delivery_price'] = 0
    ctx.user_data['delivery_desc'] = desc
    await upd.message.reply_text(f"✅ {desc}\n\n🔧 **Для какой оси нужны запчасти?**", reply_markup=axle_kb)
    return AXLE_TYPE
async def get_axle(upd, ctx):
    ctx.user_data['axle_type'] = upd.message.text
    await upd.message.reply_text(f"✅ Выбрано: {upd.message.text}\n\n🔧 **Какие запчасти нужны?**\n(напишите список)")
    return PARTS
async def get_parts(upd, ctx):
    data = ctx.user_data
    order_num = save_order({
        'user_id': upd.effective_user.id, 'user_name': upd.effective_user.full_name,
        'vin': data.get('vin',''), 'mileage': data['mileage'],
        'driving_style': data['driving_style'], 'speed_city': data.get('speed_city',0),
        'speed_highway': data.get('speed_highway',0), 'city': data['city'],
        'distance': data.get('distance',0), 'delivery_type': data['delivery_type'],
        'delivery_price': data.get('delivery_price',0), 'delivery_desc': data['delivery_desc'],
        'axle_type': data.get('axle_type',''), 'needed_parts': upd.message.text
    })
    await ctx.bot.send_message(MANAGER_ID, 
        f"🆕 ЗАКАЗ #{order_num}\n👤 {upd.effective_user.full_name}\n🚗 VIN: {data['vin']}\n📊 Пробег: {data['mileage']} км\n🏎️ {data['driving_style']}\n🌆 Город: {data['city']} | {data.get('speed_city',0)} км/ч\n🛣️ Трасса: {data.get('speed_highway',0)} км/ч\n📍 {data.get('distance',0)} км от МКАД\n{data['delivery_desc']}\n🔧 Ось: {data.get('axle_type','')}\n📝 {upd.message.text}")
    await upd.message.reply_text(f"✅ Заказ #{order_num} создан!\n{data['delivery_desc']}\n\nОжидайте подбора (15-30 мин)", reply_markup=main_menu)
    return ConversationHandler.END

async def my_orders(upd, ctx):
    orders = get_user_orders(upd.effective_user.id)
    if not orders: await upd.message.reply_text("📭 Нет заказов"); return
    kb = [[InlineKeyboardButton(f"{o[0]} - {o[1][:15]}", callback_data=f"view_{o[0]}")] for o in orders]
    await upd.message.reply_text("📦 ВАШИ ЗАКАЗЫ:", reply_markup=InlineKeyboardMarkup(kb))

async def view_order(upd, ctx):
    q = upd.callback_query; await q.answer()
    order = get_order(q.data[5:])
    if not order: await q.edit_message_text("❌ Заказ не найден"); return
    text = f"📋 ЗАКАЗ {order['order_number']}\n👤 {order['user_name']}\n📅 {order['created_at']}\n🚗 VIN: {order['vin']}\n📊 {order['mileage']} км\n🏎️ {order['driving_style']}\n🌆 {order['city']} | {order['speed_city']} км/ч\n🛣️ Трасса: {order['speed_highway']} км/ч\n{order['delivery_desc']}\n🔧 Ось: {order['axle_type']}\n━━━━━━━━━━━━━━\n💰 Подбор:\n{order['selected_products'] or '-'}\n📦 {order['status_text']}"
    if order['tracking_number']: text += f"\n📦 Трек: {order['tracking_number']}"
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="back_orders")]]))

async def back_orders(upd, ctx):
    q = upd.callback_query; await q.answer()
    orders = get_user_orders(q.from_user.id)
    if orders: await q.edit_message_text("📦 ВАШИ ЗАКАЗЫ:", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton(f"{o[0]} - {o[1][:15]}", callback_data=f"view_{o[0]}")] for o in orders]))
    else: await q.edit_message_text("📭 Нет заказов")

async def bonus_cmd(upd, ctx):
    uid = upd.effective_user.id
    bal = get_bonus(uid)
    total = get_user_total(uid)
    percent = get_bonus_percent(uid)
    text = f"🎁 **БОНУСЫ**\n💰 Баланс: {int(bal)}\n📦 Накоплено: {int(total)} ₽\n⭐ Начисление: {percent}%\n\n📊 Градация:\n1%→100к 2%→200к 3%→300к 4%→400к 5%→500к 6%→600к 7%→700к 8%→800к 9%→900к 10%→1М"
    await upd.message.reply_text(text, parse_mode='Markdown')

async def referral_cmd(upd, ctx):
    link = f"https://t.me/{(await ctx.bot.get_me()).username}?start=ref_{upd.effective_user.id}"
    await upd.message.reply_text(f"🔗 **Реферальная ссылка**\n{link}\n\n0.5% от заказов друзей вам бонусами!", parse_mode='Markdown')

async def delivery_cmd(upd, ctx):
    text = f"🚚 **ДОСТАВКА**\nБаза: {DELIVERY_BASE} ₽\n\n| км | тариф | итого |\n| 0 | 0 | {DELIVERY_BASE} |\n| 1-50 | 25 | {DELIVERY_BASE}+км×25 |\n| 51-100 | 35 | {DELIVERY_BASE}+км×35 |\n| 101+ | 50 | {DELIVERY_BASE}+км×50 |\n\n📌 Скидка на доставку от суммы заказа:\n10к→5%, 15к→10%, 20к→15%... до 100%"
    await upd.message.reply_text(text, parse_mode='Markdown')

async def help_cmd(upd, ctx):
    await upd.message.reply_text("📖 ПОМОЩЬ\n/start - меню\n/my_orders - заказы\n/bonus - бонусы\n/referral - рефералы\n/delivery - доставка\n/menu - админ-панель", reply_markup=main_menu)

# ========== МЕНЕДЖЕР ==========
user_selections = {}

async def manager_reply(upd, ctx):
    if upd.effective_user.id != MANAGER_ID or not upd.message.reply_to_message: return
    match = re.search(r"ЗАКАЗ #(RVN-\w{6})", upd.message.reply_to_message.text or "")
    if not match: return
    order_num = match.group(1)
    products = parse_products(upd.message.text)
    if not products: await upd.message.reply_text("❌ Формат: Название = цена"); return
    update_order(order_num, selected_products=upd.message.text, status='waiting_selection', status_text='🟡 Ожидает выбора')
    order = get_order(order_num)
    if order:
        kb = []
        for i,p in enumerate(products): kb.append([InlineKeyboardButton(f"⬜ {p['name']} — {int(p['price'])} ₽", callback_data=f"sel_{order_num}_{i}")])
        kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ", callback_data=f"fin_{order_num}")])
        await ctx.bot.send_message(order['user_id'], text=f"🛒 ВЫБЕРИТЕ ЗАПЧАСТИ\nЗаказ {order_num}", reply_markup=InlineKeyboardMarkup(kb))
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
        kb.append([InlineKeyboardButton(f"{cb} {p['name']} — {int(p['price'])} ₽", callback_data=f"sel_{order_num}_{i}")])
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
    delivery_final = int(delivery_price * (100 - delivery_disc) / 100) if delivery_disc < 100 else 0
    final_total = total + delivery_final
    
    update_order(order_num, final_order=f"✅ ЗАКАЗ\n" + "\n".join([f"• {p['name']} — {int(p['price'])} ₽" for p in selected]) + f"\n🚚 Доставка: {delivery_final} ₽\n💰 ИТОГО: {final_total} ₽", total_price=total)
    
    bonus_percent = get_bonus_percent(uid)
    bonus = int(total * bonus_percent / 100)
    add_bonus(uid, order_num, bonus, f"Заказ {order_num} ({bonus_percent}%)")
    if bonus > 0: await q.edit_message_text(f"✅ ЗАКАЗ ПОДТВЕРЖДЕН!\n\n" + "\n".join([f"• {p['name']} — {int(p['price'])} ₽" for p in selected]) + f"\n🚚 Доставка: {delivery_final} ₽\n💰 ИТОГО: {final_total} ₽\n\n🎁 +{bonus} бонусов начислено!")
    else: await q.edit_message_text(f"✅ ЗАКАЗ ПОДТВЕРЖДЕН!\n\n" + "\n".join([f"• {p['name']} — {int(p['price'])} ₽" for p in selected]) + f"\n🚚 Доставка: {delivery_final} ₽\n💰 ИТОГО: {final_total} ₽")
    
    await ctx.bot.send_message(MANAGER_ID, f"✅ ЗАКАЗ {order_num} ПОДТВЕРЖДЕН\n👤 {order['user_name']}\n💰 Сумма товаров: {int(total)} ₽\n🚚 Доставка: {delivery_price} → {delivery_final} ₽\n💎 Итого: {final_total} ₽")
    del user_selections[uid][order_num]

# ========== АДМИН ==========
async def admin_menu(upd, ctx):
    if upd.effective_user.id != MANAGER_ID: await upd.message.reply_text("⛔ Доступ запрещён"); return
    kb = admin_kb()
    if not kb: await upd.message.reply_text("📭 Нет заказов\n/fix - создать тестовый"); return
    await upd.message.reply_text("👨‍💼 МЕНЮ АДМИНА", reply_markup=kb)

async def admin_order(upd, ctx):
    q = upd.callback_query; await q.answer()
    order = get_order(q.data[4:])
    if not order: await q.edit_message_text("❌ Не найден"); return
    text = f"📋 {order['order_number']}\n👤 {order['user_name']}\n🌆 {order['city']} | {order['speed_city']} км/ч\n🛣️ Трасса: {order['speed_highway']} км/ч\n💰 Сумма: {int(order['total_price'])} ₽\n{order['delivery_desc']}\n🔧 Ось: {order['axle_type']}\n📦 {order['status_text']}"
    kb = [
        [InlineKeyboardButton("💰 Оплачен", callback_data=f"pay_{order['order_number']}")],
        [InlineKeyboardButton("🚚 Отправлен", callback_data=f"ship_{order['order_number']}")],
        [InlineKeyboardButton("🏠 Доставлен", callback_data=f"del_{order['order_number']}")],
        [InlineKeyboardButton("✏️ Изменить доставку", callback_data=f"edit_delivery_{order['order_number']}")],
        [InlineKeyboardButton("💬 Чат", callback_data=f"chat_{order['order_number']}")],
        [InlineKeyboardButton("◀️ Назад", callback_data="adm_back")]
    ]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def edit_delivery(upd, ctx):
    q = upd.callback_query; await q.answer()
    parts = q.data.split('_')
    order_num = parts[2]
    if len(parts) > 3:
        new_delivery = parts[3]
        order = get_order(order_num)
        km = order['distance']
        if new_delivery == "Курьером":
            price = calc_delivery_price(km)
            desc = f"🚚 Курьер: {km} км от МКАД = {price} ₽"
        elif new_delivery == "Самовывоз":
            price = 0
            desc = "📦 Самовывоз (бесплатно)"
        else:
            price = 0
            desc = "🚛 Сторонняя фирма (стоимость уточнит менеджер)"
        update_order(order_num, delivery_type=new_delivery, delivery_price=price, delivery_desc=desc)
        await ctx.bot.send_message(order['user_id'], f"✏️ Менеджер изменил способ доставки на: {desc}")
        await q.edit_message_text(f"✅ Доставка изменена на: {desc}\n\n💰 Стоимость: {price} ₽")
    else:
        await q.edit_message_text("✏️ **Выберите новый способ доставки:**", reply_markup=edit_delivery_kb(order_num))

async def admin_back(upd, ctx):
    q = upd.callback_query; await q.answer()
    kb = admin_kb()
    if kb: await q.edit_message_text("👨‍💼 МЕНЮ АДМИНА", reply_markup=kb)
    else: await q.edit_message_text("📭 Нет заказов")

async def pay_order(upd, ctx):
    q = upd.callback_query; await q.answer()
    order_num = q.data[4:]
    update_order(order_num, status='paid', status_text='💰 Оплачен')
    order = get_order(order_num)
    if order: await ctx.bot.send_message(order['user_id'], f"✅ Заказ {order_num} оплачен!")
    await q.edit_message_text(q.message.text + "\n✅ Оплачен")

async def ship_order(upd, ctx):
    q = upd.callback_query; await q.answer()
    ctx.user_data['track_for'] = q.data[5:]
    await q.edit_message_text("📦 Введите трек-номер:")

async def deliver_order(upd, ctx):
    q = upd.callback_query; await q.answer()
    order_num = q.data[5:]
    update_order(order_num, status='delivered', status_text='🏠 Доставлен')
    order = get_order(order_num)
    if order: await ctx.bot.send_message(order['user_id'], f"🏠 Заказ {order_num} доставлен! Спасибо!")
    await q.edit_message_text(q.message.text + "\n✅ Доставлен")

async def track_input(upd, ctx):
    if upd.effective_user.id != MANAGER_ID: return
    if 'track_for' in ctx.user_data:
        order_num = ctx.user_data['track_for']
        update_order(order_num, tracking_number=upd.message.text, status='shipped', status_text='🚚 Отправлен')
        order = get_order(order_num)
        if order: await ctx.bot.send_message(order['user_id'], f"📦 Заказ {order_num} отправлен!\nТрек: {upd.message.text}")
        await upd.message.reply_text(f"✅ Трек добавлен к {order_num}")
        del ctx.user_data['track_for']

async def fix_orders(upd, ctx):
    if upd.effective_user.id != MANAGER_ID: return
    conn = sqlite3.connect('shop_bot.db')
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM orders')
    if c.fetchone()[0] == 0:
        num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
        c.execute('''INSERT INTO orders (order_number, user_id, user_name, vin, mileage,
            driving_style, speed_city, speed_highway, city, distance,
            delivery_type, delivery_price, delivery_desc, axle_type, needed_parts,
            status, status_text, created_at)
            VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
            (num, MANAGER_ID, 'Тестовый Клиент', 'TEST123', '50000', '🚗 Спокойный',
             60, 110, 'Москва', 0, 'Курьером', 500, '🚚 Курьер: 0 км от МКАД = 500 ₽',
             'Передняя + Задняя', 'Тестовый заказ', 'pending', '🟡 Ожидает подбора',
             datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        await upd.message.reply_text(f"✅ Тестовый заказ {num} создан!\n/menu")
    else:
        await upd.message.reply_text("📊 Заказы уже есть\n/menu")
    conn.close()

# ========== ЗАПУСК ==========
def main():
    init_db()
    app = Application.builder().token(BOT_TOKEN).build()
    
    conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^(🛒 Новый заказ)$"), new_order)],
        states={
            VIN: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_vin)],
            MILEAGE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_mileage)],
            DRIVING_STYLE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_style)],
            CITY: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_city)],
            SPEED_CITY: [CallbackQueryHandler(get_speed_city, pattern="^sc_")],
            SPEED_HIGHWAY: [CallbackQueryHandler(get_speed_highway, pattern="^sh_")],
            DISTANCE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_dist)],
            DELIVERY_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_delivery)],
            AXLE_TYPE: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_axle)],
            PARTS: [MessageHandler(filters.TEXT & ~filters.COMMAND, get_parts)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    app.add_handler(CommandHandler("start", start))
    app.add_handler(conv)
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), manager_reply))
    app.add_handler(MessageHandler(filters.Chat(chat_id=MANAGER_ID), track_input))
    app.add_handler(CommandHandler("menu", admin_menu))
    app.add_handler(CommandHandler("fix", fix_orders))
    
    app.add_handler(CallbackQueryHandler(select_cb, pattern="^sel_"))
    app.add_handler(CallbackQueryHandler(finalize_cb, pattern="^fin_"))
    app.add_handler(CallbackQueryHandler(view_order, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(back_orders, pattern="^back_orders$"))
    app.add_handler(CallbackQueryHandler(admin_order, pattern="^adm_"))
    app.add_handler(CallbackQueryHandler(admin_back, pattern="^adm_back$"))
    app.add_handler(CallbackQueryHandler(pay_order, pattern="^pay_"))
    app.add_handler(CallbackQueryHandler(ship_order, pattern="^ship_"))
    app.add_handler(CallbackQueryHandler(deliver_order, pattern="^del_"))
    app.add_handler(CallbackQueryHandler(edit_delivery, pattern="^edit_delivery_"))
    
    print("🤖 БОТ ЗАПУЩЕН!")
    print(f"👨‍💼 Админ ID: {MANAGER_ID}")
    print("📋 Команды:")
    print("   /menu - админ-панель")
    print("   /fix - создать тестовый заказ")
    app.run_polling()

if __name__ == "__main__":
    main()