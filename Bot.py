# ========== БОНУСЫ ==========
def get_bonus(user_id: int) -> Dict:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT balance, total_earned, total_spent FROM bonuses WHERE user_id = ?', (user_id,))
        r = c.fetchone()
        if r:
            return {'balance': r[0] or 0, 'total_earned': r[1] or 0, 'total_spent': r[2] or 0}
        return {'balance': 0, 'total_earned': 0, 'total_spent': 0}
    except Exception as e:
        logger.error(f"Ошибка получения бонусов: {e}")
        return {'balance': 0, 'total_earned': 0, 'total_spent': 0}
    finally:
        if conn:
            conn.close()

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
                  (user_id, str(order_num) if order_num else "", amount, 'earned', 
                   security.escape_text(desc[:200]), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
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
            conn.close()

def use_bonus(user_id: int, order_num: str, amount: int, desc: str) -> bool:
    if amount <= 0:
        return False
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('BEGIN IMMEDIATE')
        c.execute('SELECT balance FROM bonuses WHERE user_id = ?', (user_id,))
        row = c.fetchone()
        if not row or row[0] < amount:
            conn.rollback()
            return False
        c.execute('UPDATE bonuses SET balance = balance - ?, total_spent = total_spent + ? WHERE user_id = ?',
                  (amount, amount, user_id))
        c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (user_id, str(order_num) if order_num else "", amount, 'spent', 
                   security.escape_text(desc[:200]), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
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
            conn.close()

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
        if not row or row[0] < amount:
            conn.rollback()
            return False
        c.execute('UPDATE bonuses SET balance = balance - ?, total_earned = total_earned - ? WHERE user_id = ?',
                  (amount, amount, user_id))
        c.execute('''INSERT INTO bonus_history (user_id, order_number, amount, type, description, created_at)
                     VALUES (?,?,?,?,?,?)''',
                  (user_id, str(order_num) if order_num else "", amount, 'refund', 
                   security.escape_text(desc[:200]), datetime.now().strftime("%Y-%m-%d %H:%M:%S")))
        conn.commit()
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
            conn.close()

def get_user_total(user_id: int) -> int:
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('SELECT COALESCE(SUM(total_price), 0) FROM orders WHERE user_id = ? AND status != "pending"', (user_id,))
        r = c.fetchone()
        return r[0] if r else 0
    except Exception as e:
        logger.error(f"Ошибка получения суммы: {e}")
        return 0
    finally:
        if conn:
            conn.close()

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

def calculate_bonus_eligible_sum(order: Dict) -> int:
    if not order:
        return 0
    total = 0
    final_order = str(order.get('final_order', ''))
    if final_order and final_order not in ['None', '[]', '{}']:
        parts = security.safe_parse_parts(final_order)
        for p in parts:
            if isinstance(p, dict):
                name = str(p.get('name', '')).lower()
                price = int(p.get('price', 0))
                if not any(brand in name for brand in RESTRICTED_BRANDS) and price > 0:
                    total += price
    return total

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
            conn.close()

def has_ravenol_only(order: Dict) -> Tuple[bool, bool, int, int]:
    ravenol_sum = 0
    other_sum = 0
    has_ravenol = False
    has_other = False
    if not order:
        return has_ravenol, has_other, ravenol_sum, other_sum
    final_order = str(order.get('final_order', ''))
    if final_order and final_order not in [None, 'None', '[]', '{}']:
        parts = security.safe_parse_parts(final_order)
        for part in parts:
            if isinstance(part, dict):
                part_name = str(part.get('name', ''))
                part_price = int(part.get('price', 0))
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
    delivery_price = int(order.get('delivery_price', 0))
    total_parts = int(order.get('total_price', 0))
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

def get_recommendations(user_id: int) -> List[Dict]:
    """Рекомендации на основе истории заказов"""
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        
        c.execute('''
            SELECT final_order FROM orders 
            WHERE user_id = ? AND status NOT IN ('cancelled', 'cancelled_by_user')
            ORDER BY id DESC LIMIT 10
        ''', (user_id,))
        
        orders = c.fetchall()
        conn.close()
        
        if not orders:
            return []
        
        brands = []
        categories = []
        
        for order in orders:
            if order[0] and order[0] not in ['None', '[]', '{}']:
                parts = security.safe_parse_parts(order[0])
                for part in parts:
                    if isinstance(part, dict):
                        name = part.get('name', '')
                        for brand in ['Hyundai-KIA', 'ILJIN', 'CTR', 'ZF', 'SACHS', 'BOSCH', 'NGK', 'DENSO']:
                            if brand in name:
                                brands.append(brand)
                                break
                        for cat in ['стойка', 'ступица', 'наконечник', 'тяга', 'сайлентблок', 'амортизатор']:
                            if cat in name.lower():
                                categories.append(cat)
                                break
        
        recommendations = []
        if brands:
            top_brand = max(set(brands), key=brands.count)
            recommendations.append({
                'type': 'brand',
                'text': f"🏭 Популярный бренд: {top_brand}",
                'value': top_brand
            })
        if categories:
            top_cat = max(set(categories), key=categories.count)
            recommendations.append({
                'type': 'category',
                'text': f"🔧 Часто заказывают: {top_cat}",
                'value': top_cat
            })
        
        return recommendations
    except Exception as e:
        logger.error(f"Ошибка получения рекомендаций: {e}")
        return []
    finally:
        if conn:
            conn.close()

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
            data = str(query.data)
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

# ========== ОСНОВНЫЕ КОМАНДЫ ==========
async def start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return
        
        if not await anti_spam(upd):
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
                    await ctx.bot.send_message(ref_id, f"👋 {upd.effective_user.full_name} перешёл по вашей реферальной ссылке!")
                except Exception as e:
                    logger.error(f"Ошибка реферала: {e}")
                finally:
                    if conn:
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
    except Exception as e:
        logger.error(f"Ошибка в start: {e}")

# ========== НОВЫЙ ЗАКАЗ ==========
async def new_order(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return ConversationHandler.END
        
        if not await anti_spam(upd):
            return ConversationHandler.END
        
        cars = get_cars(upd.effective_user.id)
        if cars and len(cars) > 0:
            keyboard = [[InlineKeyboardButton("🆕 Ввести VIN вручную", callback_data="order_manual")]]
            for car in cars:
                if car and len(car) > 0:
                    vin = str(car[0])
                    description = str(car[1]) if len(car) > 1 else ""
                    comment = str(car[2]) if len(car) > 2 else ""
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

async def get_vin(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.VIN
        
        if not await anti_spam(upd):
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

async def get_mileage(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.MILEAGE
        
        if not await anti_spam(upd):
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

async def get_address(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.ADDRESS
        
        if not await anti_spam(upd):
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

async def get_phone(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.PHONE
        
        if not await anti_spam(upd):
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

async def get_parts(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return OrderStates.PARTS
        
        if not await anti_spam(upd):
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
        
        if not await anti_spam(upd):
            return ConversationHandler.END
        
        if upd.message.text == "✅ Готово":
            data = ctx.user_data.copy() if ctx.user_data else {}
            if not data.get('vin'):
                await upd.message.reply_text("❌ Ошибка: VIN не указан. Начните заказ заново.")
                return ConversationHandler.END
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
        
        if not await anti_spam(upd):
            return
        
        await upd.effective_chat.send_action(action="typing")
        
        page = 1
        if ctx.args and len(ctx.args) > 0:
            try:
                page = int(ctx.args[0])
                if page < 1:
                    page = 1
            except:
                pass
        
        result = get_user_orders_paginated(upd.effective_user.id, page)
        orders = result['orders']
        
        if not orders or len(orders) == 0:
            await upd.message.reply_text("📭 У вас пока нет заказов", reply_markup=main_menu)
            return
        
        text = f"📦 ВАШИ ЗАКАЗЫ (стр. {page}/{result['total_pages']}):\n\n"
        kb = []
        
        for order in orders:
            if order and len(order) > 0:
                order_num = str(order[0])
                status_text = str(order[1]) if len(order) > 1 else ""
                created = str(order[2]) if len(order) > 2 else ""
                tp = int(order[3]) if len(order) > 3 else 0
                dp = int(order[4]) if len(order) > 4 else 0
                total = tp + dp
                icon = get_status_icon(status_text)
                text += f"{icon} {order_num} — {created[:10]} — {total} руб.\n"
                kb.append([InlineKeyboardButton(f"🔍 {order_num}", callback_data=f"view_{order_num}")])
        
        nav_buttons = []
        if page > 1:
            nav_buttons.append(InlineKeyboardButton("⬅️ Назад", callback_data=f"orders_page_{page-1}"))
        if page < result['total_pages']:
            nav_buttons.append(InlineKeyboardButton("➡️ Вперед", callback_data=f"orders_page_{page+1}"))
        if nav_buttons:
            kb.append(nav_buttons)
        
        kb.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")])
        
        await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Ошибка в my_orders: {e}")

async def orders_page_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        page = int(query.data.split('_')[2])
        ctx.args = [str(page)]
        await my_orders(upd, ctx)
    except Exception as e:
        logger.error(f"Ошибка в orders_page_callback: {e}")

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
        status = order.get('status', '')
        
        status_bar = get_status_progress(status)
        
        text = (f"📋 ЗАКАЗ {order_num}\n\n"
                f"{status_bar}\n\n"
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
        
        final_order = order.get('final_order', '')
        if final_order and final_order not in ['None', '[]', '{}']:
            text += "\n\n📦 ВАШИ ТОВАРЫ:\n"
            parts = security.safe_parse_parts(final_order)
            if parts:
                for i, part in enumerate(parts):
                    if isinstance(part, dict):
                        part_name = part.get('name', 'неизвестно')
                        part_price = part.get('price', 0)
                        ravenol_mark = " (Ravenol)" if is_ravenol_product(part_name) else ""
                        text += f"{i+1}. {part_name}\n   → {part_price} руб.{ravenol_mark}\n"
        
        kb = get_client_quick_actions(order)
        
        if status in ['delivered', 'issued']:
            recommendations = get_recommendations(order.get('user_id'))
            if recommendations:
                rec_text = "\n\n💡 РЕКОМЕНДАЦИИ:\n"
                for rec in recommendations:
                    rec_text += f"• {rec['text']}\n"
                await query.edit_message_text(text + rec_text, reply_markup=kb)
                return
        
        await query.edit_message_text(text, reply_markup=kb)
    except Exception as e:
        logger.error(f"Ошибка в view_order: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")

def get_client_quick_actions(order: Dict) -> InlineKeyboardMarkup:
    """Быстрые кнопки для клиента"""
    kb = []
    status = order.get('status', '')
    order_num = order.get('order_number', '')
    
    if status == 'waiting_payment':
        kb.append([InlineKeyboardButton("💳 Оплатить", callback_data=f"pay_document_{order_num}")])
        kb.append([InlineKeyboardButton("❓ Как оплатить?", callback_data="help_payment")])
    
    if status in ['shipped', 'delivered']:
        if order.get('tracking_number'):
            kb.append([InlineKeyboardButton("📮 Трек-номер", callback_data=f"tracking_{order_num}")])
    
    if status in ['ready', 'shipped', 'delivered']:
        kb.append([InlineKeyboardButton("⭐ Оставить отзыв", callback_data=f"feedback_{order_num}")])
    
    kb.append([InlineKeyboardButton("📞 Связаться с менеджером", callback_data="contact_manager")])
    kb.append([InlineKeyboardButton("◀️ Назад к списку", callback_data="back_orders_list")])
    
    return InlineKeyboardMarkup(kb)

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
        
        if not await anti_spam(upd):
            return ConversationHandler.END
        
        user_id = upd.effective_user.id
        order_num = ctx.user_data.get('payment_order') if ctx.user_data else None
        
        if not order_num:
            await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
            return ConversationHandler.END
        
        order = get_order(order_num)
        if not order:
            await upd.message.reply_text("❌ Заказ не найден")
            return ConversationHandler.END
        
        if order.get('status') != 'waiting_payment':
            await upd.message.reply_text(
                f"❌ Оплату можно отправить только для заказа в статусе 'Ожидает оплаты'\n"
                f"📦 Текущий статус: {order.get('status_text', 'неизвестен')}"
            )
            return ConversationHandler.END
        
        caption = ""
        file_id = None
        file_type = "unknown"
        
        if upd.message.photo:
            file_id = upd.message.photo[-1].file_id
            file_type = "photo"
            caption = upd.message.caption or ""
            logger.info(f"[PAYMENT] Получено фото для заказа {order_num}")
        elif upd.message.document:
            file_id = upd.message.document.file_id
            file_type = "document"
            caption = upd.message.caption or ""
            logger.info(f"[PAYMENT] Получен документ для заказа {order_num}")
        else:
            await upd.message.reply_text(
                "❌ Пожалуйста, отправьте ФОТО или ДОКУМЕНТ (PDF, JPG, PNG)\n\n"
                "📸 Фото чека\n"
                "📎 PDF-документ\n"
                "🖼️ Скриншот платежа\n\n"
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
                f"💰 Сумма: {total_sum:,} руб.\n"
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
            f"💰 Сумма: {total_sum:,} руб.\n"
            f"📎 Файл: {'Фото' if file_type == 'photo' else 'Документ'}\n\n"
            f"⏳ Менеджер проверит оплату и изменит статус заказа.\n"
            f"Вы получите уведомление, когда заказ будет подтверждён.\n\n"
            f"Вернуться в главное меню: /start"
        )
        
        if ctx.user_data and 'payment_order' in ctx.user_data:
            del ctx.user_data['payment_order']
        
        return ConversationHandler.END
        
    except Exception as e:
        logger.error(f"Ошибка в handle_payment_document: {e}", exc_info=True)
        await upd.message.reply_text(f"❌ Произошла ошибка: {str(e)[:100]}")
        return ConversationHandler.END

# ========== ОБРАБОТЧИК МЕНЕДЖЕРА ==========
@require_manager
async def manager_message_handler(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    """
    Обработка сообщений от менеджера
    - Текст с ценами → ПАРСИТ как подбор и отправляет клиенту
    - Трек-номер → добавляет к заказу
    - Ответ клиенту → пересылает
    """
    try:
        if not upd or not upd.message:
            return
        
        msg_text = upd.message.text or ""
        reply_to = upd.message.reply_to_message
        
        has_prices = re.search(r'\d+\s*(?:руб|₽|р\.)', msg_text, re.I)
        
        if has_prices and len(msg_text) > 20:
            products = parse_full_client_text(msg_text)
            
            if not products:
                products = parse_manager_text(msg_text)
            
            if products:
                order_num = find_order_number(
                    msg_text, 
                    reply_to.text if reply_to else None
                )
                
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
                
                total_price = sum(p.get('price', 0) for p in products)
                delivery_price = order.get('delivery_price', 0)
                total_sum = total_price + delivery_price
                
                update_order(
                    order_num,
                    selected_products=msg_text[:MAX_MESSAGE_LENGTH],
                    final_order=str(products),
                    total_price=total_price,
                    status='waiting_selection'
                )
                
                save_selection_history(order_num, products, MANAGER_ID)
                
                client_text = f"🛒 **ПОДБОР ЗАПЧАСТЕЙ ДЛЯ ЗАКАЗА #{order_num}**\n\n"
                client_text += f"Менеджер подобрал для вас следующие позиции:\n\n"
                
                for i, p in enumerate(products, 1):
                    client_text += f"**{i}. {p.get('name', '')}**\n"
                    client_text += f"   💰 {p.get('price', 0):,} ₽"
                    if p.get('quantity', 1) > 1:
                        client_text += f" × {p.get('quantity')} шт"
                    if p.get('manufacturer'):
                        client_text += f"\n   🏭 Производство: {p['manufacturer']}"
                    if p.get('country'):
                        client_text += f"\n   🌍 Страна: {p['country']}"
                    client_text += "\n\n"
                
                client_text += "─" * 30 + "\n"
                client_text += f"📦 **ИТОГО:**\n"
                client_text += f"   • Всего позиций: {len(products)}\n"
                client_text += f"   • Сумма товаров: **{total_price:,} ₽**\n"
                client_text += f"   • 🚚 Доставка: {delivery_price:,} ₽\n"
                client_text += f"   • 💳 **ИТОГО К ОПЛАТЕ: {total_sum:,} ₽**\n\n"
                client_text += f"✅ Выберите нужные запчасти (можно отметить несколько):"
                
                kb = []
                for i, p in enumerate(products[:50]):
                    display_name = p.get('name', '')[:25] + ".." if len(p.get('name', '')) > 25 else p.get('name', '')
                    kb.append([InlineKeyboardButton(
                        f"⬜ {display_name} — {p.get('price', 0)} руб.",
                        callback_data=f"sel_{order_num}_{i}"
                    )])
                kb.append([InlineKeyboardButton(
                    "✅ ПОДТВЕРДИТЬ ВЫБОР",
                    callback_data=f"fin_{order_num}"
                )])
                
                await ctx.bot.send_message(
                    order.get('user_id'),
                    text=client_text,
                    reply_markup=InlineKeyboardMarkup(kb),
                    parse_mode='Markdown'
                )
                
                products_preview = "\n".join([
                    f"   {i+1}. {p.get('name', '')[:30]} = {p.get('price', 0)} руб."
                    for i, p in enumerate(products[:5])
                ])
                if len(products) > 5:
                    products_preview += f"\n   ... и ещё {len(products) - 5} товаров"
                
                await upd.message.reply_text(
                    f"✅ **ПОДБОР ОТПРАВЛЕН КЛИЕНТУ!**\n\n"
                    f"📦 **Заказ:** {order_num}\n"
                    f"👤 **Клиент:** {order.get('user_name', '')}\n"
                    f"📦 **Товаров:** {len(products)}\n"
                    f"💰 **Сумма:** {total_sum:,} руб.\n\n"
                    f"📋 **Товары:**\n{products_preview}"
                )
                
                audit_log(MANAGER_ID, 'send_selection', 
                         f"Заказ {order_num}: отправлен подбор из {len(products)} позиций", "success")
                return
        
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
        
        await upd.message.reply_text(
            "📝 **ЧТО Я МОГУ СДЕЛАТЬ:**\n\n"
            "1️⃣ **Отправить подбор запчастей:**\n"
            "   Просто отправьте текст с ценами\n"
            "   Пример:\n"
            "   1. Название = 1000 руб\n"
            "   2. Название = 2000 руб\n\n"
            "2️⃣ **Ответить клиенту:**\n"
            "   Ответьте на сообщение клиента\n\n"
            "3️⃣ **Добавить трек-номер:**\n"
            "   Сначала нажмите '🚚 Отправлен' в админ-панели\n"
            "   Затем ответьте на запрос бота с трек-номером\n\n"
            "4️⃣ **Команды:**\n"
            "   /search - умный поиск заказов\n"
            "   /template - шаблоны сообщений\n"
            "   /dashboard - дашборд аналитики\n"
            "   /export - экспорт отчетов"
        )
        
    except Exception as e:
        logger.error(f"Ошибка в manager_message_handler: {e}", exc_info=True)
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:200]}")

def save_selection_history(order_num: str, products: List[Dict], manager_id: int):
    conn = None
    try:
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        c.execute('''
            CREATE TABLE IF NOT EXISTS selection_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                order_number TEXT,
                products TEXT,
                manager_id INTEGER,
                created_at TEXT
            )
        ''')
        c.execute('''
            INSERT INTO selection_history (order_number, products, manager_id, created_at)
            VALUES (?, ?, ?, ?)
        ''', (order_num, json.dumps(products, ensure_ascii=False), manager_id, datetime.now().isoformat()))
        conn.commit()
    except Exception as e:
        logger.error(f"Ошибка сохранения истории подборов: {e}")
    finally:
        if conn:
            conn.close()

# ========== ВЫБОР ТОВАРОВ КЛИЕНТОМ ==========
async def select_cb(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        parts = query.data.split('_')
        if len(parts) < 3:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[1]
        idx = safe_int(parts[2])
        uid = query.from_user.id
        
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        if order.get('user_id') != uid:
            await query.answer("❌ Это не ваш заказ!", show_alert=True)
            return
        
        products = security.safe_parse_parts(order.get('final_order', ''))
        if not products:
            await query.edit_message_text("❌ Товары не найдены")
            return
        
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
        for i, p in enumerate(products[:50]):
            cb = "✅" if i in s else "⬜"
            display_name = p['name'][:25] + ".." if len(p['name']) > 25 else p['name']
            kb.append([InlineKeyboardButton(
                f"{cb} {display_name} — {p['price']} руб.",
                callback_data=f"sel_{order_num}_{i}"
            )])
        kb.append([InlineKeyboardButton(
            "✅ ПОДТВЕРДИТЬ ВЫБОР",
            callback_data=f"fin_{order_num}"
        )])
        
        await query.edit_message_reply_markup(reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Ошибка в select_cb: {e}")

async def finalize_cb(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        parts = query.data.split('_')
        if len(parts) < 2:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[1]
        uid = query.from_user.id
        
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        if order.get('user_id') != uid:
            await query.answer("❌ Это не ваш заказ!", show_alert=True)
            return
        
        if uid not in user_selections or order_num not in user_selections[uid]:
            await query.edit_message_text("❌ Ничего не выбрано")
            return
        
        products = security.safe_parse_parts(order.get('final_order', ''))
        selected = []
        total = 0
        
        for idx in user_selections[uid][order_num]:
            if idx < len(products):
                selected.append(products[idx])
                total += products[idx]['price']
        
        if not selected:
            await query.edit_message_text("❌ Вы не выбрали ни одной запчасти")
            return
        
        delivery_price = order.get('delivery_price', 0)
        final_total = total + delivery_price
        
        update_order(order_num, total_price=total, status='waiting_payment')
        
        result = f"✅ ЗАКАЗ #{order_num} ПОДТВЕРЖДЁН!\n\n"
        for p in selected[:20]:
            wrapped_name = wrap_text(p['name'], 25)
            ravenol_mark = " (Ravenol)" if is_ravenol_product(p['name']) else ""
            result += f"• {wrapped_name}\n   → {p['price']} руб.{ravenol_mark}\n"
        if len(selected) > 20:
            result += f"\n... и ещё {len(selected) - 20} позиций\n"
        result += f"\n🚚 Доставка: {delivery_price} руб.\n"
        result += f"\n💰 ИТОГО К ОПЛАТЕ: {final_total} руб."
        result += "\n\n📞 Менеджер свяжется с вами для уточнения оплаты."
        
        await query.edit_message_text(result)
        
        await ctx.bot.send_message(
            MANAGER_ID,
            f"✅ ЗАКАЗ {order_num} ПОДТВЕРЖДЁН КЛИЕНТОМ!\n\n"
            f"👤 Клиент: {order.get('user_name', '')}\n"
            f"💰 Сумма: {final_total} руб."
        )
        
        if uid in user_selections:
            del user_selections[uid][order_num]
    except Exception as e:
        logger.error(f"Ошибка в finalize_cb: {e}")

# ========== УМНЫЙ ПОИСК ==========
@require_manager
async def smart_search(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = ' '.join(ctx.args) if ctx.args else ""
        if not query:
            await upd.message.reply_text(
                "🔍 **УМНЫЙ ПОИСК**\n\n"
                "Использование: /search ТЕКСТ\n\n"
                "Ищет по:\n"
                "• Номеру заказа (RVN-xxxxxx)\n"
                "• VIN (17 символов)\n"
                "• Телефону\n"
                "• Имени клиента\n"
                "• Артикулу запчасти\n\n"
                "Пример: /search RVN-ABCD12"
            )
            return
        
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        
        c.execute('''
            SELECT order_number, user_name, phone, vin, status_text, total_price, delivery_price, created_at
            FROM orders
            WHERE order_number LIKE ? 
               OR user_name LIKE ?
               OR phone LIKE ?
               OR vin LIKE ?
               OR final_order LIKE ?
            ORDER BY id DESC
            LIMIT 20
        ''', (f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%', f'%{query}%'))
        
        results = c.fetchall()
        conn.close()
        
        if not results:
            await upd.message.reply_text(f"🔍 По запросу «{query}» ничего не найдено")
            return
        
        text = f"🔍 **РЕЗУЛЬТАТЫ ПОИСКА** «{query}»\n\n"
        for r in results[:10]:
            total = (r[5] or 0) + (r[6] or 0)
            text += f"📦 **{r[0]}** | {r[1]}\n"
            text += f"   📞 {r[2]} | 🚗 {r[3]}\n"
            text += f"   📦 {r[4]} | 💰 {total:,} руб.\n"
            text += f"   📅 {r[7][:10] if r[7] else ''}\n\n"
        
        await upd.message.reply_text(text[:4000], parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка поиска: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

# ========== ШАБЛОНЫ ==========
@require_manager
async def quick_template(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    args = ctx.args
    if len(args) < 2:
        await upd.message.reply_text(
            "📝 **ШАБЛОНЫ**\n\n"
            "Доступные шаблоны:\n"
            "• `status_update` - обновление статуса\n"
            "• `delivery_info` - информация о доставке\n"
            "• `price_change` - изменение цены\n"
            "• `stock_info` - наличие на складе\n"
            "• `payment_confirm` - подтверждение оплаты\n\n"
            "Использование:\n"
            "/template ШАБЛОН ЗАКАЗ ТЕКСТ\n\n"
            "Пример:\n"
            "/template status_update RVN-ABCD12 \"в обработке\""
        )
        return
    
    template_name = args[0]
    order_num = args[1] if len(args) > 1 else ""
    text = ' '.join(args[2:]) if len(args) > 2 else ""
    
    if template_name not in TEMPLATES:
        await upd.message.reply_text(f"❌ Шаблон {template_name} не найден")
        return
    
    order = get_order(order_num)
    if not order:
        await upd.message.reply_text(f"❌ Заказ {order_num} не найден")
        return
    
    message = TEMPLATES[template_name].format(
        order=order_num,
        status=text or order.get('status_text', ''),
        tracking=text or order.get('tracking_number', ''),
        product=text or 'товар',
        old='',
        new='',
        stock=text or '0',
        amount=text or str(order.get('total_price', 0))
    )
    
    await ctx.bot.send_message(order.get('user_id'), text=message)
    await upd.message.reply_text(f"✅ Шаблон отправлен клиенту по заказу {order_num}")
    
    audit_log(MANAGER_ID, 'quick_template', f"Заказ {order_num}: отправлен шаблон {template_name}", "success")

# ========== ДАШБОРД ==========
@require_manager
async def dashboard(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    conn = sqlite3.connect(DB_PATH, timeout=30)
    c = conn.cursor()
    
    c.execute('SELECT COUNT(*) FROM orders')
    total_orders = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM orders WHERE status = "pending"')
    pending = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM orders WHERE status = "waiting_payment"')
    waiting_payment = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM orders WHERE status = "paid"')
    paid = c.fetchone()[0]
    
    c.execute('SELECT COUNT(*) FROM orders WHERE status IN ("shipped", "delivered")')
    shipped = c.fetchone()[0]
    
    c.execute('''
        SELECT COALESCE(SUM(total_price + delivery_price), 0) 
        FROM orders 
        WHERE date(created_at) = date('now')
    ''')
    today_sum = c.fetchone()[0]
    
    c.execute('''
        SELECT COALESCE(SUM(total_price + delivery_price), 0) 
        FROM orders 
        WHERE date(created_at) >= date('now', '-30 days')
    ''')
    month_sum = c.fetchone()[0]
    
    c.execute('''
        SELECT user_name, COUNT(*) as orders, SUM(total_price + delivery_price) as total
        FROM orders 
        WHERE status NOT IN ('cancelled', 'cancelled_by_user')
        GROUP BY user_name 
        ORDER BY total DESC 
        LIMIT 5
    ''')
    top_clients = c.fetchall()
    
    conn.close()
    
    text = f"📊 **ДАШБОРД**\n\n"
    text += f"📦 Всего заказов: {total_orders}\n"
    text += f"🆕 Ожидают подбора: {pending}\n"
    text += f"💰 Ожидают оплаты: {waiting_payment}\n"
    text += f"✅ Оплачено: {paid}\n"
    text += f"🚚 Отправлено/доставлено: {shipped}\n\n"
    text += f"📈 Сегодня: {today_sum:,} руб.\n"
    text += f"📈 Месяц: {month_sum:,} руб.\n\n"
    text += f"🏆 **ТОП КЛИЕНТЫ:**\n"
    
    for client in top_clients:
        text += f"• {client[0]} - {client[1]} заказов ({client[2]:,} руб.)\n"
    
    await upd.message.reply_text(text, parse_mode='Markdown')

# ========== ЭКСПОРТ ОТЧЕТОВ ==========
@require_manager
async def export_report(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await upd.message.reply_text("⏳ Генерация отчета...")
    
    conn = sqlite3.connect(DB_PATH, timeout=30)
    c = conn.cursor()
    
    c.execute('''
        SELECT order_number, user_name, phone, vin, total_price, delivery_price, 
               status_text, created_at, tracking_number
        FROM orders 
        ORDER BY id DESC
    ''')
    
    orders = c.fetchall()
    conn.close()
    
    output = io.StringIO()
    writer = csv.writer(output, delimiter=';')
    writer.writerow(['Номер', 'Клиент', 'Телефон', 'VIN', 'Сумма', 'Доставка', 'Статус', 'Дата', 'Трек-номер'])
    
    for o in orders:
        total = (o[4] or 0) + (o[5] or 0)
        writer.writerow([
            o[0], o[1], o[2], o[3] or '', total, o[5] or 0, o[6], o[7][:10] if o[7] else '', o[8] or ''
        ])
    
    await upd.message.reply_document(
        document=output.getvalue().encode('utf-8-sig'),
        filename=f"orders_report_{datetime.now().strftime('%Y%m%d')}.csv"
    )
    await upd.message.reply_text("✅ Отчет сгенерирован!")

# ========== ОТЗЫВЫ ==========
async def feedback_callback(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        
        parts = query.data.split('_')
        if len(parts) < 2:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        
        order_num = parts[1]
        
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("⭐", callback_data=f"rating_{order_num}_1"),
             InlineKeyboardButton("⭐⭐", callback_data=f"rating_{order_num}_2"),
             InlineKeyboardButton("⭐⭐⭐", callback_data=f"rating_{order_num}_3"),
             InlineKeyboardButton("⭐⭐⭐⭐", callback_data=f"rating_{order_num}_4"),
             InlineKeyboardButton("⭐⭐⭐⭐⭐", callback_data=f"rating_{order_num}_5")]
        ])
        
        await query.edit_message_text(
            f"⭐ Оцените качество обслуживания по заказу {order_num}",
            reply_markup=kb
        )
    except Exception as e:
        logger.error(f"Ошибка в feedback_callback: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")

async def save_rating(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        
        parts = query.data.split('_')
        if len(parts) < 3:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        
        order_num = parts[1]
        rating = int(parts[2])
        
        conn = sqlite3.connect(DB_PATH, timeout=30)
        c = conn.cursor()
        
        c.execute('SELECT id FROM feedback WHERE order_number = ? AND user_id = ?', 
                  (order_num, query.from_user.id))
        existing = c.fetchone()
        
        if existing:
            c.execute('UPDATE feedback SET rating = ?, created_at = ? WHERE id = ?',
                      (rating, datetime.now().isoformat(), existing[0]))
        else:
            c.execute('''
                INSERT INTO feedback (order_number, user_id, rating, created_at)
                VALUES (?, ?, ?, ?)
            ''', (order_num, query.from_user.id, rating, datetime.now().isoformat()))
        
        conn.commit()
        conn.close()
        
        messages = {
            1: "😞 Спасибо за честный отзыв! Мы постараемся исправиться.",
            2: "😕 Спасибо за отзыв! Расскажите, что нам улучшить?",
            3: "😐 Спасибо за отзыв! Мы будем работать над качеством.",
            4: "😊 Спасибо за хорошую оценку!",
            5: "🌟 Спасибо за отличную оценку! Мы рады, что вам понравилось!"
        }
        
        await query.edit_message_text(f"{messages.get(rating, 'Спасибо за отзыв!')}")
    except Exception as e:
        logger.error(f"Ошибка в save_rating: {e}")
        await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")

async def contact_manager(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "📞 **СВЯЗЬ С МЕНЕДЖЕРОМ**\n\n"
            "Вы можете связаться с менеджером следующими способами:\n\n"
            "1️⃣ **Написать в чат**\n"
            "   Просто ответьте на любое сообщение бота\n\n"
            "2️⃣ **Позвонить**\n"
            "   Контактный телефон: +7 XXX XXX-XX-XX\n\n"
            "3️⃣ **Оставить заявку**\n"
            "   Напишите свой вопрос, и мы свяжемся с вами\n\n"
            "⏳ Обычно мы отвечаем в течение 15 минут"
        )
    except Exception as e:
        logger.error(f"Ошибка в contact_manager: {e}")

async def help_payment(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        
        await query.edit_message_text(
            "💳 **КАК ОПЛАТИТЬ?**\n\n"
            "1️⃣ **Перевод на карту**\n"
            "   • Номер карты: XXXX XXXX XXXX XXXX\n"
            "   • Получатель: Имя Фамилия\n\n"
            "2️⃣ **Оплата на сайте**\n"
            "   • Перейдите по ссылке: example.com/pay\n"
            "   • Введите номер заказа и сумму\n\n"
            "3️⃣ **Наличными при получении**\n"
            "   • Оплата курьеру\n"
            "   • В пункте выдачи\n\n"
            "📌 **После оплаты:**\n"
            "• Отправьте фото чека в бот\n"
            "• Менеджер подтвердит оплату\n"
            "• Статус заказа изменится на «Оплачен»"
        )
    except Exception as e:
        logger.error(f"Ошибка в help_payment: {e}")

async def tracking_info(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        
        parts = query.data.split('_')
        if len(parts) < 2:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        
        order_num = parts[1]
        order = get_order(order_num)
        
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        
        tracking = order.get('tracking_number', '')
        if not tracking:
            await query.edit_message_text(
                f"📮 Трек-номер для заказа {order_num} пока не добавлен.\n\n"
                f"Менеджер добавит его после отправки заказа."
            )
            return
        
        await query.edit_message_text(
            f"📮 **ТРЕК-НОМЕР**\n\n"
            f"📦 Заказ: {order_num}\n"
            f"🔢 Трек-номер: `{tracking}`\n\n"
            f"📍 Отследить можно на сайте:\n"
            f"https://www.pochta.ru/tracking#{tracking}\n\n"
            f"📌 Статус: {order.get('status_text', 'Неизвестен')}"
        )
    except Exception as e:
        logger.error(f"Ошибка в tracking_info: {e}")

# ========== БОНУСЫ КЛИЕНТА ==========
async def bonus_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return
        
        if not await anti_spam(upd):
            return
        
        uid = upd.effective_user.id
        bonus_data = get_bonus(uid)
        text = (f"🎁 **БОНУСНАЯ ПРОГРАММА**\n\n"
                f"💰 Текущий баланс: {bonus_data['balance']} бонусов\n"
                f"📈 Всего начислено: {bonus_data['total_earned']} бонусов\n"
                f"📉 Всего потрачено: {bonus_data['total_spent']} бонусов\n"
                f"⭐ Процент начисления: {get_bonus_percent(uid)}%\n\n"
                f"📋 **ПРАВИЛА:**\n"
                f"• Списание до {MAX_BONUS_SPEND_PERCENT}% от суммы запчастей\n"
                f"• ❌ Не действует на RAVENOL и доставку\n"
                f"• 💳 Минимальная оплата деньгами: {MIN_CASH_PAYMENT} руб.")
        await upd.message.reply_text(text, parse_mode='Markdown')
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
        text = "📜 **ИСТОРИЯ БОНУСОВ**\n\n"
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
            f"🎁 **СПИСАНИЕ БОНУСОВ**\n\n"
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
            f"⚠️ **ПОДТВЕРЖДЕНИЕ СПИСАНИЯ**\n\n"
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
                f"✅ **БОНУСЫ УСПЕШНО СПИСАНЫ!**\n\n"
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
                f"✏️ **ВВЕДИТЕ СУММУ СПИСАНИЯ**\n\n"
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
            f"⚠️ **ПОДТВЕРЖДЕНИЕ СПИСАНИЯ**\n\n"
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

# ========== УДАЛЕНИЕ ТОВАРОВ (КЛИЕНТ) ==========
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
        parts = security.safe_parse_parts(final_order)
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
        text = f"🗑️ **УДАЛЕНИЕ ТОВАРОВ** ИЗ ЗАКАЗА {order_num}\n\n"
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
        text = f"🗑️ **УДАЛЕНИЕ ТОВАРОВ** ИЗ ЗАКАЗА {order_num}\n\n"
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
            f"🗑️ **УДАЛЕНИЕ ТОВАРОВ** ИЗ ЗАКАЗА {order_num}\n\n"
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

# ========== ОТМЕНА ЗАКАЗА (КЛИЕНТ) ==========
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
            f"⚠️ **ВНИМАНИЕ!**\n\n"
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
                conn.close()
        
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

# ========== РЕФЕРАЛЫ, ДОСТАВКА, ПОМОЩЬ ==========
async def referral_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return
        
        if not await anti_spam(upd):
            return
        
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
                conn.close()
        
        await upd.message.reply_text(
            f"🔗 **РЕФЕРАЛЬНАЯ ПРОГРАММА**\n\n"
            f"Ваша ссылка:\n{link}\n\n"
            f"👥 Приглашено друзей: {referrals_count}\n"
            f"🎁 Друг получает 500 бонусов!\n"
            f"💰 Ваш баланс: {get_bonus(upd.effective_user.id)['balance']} бонусов"
        )
    except Exception as e:
        logger.error(f"Ошибка в referral_cmd: {e}")

async def delivery_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return
        
        if not await anti_spam(upd):
            return
        
        text = (f"🚚 **РАСЧЁТ ДОСТАВКИ**\n\n"
                f"Базовая стоимость: {DELIVERY_BASE} руб.\n"
                f"• 1-50 км: {DELIVERY_BASE} + км × {DELIVERY_RATE_UP_TO_50}\n"
                f"• 51-100 км: {DELIVERY_BASE} + км × {DELIVERY_RATE_UP_TO_100}\n"
                f"• 101+ км: {DELIVERY_BASE} + км × {DELIVERY_RATE_OVER_100}\n\n"
                f"📌 Самовывоз: бесплатно\n"
                f"📍 Метро Давыдково, Южная, Строгино")
        await upd.message.reply_text(text)
    except Exception as e:
        logger.error(f"Ошибка в delivery_cmd: {e}")

async def help_cmd(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return
        
        if not await anti_spam(upd):
            return
        
        text = ("📖 **ПОМОЩЬ**\n\n"
                "**Основные команды:**\n"
                "/start - Главное меню\n"
                "/my_orders - Мои заказы\n"
                "/bonus - Бонусы\n"
                "/referral - Рефералы\n"
                "/delivery - Доставка\n"
                "/help - Справка\n\n"
                "**Команды для менеджера:**\n"
                "/search - Умный поиск\n"
                "/template - Шаблоны\n"
                "/dashboard - Дашборд\n"
                "/export - Экспорт отчетов\n"
                "/menu - Админ-панель\n"
                "/allorders - Все заказы\n"
                "/fix_orders - Проверка заказов\n"
                "/delorder - Удалить заказ\n"
                "/batch_del - Массовое удаление\n"
                "/payment_docs - Документы оплаты\n"
                "/select - Отправить подбор\n"
                "/msg - Написать клиенту\n\n"
                "По вопросам обращайтесь к менеджеру")
        await upd.message.reply_text(text, parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка в help_cmd: {e}")

# ========== АДМИН КОМАНДЫ ==========
@require_manager
async def menu_command(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    await admin_menu(upd, ctx)

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
        
        text = "📊 **ВСЕ ЗАКАЗЫ В БАЗЕ ДАННЫХ**\n\n"
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
                await upd.message.reply_text(part, parse_mode='Markdown')
        else:
            await upd.message.reply_text(text, parse_mode='Markdown')
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
        
        text = "📊 **АНАЛИЗ ЗАКАЗОВ В БАЗЕ ДАННЫХ**\n\n"
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
        
        text += "\n📋 **ПОСЛЕДНИЕ 20 ЗАКАЗОВ:**\n\n"
        for o in db_orders[:20]:
            order_num = safe_str(o[0])
            status_text = safe_str(o[2]) if len(o) > 2 else STATUS_TEXT_MAP.get(o[1] if len(o) > 1 else '', 'Неизвестно')
            text += f"• {order_num} - {status_text}\n"
        
        kb = [
            [InlineKeyboardButton("🗑️ Удалить все отменённые", callback_data="admin_clear_all_cancelled")],
            [InlineKeyboardButton("🔄 Синхронизировать список", callback_data="admin_refresh")],
            [InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]
        ]
        await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
    except Exception as e:
        logger.error(f"Ошибка в fix_orphan_orders: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

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
                "⚠️ **ВНИМАНИЕ:** заказ будет удалён без проверки статуса!"
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
                "⚠️ **ВНИМАНИЕ:** заказы будут удалены без проверки статуса!"
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
        text = "🗑️ **РЕЗУЛЬТАТ УДАЛЕНИЯ**\n\n"
        if deleted:
            text += f"✅ Удалено: {', '.join(deleted)}\n\n"
        if not_found:
            text += f"❌ Не найдено/ошибка: {', '.join(not_found)}\n\n"
        await upd.message.reply_text(text)
    except Exception as e:
        logger.error(f"Ошибка в batch_delete_orders: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

@require_manager
async def show_payment_docs(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        args = ctx.args
        if not args or len(args) == 0:
            await upd.message.reply_text("📎 Пример: /payment_docs RVN-ABCD12")
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

@require_manager
async def select_command(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        args = ctx.args
        if not args or len(args) < 2:
            await upd.message.reply_text(
                "📦 **ОТПРАВКА ПОДБОРА ЗАПЧАСТЕЙ**\n\n"
                "Использование:\n"
                "/select НОМЕР_ЗАКАЗА ТЕКСТ_С_ЗАПЧАСТЯМИ\n\n"
                "Пример:\n"
                "/select RVN-7JTSO3 Масло RAVENOL = 11758 руб, Фильтр = 1066 руб\n\n"
                "Или отправьте /select НОМЕР_ЗАКАЗА\n"
                "а затем отдельным сообщением список запчастей"
            )
            return
        order_num = clean_order_number(args[0])
        if not order_num:
            await upd.message.reply_text("❌ Неверный формат номера заказа")
            return
        
        if len(args) >= 2:
            parts_text = ' '.join(args[1:])[:MAX_MESSAGE_LENGTH]
            products = parse_products(parts_text)
            if not products:
                await upd.message.reply_text(f"❌ Не распознаны запчасти. Используйте формат: Название = Цена руб")
                return
            order = get_order(order_num)
            if not order:
                await upd.message.reply_text(f"❌ Заказ {order_num} не найден")
                return
            if order.get('status') == 'pending':
                update_order(order_num, status='waiting_selection')
            update_order(order_num, selected_products=parts_text)
            
            kb = []
            for i, p in enumerate(products[:50]):
                display_name = p['name'][:30] + ".." if len(p['name']) > 30 else p['name']
                kb.append([InlineKeyboardButton(f"⬜ {display_name} — {p['price']} руб.", 
                                               callback_data=f"sel_{order_num}_{i}")])
            kb.append([InlineKeyboardButton("✅ ПОДТВЕРДИТЬ ВЫБОР", callback_data=f"fin_{order_num}")])
            
            await ctx.bot.send_message(
                order.get('user_id'),
                text=f"🛒 **ПОДБОР ЗАПЧАСТЕЙ** ДЛЯ ЗАКАЗА #{order_num}\n\n"
                     f"Менеджер подобрал для вас следующие позиции:\n\n"
                     f"Выберите нужные запчасти (можно отметить несколько):",
                reply_markup=InlineKeyboardMarkup(kb)
            )
            audit_log(MANAGER_ID, 'select_command', f"Заказ {order_num}: отправлен подбор", "success")
            await upd.message.reply_text(f"✅ Подбор для заказа {order_num} отправлен клиенту!")
        else:
            ctx.user_data['pending_selection'] = order_num
            await upd.message.reply_text(
                f"📦 Заказ {order_num}\n\n"
                f"Отправьте список запчастей следующим сообщением в формате:\n"
                f"Название = Цена руб\n\n"
                f"Пример:\n"
                f"Масло RAVENOL DXG 5W-30 5л = 11758 руб\n"
                f"Фильтр масляный = 1066 руб"
            )
    except Exception as e:
        logger.error(f"Ошибка select_command: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

@require_manager
async def send_message_to_client(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        args = ctx.args
        if not args or len(args) < 2:
            await upd.message.reply_text(
                "📨 **ОТПРАВКА СООБЩЕНИЯ КЛИЕНТУ**\n\n"
                "Использование:\n"
                "/msg НОМЕР_ЗАКАЗА ТЕКСТ_СООБЩЕНИЯ\n\n"
                "Пример:\n"
                "/msg RVN-7JTSO3 Ваш заказ готов к выдаче!\n\n"
                "Сообщение будет отправлено клиенту в личные сообщения."
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
        user_id = order.get('user_id')
        if not user_id:
            await upd.message.reply_text("❌ У заказа нет пользователя")
            return
        msg_text = ' '.join(args[1:])
        if len(msg_text) < 2 or len(msg_text) > 4000:
            await upd.message.reply_text("❌ Сообщение должно быть от 2 до 4000 символов")
            return
        msg_text = security.escape_text(msg_text)
        try:
            await ctx.bot.send_message(
                user_id,
                text=f"📨 **СООБЩЕНИЕ ОТ МЕНЕДЖЕРА**\n\n"
                     f"По заказу {order_num}:\n\n"
                     f"{msg_text}\n\n"
                     f"---\n"
                     f"Вы можете ответить на это сообщение, и менеджер его получит."
            )
            audit_log(MANAGER_ID, 'send_message_to_client', f"Заказ {order_num}: {msg_text[:100]}", "success")
            await upd.message.reply_text(f"✅ Сообщение отправлено клиенту по заказу {order_num}!")
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения клиенту: {e}")
            await upd.message.reply_text(f"❌ Ошибка отправки: {str(e)[:100]}")
    except Exception as e:
        logger.error(f"Ошибка в send_message_to_client: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

# ========== АДМИН ПАНЕЛЬ ==========
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
            [InlineKeyboardButton("📦 Архив заказов", callback_data="admin_archive")],
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
        
        text = f"👨‍💼 **АДМИН ПАНЕЛЬ**\n\n"
        text += f"📌 Фильтр: {current_filter_name}\n"
        text += f"📦 Показано заказов: {order_count}\n"
        text += f"🗑️ Отменённых заказов: {cancelled_count}\n\n"
        text += "📋 Для удаления заказа:\n"
        text += "1️⃣ Сначала отмените заказ (кнопка ❌ Отменить)\n"
        text += "2️⃣ Затем нажмите 🗑️ УДАЛИТЬ ЗАКАЗ НАВСЕГДА\n\n"
        text += "⬇️ Выберите заказ для управления:"
        
        if message:
            try:
                await message.edit_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
            except Exception as e:
                if "Message is not modified" not in str(e):
                    logger.error(f"Ошибка редактирования меню: {e}")
        else:
            if upd.message:
                await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(keyboard), parse_mode='Markdown')
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
        text = "📋 **ВСЕ ЗАКАЗЫ В БАЗЕ ДАННЫХ**\n\n"
        for o in orders[:50]:
            if o and safe_len(o) > 0:
                order_num = safe_str(o[0])
                user_name = safe_str(o[1])[:15] if safe_len(o) > 1 else "Неизвестно"
                status_text = safe_str(o[2]) if safe_len(o) > 2 else ''
                created = safe_str(o[3])[:10] if safe_len(o) > 3 else ''
                text += f"• {order_num} | {user_name} | {status_text} | {created}\n"
        kb = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        return
    
    if data == "admin_cancelled_list":
        orders = get_cancelled_orders()
        if not orders:
            await query.edit_message_text("📭 Нет отменённых заказов", reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]))
            return
        text = "🗑️ **ОТМЕНЁННЫЕ ЗАКАЗЫ**\n\n"
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
        text = f"⚠️ **ВНИМАНИЕ!**\n\n"
        text += f"Вы уверены, что хотите удалить ВСЕ отменённые заказы?\n\n"
        text += f"📦 Будет удалено заказов: {safe_len(orders)}\n\n"
        text += f"Это действие **НЕЛЬЗЯ** будет отменить!"
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ДА, УДАЛИТЬ ВСЕ", callback_data="admin_confirm_clear_all")],
            [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="admin_back")]
        ])
        await query.edit_message_text(text, reply_markup=kb, parse_mode='Markdown')
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
                f"Удалить можно только **ОТМЕНЁННЫЙ** заказ.\n"
                f"Сначала отмените заказ, затем удалите."
            )
            return
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ ДА, УДАЛИТЬ", callback_data=f"admin_confirm_delete_{order_num}")],
            [InlineKeyboardButton("❌ НЕТ, ОТМЕНА", callback_data="admin_back")]
        ])
        await query.edit_message_text(
            f"⚠️ **ВНИМАНИЕ!** Вы уверены, что хотите НАВСЕГДА удалить заказ {order_num}?\n\n"
            f"👤 Клиент: {order.get('user_name', 'Неизвестно')}\n"
            f"📦 Статус: {order.get('status_text', 'Неизвестен')}\n\n"
            f"Это действие **НЕЛЬЗЯ** будет отменить!",
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
                        text=f"🗑️ Ваш заказ {order_num} был удалён администратором.\n\n"
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
                f"📊 **СТАТИСТИКА**\n\n"
                f"📦 Заказов: {total_orders}\n"
                f"💰 Сумма: {total_sum:,} руб.\n\n"
                f"**По статусам:**\n{status_text}",
                reply_markup=InlineKeyboardMarkup([[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]),
                parse_mode='Markdown'
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
    
    if data == "admin_archive":
        conn = None
        try:
            conn = sqlite3.connect(DB_PATH, timeout=30)
            c = conn.cursor()
            c.execute('SELECT COUNT(*) FROM orders_archive')
            count = c.fetchone()[0]
            
            if count == 0:
                await query.edit_message_text("📭 Архив пуст")
                return
            
            c.execute('''
                SELECT order_number, user_name, status_text, created_at, total_price
                FROM orders_archive
                ORDER BY id DESC
                LIMIT 20
            ''')
            archived = c.fetchall()
            
            text = f"📦 **АРХИВ ЗАКАЗОВ** ({count} всего)\n\n"
            for a in archived:
                text += f"• {a[0]} | {a[1]} | {a[2]} | {a[3][:10]} | {a[4]} руб.\n"
            
            kb = [[InlineKeyboardButton("◀️ Назад", callback_data="admin_back")]]
            await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        except Exception as e:
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        finally:
            if conn:
                conn.close()
        return
    
    if data == "admin_back":
        admin_status_filter = 'all'
        await admin_menu(upd, ctx, query.message)
        return
    
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
            
            text = f"📋 **ЗАКАЗ {order.get('order_number', '')}**\n\n"
            text += f"👤 {order.get('user_name', '')}\n"
            text += f"📞 {order.get('phone', 'не указан')}\n"
            text += f"🏙️ Стиль город: {order.get('style_city', 'не указан')}\n"
            text += f"🛣️ Стиль трасса: {order.get('style_highway', 'не указан')}\n"
            text += f"🏙️ Город: {order.get('city', 'не указан')}\n"
            text += f"📍 Адрес: {order.get('delivery_address', 'не указан')}\n"
            text += f"🚚 Доставка: {order.get('delivery_type', 'не указана')} | {order.get('delivery_price', 0)} руб.\n"
            text += f"💰 Сумма: {total_sum} руб.\n"
            text += f"📦 Статус: {order.get('status_text', 'неизвестен')}"
            
            if order.get('tracking_number'):
                text += f"\n📮 Трек-номер: {order.get('tracking_number')}"
            if order.get('final_order'):
                text += f"\n\n📦 **ТОВАРЫ:**\n{order.get('final_order')[:300]}"
            elif order.get('selected_products'):
                text += f"\n\n📦 **ПОДБОР МЕНЕДЖЕРА:**\n{order.get('selected_products')[:300]}"
            
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
            
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
            logger.info(f"[ADMIN] Заказ {order_num} успешно открыт")
        except Exception as e:
            logger.error(f"Ошибка просмотра заказа: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
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
        text = f"📜 **ИСТОРИЯ ИЗМЕНЕНИЙ** ЗАКАЗА {order_num}\n\n"
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
        text = f"🔍 **ПОЛНАЯ ИНФОРМАЦИЯ** О ЗАКАЗЕ {order_num}\n\n"
        text += f"👤 Клиент: {order.get('user_name', 'Не указан')}\n"
        text += f"📞 Телефон: {order.get('phone', 'Не указан')}\n"
        text += f"🚗 VIN: {order.get('vin', 'Не указан')}\n"
        text += f"📊 Пробег: {order.get('mileage', 'Не указан')} км\n"
        text += f"🏙️ Стиль город: {order.get('style_city', 'Не указан')}\n"
        text += f"🛣️ Стиль трасса: {order.get('style_highway', 'Не указан')}\n"
        text += f"🏙️ Город: {order.get('city', 'Не указан')}\n"
        text += f"📍 Адрес: {order.get('delivery_address', 'Не указан')}\n"
        text += f"🚚 Доставка: {order.get('delivery_type', 'Не указана')} | {order.get('delivery_price', 0)} руб.\n"
        text += f"💰 Сумма запчастей: {order.get('total_price', 0)} руб.\n"
        text += f"📦 Статус: {order.get('status_text', 'Неизвестен')}\n"
        text += f"📅 Создан: {order.get('created_at', 'Не указана')}"
        if order.get('tracking_number'):
            text += f"\n📮 Трек-номер: {order.get('tracking_number')}"
        if order.get('selected_products'):
            text += f"\n\n📦 **ПОДБОР МЕНЕДЖЕРА:**\n{order.get('selected_products')[:500]}"
        if order.get('needed_parts'):
            text += f"\n\n📝 **ЗАПЧАСТИ КЛИЕНТА:**\n{order.get('needed_parts')[:500]}"
        if order.get('final_order') and order.get('final_order') not in [None, 'None', '[]', '{}']:
            text += f"\n\n✅ **ВЫБРАННЫЕ ЗАПЧАСТИ:**\n{order.get('final_order')[:500]}"
        kb = [[InlineKeyboardButton("◀️ Назад к заказу", callback_data=f"admin_order_{order_num}")]]
        await query.edit_message_text(text[:4000], reply_markup=InlineKeyboardMarkup(kb))
        return
    
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
        text = f"✏️ **РЕДАКТИРОВАНИЕ ЗАКАЗА** {order_num}\n\n"
        text += f"💰 Текущая сумма: {order.get('total_price', 0)} руб.\n\n"
        text += "**Товары в заказе:**\n"
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
    
    if data.startswith("admin_remove_items_"):
        order_num = data[18:]
        order_num = clean_order_number(order_num)
        logger.info(f"[ADMIN_REMOVE] Начало удаления товаров для заказа: {order_num}")
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
            text = f"🗑️ **УДАЛЕНИЕ ТОВАРОВ** ИЗ ЗАКАЗА {order_num}\n\n"
            text += "Нажмите на товар, чтобы отметить его для удаления.\n\n"
            text += "⬜ - товар остаётся\n"
            text += "✅ - товар будет удалён\n\n"
            text += f"💰 Текущая сумма: {order.get('total_price', 0)} руб.\n"
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            logger.error(f"[ADMIN_REMOVE] Ошибка: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
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
        text = f"🗑️ **УДАЛЕНИЕ ТОВАРОВ** ИЗ ЗАКАЗА {order_num}\n\n"
        text += "Нажмите на товар, чтобы отметить его для удаления.\n\n"
        text += "⬜ - товар остаётся\n"
        text += "✅ - товар будет удалён\n\n"
        if order:
            text += f"💰 Текущая сумма: {order.get('total_price', 0)} руб.\n"
        try:
            await query.edit_message_text(text, reply_markup=InlineKeyboardMarkup(kb))
        except Exception as e:
            if "Message is not modified" not in str(e):
                logger.error(f"Ошибка обновления сообщения: {e}")
        return
    
    if data.startswith("admin_remove_confirm_"):
        order_num = data[20:]
        order_num = clean_order_number(order_num)
        logger.info(f"[ADMIN_CONFIRM] Подтверждение удаления для заказа: {order_num}")
        if order_num not in admin_remove_sessions:
            await query.edit_message_text("❌ Сессия истекла. Начните заново.")
            return
        selected_items = admin_remove_sessions[order_num]['selected']
        selected_parts = admin_remove_sessions[order_num]['parts']
        if not selected_items:
            await query.edit_message_text("❌ Не выбрано ни одного товара")
            return
        if len(selected_items) >= len(selected_parts):
            await query.edit_message_text(
                "❌ Нельзя удалить все товары из заказа!\n\n"
                "В заказе должен остаться хотя бы один товар."
            )
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
                         f"💰 Новая сумма: {new_total + delivery_price} руб.\n\n"
                         f"По вопросам обращайтесь к менеджеру."
                )
            except Exception as e:
                logger.error(f"Ошибка уведомления пользователя: {e}")
        del admin_remove_sessions[order_num]
        await query.edit_message_text(
            f"✅ Товары удалены!\n\n"
            f"📦 Заказ: {order_num}\n"
            f"🗑️ Удалено: {len(selected_items)} товаров\n"
            f"💰 Новая сумма: {new_total + delivery_price} руб.\n\n"
            f"Клиент получил уведомление."
        )
        return
    
    if data.startswith("admin_add_item_"):
        order_num = data[16:]
        order_num = clean_order_number(order_num)
        ctx.user_data['admin_add_item_order'] = order_num
        await query.edit_message_text(
            f"➕ **ДОБАВЛЕНИЕ ТОВАРА** В ЗАКАЗ {order_num}\n\n"
            f"Введите название товара:"
        )
        return AdminAddItemStates.NAME
    
    if data.startswith("admin_change_price_"):
        order_num = data[18:]
        order_num = clean_order_number(order_num)
        order = get_order(order_num)
        if not order:
            await query.edit_message_text("❌ Заказ не найден")
            return
        final_order = order.get('final_order', '')
        if not final_order or final_order in [None, 'None', '[]', '{}']:
            await query.edit_message_text("❌ В заказе нет товаров для изменения цены")
            return
        try:
            selected_parts = ast.literal_eval(final_order)
            if not isinstance(selected_parts, list) or not selected_parts:
                await query.edit_message_text("❌ Нет товаров для изменения цены")
                return
            ctx.user_data['admin_change_price_order'] = order_num
            ctx.user_data['admin_change_price_parts'] = selected_parts.copy()
            kb = []
            for i, part in enumerate(selected_parts):
                if isinstance(part, dict):
                    part_name = part.get('name', 'Неизвестно')[:35]
                    part_price = part.get('price', 0)
                    kb.append([InlineKeyboardButton(f"💰 {part_name} — {part_price} руб.", 
                                                   callback_data=f"admin_select_price_item_{order_num}_{i}")])
            kb.append([InlineKeyboardButton("◀️ Назад", callback_data=f"admin_edit_items_{order_num}")])
            await query.edit_message_text(
                f"💰 **ИЗМЕНЕНИЕ ЦЕНЫ** ТОВАРА В ЗАКАЗЕ {order_num}\n\n"
                f"Выберите товар, цену которого хотите изменить:",
                reply_markup=InlineKeyboardMarkup(kb)
            )
        except Exception as e:
            logger.error(f"Ошибка изменения цены: {e}")
            await query.edit_message_text(f"❌ Ошибка: {str(e)[:100]}")
        return
    
    if data.startswith("admin_select_price_item_"):
        await query.answer()
        parts = data.split('_')
        if len(parts) < 6:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[4]
        item_idx = safe_int(parts[5])
        if ctx.user_data.get('admin_change_price_order') != order_num:
            await query.edit_message_text("❌ Сессия истекла")
            return
        ctx.user_data['admin_change_price_idx'] = item_idx
        selected_parts = ctx.user_data.get('admin_change_price_parts', [])
        if item_idx < safe_len(selected_parts) and isinstance(selected_parts[item_idx], dict):
            part_name = selected_parts[item_idx].get('name', 'Товар')
            part_price = selected_parts[item_idx].get('price', 0)
            await query.edit_message_text(
                f"💰 **ИЗМЕНЕНИЕ ЦЕНЫ ТОВАРА**\n\n"
                f"📦 Заказ: {order_num}\n"
                f"📝 Товар: {part_name}\n"
                f"💵 Текущая цена: {part_price} руб.\n\n"
                f"Введите новую цену (целое число, руб.):"
            )
            return AdminChangePriceStates.NEW_PRICE
        else:
            await query.edit_message_text("❌ Товар не найден")
        return
    
    if data.startswith("edit_delivery_"):
        order_num = data[14:]
        order_num = clean_order_number(order_num)
        kb = [
            [InlineKeyboardButton("🚚 Курьером", callback_data=f"set_delivery_{order_num}_Курьером")],
            [InlineKeyboardButton("📦 Самовывоз", callback_data=f"set_delivery_{order_num}_Самовывоз")],
            [InlineKeyboardButton("🚛 Сторонняя фирма", callback_data=f"set_delivery_{order_num}_Сторонняя фирма")],
            [InlineKeyboardButton("◀️ Назад", callback_data=f"admin_order_{order_num}")]
        ]
        await query.edit_message_text("✏️ Выберите способ доставки:", reply_markup=InlineKeyboardMarkup(kb))
        return
    
    if data.startswith("set_delivery_"):
        parts = data.split('_')
        if len(parts) < 4:
            await query.edit_message_text("❌ Ошибка формата данных")
            return
        order_num = parts[2]
        new_delivery = parts[3]
        order_num = clean_order_number(order_num)
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
            await ctx.bot.send_message(order.get('user_id'), text=f"✏️ Доставка изменена: {new_delivery}\n{desc}")
            await query.edit_message_text(f"✅ Доставка изменена!\n\n{desc}")
        return
    
    if data.startswith("pay_"):
        order_num = data[4:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='paid'):
            order = get_order(order_num)
            if order:
                try:
                    await ctx.bot.send_message(order.get('user_id'), text=f"✅ Заказ {order_num} оплачен! Спасибо за покупку!")
                    final_order = order.get('final_order', '')
                    if final_order and final_order not in [None, 'None', '[]', '{}']:
                        try:
                            selected_parts = ast.literal_eval(final_order)
                            if isinstance(selected_parts, list):
                                bonus_percent = get_bonus_percent(order.get('user_id'))
                                eligible_for_bonus = 0
                                for p in selected_parts:
                                    if isinstance(p, dict) and not is_ravenol_product(p.get('name', '')):
                                        eligible_for_bonus += p.get('price', 0)
                                bonus = int(eligible_for_bonus * bonus_percent / 100)
                                if bonus > 0:
                                    add_bonus(order.get('user_id'), order_num, bonus, f"Заказ {order_num} ({bonus_percent}% от {eligible_for_bonus} руб.)")
                        except Exception as e:
                            logger.error(f"Ошибка расчёта бонусов: {e}")
                except:
                    pass
            await query.edit_message_text("✅ СТАТУС: ОПЛАЧЕН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("ordered_"):
        order_num = data[8:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='ordered'):
            order = get_order(order_num)
            if order:
                try:
                    await ctx.bot.send_message(order.get('user_id'), text=f"📦 Заказ {order_num} заказан у поставщика! Ожидайте поступления.")
                except:
                    pass
            await query.edit_message_text("✅ СТАТУС: ЗАКАЗАН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("arrived_"):
        order_num = data[8:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='arrived'):
            order = get_order(order_num)
            if order:
                try:
                    await ctx.bot.send_message(order.get('user_id'), text=f"📦✅ Заказ {order_num}\n\nТовар поступил на склад!")
                except:
                    pass
            await query.edit_message_text("✅ СТАТУС: ТОВАР ПОСТУПИЛ")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("ready_"):
        order_num = data[6:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='ready'):
            order = get_order(order_num)
            if order:
                try:
                    await ctx.bot.send_message(order.get('user_id'), text=f"✅ Заказ {order_num} готов к выдаче! Можете забрать.")
                except:
                    pass
            await query.edit_message_text("✅ СТАТУС: ГОТОВ К ВЫДАЧЕ")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("ship_"):
        order_num = data[5:]
        order_num = clean_order_number(order_num)
        if not order_num:
            await query.edit_message_text("❌ Не удалось определить номер заказа")
            return
        order = get_order(order_num)
        if not order:
            await query.edit_message_text(f"❌ Заказ {order_num} не найден!")
            return
        if order.get('status') not in ['ready']:
            await query.edit_message_text(
                f"❌ Отправить можно только заказ в статусе '✅ Готов к выдаче'\n"
                f"📦 Текущий статус: {order.get('status_text', 'неизвестен')}\n\n"
                f"Сначала переведите заказ в статус '✅ Готов к выдаче'"
            )
            return
        ctx.user_data['track_for'] = order_num
        await query.edit_message_text(
            f"📦 Введите трек-номер для заказа {order_num}:\n\n"
            f"Напишите ответом на это сообщение трек-номер"
        )
        return
    
    if data.startswith("del_"):
        order_num = data[4:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='delivered'):
            order = get_order(order_num)
            if order:
                try:
                    await ctx.bot.send_message(order.get('user_id'), text=f"🏠 Заказ {order_num} доставлен! Спасибо за покупку!")
                except:
                    pass
            await query.edit_message_text("✅ СТАТУС: ДОСТАВЛЕН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("issued_"):
        order_num = data[7:]
        order_num = clean_order_number(order_num)
        if update_order(order_num, status='issued'):
            order = get_order(order_num)
            if order:
                try:
                    await ctx.bot.send_message(order.get('user_id'), text=f"📋 Заказ {order_num} ВЫДАН!\n\nСпасибо за покупку!")
                except:
                    pass
            await query.edit_message_text("✅ СТАТУС: ВЫДАН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    if data.startswith("cancel_"):
        order_num = data[7:]
        order_num = clean_order_number(order_num)
        order = get_order(order_num)
        if order:
            conn = None
            try:
                conn = sqlite3.connect(DB_PATH, timeout=30)
                c = conn.cursor()
                c.execute('SELECT amount FROM bonus_history WHERE order_number = ? AND type = "earned"', (order_num,))
                bonus_row = c.fetchone()
                if bonus_row and bonus_row[0] > 0:
                    refund_bonus(order.get('user_id'), order_num, bonus_row[0], f"Возврат бонусов при отмене заказа {order_num}")
                    await ctx.bot.send_message(order.get('user_id'), text=f"❌ Заказ {order_num} отменён менеджером.\n\n💰 Бонусы в размере {bonus_row[0]} руб. были списаны.")
                else:
                    await ctx.bot.send_message(order.get('user_id'), text=f"❌ Заказ {order_num} отменён менеджером.")
            except Exception as e:
                logger.error(f"Ошибка отмены бонусов: {e}")
            finally:
                if conn:
                    try:
                        conn.close()
                    except:
                        pass
        if update_order(order_num, status='cancelled'):
            await query.edit_message_text("✅ СТАТУС: ОТМЕНЁН")
        else:
            await query.edit_message_text("❌ Ошибка при обновлении статуса")
        return
    
    await admin_menu(upd, ctx, query.message)

# ========== АДМИН: ДОБАВЛЕНИЕ ТОВАРА (ВВОД) ==========
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
            f"➕ **ДОБАВЛЕНИЕ ТОВАРА** В ЗАКАЗ {order_num}\n\n"
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
        selected_parts = security.safe_parse_parts(final_order)
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
                 f"💰 Новая сумма: {new_total + delivery_price} руб.\n\n"
                 f"По вопросам обращайтесь к менеджеру."
        )
        await upd.message.reply_text(
            f"✅ Товар добавлен в заказ {order_num}!\n\n"
            f"➕ {name} — {price} руб.\n"
            f"💰 Новая сумма: {new_total + delivery_price} руб.\n\n"
            f"Клиент получил уведомление."
        )
        if ctx.user_data:
            ctx.user_data.pop('admin_add_item_order', None)
            ctx.user_data.pop('admin_add_item_name', None)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в admin_add_item_price_input: {e}")
        return ConversationHandler.END

# ========== АДМИН: ИЗМЕНЕНИЕ ЦЕНЫ (ВВОД) ==========
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
                     f"💰 Новая сумма заказа: {new_total + delivery_price} руб.\n\n"
                     f"По вопросам обращайтесь к менеджеру."
            )
        await upd.message.reply_text(
            f"✅ Цена изменена!\n\n"
            f"📦 Заказ: {order_num}\n"
            f"📝 Товар: {old_name}\n"
            f"💰 {old_price} руб. → {new_price} руб.\n"
            f"💳 Новая сумма заказа: {new_total + delivery_price} руб.\n\n"
            f"Клиент получил уведомление."
        )
        if ctx.user_data:
            ctx.user_data.pop('admin_change_price_order', None)
            ctx.user_data.pop('admin_change_price_parts', None)
            ctx.user_data.pop('admin_change_price_idx', None)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в admin_change_price_input: {e}")
        return ConversationHandler.END

# ========== ГАРАЖ (ОБРАБОТЧИКИ) ==========
async def garage_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.effective_user:
            return
        
        if not await anti_spam(upd):
            return
        
        cars = get_cars(upd.effective_user.id)
        if cars:
            text = "🚗 **ВАШ ГАРАЖ**\n\n"
            kb = []
            for car in cars:
                if car and len(car) > 0:
                    vin = str(car[0])
                    description = str(car[1]) if len(car) > 1 else ""
                    comment = str(car[2]) if len(car) > 2 else ""
                    created = str(car[3]) if len(car) > 3 else ""
                    display = f"🚗 {vin}"
                    if description:
                        display += f" ({description[:20]})"
                    if comment:
                        display += f" [{comment[:15]}]"
                    kb.append([InlineKeyboardButton(display, callback_data=f"garage_view_{vin}")])
                    kb.append([InlineKeyboardButton(f"🗑️ Удалить {vin}", callback_data=f"garage_del_{vin}")])
            kb.append([InlineKeyboardButton("➕ Добавить автомобиль", callback_data="garage_add")])
            kb.append([InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")])
            await upd.message.reply_text(text, reply_markup=InlineKeyboardMarkup(kb), parse_mode='Markdown')
        else:
            kb = [
                [InlineKeyboardButton("➕ Добавить автомобиль", callback_data="garage_add")],
                [InlineKeyboardButton("◀️ Назад в меню", callback_data="main_menu_back")]
            ]
            await upd.message.reply_text("🚗 В вашем гараже пока нет автомобилей.\n\nДобавьте первый автомобиль!", reply_markup=InlineKeyboardMarkup(kb))
    except Exception as e:
        logger.error(f"Ошибка в garage_menu: {e}")

async def garage_add_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        await query.edit_message_text("🔧 Введите VIN номер автомобиля (17 символов):")
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
            await upd.message.reply_text("❌ Неверный VIN. Попробуйте снова:")
            return GarageStates.VIN
        ctx.user_data['garage_vin'] = vin
        await upd.message.reply_text("📝 Введите описание автомобиля (марка, модель, год):")
        return GarageStates.DESCRIPTION
    except Exception as e:
        logger.error(f"Ошибка в garage_get_vin: {e}")
        return GarageStates.VIN

async def garage_get_description(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return GarageStates.DESCRIPTION
        description = upd.message.text.strip()
        vin = ctx.user_data.get('garage_vin') if ctx.user_data else None
        if not vin:
            await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
            return ConversationHandler.END
        if save_car(upd.effective_user.id, vin, description, ""):
            await upd.message.reply_text(f"✅ Автомобиль {vin} сохранён в гараж!")
        else:
            await upd.message.reply_text(f"❌ Автомобиль {vin} уже есть в гараже!")
        if ctx.user_data and 'garage_vin' in ctx.user_data:
            del ctx.user_data['garage_vin']
        await garage_menu(upd, ctx)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в garage_get_description: {e}")
        return ConversationHandler.END

async def garage_comment_start(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        vin = query.data[15:]
        ctx.user_data['garage_comment_vin'] = vin
        await query.edit_message_text(
            f"✏️ Введите новый комментарий для автомобиля {vin}:\n\n"
            f"Максимум 100 символов.\n"
            f"Или отправьте '-' чтобы удалить комментарий:"
        )
        return GarageStates.DESCRIPTION
    except Exception as e:
        logger.error(f"Ошибка в garage_comment_start: {e}")
        return ConversationHandler.END

async def garage_comment_input(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return GarageStates.DESCRIPTION
        comment = upd.message.text.strip()
        if len(comment) > 100:
            await upd.message.reply_text("❌ Комментарий слишком длинный (максимум 100 символов).")
            return GarageStates.DESCRIPTION
        vin = ctx.user_data.get('garage_comment_vin') if ctx.user_data else None
        if not vin:
            await upd.message.reply_text("❌ Ошибка. Попробуйте снова.")
            return ConversationHandler.END
        if comment == "-":
            comment = ""
        if update_car_comment(upd.effective_user.id, vin, comment):
            await upd.message.reply_text(f"✅ Комментарий для {vin} обновлён!")
        else:
            await upd.message.reply_text(f"❌ Ошибка при обновлении комментария")
        if ctx.user_data and 'garage_comment_vin' in ctx.user_data:
            del ctx.user_data['garage_comment_vin']
        await garage_menu(upd, ctx)
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в garage_comment_input: {e}")
        return ConversationHandler.END

async def garage_back_to_menu(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        await garage_menu(upd, ctx)
    except Exception as e:
        logger.error(f"Ошибка в garage_back_to_menu: {e}")

async def garage_delete(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        vin = query.data[11:]
        kb = InlineKeyboardMarkup([
            [InlineKeyboardButton("✅ Да, удалить", callback_data=f"garage_confirm_del_{vin}")],
            [InlineKeyboardButton("❌ Нет, отмена", callback_data="garage_back_to_menu")]
        ])
        await query.edit_message_text(f"⚠️ Вы уверены, что хотите удалить автомобиль {vin} из гаража?", reply_markup=kb)
    except Exception as e:
        logger.error(f"Ошибка в garage_delete: {e}")

async def garage_confirm_delete(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        query = upd.callback_query
        await query.answer()
        vin = query.data[19:]
        if delete_car(query.from_user.id, vin):
            await query.edit_message_text(f"✅ Автомобиль {vin} удалён из гаража!")
        else:
            await query.edit_message_text(f"❌ Ошибка при удалении {vin}")
        await garage_menu(upd, ctx)
    except Exception as e:
        logger.error(f"Ошибка в garage_confirm_delete: {e}")

# ========== ПОДТВЕРЖДЕНИЕ ОПЛАТЫ (МЕНЕДЖЕР) ==========
@require_manager
async def confirm_payment_command(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        args = ctx.args
        if not args or len(args) == 0:
            await upd.message.reply_text("❌ Укажите номер заказа\n\nПример: /confirm_payment RVN-ABCD12")
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
            await upd.message.reply_text(f"❌ Заказ {order_num} не в статусе 'Ожидает оплаты'\nТекущий статус: {order.get('status_text', 'неизвестен')}")
            return
        if update_order(order_num, status='paid'):
            await ctx.bot.send_message(
                order.get('user_id'),
                text=f"✅ Оплата заказа {order_num} подтверждена!\n\n"
                     f"Спасибо за покупку! Ваш заказ передан в обработку."
            )
            await upd.message.reply_text(f"✅ Оплата заказа {order_num} подтверждена!\n\nКлиент уведомлён.")
            final_order = order.get('final_order', '')
            if final_order and final_order not in [None, 'None', '[]', '{}']:
                try:
                    selected_parts = ast.literal_eval(final_order)
                    if isinstance(selected_parts, list):
                        bonus_percent = get_bonus_percent(order.get('user_id'))
                        eligible_for_bonus = 0
                        for p in selected_parts:
                            if isinstance(p, dict) and not is_ravenol_product(p.get('name', '')):
                                eligible_for_bonus += p.get('price', 0)
                        bonus = int(eligible_for_bonus * bonus_percent / 100)
                        if bonus > 0:
                            add_bonus(order.get('user_id'), order_num, bonus, f"Заказ {order_num} ({bonus_percent}% от {eligible_for_bonus} руб.)")
                            await upd.message.reply_text(f"🎁 Начислено бонусов: {bonus} руб. ({bonus_percent}%)")
                except Exception as e:
                    logger.error(f"Ошибка расчёта бонусов: {e}")
        else:
            await upd.message.reply_text("❌ Ошибка при обновлении статуса")
    except Exception as e:
        logger.error(f"Ошибка в confirm_payment_command: {e}")
        await upd.message.reply_text(f"❌ Ошибка: {str(e)[:100]}")

# ========== ОБРАБОТКА ОШИБОК ==========
async def cancel_command(upd: Update, ctx: ContextTypes.DEFAULT_TYPE):
    try:
        if not upd or not upd.message:
            return
        
        if ctx.user_data:
            ctx.user_data.clear()
        
        await upd.message.reply_text(
            "❌ Действие отменено.\n\n"
            "Вы можете начать заново через главное меню.",
            reply_markup=main_menu
        )
        return ConversationHandler.END
    except Exception as e:
        logger.error(f"Ошибка в cancel_command: {e}")
        return ConversationHandler.END

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error_str = str(context.error)
    logger.error(f"Исключение: {error_str}")
    ignore_patterns = [
        "Message is not modified",
        "Inline keyboard expected",
        "Conflict",
        "Query is too old",
        "Bot was blocked"
    ]
    for pattern in ignore_patterns:
        if pattern in error_str:
            logger.info(f"Игнорируемая ошибка: {pattern}")
            return
    if update and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Произошла ошибка. Попробуйте позже.")
        except Exception as e:
            logger.error(f"Ошибка отправки сообщения об ошибке: {e}")

# ========== ЗАПУСК ==========
def main():
    try:
        init_db()
        logger.info("База данных инициализирована")
    except Exception as e:
        logger.error(f"Ошибка инициализации БД: {e}")
    
    try:
        scheduler = BackgroundScheduler()
        scheduler.add_job(backup_db, 'cron', hour=3, minute=0)
        scheduler.start()
        logger.info("Планировщик запущен")
    except Exception as e:
        logger.error(f"Ошибка планировщика: {e}")
    
    app = Application.builder().token(BOT_TOKEN).build()
    
    # Conversation для заказов
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
    
    # Conversation для гаража
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
    
    # Conversation для оплаты
    payment_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(payment_document_callback, pattern="^pay_document_")],
        states={
            PaymentStates.WAITING_DOCUMENT: [
                MessageHandler(filters.PHOTO, handle_payment_document),
                MessageHandler(filters.Document.ALL, handle_payment_document),
            ]
        },
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=600
    )
    
    # Conversation для бонусов
    spend_bonus_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(spend_bonus_custom_callback, pattern="^spend_bonus_custom_")],
        states={BonusStates.SPEND: [MessageHandler(filters.TEXT & ~filters.COMMAND, spend_bonus_custom_input)]},
        fallbacks=[CommandHandler("cancel", start)],
        conversation_timeout=300
    )
    
    # Conversation для удаления товаров
    remove_items_conv = ConversationHandler(
        entry_points=[],
        states={RemoveStates.COMMENT: [MessageHandler(filters.TEXT & ~filters.COMMAND, remove_comment_input)]},
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # Conversation для админа (добавление товара)
    admin_add_item_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_add_item_")],
        states={
            AdminAddItemStates.NAME: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_item_name_input)],
            AdminAddItemStates.PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_add_item_price_input)],
        },
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # Conversation для админа (изменение цены)
    admin_change_price_conv = ConversationHandler(
        entry_points=[CallbackQueryHandler(admin_callback, pattern="^admin_change_price_")],
        states={AdminChangePriceStates.NEW_PRICE: [MessageHandler(filters.TEXT & ~filters.COMMAND, admin_change_price_input)]},
        fallbacks=[CommandHandler("cancel", start)],
    )
    
    # Регистрация
    app.add_handler(CommandHandler("start", start))
    app.add_handler(order_conv)
    app.add_handler(garage_conv)
    app.add_handler(garage_comment_conv)
    app.add_handler(payment_conv)
    app.add_handler(spend_bonus_conv)
    app.add_handler(remove_items_conv)
    app.add_handler(admin_add_item_conv)
    app.add_handler(admin_change_price_conv)
    
    app.add_handler(CommandHandler("my_orders", my_orders))
    app.add_handler(CommandHandler("bonus", bonus_cmd))
    app.add_handler(CommandHandler("referral", referral_cmd))
    app.add_handler(CommandHandler("delivery", delivery_cmd))
    app.add_handler(CommandHandler("help", help_cmd))
    app.add_handler(CommandHandler("confirm_payment", confirm_payment_command))
    
    app.add_handler(CommandHandler("search", smart_search))
    app.add_handler(CommandHandler("template", quick_template))
    app.add_handler(CommandHandler("dashboard", dashboard))
    app.add_handler(CommandHandler("export", export_report))
    app.add_handler(CommandHandler("menu", menu_command))
    app.add_handler(CommandHandler("allorders", show_all_orders_raw))
    app.add_handler(CommandHandler("fix_orders", fix_orphan_orders))
    app.add_handler(CommandHandler("delorder", delete_order_command))
    app.add_handler(CommandHandler("batch_del", batch_delete_orders))
    app.add_handler(CommandHandler("payment_docs", show_payment_docs))
    app.add_handler(CommandHandler("select", select_command))
    app.add_handler(CommandHandler("msg", send_message_to_client))
    app.add_handler(CommandHandler("cancel", cancel_command))
    
    app.add_handler(MessageHandler(filters.Regex("^(🚗 Мой гараж)$"), garage_menu))
    app.add_handler(MessageHandler(filters.Regex("^(📦 Мои заказы)$"), my_orders))
    app.add_handler(MessageHandler(filters.Regex("^(🎁 Бонусы)$"), bonus_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🔗 Рефералы)$"), referral_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(🚚 Доставка)$"), delivery_cmd))
    app.add_handler(MessageHandler(filters.Regex("^(ℹ️ Помощь)$"), help_cmd))
    
    app.add_handler(CallbackQueryHandler(view_order, pattern="^view_"))
    app.add_handler(CallbackQueryHandler(my_orders, pattern="^back_orders_list$"))
    app.add_handler(CallbackQueryHandler(start, pattern="^main_menu_back$"))
    app.add_handler(CallbackQueryHandler(garage_menu, pattern="^garage_"))
    app.add_handler(CallbackQueryHandler(garage_back_to_menu, pattern="^garage_back_to_menu$"))
    app.add_handler(CallbackQueryHandler(garage_delete, pattern="^garage_del_"))
    app.add_handler(CallbackQueryHandler(garage_confirm_delete, pattern="^garage_confirm_del_"))
    app.add_handler(CallbackQueryHandler(orders_page_callback, pattern="^orders_page_"))
    
    app.add_handler(CallbackQueryHandler(select_cb, pattern="^sel_"))
    app.add_handler(CallbackQueryHandler(finalize_cb, pattern="^fin_"))
    
    app.add_handler(CallbackQueryHandler(feedback_callback, pattern="^feedback_"))
    app.add_handler(CallbackQueryHandler(save_rating, pattern="^rating_"))
    app.add_handler(CallbackQueryHandler(contact_manager, pattern="^contact_manager$"))
    app.add_handler(CallbackQueryHandler(help_payment, pattern="^help_payment$"))
    app.add_handler(CallbackQueryHandler(tracking_info, pattern="^tracking_"))
    app.add_handler(CallbackQueryHandler(apply_bonus_callback, pattern="^apply_bonus_"))
    app.add_handler(CallbackQueryHandler(spend_bonus_percent_callback, pattern="^spend_bonus_percent_"))
    app.add_handler(CallbackQueryHandler(confirm_spend_callback, pattern="^confirm_spend_"))
    app.add_handler(CallbackQueryHandler(remove_items_callback, pattern="^remove_items_"))
    app.add_handler(CallbackQueryHandler(client_toggle_callback, pattern="^client_toggle_"))
    app.add_handler(CallbackQueryHandler(client_confirm_remove_callback, pattern="^client_confirm_remove_"))
    app.add_handler(CallbackQueryHandler(cancel_by_user_callback, pattern="^cancel_by_user_"))
    app.add_handler(CallbackQueryHandler(confirm_user_cancel_callback, pattern="^confirm_user_cancel_"))
    app.add_handler(CallbackQueryHandler(bonus_history_callback, pattern="^bonus_history$"))
    app.add_handler(CallbackQueryHandler(bonus_back_callback, pattern="^bonus_back$"))
    
    app.add_handler(CallbackQueryHandler(admin_callback, pattern="^(admin_|pay_|ordered_|arrived_|ready_|ship_|del_|issued_|cancel_|edit_delivery_|set_delivery_|detail_|order_changes_|admin_edit_items_|admin_remove_items_|admin_toggle_|admin_remove_confirm_|admin_add_item_|admin_change_price_|admin_select_price_item_|waiting_selection_|waiting_payment_)"))
    
    app.add_handler(
        MessageHandler(
            filters.Chat(chat_id=MANAGER_ID) & filters.TEXT & ~filters.COMMAND,
            manager_message_handler
        )
    )
    
    app.add_error_handler(error_handler)
    
    logger.info(f"🤖 Бот запущен! ID менеджера: {MANAGER_ID}")
    app.run_polling(allowed_updates=Update.ALL_TYPES)

if __name__ == "__main__":
    main()
