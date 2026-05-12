# ========== ТОЛЬКО АДМИН-ПАНЕЛЬ И УДАЛЕНИЕ ==========
# Вставь ЭТИ функции в твой Bot.py, заменив старые

async def admin_menu(upd, ctx):
    if upd.effective_user.id != MANAGER_ID:
        await upd.message.reply_text("⛔ Доступ запрещён")
        return
    
    orders = get_all_orders()
    keyboard = []
    
    for o in orders[:10]:
        order_num = o[0]
        user_name = o[1][:12] if len(o[1]) > 12 else o[1]
        icon = "🆕" if "Ожидает" in o[2] else "📦"
        keyboard.append([InlineKeyboardButton(f"{icon} {order_num} | {user_name}", callback_data=f"admin_order_{order_num}")])
    
    keyboard.append([InlineKeyboardButton("➕ Тестовый заказ", callback_data="admin_fix")])
    
    await upd.message.reply_text("👨‍💼 АДМИН ПАНЕЛЬ\n\nВыберите заказ:", reply_markup=InlineKeyboardMarkup(keyboard))

async def admin_callback(upd, ctx):
    q = upd.callback_query
    await q.answer()
    data = q.data
    print(f"🔍 Callback: {data}")
    
    if data == "admin_fix":
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        num = f"RVN-{''.join(random.choices(string.ascii_uppercase + string.digits, k=6))}"
        c.execute('''INSERT INTO orders (order_number, user_id, user_name, status, status_text, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (num, MANAGER_ID, 'Тестовый Клиент', 'pending', '🆕 Ожидает подбора', datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
        conn.close()
        await q.edit_message_text(f"✅ Тестовый заказ {num} создан!")
        await admin_menu(upd, ctx)
        return
    
    if data.startswith("admin_order_"):
        order_num = data[12:]
        order = get_order(order_num)
        if not order:
            await q.edit_message_text("❌ Заказ не найден")
            return
        
        text = (f"📋 ЗАКАЗ {order['order_number']}\n\n"
                f"👤 {order['user_name']}\n"
                f"📞 {order.get('phone', '-')}\n"
                f"🏙️ {order.get('city', '-')}\n"
                f"📦 Статус: {order.get('status_text', '-')}")
        
        kb = [
            [InlineKeyboardButton("🗑️ УДАЛИТЬ", callback_data=f"delete_{order_num}")],
            [InlineKeyboardButton("◀️ НАЗАД", callback_data="admin_back")]
        ]
        await q.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data.startswith("delete_"):
        order_num = data[7:]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"confirm_delete_{order_num}")],
            [InlineKeyboardButton("❌ НЕТ, НАЗАД", callback_data=f"admin_order_{order_num}")]
        ])
        await q.edit_message_text(f"⚠️ Удалить заказ {order_num}?", reply_markup=kb)
        return
    
    if data.startswith("confirm_delete_"):
        order_num = data[14:]
        print(f"🔍 Удаляем: {order_num}")
        
        # УДАЛЯЕМ ИЗ БД
        conn = sqlite3.connect(DB_PATH)
        c = conn.cursor()
        c.execute('DELETE FROM orders WHERE order_number = ?', (order_num,))
        conn.commit()
        conn.close()
        
        # ПОКАЗЫВАЕМ ОБНОВЛЁННУЮ ПАНЕЛЬ
        await q.edit_message_text(f"✅ Заказ {order_num} УДАЛЁН!")
        await admin_menu(upd, ctx)
        return
    
    if data == "admin_back":
        await admin_menu(upd, ctx)
        return

# ЗАМЕНИ В main() ЭТИ СТРОКИ:
# app.add_handler(CommandHandler("menu", admin_menu))
# app.add_handler(CallbackQueryHandler(admin_callback))
