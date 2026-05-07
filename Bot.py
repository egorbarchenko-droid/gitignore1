import os
import sqlite3
import random
import string
import re
from datetime import datetime
from telegram import Update, ReplyKeyboardMarkup, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, MessageHandler, CallbackQueryHandler, ContextTypes, ConversationHandler, filters

# ========== НАСТРОЙКИ ==========
BOT_TOKEN = os.getenv("BOT_TOKEN")
MANAGER_ID = 804070528
DELIVERY_BASE = 500

# Путь к базе данных - используем папку внутри приложения
DB_DIR = os.path.join(os.getcwd(), "data")
DB_PATH = os.path.join(DB_DIR, "shop_bot.db")

# СОЗДАЁМ ПАПКУ, ЕСЛИ ЕЁ НЕТ
os.makedirs(DB_DIR, exist_ok=True)
# =================================

VIN, MILEAGE, DRIVING_STYLE, SPEED, CITY, DISTANCE, DELIVERY_TYPE, PARTS = range(8)

# ========== БАЗА ДАННЫХ ==========
def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS orders (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        order_number TEXT UNIQUE,
        user_id INTEGER, user_name TEXT, vin TEXT, mileage TEXT,
        driving_style TEXT, speed TEXT, city TEXT, distance INTEGER DEFAULT 0,
        delivery_type TEXT, delivery_price INTEGER DEFAULT 0, delivery_desc TEXT,
        needed_parts TEXT, selected_products TEXT, final_order TEXT,
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
    print(f"✅ База данных инициализирована: {DB_PATH}")

def save_order(data):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
    c.execute('''INSERT INTO orders (order_number, user_id, user_name, vin, mileage,
        driving_style, speed, city, distance, delivery_type, delivery_price, delivery_desc,
        needed_parts, status, status_text, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (num, data['user_id'], data['user_name'], data['vin'], data['mileage'],
         data['driving_style'], data['speed'], data['city'], data.get('distance',0),
         data.get('delivery_type',''), data.get('delivery_price',0),
         data.get('delivery_desc',''), data['needed_parts'],
         'pending', '🟡 Ожидает подбора', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
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
                'mileage': r[5], 'driving_style': r[6], 'speed': r[7], 'city': r[8],
                'distance': r[9], 'delivery_type': r[10], 'delivery_price': r[11],
                'delivery_desc': r[12], 'needed_parts': r[13], 'selected_products': r[14],
                'final_order': r[15], 'status': r[16], 'status_text': r[17],
                'tracking_number': r[18], 'total_price': r[19], 'client_messages': r[20],
                'manager_messages': r[21], 'created_at': r[22]}
    return None

def get_user_orders(user_id):
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT order_number, status_text, created_at FROM orders WHERE user_id = ? ORDER BY id DESC', (user_id,))
    rows = c.fetchall()
    conn.close()
    return rows

def get_all_orders():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT order_number, user_name, status_text, created_at FROM orders ORDER BY id DESC')
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
    if not text: return []
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

def admin_kb():
    orders = get_all_orders()
    if not orders: return None
    kb = [[InlineKeyboardButton(f"{o[0]} | {o[1][:10]} | {o[2][:10]}", callback_data=f"adm_{o[0]}")] for o in orders[:10]]
    kb.append([InlineKeyboardButton("📊 Статистика", callback_data="adm_stats")])
    return InlineKeyboardMarkup(kb)

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
            await upd.message.reply_text("🎉 +500 бонусов за регистрацию!")
    await upd.message.reply_text("🏎️ Добро пожаловать!\nНажмите 🛒 Новый заказ", reply_markup=main_menu)

async def new_order(upd, ctx): await upd.message.reply_text("🔧 VIN номер:"); return VIN
async def get_vin(upd, ctx): ctx.user_data['vin'] = upd.message.text; await upd.message.reply_text("📊 Пробег (км):"); return MILEAGE
async def get_mileage(upd, ctx): ctx.user_data['mileage'] = upd.message.text; await upd.message.reply_text("🏎️ Стиль езды:", reply_markup=style_kb); return DRIVING_STYLE
async def get_style(upd, ctx): ctx.user_data['driving_style'] = upd.message.text; await upd.message.reply_text("⚡ Скорость на трассе (км/ч):"); return SPEED
async def get_speed(upd, ctx): ctx.user_data['speed'] = upd.message.text; await upd.message.reply_text("🏙️ Ваш город:"); return CITY
async def get_city(upd, ctx): ctx.user_data['city'] = upd.message.text; await upd.message.reply_text("📍 Расстояние от МКАД (км):\n0 - если Москва"); return DISTANCE
async def get_dist(upd, ctx):
    try:
        ctx.user_data['distance'] = int(upd.message.text)
        await upd.message.reply_text("🚚 Выберите способ доставки:", reply_markup=delivery_kb)
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
    await upd.message.reply_text(f"✅ {desc}\n\n🔧 Какие запчасти нужны?")
    return PARTS

async def get_parts(upd, ctx):
    data = ctx.user_data
    order_num = save_order({
        'user_id': upd.effective_user.id, 'user_name': upd.effective_user.full_name,
        'vin': data.get('vin',''), 'mileage': data['mileage'], 'driving_style': data['driving_style'],
        'speed': data['speed'], 'city': data['city'], 'distance': data.get('distance',0),
        'delivery_type': data['delivery_type'], 'delivery_price': data.get('delivery_price',0),
        'delivery_desc': data['delivery_desc'], 'needed_parts': upd.message.text
    })
    await ctx.bot.send_message(MANAGER_ID, f"🆕 ЗАКАЗ #{order_num}\n👤 {upd.effective_user.full_name}\n🚗 VIN: {data['vin']}\n📊 Пробег: {data['mileage']} км\n🏎️ {data['driving_style']}\n🏙️ {data['city']} ({data.get('distance',0)} км от МКАД)\n{data['delivery_desc']}\n🔧 {upd.message.text}")
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
    text = f"📋 ЗАКАЗ {order['order_number']}\n👤 {order['user_name']}\n📅 {order['created_at']}\n🚗 VIN: {order['vin']}\n📊 {order['mileage']} км\n🏎️ {order['driving_style']}\n🏙️ {order['city']}\n{order['delivery_desc']}\n━━━━━━━━━━━━━━\n💰 Подбор:\n{order['selected_products'] or '-'}\n📦 {order['status_text']}"
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
    
    text = f"""🎁 **БОНУСНАЯ ПРОГРАММА**

━━━━━━━━━━━━━━━━━━━

💰 **Ваш текущий баланс:** {int(bal)} бонусов

📦 **Накоплено покупками:** {int(total)} ₽

⭐ **Ваш процент начисления:** {percent}%

━━━━━━━━━━━━━━━━━━━

📊 **Как растёт процент начисления:**

| Сумма покупок | % начисления |
|:---|:---|
| до 100 000 ₽ | 1% |
| от 100 000 ₽ | 2% |
| от 200 000 ₽ | 3% |
| от 300 000 ₽ | 4% |
| от 400 000 ₽ | 5% |
| от 500 000 ₽ | 6% |
| от 600 000 ₽ | 7% |
| от 700 000 ₽ | 8% |
| от 800 000 ₽ | 9% |
| от 900 000 ₽ | 10% |

━━━━━━━━━━━━━━━━━━━

💡 **Как это работает?**
• Бонусы начисляются после подтверждения заказа
• 1 бонус = 1 рубль скидки на следующий заказ
• Чем больше покупаете, тем выше процент начисления

🔗 **Приглашайте друзей:** реферальная ссылка в меню"""
    
    await upd.message.reply_text(text, parse_mode='Markdown')

async def referral_cmd(upd, ctx):
    link = f"https://t.me/{(await ctx.bot.get_me()).username}?start=ref_{upd.effective_user.id}"
    await upd.message.reply_text(f"🔗 **Реферальная ссылка**\n{link}\n\n0.5% от заказов друзей вам бонусами!", parse_mode='Markdown')

async def delivery_cmd(upd, ctx):
    text = f"""🚚 **ДОСТАВКА**

Базовая стоимость доставки: {DELIVERY_BASE} ₽

━━━━━━━━━━━━━━━━━━━

📊 **Тарифы доставки курьером (от МКАД):**

🔹 0 км → {DELIVERY_BASE} ₽
🔹 1-50 км → {DELIVERY_BASE} + (км × 25) ₽
🔹 51-100 км → {DELIVERY_BASE} + (км × 35) ₽
🔹 101+ км → {DELIVERY_BASE} + (км × 50) ₽

━━━━━━━━━━━━━━━━━━━

🎁 **Скидка на доставку в зависимости от суммы заказа:**

| Сумма заказа | Скидка на доставку |
|:---|:---|
| от 10 000 ₽ | 5% |
| от 15 000 ₽ | 10% |
| от 20 000 ₽ | 15% |
| от 25 000 ₽ | 20% |
| от 30 000 ₽ | 25% |
| от 35 000 ₽ | 30% |
| от 40 000 ₽ | 35% |
| от 45 000 ₽ | 40% |
| от 50 000 ₽ | 45% |
| от 100 000 ₽ и выше | 100% (доставка бесплатно) |

━━━━━━━━━━━━━━━━━━━

📌 **Пример расчёта:**
Заказ на 35 000 ₽ + доставка 500 ₽
→ скидка 30% → вы платите за доставку 350 ₽

━━━━━━━━━━━━━━━━━━━

✅ **Самовывоз** — бесплатно
🚛 **Доставка сторонней ТК** — стоимость уточнит менеджер

📞 По вопросам доставки пишите менеджеру"""
    await upd.message.reply_text(text, parse_mode='Markdown')

async def help_cmd(upd, ctx):
    text = """📖 **ПОМОЩЬ И КОМАНДЫ**

━━━━━━━━━━━━━━━━━━━

🔹 **/start** — Главное меню

🔹 **🛒 Новый заказ** — Оформить заказ на запчасти

🔹 **📦 Мои заказы** — История и статусы заказов

🔹 **🎁 Бонусы** — Баланс бонусов и условия начисления

🔹 **🔗 Рефералы** — Приглашайте друзей и получайте бонусы

🔹 **🚚 Доставка** — Тарифы и условия доставки

━━━━━━━━━━━━━━━━━━━

📞 **По всем вопросам:**
Напишите нашему менеджеру — ответим в течение 15-30 минут

⏱️ **Время работы:** Пн-Пт с 9:00 до 20:00"""
    
    await upd.message.reply_text(text, parse_mode='Markdown', reply_markup=main_menu)

# ========== АДМИН ==========
user_selections = {}

async def admin_cmd(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        await upd.message.reply_text("⛔ Доступ запрещён.")
        return
    kb = admin_kb()
    if not kb:
        await upd.message.reply_text("📭 Нет заказов\nСоздайте тестовый заказ: /fix")
        return
    await upd.message.reply_text("👨‍💼 **АДМИН-ПАНЕЛЬ**\n\nВыберите заказ:", parse_mode='Markdown', reply_markup=kb)

async def admin_stats(upd, ctx):
    if upd.effective_user.id != MANAGER_ID: return
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('SELECT COUNT(*) FROM orders')
    total_orders = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM orders WHERE status = "pending"')
    pending = c.fetchone()[0]
    c.execute('SELECT COUNT(*) FROM orders WHERE status = "delivered"')
    delivered = c.fetchone()[0]
    c.execute('SELECT SUM(total_price) FROM orders WHERE status != "pending"')
    total_sum = c.fetchone()[0] or 0
    conn.close()
    text = f"📊 **СТАТИСТИКА**\n\n📦 Всего: {total_orders}\n🟡 В обработке: {pending}\n🏠 Доставлено: {delivered}\n💰 Выручка: {int(total_sum)} ₽"
    await upd.edit_message_text(text, parse_mode='Markdown', reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="adm_back")]]))

async def admin_order(upd, ctx):
    q = upd.callback_query; await q.answer()
    order = get_order(q.data[4:])
    if not order: await q.edit_message_text("❌ Не найден"); return
    text = f"📋 {order['order_number']}\n👤 {order['user_name']}\n💰 Сумма: {int(order['total_price'])} ₽\n{order['delivery_desc']}\n📦 {order['status_text']}"
    kb = [[InlineKeyboardButton("💰 Оплачен", callback_data=f"pay_{order['order_number']}")],
          [InlineKeyboardButton("🚚 Отправлен", callback_data=f"ship_{order['order_number']}")],
          [InlineKeyboardButton("🏠 Доставлен", callback_data=f"del_{order['order_number']}")],
          [InlineKeyboardButton("◀️ Назад", callback_data="adm_back")]]
    await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))

async def admin_back(upd, ctx):
    q = upd.callback_query; await q.answer()
    kb = admin_kb()
    if kb: await q.edit_message_text("👨‍💼 **АДМИН-ПАНЕЛЬ**\n\nВыберите заказ:", parse_mode='Markdown', reply_markup=kb)
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
    if order: await ctx.bot.send_message(order['user_id'], f"🏠 Заказ {order_num} доставлен!")
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
    await ctx.bot.send_message(MANAGER_ID, f"✅ ЗАКАЗ {order_num} ПОДТВЕРЖДЕН\n👤 {order['user_name']}\n💰 Сумма: {int(total)} ₽\n🚚 Доставка: {delivery_price} → {delivery_final} ₽\n💎 Итого: {final_total} ₽")
    del user_selections[uid][order_num]

async def my_id(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text(f"🆔 Ваш ID: `{upd.effective_user.id}`", parse_mode='Markdown')

async def fix_cmd(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        await upd.message.reply_text("⛔ Доступ запрещён.")
        return
    test_order_num = f"RVN-TEST{random.randint(1000,9999)}"
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''INSERT INTO orders (order_number, user_id, user_name, vin, mileage,
        driving_style, speed, city, distance, delivery_type, delivery_price, delivery_desc,
        needed_parts, status, status_text, created_at)
        VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)''',
        (test_order_num, MANAGER_ID, "Тестовый", "TEST123", "50000",
         "Спокойный", "120", "Москва", 0, "Курьером", 500, "🚚 Курьер",
         "Тестовый заказ", 'pending', '🟡 Ожидает подбора', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
    conn.commit()
    conn.close()
    await upd.message.reply_text(f"➕ Тестовый заказ #{test_order_num} создан!\nИспользуйте /admin для управления")

# ========== ЗАПУСК ==========
def main():
    print(f"📁 Текущая директория: {os.getcwd()}")
    print(f"📁 Путь к БД: {DB_PATH}")
    print(f"📁 Папка data существует: {os.path.exists(DB_DIR)}")
    init_db()
    app