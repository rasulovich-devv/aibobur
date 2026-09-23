import telebot
from telebot import types
import requests
import sqlite3
import datetime
import os
from urllib.parse import quote
from apscheduler.schedulers.background import BackgroundScheduler
from dotenv import load_dotenv

# 1. BOT SOZLAMALARI
load_dotenv()
TOKEN = os.getenv('BOT_TOKEN')
if not TOKEN:
    raise RuntimeError("BOT_TOKEN topilmadi. .env fayliga BOT_TOKEN qiymatini kiriting.")
bot = telebot.TeleBot(TOKEN)
# Adminlar ro'yxati (Telegram user ID lari bilan)
# Misol: ADMINS = [8064362408, 5300777905]
ADMINS = [8064362408, 5300777905]

# 2. BAZANI ISHGA TUSHIRISH
def init_db():
    conn = sqlite3.connect('smart_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS users (user_id INTEGER PRIMARY KEY, city TEXT)''')
    conn.commit()
    return conn
db = init_db()


def init_stats_db():
    conn = sqlite3.connect('bot_stats.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS bot_users (
        user_id INTEGER PRIMARY KEY,
        first_seen TEXT,
        last_seen TEXT,
        month_key TEXT
    )''')
    conn.commit()
    return conn

stats_db = init_stats_db()







def register_user_visit(message_or_user_id):
    # Accept Message, CallbackQuery, or numeric user_id
    if hasattr(message_or_user_id, 'from_user') and getattr(message_or_user_id, 'from_user'):
        user_id = message_or_user_id.from_user.id
    elif hasattr(message_or_user_id, 'chat') and getattr(message_or_user_id, 'chat'):
        user_id = message_or_user_id.chat.id
    else:
        user_id = int(message_or_user_id)
    now = datetime.datetime.now().strftime('%Y-%m-%d %H:%M:%S')
    month_key = datetime.datetime.now().strftime('%Y-%m')
    cursor = stats_db.cursor()
    cursor.execute(
        "INSERT OR IGNORE INTO bot_users (user_id, first_seen, last_seen, month_key) VALUES (?, ?, ?, ?)",
        (user_id, now, now, month_key)
    )
    cursor.execute(
        "UPDATE bot_users SET last_seen = ?, month_key = ? WHERE user_id = ?",
        (now, month_key, user_id)
    )
    stats_db.commit()


def get_statistics_text():
    cursor = stats_db.cursor()
    total = cursor.execute("SELECT COUNT(*) FROM bot_users").fetchone()[0]
    month_key = datetime.datetime.now().strftime('%Y-%m')
    monthly = cursor.execute("SELECT COUNT(*) FROM bot_users WHERE month_key = ?", (month_key,)).fetchone()[0]
    today = cursor.execute("SELECT COUNT(*) FROM bot_users WHERE date(last_seen) = date('now')").fetchone()[0]
    # Faol foydalanuvchilar: oxirgi 5 daqiqa ichida faol bo'lganlar
    threshold = (datetime.datetime.now() - datetime.timedelta(minutes=5)).strftime('%Y-%m-%d %H:%M:%S')
    active = cursor.execute("SELECT COUNT(*) FROM bot_users WHERE last_seen >= ?", (threshold,)).fetchone()[0]
    return (
        "📊 Bot foydalanuvchilari:\n\n"
        f"🧾 Umumiy: {total:,} ta\n"
        f"📅 Bu oy: {monthly:,} ta\n"
        f"✅ Bugun: {today:,} ta\n"
        f"⚡ Faol (oxirgi 5 daqiqa): {active:,} ta"
    )

# --- ASOSIY MENYU ---
def main_menu(message):
    try:
        register_user_visit(message)
    except Exception:
        pass
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, row_width=2)
    # Statistika tugmasi faqat adminlarga ko'rinadi
    base_buttons = ["💰 Valyuta kurslari", "🕌 Namoz vaqtlari", "☀️ Ob-havo", "📖 Men haqimda", "📞 Aloqa"]
    if hasattr(message, 'from_user') and message.from_user and message.from_user.id in ADMINS:
        base_buttons.insert(4, "📊 Statistika")
    markup.add(*base_buttons)
    bot.send_message(message.chat.id, "🏠 Asosiy menyu. Kerakli bo'limni tanlang:", reply_markup=markup)

# 2. MA'LUMOTLAR BAZASI (Eslatma uchun foydalanuvchini saqlash)
def init_db_prayer():
    conn = sqlite3.connect('namoz_bot.db', check_same_thread=False)
    cursor = conn.cursor()
    cursor.execute('''CREATE TABLE IF NOT EXISTS prayers 
                     (user_id INTEGER PRIMARY KEY, city TEXT)''')
    conn.commit()
    return conn

db_conn = init_db_prayer()

# 3. BILDIRISHNOMA TIZIMI

def normalize_prayer_time(value):
    if value is None:
        return ""
    if isinstance(value, str):
        value = value.strip()
        if len(value) >= 5:
            value = value[:5]
        return value
    return str(value)[:5]


def fetch_prayer_times(city):
    city = str(city).strip()
    if not city:
        return None

    candidates = []
    for item in [city, city.replace("'", "")]:
        if item and item not in candidates:
            candidates.append(item)

    # Uzbekistan-specific API is more accurate for regional times.
    for cand in candidates:
        for region in [cand, cand.lower(), cand.title()]:
            if not region:
                continue
            try:
                encoded = quote(region)
                url = f"https://islomapi.uz/api/present/day?region={encoded}"
                response = requests.get(url, timeout=8)
                if response.status_code != 200:
                    continue
                res = response.json()
                data = res.get('data') or res
                times = data.get('times') if isinstance(data, dict) and isinstance(data.get('times'), dict) else None
                if not times:
                    times = data.get('timings') if isinstance(data, dict) and isinstance(data.get('timings'), dict) else None
                if not times:
                    for key, value in (data.items() if isinstance(data, dict) else []):
                        if isinstance(value, dict):
                            times = value
                            break
                if not times:
                    continue

                normalized = {}
                for k, v in times.items():
                    key = str(k).lower()
                    if 'bomdod' in key or 'fajr' in key or 'saharlik' in key or 'tong' in key:
                        normalized['Fajr'] = normalize_prayer_time(v)
                    elif 'peshin' in key or 'dhuhr' in key or 'duhur' in key:
                        normalized['Dhuhr'] = normalize_prayer_time(v)
                    elif 'asr' in key:
                        normalized['Asr'] = normalize_prayer_time(v)
                    elif 'shom' in key or 'maghrib' in key:
                        normalized['Maghrib'] = normalize_prayer_time(v)
                    elif 'xufton' in key or 'isha' in key or 'kechki' in key:
                        normalized['Isha'] = normalize_prayer_time(v)

                if all(k in normalized for k in ['Fajr', 'Dhuhr', 'Asr', 'Maghrib', 'Isha']):
                    return normalized
            except Exception:
                continue

    # Fallback to Aladhan if Uzbek API is unavailable.
    city_api = city.replace("'", "")
    url = f"http://api.aladhan.com/v1/timingsByCity?city={quote(city_api)}&country=Uzbekistan&method=3"
    try:
        response = requests.get(url, timeout=8)
        if response.status_code != 200:
            return None
        res = response.json()
        if res.get('code') != 200:
            return None
        timings = res.get('data', {}).get('timings')
        if not timings:
            return None
        return {
            'Fajr': normalize_prayer_time(timings.get('Fajr')),
            'Dhuhr': normalize_prayer_time(timings.get('Dhuhr')),
            'Asr': normalize_prayer_time(timings.get('Asr')),
            'Maghrib': normalize_prayer_time(timings.get('Maghrib')),
            'Isha': normalize_prayer_time(timings.get('Isha')),
        }
    except Exception:
        return None


def send_prayer_alerts():
    now = datetime.datetime.now().strftime("%H:%M")
    cursor = db_conn.cursor()
    cursor.execute("SELECT user_id, city FROM prayers")
    users = cursor.fetchall()

    for user_id, city in users:
        try:
            times = fetch_prayer_times(city)
            if not times:
                continue
            for key, name in {'Fajr': 'Bomdod', 'Dhuhr': 'Peshin', 'Asr': 'Asr', 'Maghrib': 'Shom', 'Isha': 'Xufton'}.items():
                if normalize_prayer_time(times.get(key)) == now:
                    bot.send_message(user_id, f"🕌 {name} vaqti bo'ldi!\n📍 Hudud: {city}\n\nIbodatingiz qabul bo'lsin!", disable_notification=False)
        except Exception as e:
            print(f"Namoz vaqti xatosi ({city}): {str(e)}")
            continue

# 1. BARCHA VILOYAT, TUMAN VA SHAHARLAR RO'YXATI
LOCATIONS = {
    "Toshkent": [
        "Tashkent", "Angren", "Olmaliq", "Bekobod", "Chirchiq", "Yangiyo'l", 
        "Ohangaron", "Gazalkent", "Chinoz", "Piskent", "Parkent", "Keles", 
        "Bostonliq", "Zangiota", "Qibray", "Okkorgon", "Boka", "Yangibozor"
    ],
    "Andijon": [
        "Andijan", "Asaka", "Shahrixon", "Khanobod", "Korgontepa", "Marhamat", 
        "Paxtaobod", "Khojabod", "Baliqchi", "Boston", "Buloqboshi", "Izboskan", 
        "Jalaquduq", "Oltinko'l", "Ulughnor", "Khujobod"
    ],
    "Farg'ona": [
        "Fergana", "Kokand", "Margilan", "Quva", "Rishton", "Oltiariq", "Yaypan", 
        "Besharik", "Yozyovon", "Uchkuprik", "Bagdod", "Buvayda", "Dangara", 
        "Furqat", "Sokh", "Tashloq", "Quvasoy", "Vodzil"
    ],
    "Namangan": [
        "Namangan", "Chust", "Kosonsoy", "Uchkorgon", "Pop", "Hakkulobod", 
        "Chortoq", "Norin", "Mingbuloq", "Torakorgon", "Uychi", "Yangikorgon"
    ],
    "Samarqand": [
        "Samarkand", "Kattakorgon", "Urgut", "Akdarya", "Bulungur", "Jomboy", 
        "Ishtikhan", "Narpay", "Nurabod", "Payariq", "Pasdargom", "Paxtachi", 
        "Toyloq", "Koshrabot", "Chelak"
    ],
    "Buxoro": [
        "Bukhara", "Gijduvon", "Kagan", "Karakol", "Olot", "Vobkent", "Shofirkon", 
        "Peshku", "Romitan", "Jondor", "Karovulbozor", "Galaosiyo"
    ],
    "Qashqadaryo": [
        "Qarshi", "Shahrisabz", "Koson", "Mubarek", "Guzar", "Kitab", "Kamashi", 
        "Chiroqchi", "Dehkhanobod", "Kasbi", "Mirishkor", "Nishon", "Yakkabog'"
    ],
    "Surxondaryo": [
        "Termez", "Denov", "Sherobod", "Jarqorgon", "Shorchi", "Boysun", 
        "Kumkorgon", "Sariosiyo", "Uzun", "Muzrabot", "Angor", "Qizirik", "Oltinsoy"
    ],
    "Xorazm": [
        "Urgench", "Khiva", "Gurlan", "Bogot", "Shovot", "Hazarasp", "Koshkupir", 
        "Yangibozor", "Yangiariq", "Tuprokqala", "Pitnak"
    ],
    "Navoiy": [
        "Navoi", "Zarafshan", "Uchquduq", "Karmana", "Qiziltepa", "Nurota", 
        "Khatirchi", "Konimex", "Tomdi"
    ],
    "Jizzax": [
        "Jizzakh", "Gallaorol", "Zomin", "Dostlik", "Paxtakor", "Mirzachul", 
        "Forish", "Baxmal", "Arnasoy", "Gubdin", "Yangiobod"
    ],
    "Sirdaryo": [
        "Guliston", "Yangiyer", "Shirin", "Sirdarya", "Baxt", "Khovos", "Boyovut", 
        "Sayhunobod", "Mirzaobod", "Oqoltin", "Sardoba"
    ],
    "Qoraqalpog'iston": [
        "Nukus", "Khojeli", "Kongrat", "Beruniy", "Tortkul", "Moynaq", 
        "Amudarya", "Chimboy", "Ellikkala", "Kegeyli", "Qanlikul", "Qoraoozak", 
        "Shumanay", "Takhtakopir", "Takiatosh"
    ]
}

# --- HANDLERLAR ---

@bot.message_handler(func=lambda m: m.text == "🕌 Namoz vaqtlari")
def prayer_main(message):
    try:
        register_user_visit(message)
    except Exception:
        pass
    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = [types.InlineKeyboardButton(text=r, callback_data=f"r_{r}") for r in LOCATIONS.keys()]
    markup.add(*btns)
    bot.send_message(message.chat.id, "📍 Viloyatingizni tanlang:", reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("r_"))
def prayer_region(call):
    reg = call.data.split("_")[1]
    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = [types.InlineKeyboardButton(text=c, callback_data=f"c_{c}") for c in LOCATIONS[reg]]
    markup.add(*btns, types.InlineKeyboardButton(text="⬅️ Orqaga", callback_data="back_to_r"))
    bot.edit_message_text(f"🏙 {reg} viloyati. Shaharni tanlang:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data == "back_to_r")
def back_to_r(call):
    markup = types.InlineKeyboardMarkup(row_width=2)
    btns = [types.InlineKeyboardButton(text=r, callback_data=f"r_{r}") for r in LOCATIONS.keys()]
    markup.add(*btns)
    bot.edit_message_text("📍 Viloyatingizni tanlang:", call.message.chat.id, call.message.message_id, reply_markup=markup)

@bot.callback_query_handler(func=lambda call: call.data.startswith("c_"))
def prayer_final(call):
    city = call.data.replace("c_", "", 1)
    user_id = call.from_user.id

    try:
        register_user_visit(call)
    except Exception:
        pass

    cursor = db_conn.cursor()
    cursor.execute("INSERT OR REPLACE INTO prayers (user_id, city) VALUES (?, ?)", (user_id, city))
    db_conn.commit()

    times = fetch_prayer_times(city)
    if not times:
        bot.answer_callback_query(call.id, "⚠️ Namoz vaqtlari ma'lumoti olishda xatolik.")
        return

    text = (
        f"🕋 {city} shahri vaqtlari:\n"
        f"{'='*30}\n\n"
        f"🏙 Bomdod: {times.get('Fajr', 'N/A')}\n"
        f"☀️ Peshin: {times.get('Dhuhr', 'N/A')}\n"
        f"🌇 Asr: {times.get('Asr', 'N/A')}\n"
        f"🌌 Shom: {times.get('Maghrib', 'N/A')}\n"
        f"🌃 Xufton: {times.get('Isha', 'N/A')}\n\n"
        f"Siz ushbu hududga obuna bo'ldingiz.\nNamoz vaqti bo'lganda bot xabar yuboradi."
    )

    bot.send_message(call.message.chat.id, text)
    bot.delete_message(call.message.chat.id, call.message.message_id)

# --- 🌤 MUKAMMAL OB-HAVO BO'LIMI ---

@bot.message_handler(func=lambda m: m.text == "☀️ Ob-havo")
def weather_request(message):
    try:
        register_user_visit(message)
    except Exception:
        pass
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True, one_time_keyboard=True)
    loc_btn = types.KeyboardButton(text="📍 Joylashuvni ulashish", request_location=True)
    back_btn = types.KeyboardButton(text="⬅️ Bosh menyuga qaytish")
    markup.add(loc_btn, back_btn)

    msg = (
        "🌤 Ob-havo ma'lumotlarini olish:\n\n"
        "Aniq ma'lumotlar uchun pastdagi tugmani bosing va GPS orqali joylashuvingizni yuboring.\n"
        "(Namlik, shamol va quyosh vaqtlari ko'rsatiladi)"
    )
    bot.send_message(message.chat.id, msg, reply_markup=markup)

@bot.message_handler(content_types=['location'])
def handle_location_weather(message):
    try:
        try:
            register_user_visit(message)
        except Exception:
            pass
        lat = message.location.latitude
        lon = message.location.longitude
        url = f"https://api.open-meteo.com/v1/forecast?latitude={lat}&longitude={lon}&current=temperature_2m,relative_humidity_2m,weather_code,wind_speed_10m&daily=sunrise,sunset&timezone=auto"
        res = requests.get(url, timeout=10).json()

        if 'current' not in res:
            bot.send_message(message.chat.id, "⚠️ API dan ma'lumot kelmadi.")
            return

        curr = res['current']
        daily = res['daily']
        temp = curr.get('temperature_2m', 'N/A')
        humidity = curr.get('relative_humidity_2m', 0)
        weather_code = curr.get('weather_code', 0)
        wind_speed = curr.get('wind_speed_10m', 0)
        sunrise = daily['sunrise'][0].split("T")[1] if daily.get('sunrise') else 'N/A'
        sunset = daily['sunset'][0].split("T")[1] if daily.get('sunset') else 'N/A'

        weather_codes = {
            0: "Musaffo osmon ☀️", 1: "Asosan ochiq 🌤", 2: "Qisman bulutli ⛅", 3: "Bulutli ☁️",
            45: "Tuman 🌫", 51: "Yengil yomg'ir 🌦", 61: "Yomg'ir 🌧", 71: "Qor ❄️", 95: "Momaqaldiroq ⛈"
        }
        status = weather_codes.get(weather_code, "Noma'lum 🌡")

        weather_text = (
            f"📍 Sizning hududingiz bo'yicha ob-havo:\n\n"
            f"🌡 Harorat: {temp}°C\n"
            f"🌈 Holat: {status}\n"
            f"💧 Namlik: {humidity}%\n"
            f"💨 Shamol: {wind_speed} km/soat\n"
            f"🌅 Quyosh chiqishi: {sunrise}\n"
            f"🌇 Quyosh botishi: {sunset}\n\n"
            f"Ma'lumotlar GPS orqali yangilandi."
        )

        markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
        markup.add(types.KeyboardButton("⬅️ Bosh menyuga qaytish"))

        bot.send_message(message.chat.id, weather_text, reply_markup=markup)

    except Exception as e:
        print(f"Ob-havo xatosi: {str(e)}")
        import traceback
        traceback.print_exc()
        bot.send_message(message.chat.id, "⚠️ Ma'lumotlarni yuklashda xatolik. Qaytadan urinib ko'ring.")

@bot.message_handler(func=lambda m: m.text == "💰 Valyuta kurslari")
def currency_request(message):
    try:
        register_user_visit(message)
    except Exception:
        pass
    kurs_text = get_currency()
    bot.send_message(message.chat.id, kurs_text)

# 3. Valyuta kurslari

def get_currency():
    url = "https://open.er-api.com/v6/latest/USD"
    try:
        response = requests.get(url, timeout=10)

        if response.status_code != 200:
            print(f"API xatosi: {response.status_code}")
            return "⚠️ Kurs ma'lumotlarini olib bo'lmadi."

        data = response.json()
        if data.get('result') == 'success':
            rates = data.get('rates', {})
            if 'UZS' not in rates or 'EUR' not in rates or 'RUB' not in rates:
                return "⚠️ So'mga kurs ma'lumotlari topilmadi."

            usd_to_uzs = rates['UZS']
            eur_in_usd = rates['EUR']
            rub_in_usd = rates['RUB']
            eur_to_uzs = usd_to_uzs / eur_in_usd
            rub_to_uzs = usd_to_uzs / rub_in_usd
            return (f"📊 Dunyo bozori kurslari (UZS):\n\n"
                    f"🇺🇸 Dollar: {usd_to_uzs:,.2f} so'm\n"
                    f"🇪🇺 Euro: {eur_to_uzs:,.2f} so'm\n"
                    f"🇷🇺 Rubl: {rub_to_uzs:,.2f} so'm\n\n"
                    f"Yangilandi: {data.get('time_last_update_utc', 'N/A')[:16]}")
        else:
            print(f"API javob xatosi: {data.get('result')}")
            return "⚠️ Kurs ma'lumotlarini olib bo'lmadi."
    except Exception as e:
        print(f"Valyuta xatosi: {str(e)}")
        import traceback
        traceback.print_exc()
        return "⚠️ Internet ulanishda muammo. Qaytadan urinib ko'ring."

@bot.message_handler(func=lambda m: m.text == "📖 Men haqimda")
def about_me(message):
    try:
        register_user_visit(message)
    except Exception:
        pass
    about_text = (
        "🤖 Men haqimda:\n\n"
        "Salom! Men ko'p funksiyali Telegram boti'man.\n"
        "Sizga quyidagi xizmatlarni beray olayman:\n\n"
        "💰 Valyuta kurslari - Dunyo bozori kurslari\n"
        "🕌 Namoz vaqtlari - Hududingizning namoz vaqtlari\n"
        "☀️ Ob-havo - Real vaqt ob-havo ma'lumotlari\n\n"
        "Barcha xizmatlar tekinga va har vaqt mavjud!\n"
        "Agar muammoga duch kelsangiz, /support buyrug'ini yuboring."
    )
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⬅️ Bosh menyuga qaytish")
    bot.send_message(message.chat.id, about_text, reply_markup=markup)

@bot.message_handler(func=lambda m: m.text == "📞 Aloqa")
def contact(message):
    try:
        register_user_visit(message)
    except Exception:
        pass
    contact_text = (
        "📞 Biz bilan aloqa:\n\n"
        "Agar sizda savollar yoki takliflar bo'lsa:\n\n"
        "📱 Telegram 1: <a href=\"https://t.me/rasulovich_o7\">@rasulovich_o7</a>\n"
        "📱 Telegram 2: <a href=\"https://t.me/rasulovich_o77\">@rasulovich_o77</a>\n\n"
        "Biz 24/7 sizga javob berishga tayyor!\n"
        "Rahmat, bizni eslagan uchun!"
    )
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⬅️ Bosh menyuga qaytish")
    bot.send_message(message.chat.id, contact_text, reply_markup=markup, parse_mode="HTML")

@bot.message_handler(func=lambda m: m.text == "⬅️ Bosh menyuga qaytish")
def back_to_menu(message):
    main_menu(message)

# --- Bot buyruqlari ---

@bot.message_handler(commands=['start'])
def start(message):
    try:
        register_user_visit(message)
    except Exception:
        pass
    main_menu(message)

@bot.message_handler(commands=['support'])
def support_cmd(message):
    try:
        register_user_visit(message)
    except Exception:
        pass
    support_text = (
        "📞 Biz bilan aloqa:\n\n"
        "Agar sizda savollar yoki takliflar bo'lsa:\n\n"
        "📱 Telegram 1: <a href=\"https://t.me/rasulovich_o7\">@rasulovich_o7</a>\n"
        "📱 Telegram 2: <a href=\"https://t.me/rasulovich_o77\">@rasulovich_o77</a>\n\n"
        "Biz 24/7 sizga javob berishga tayyor!\n"
        "Rahmat, bizni eslagan uchun!"
    )
    markup = types.ReplyKeyboardMarkup(resize_keyboard=True)
    markup.add("⬅️ Bosh menyuga qaytish")
    bot.send_message(message.chat.id, support_text, reply_markup=markup, parse_mode="HTML")


@bot.message_handler(func=lambda m: m.text == "📊 Statistika")
def show_stats(message):
    # Faqat adminlarga ruxsat
    try:
        uid = message.from_user.id
    except Exception:
        uid = None
    try:
        register_user_visit(message)
    except Exception:
        pass
    if uid not in ADMINS:
        bot.send_message(message.chat.id, "⚠️ Sizda ruxsat yo'q.")
        return
    stats_text = get_statistics_text()
    bot.send_message(message.chat.id, stats_text)

scheduler = BackgroundScheduler()
scheduler.add_job(send_prayer_alerts, 'interval', minutes=10)
scheduler.start()

# ... barcha handlerlar tugagandan keyin ...

print("-----------------------------------------")
print("🚀 Bot muvaffaqiyatli ishga tushdi!")
print(f"🕒 Vaqt: {datetime.datetime.now().strftime('%H:%M:%S')}")
print("-----------------------------------------")

bot.infinity_polling()