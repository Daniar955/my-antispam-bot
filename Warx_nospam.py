import telebot
from telebot import types
import time
from collections import defaultdict
import re
import random
import os
from datetime import datetime, timedelta
from flask import Flask, request
import psycopg2
import psycopg2.extras
from urllib.parse import urlparse
import threading
import queue

# ============================================
# НАСТРОЙКИ
# ============================================
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise ValueError("❌ Нет токена! Добавь BOT_TOKEN в переменные окружения!")

SUPER_ADMIN_ID = int(os.environ.get('SUPER_ADMIN_ID', 6647021953))

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# Очередь для быстрого удаления сообщений
delete_queue = queue.Queue()

# ============================================
# ПОТОК ДЛЯ БЫСТРОГО УДАЛЕНИЯ
# ============================================
def delete_worker():
    """Фоновый поток для мгновенного удаления сообщений"""
    while True:
        try:
            chat_id, message_id = delete_queue.get(timeout=1)
            try:
                bot.delete_message(chat_id, message_id)
            except Exception as e:
                print(f"Ошибка удаления: {e}")
            finally:
                delete_queue.task_done()
        except queue.Empty:
            time.sleep(0.01)  # Короткая пауза

# Запускаем поток удаления
delete_thread = threading.Thread(target=delete_worker, daemon=True)
delete_thread.start()

# ============================================
# ПОДКЛЮЧЕНИЕ К POSTGRESQL
# ============================================
def get_db_connection():
    """Создает подключение к PostgreSQL"""
    db_url = os.environ.get('DATABASE_URL')
    if not db_url:
        raise ValueError("❌ Нет DATABASE_URL в переменных окружения!")
    
    result = urlparse(db_url)
    conn = psycopg2.connect(
        database=result.path[1:],
        user=result.username,
        password=result.password,
        host=result.hostname,
        port=result.port
    )
    conn.autocommit = True
    return conn

# ============================================
# БАЗА ДАННЫХ
# ============================================
class Database:
    def __init__(self):
        self.conn = get_db_connection()
        self.cursor = self.conn.cursor(cursor_factory=psycopg2.extras.DictCursor)
        self.create_tables()
        print("✅ База данных PostgreSQL подключена")
    
    def create_tables(self):
        # Таблица настроек групп
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id BIGINT PRIMARY KEY,
                enabled BOOLEAN DEFAULT TRUE,
                flood_enabled BOOLEAN DEFAULT TRUE,
                caps_enabled BOOLEAN DEFAULT TRUE,
                emoji_enabled BOOLEAN DEFAULT TRUE,
                repeat_enabled BOOLEAN DEFAULT TRUE,
                links_enabled BOOLEAN DEFAULT TRUE,
                swear_enabled BOOLEAN DEFAULT TRUE,
                max_messages INTEGER DEFAULT 4,
                time_window INTEGER DEFAULT 3,
                caps_limit INTEGER DEFAULT 50,
                emoji_limit INTEGER DEFAULT 5,
                link_kd INTEGER DEFAULT 10,
                warn_limit INTEGER DEFAULT 5,
                auto_mute BOOLEAN DEFAULT TRUE,
                mute_time INTEGER DEFAULT 60,
                max_length INTEGER DEFAULT 1000
            )
        ''')
        
        # Таблица админов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_admins (
                chat_id BIGINT,
                user_id BIGINT,
                username TEXT,
                added_by BIGINT,
                date_added TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
        ''')
        
        # Таблица нарушителей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS offenders (
                chat_id BIGINT,
                user_id BIGINT,
                username TEXT,
                warns INTEGER DEFAULT 0,
                last_offense TIMESTAMP,
                muted_until TIMESTAMP,
                join_time TIMESTAMP,
                last_reason TEXT,
                PRIMARY KEY (chat_id, user_id)
            )
        ''')
        
        # Таблица запрещенных слов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS ban_words (
                chat_id BIGINT,
                word TEXT,
                added_by BIGINT,
                PRIMARY KEY (chat_id, word)
            )
        ''')
        
        # Таблица логов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id SERIAL PRIMARY KEY,
                chat_id BIGINT,
                user_id BIGINT,
                username TEXT,
                action TEXT,
                reason TEXT,
                timestamp TIMESTAMP
            )
        ''')
        
        # Таблица приветствий
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS greetings (
                chat_id BIGINT PRIMARY KEY,
                message TEXT DEFAULT '👋 Добро пожаловать, {user}!'
            )
        ''')
        
        self.conn.commit()
        print("✅ Все таблицы созданы")
    
    def get_group_settings(self, chat_id):
        self.cursor.execute('SELECT * FROM group_settings WHERE chat_id = %s', (chat_id,))
        result = self.cursor.fetchone()
        
        if not result:
            self.cursor.execute('''
                INSERT INTO group_settings (chat_id) 
                VALUES (%s) RETURNING *
            ''', (chat_id,))
            result = self.cursor.fetchone()
        
        return dict(result)
    
    def update_setting(self, chat_id, setting, value):
        self.cursor.execute(f'UPDATE group_settings SET {setting} = %s WHERE chat_id = %s', (value, chat_id))
        self.conn.commit()
    
    def toggle_function(self, chat_id, function_name):
        current = self.get_group_settings(chat_id)[function_name]
        self.update_setting(chat_id, function_name, not current)
        return not current
    
    def is_group_admin(self, chat_id, user_id):
        if user_id == SUPER_ADMIN_ID:
            return True
        self.cursor.execute('SELECT * FROM group_admins WHERE chat_id = %s AND user_id = %s', (chat_id, user_id))
        return self.cursor.fetchone() is not None
    
    def add_group_admin(self, chat_id, user_id, username, added_by):
        self.cursor.execute('''
            INSERT INTO group_admins (chat_id, user_id, username, added_by, date_added)
            VALUES (%s, %s, %s, %s, %s)
            ON CONFLICT (chat_id, user_id) DO UPDATE
            SET username = EXCLUDED.username, added_by = EXCLUDED.added_by, date_added = EXCLUDED.date_added
        ''', (chat_id, user_id, username, added_by, datetime.now()))
        self.conn.commit()
    
    def remove_group_admin(self, chat_id, user_id):
        self.cursor.execute('DELETE FROM group_admins WHERE chat_id = %s AND user_id = %s', (chat_id, user_id))
        self.conn.commit()
    
    def get_group_admins(self, chat_id):
        self.cursor.execute('SELECT * FROM group_admins WHERE chat_id = %s', (chat_id,))
        result = self.cursor.fetchall()
        return [dict(row) for row in result]
    
    def get_ban_words(self, chat_id):
        self.cursor.execute('SELECT word FROM ban_words WHERE chat_id = %s', (chat_id,))
        return [row[0] for row in self.cursor.fetchall()]
    
    def add_ban_word(self, chat_id, word, added_by):
        self.cursor.execute('''
            INSERT INTO ban_words (chat_id, word, added_by)
            VALUES (%s, %s, %s)
            ON CONFLICT (chat_id, word) DO UPDATE
            SET added_by = EXCLUDED.added_by
        ''', (chat_id, word.lower(), added_by))
        self.conn.commit()
    
    def remove_ban_word(self, chat_id, word):
        self.cursor.execute('DELETE FROM ban_words WHERE chat_id = %s AND word = %s', (chat_id, word.lower()))
        self.conn.commit()
    
    def add_warn(self, chat_id, user_id, username, reason):
        # Получаем текущие данные нарушителя
        self.cursor.execute('SELECT * FROM offenders WHERE chat_id = %s AND user_id = %s', (chat_id, user_id))
        offender = self.cursor.fetchone()
        settings = self.get_group_settings(chat_id)
        
        if offender:
            new_warns = offender['warns'] + 1
            self.cursor.execute('''
                UPDATE offenders 
                SET warns = warns + 1, last_offense = %s, username = %s, last_reason = %s
                WHERE chat_id = %s AND user_id = %s
                RETURNING warns
            ''', (datetime.now(), username, reason, chat_id, user_id))
            new_warns = self.cursor.fetchone()[0]
        else:
            new_warns = 1
            self.cursor.execute('''
                INSERT INTO offenders (chat_id, user_id, username, warns, last_offense, join_time, last_reason)
                VALUES (%s, %s, %s, 1, %s, %s, %s)
            ''', (chat_id, user_id, username, datetime.now(), datetime.now(), reason))
        
        self.log_action(chat_id, user_id, username, 'WARN', reason)
        self.conn.commit()
        
        if settings['auto_mute'] and new_warns >= settings['warn_limit']:
            mute_until = datetime.now() + timedelta(seconds=settings['mute_time'])
            self.cursor.execute('''
                UPDATE offenders SET muted_until = %s 
                WHERE chat_id = %s AND user_id = %s
            ''', (mute_until, chat_id, user_id))
            self.conn.commit()
            return new_warns, mute_until
        
        return new_warns, None
    
    def mute_user(self, chat_id, user_id, username, minutes, reason="Ручной мут"):
        seconds = minutes * 60
        mute_until = datetime.now() + timedelta(seconds=seconds)
        
        self.cursor.execute('''
            INSERT INTO offenders (chat_id, user_id, username, warns, last_offense, join_time, muted_until, last_reason)
            VALUES (%s, %s, %s, 0, %s, %s, %s, %s)
            ON CONFLICT (chat_id, user_id) DO UPDATE
            SET muted_until = EXCLUDED.muted_until,
                last_offense = EXCLUDED.last_offense,
                username = EXCLUDED.username,
                last_reason = EXCLUDED.last_reason
        ''', (chat_id, user_id, username, datetime.now(), datetime.now(), mute_until, reason))
        
        self.log_action(chat_id, user_id, username, 'MUTE', f"{reason} на {minutes} мин")
        self.conn.commit()
        print(f"✅ Пользователь {username} замучен до {mute_until}")
        return mute_until
    
    def unmute_user(self, chat_id, user_id):
        self.cursor.execute('''
            UPDATE offenders SET muted_until = NULL 
            WHERE chat_id = %s AND user_id = %s
        ''', (chat_id, user_id))
        self.log_action(chat_id, user_id, "unknown", 'UNMUTE', "Снятие мута")
        self.conn.commit()
        print(f"✅ Пользователь {user_id} размучен")
    
    def get_offender(self, chat_id, user_id):
        self.cursor.execute('SELECT * FROM offenders WHERE chat_id = %s AND user_id = %s', (chat_id, user_id))
        result = self.cursor.fetchone()
        return dict(result) if result else None
    
    def is_muted(self, chat_id, user_id):
        self.cursor.execute('SELECT muted_until FROM offenders WHERE chat_id = %s AND user_id = %s', (chat_id, user_id))
        result = self.cursor.fetchone()
        
        if result and result[0]:
            mute_until = result[0]
            now = datetime.now()
            
            if mute_until.tzinfo:
                now = now.replace(tzinfo=mute_until.tzinfo)
            
            if now < mute_until:
                return True
            else:
                self.unmute_user(chat_id, user_id)
                return False
        return False
    
    def reset_warns(self, chat_id, user_id):
        self.cursor.execute('''
            UPDATE offenders SET warns = 0 
            WHERE chat_id = %s AND user_id = %s
        ''', (chat_id, user_id))
        self.unmute_user(chat_id, user_id)
        self.conn.commit()
    
    def log_action(self, chat_id, user_id, username, action, reason):
        self.cursor.execute('''
            INSERT INTO logs (chat_id, user_id, username, action, reason, timestamp)
            VALUES (%s, %s, %s, %s, %s, %s)
        ''', (chat_id, user_id, username, action, reason, datetime.now()))
        self.conn.commit()
    
    def get_logs(self, chat_id, limit=20):
        self.cursor.execute('''
            SELECT * FROM logs WHERE chat_id = %s 
            ORDER BY timestamp DESC LIMIT %s
        ''', (chat_id, limit))
        result = self.cursor.fetchall()
        return [dict(row) for row in result]
    
    def set_greeting(self, chat_id, message):
        self.cursor.execute('''
            INSERT INTO greetings (chat_id, message) VALUES (%s, %s)
            ON CONFLICT (chat_id) DO UPDATE SET message = EXCLUDED.message
        ''', (chat_id, message))
        self.conn.commit()
    
    def get_greeting(self, chat_id):
        self.cursor.execute('SELECT message FROM greetings WHERE chat_id = %s', (chat_id,))
        result = self.cursor.fetchone()
        return result[0] if result else "👋 Добро пожаловать, {user}!"

db = Database()

# ============================================
# КЛАСС АНТИСПАМА
# ============================================
class AntiSpam:
    def __init__(self):
        self.user_messages = defaultdict(list)
        
        self.warnings = {
            'flood': [
                "⚡ **ФЛУД!** {msgs} за {sec} сек!",
                "🤬 **ТЫ ЧЕ ТАК ЧАСТО ПИШЕШЬ?**",
                "🚫 **ФЛУД-КОНТРОЛЬ!**"
            ],
            'caps': [
                "🔇 **ХВАТИТ ОРАТЬ!**",
                "👂 **УШИ ЗАВЯЛИ!**",
                "📢 **СДЕЛАЙ ТИШЕ!**"
            ],
            'emoji': [
                "🎭 **ХВАТИТ СПАМИТЬ ЭМОДЗИ!**",
                "🎪 **ЦИРК УЕХАЛ!**"
            ],
            'repeat': [
                "🔄 **ХВАТИТ ПОВТОРЯТЬСЯ!**",
                "🔁 **ПОВТОР СООБЩЕНИЯ!**"
            ],
            'link': [
                "🔗 **НОВИЧКАМ НЕЛЬЗЯ ССЫЛКИ!**",
                "🚫 **ССЫЛКИ ЗАПРЕЩЕНЫ!**"
            ],
            'swear': [
                "🤬 **НЕ МАТЕРЬСЯ!**",
                "🚫 **ПЛОХИЕ СЛОВА ЗАПРЕЩЕНЫ!**"
            ]
        }
    
    def has_link(self, text):
        link_pattern = re.compile(r'(https?://|www\.)[^\s]+')
        return bool(link_pattern.search(text))
    
    def has_swear(self, text, ban_words):
        text_lower = text.lower()
        for word in ban_words:
            if word in text_lower:
                return True, word
        return False, None
    
    def count_emojis(self, text):
        emoji_pattern = re.compile("["
            u"\U0001F600-\U0001F64F"
            u"\U0001F300-\U0001F5FF"
            u"\U0001F680-\U0001F6FF"
            u"\U0001F1E0-\U0001F1FF"
            "]+", flags=re.UNICODE)
        return len(emoji_pattern.findall(text))
    
    def check_message(self, message):
        chat_id = message.chat.id
        user_id = message.from_user.id
        username = message.from_user.username or f"user_{user_id}"
        text = message.text or message.caption or ""
        
        settings = db.get_group_settings(chat_id)
        
        if not settings['enabled']:
            return True, None
        
        if db.is_group_admin(chat_id, user_id):
            return True, None
        
        # ПРОВЕРКА НА МУТ
        if db.is_muted(chat_id, user_id):
            return False, None  # Просто удаляем, без уведомления
        
        current_time = time.time()
        key = f"{chat_id}:{user_id}"
        
        offender = db.get_offender(chat_id, user_id)
        join_time = offender['join_time'] if offender else datetime.now()
        
        self.user_messages[key] = [msg for msg in self.user_messages[key] if current_time - msg['time'] < 60]
        
        if len(text) > settings['max_length']:
            return False, f"⚠️ **СЛИШКОМ ДЛИННО!** Макс: {settings['max_length']} симв."
        
        if settings['flood_enabled']:
            recent = [msg for msg in self.user_messages[key] if current_time - msg['time'] < settings['time_window']]
            if len(recent) >= settings['max_messages']:
                warns, mute = db.add_warn(chat_id, user_id, username, "Флуд")
                warning = random.choice(self.warnings['flood']).format(msgs=settings['max_messages'], sec=settings['time_window'])
                if mute:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']//60} мин!**"
                else:
                    warning += f"\n⚠️ **ПРЕДУПРЕЖДЕНИЕ:** {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['caps_enabled'] and len(text) > 5:
            upper = sum(1 for c in text if c.isupper()) / len(text) * 100
            if upper > settings['caps_limit']:
                warns, mute = db.add_warn(chat_id, user_id, username, "Капс")
                warning = random.choice(self.warnings['caps'])
                if mute:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']//60} мин!**"
                else:
                    warning += f"\n⚠️ **ПРЕДУПРЕЖДЕНИЕ:** {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['emoji_enabled']:
            emoji = self.count_emojis(text)
            if emoji > settings['emoji_limit']:
                warns, mute = db.add_warn(chat_id, user_id, username, "Эмодзи")
                warning = random.choice(self.warnings['emoji']) + f"\n😊 Эмодзи: {emoji}"
                if mute:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']//60} мин!**"
                else:
                    warning += f"\n⚠️ **ПРЕДУПРЕЖДЕНИЕ:** {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['repeat_enabled'] and len(self.user_messages[key]) >= 3:
            last = [msg['text'] for msg in self.user_messages[key][-3:]]
            if all(t == text for t in last):
                warns, mute = db.add_warn(chat_id, user_id, username, "Повтор")
                warning = random.choice(self.warnings['repeat'])
                if mute:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']//60} мин!**"
                else:
                    warning += f"\n⚠️ **ПРЕДУПРЕЖДЕНИЕ:** {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['links_enabled'] and self.has_link(text):
            if offender and (datetime.now() - join_time).total_seconds() < settings['link_kd'] * 60:
                warns, mute = db.add_warn(chat_id, user_id, username, "Ссылка")
                warning = random.choice(self.warnings['link']) + f"\n⏱️ ЖДИ {settings['link_kd']} МИН"
                if mute:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']//60} мин!**"
                else:
                    warning += f"\n⚠️ **ПРЕДУПРЕЖДЕНИЕ:** {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['swear_enabled']:
            ban_words = db.get_ban_words(chat_id)
            has_swear, word = self.has_swear(text, ban_words)
            if has_swear:
                warns, mute = db.add_warn(chat_id, user_id, username, "Мат")
                warning = random.choice(self.warnings['swear']) + f"\n🔴 Слово: {word}"
                if mute:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']//60} мин!**"
                else:
                    warning += f"\n⚠️ **ПРЕДУПРЕЖДЕНИЕ:** {warns}/{settings['warn_limit']}"
                return False, warning
        
        if text:
            self.user_messages[key].append({'text': text, 'time': current_time})
        
        return True, None

spam_filter = AntiSpam()

# ============================================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ============================================
def is_admin(chat_id, user_id):
    return db.is_group_admin(chat_id, user_id)

def get_username(user):
    return user.username or user.first_name or f"user_{user.id}"

# ============================================
# КОМАНДЫ БОТА
# ============================================
@bot.message_handler(commands=['start'])
def start(message):
    text = """
🦈 **SHARKYSPAM БОТ** 🔥

**🤖 ФУНКЦИИ:**
• Анти-флуд (4 за 3 сек)
• Анти-капс (>50%)
• Анти-эмодзи (>5)
• Анти-повторы
• Анти-ссылки для новичков
• Анти-мат (свой список)
• Авто-мут после N варнов
• Ручной мут (/mute)
• ⚡ **МГНОВЕННОЕ УДАЛЕНИЕ**

**👑 АДМИН КОМАНДЫ:**
/functions - вкл/выкл функции
/settings - настройки группы
/logs - логи нарушений
/mute [мин] - замутить (ответом)
/unmute - размутить (ответом)
/add_admin - добавить админа
/remove_admin - удалить админа
/admins - список админов
/reset_warns - сбросить варны
/add_banword - добавить слово в бан
/remove_banword - удалить слово из бана
/banwords - список запрещенных слов
/greeting - установить приветствие
/antispam_on - включить антиспам
/antispam_off - выключить антиспам
/set_max_msgs - макс сообщений
/set_time - временное окно (сек)
/set_caps - лимит капса (%)
/set_emoji - лимит эмодзи
/set_link_kd - задержка для ссылок (мин)
/set_warn_limit - лимит предупреждений
/set_mute_time - время мута (мин)
/set_max_len - макс длина сообщения
/check_mute - проверить статус мута
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['help'])
def help_command(message):
    start(message)

@bot.message_handler(commands=['check_mute'])
def check_mute(message):
    """Проверить статус мута (только для админов)"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ Ответь на сообщение пользователя!")
        return
    
    target = message.reply_to_message.from_user
    offender = db.get_offender(chat_id, target.id)
    
    if offender and offender['muted_until']:
        mute_until = offender['muted_until']
        now = datetime.now()
        
        if mute_until.tzinfo:
            now = now.replace(tzinfo=mute_until.tzinfo)
        
        remaining_seconds = int((mute_until - now).total_seconds())
        remaining_minutes = remaining_seconds / 60
        
        bot.reply_to(message, 
            f"🔇 Статус @{get_username(target)}:\n"
            f"Мут до: {mute_until}\n"
            f"Осталось секунд: {remaining_seconds}\n"
            f"Осталось минут: {remaining_minutes:.2f}\n"
            f"Причина: {offender.get('last_reason', 'неизвестно')}")
    else:
        bot.reply_to(message, f"✅ @{get_username(target)} не в муте")

@bot.message_handler(commands=['functions'])
def functions_menu(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    settings = db.get_group_settings(chat_id)
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = [
        types.InlineKeyboardButton(f"{'✅' if settings['flood_enabled'] else '❌'} Флуд", callback_data="toggle_flood"),
        types.InlineKeyboardButton(f"{'✅' if settings['caps_enabled'] else '❌'} Капс", callback_data="toggle_caps"),
        types.InlineKeyboardButton(f"{'✅' if settings['emoji_enabled'] else '❌'} Эмодзи", callback_data="toggle_emoji"),
        types.InlineKeyboardButton(f"{'✅' if settings['repeat_enabled'] else '❌'} Повторы", callback_data="toggle_repeat"),
        types.InlineKeyboardButton(f"{'✅' if settings['links_enabled'] else '❌'} Ссылки", callback_data="toggle_links"),
        types.InlineKeyboardButton(f"{'✅' if settings['swear_enabled'] else '❌'} Бан-слова", callback_data="toggle_swear"),
    ]
    markup.add(*buttons)
    
    bot.reply_to(message, "🔧 **УПРАВЛЕНИЕ ФУНКЦИЯМИ**\nНажми чтобы вкл/выкл:", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['settings'])
def settings_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    settings = db.get_group_settings(chat_id)
    mute_minutes = settings['mute_time'] // 60
    
    def escape_md(text):
        return str(text).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
    
    text = f"""
⚙️ **НАСТРОЙКИ ГРУППЫ** ⚙️

📌 **ОСНОВНЫЕ:**
• Флуд: {escape_md(settings['max_messages'])} за {escape_md(settings['time_window'])} сек
• Капс: >{escape_md(settings['caps_limit'])}%
• Эмодзи: >{escape_md(settings['emoji_limit'])}
• Ссылки: кд {escape_md(settings['link_kd'])} мин
• Лимит варнов: {escape_md(settings['warn_limit'])}
• Время мута: {escape_md(mute_minutes)} мин
• Макс длина: {escape_md(settings['max_length'])} симв.
• ⚡ Режим удаления: **МГНОВЕННЫЙ**
• Статус: {'✅ Вкл' if settings['enabled'] else '❌ Выкл'}

📝 **КОМАНДЫ ДЛЯ ИЗМЕНЕНИЯ:**
/set_max_msgs [1-20]
/set_time [1-10]
/set_caps [0-100]
/set_emoji [0-20]
/set_link_kd [0-60]
/set_warn_limit [1-10]
/set_mute_time [1-60] (МИНУТЫ)
/set_max_len [10-5000]
/greeting [текст]
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['logs'])
def logs_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    logs = db.get_logs(chat_id, 10)
    if not logs:
        bot.reply_to(message, "📝 Логов пока нет")
        return
    
    text = "📋 **ПОСЛЕДНИЕ ДЕЙСТВИЯ:**\n\n"
    for log in logs:
        emoji = "⚠️" if log['action'] == 'WARN' else "🔇" if log['action'] == 'MUTE' else "✅"
        text += f"{emoji} @{log['username']}: {log['reason']}\n   🕒 {log['timestamp'].strftime('%Y-%m-%d %H:%M') if log['timestamp'] else 'неизвестно'}\n\n"
    
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['mute'])
def mute_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ Ответь на сообщение пользователя!")
        return
    
    try:
        parts = message.text.split()
        minutes = 1 if len(parts) < 2 else max(1, min(int(parts[1]), 1440))
        
        target = message.reply_to_message.from_user
        target_name = get_username(target)
        
        # Мутим
        mute_until = db.mute_user(chat_id, target.id, target_name, minutes,
                                  f"Ручной мут от @{get_username(message.from_user)}")
        
        # Удаляем сообщение с командой
        try:
            bot.delete_message(chat_id, message.message_id)
        except:
            pass
        
        # Удаляем сообщение нарушителя
        try:
            bot.delete_message(chat_id, message.reply_to_message.message_id)
        except:
            pass
        
        # Уведомление
        bot.send_message(chat_id, f"🔇 @{target_name} замучен на {minutes} мин!")
        
    except:
        bot.reply_to(message, "❌ Ошибка! Используй: /mute [минуты]")

@bot.message_handler(commands=['unmute'])
def unmute_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ Ответь на сообщение пользователя!")
        return
    
    target = message.reply_to_message.from_user
    db.unmute_user(chat_id, target.id)
    bot.reply_to(message, f"✅ @{get_username(target)} размучен!")

@bot.message_handler(commands=['add_admin'])
def add_admin(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ Ответь на сообщение пользователя!")
        return
    
    new_admin = message.reply_to_message.from_user
    db.add_group_admin(chat_id, new_admin.id, get_username(new_admin), user_id)
    bot.reply_to(message, f"✅ @{get_username(new_admin)} теперь админ!")

@bot.message_handler(commands=['remove_admin'])
def remove_admin(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ Ответь на сообщение пользователя!")
        return
    
    admin = message.reply_to_message.from_user
    db.remove_group_admin(chat_id, admin.id)
    bot.reply_to(message, f"✅ @{get_username(admin)} больше не админ")

@bot.message_handler(commands=['admins'])
def admins_command(message):
    chat_id = message.chat.id
    
    admins = db.get_group_admins(chat_id)
    
    if not admins:
        bot.reply_to(message, "📝 Админов пока нет")
        return
    
    text = "👑 **АДМИНЫ БОТА:**\n"
    for admin in admins:
        text += f"• @{admin['username']}\n"
    
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['reset_warns'])
def reset_warns(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ Ответь на сообщение пользователя!")
        return
    
    target = message.reply_to_message.from_user
    db.reset_warns(chat_id, target.id)
    bot.reply_to(message, f"✅ Предупреждения сброшены для @{get_username(target)}")

@bot.message_handler(commands=['add_banword'])
def add_banword(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    try:
        word = message.text.split()[1].lower()
        db.add_ban_word(chat_id, word, user_id)
        bot.reply_to(message, f"🚫 Слово '{word}' добавлено в бан-лист!")
    except:
        bot.reply_to(message, "❌ Использование: /add_banword слово")

@bot.message_handler(commands=['remove_banword'])
def remove_banword(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    try:
        word = message.text.split()[1].lower()
        db.remove_ban_word(chat_id, word)
        bot.reply_to(message, f"✅ Слово '{word}' удалено из бан-листа!")
    except:
        bot.reply_to(message, "❌ Использование: /remove_banword слово")

@bot.message_handler(commands=['banwords'])
def banwords(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    words = db.get_ban_words(chat_id)
    if words:
        text = "🚫 **БАН-СЛОВА:**\n" + "\n".join([f"• {w}" for w in words])
    else:
        text = "📝 Бан-слов пока нет"
    
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['greeting'])
def greeting_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    try:
        greeting = message.text.split(maxsplit=1)[1]
        db.set_greeting(chat_id, greeting)
        bot.reply_to(message, f"✅ Приветствие установлено:\n{greeting}")
    except:
        bot.reply_to(message, "❌ Использование: /greeting [текст] (используй {user} для имени")

@bot.message_handler(commands=['antispam_on'])
def antispam_on(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    db.update_setting(chat_id, 'enabled', True)
    bot.reply_to(message, "🟢 **АНТИСПАМ ВКЛЮЧЕН!**", parse_mode='Markdown')

@bot.message_handler(commands=['antispam_off'])
def antispam_off(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    db.update_setting(chat_id, 'enabled', False)
    bot.reply_to(message, "🔴 **АНТИСПАМ ВЫКЛЮЧЕН!**", parse_mode='Markdown')

# Настройки параметров
@bot.message_handler(commands=['set_max_msgs'])
def set_max_msgs(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        val = int(message.text.split()[1])
        if 1 <= val <= 20:
            db.update_setting(chat_id, 'max_messages', val)
            bot.reply_to(message, f"✅ Макс сообщений: {val}")
    except:
        bot.reply_to(message, "❌ /set_max_msgs [1-20]")

@bot.message_handler(commands=['set_time'])
def set_time(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        val = int(message.text.split()[1])
        if 1 <= val <= 10:
            db.update_setting(chat_id, 'time_window', val)
            bot.reply_to(message, f"✅ Время: {val} сек")
    except:
        bot.reply_to(message, "❌ /set_time [1-10]")

@bot.message_handler(commands=['set_caps'])
def set_caps(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        val = int(message.text.split()[1])
        if 0 <= val <= 100:
            db.update_setting(chat_id, 'caps_limit', val)
            bot.reply_to(message, f"✅ Капс: {val}%")
    except:
        bot.reply_to(message, "❌ /set_caps [0-100]")

@bot.message_handler(commands=['set_emoji'])
def set_emoji(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        val = int(message.text.split()[1])
        if 0 <= val <= 20:
            db.update_setting(chat_id, 'emoji_limit', val)
            bot.reply_to(message, f"✅ Эмодзи: {val}")
    except:
        bot.reply_to(message, "❌ /set_emoji [0-20]")

@bot.message_handler(commands=['set_link_kd'])
def set_link_kd(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        val = int(message.text.split()[1])
        if 0 <= val <= 60:
            db.update_setting(chat_id, 'link_kd', val)
            bot.reply_to(message, f"✅ Кд ссылок: {val} мин")
    except:
        bot.reply_to(message, "❌ /set_link_kd [0-60]")

@bot.message_handler(commands=['set_warn_limit'])
def set_warn_limit(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        val = int(message.text.split()[1])
        if 1 <= val <= 10:
            db.update_setting(chat_id, 'warn_limit', val)
            bot.reply_to(message, f"✅ Лимит варнов: {val}")
    except:
        bot.reply_to(message, "❌ /set_warn_limit [1-10]")

@bot.message_handler(commands=['set_mute_time'])
def set_mute_time(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        minutes = int(message.text.split()[1])
        if 1 <= minutes <= 60:
            db.update_setting(chat_id, 'mute_time', minutes * 60)
            bot.reply_to(message, f"✅ Время мута: {minutes} мин")
    except:
        bot.reply_to(message, "❌ /set_mute_time [1-60] (МИНУТЫ)")

@bot.message_handler(commands=['set_max_len'])
def set_max_len(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        val = int(message.text.split()[1])
        if 10 <= val <= 5000:
            db.update_setting(chat_id, 'max_length', val)
            bot.reply_to(message, f"✅ Макс длина: {val} симв.")
    except:
        bot.reply_to(message, "❌ /set_max_len [10-5000]")

# ============================================
# CALLBACK HANDLERS
# ============================================
@bot.callback_query_handler(func=lambda call: True)
def handle_callback(call):
    chat_id = call.message.chat.id
    user_id = call.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.answer_callback_query(call.id, "❌ Только админы!")
        return
    
    if call.data.startswith('toggle_'):
        function = call.data.replace('toggle_', '') + '_enabled'
        new_state = db.toggle_function(chat_id, function)
        status = "✅ ВКЛ" if new_state else "❌ ВЫКЛ"
        bot.answer_callback_query(call.id, f"Функция {status}")
        functions_menu(call.message)

# ============================================
# ОБРАБОТЧИКИ СООБЩЕНИЙ
# ============================================
@bot.message_handler(content_types=['new_chat_members'])
def welcome_new(message):
    chat_id = message.chat.id
    settings = db.get_group_settings(chat_id)
    
    for member in message.new_chat_members:
        if member.id == bot.get_me().id:
            bot.reply_to(message, 
                "🦈 **SHARKYSPAM БОТ АКТИВИРОВАН!**\n"
                "👑 /functions - управление\n"
                "🔨 /mute - ручной мут\n"
                "⚙️ /settings - настройки\n"
                "⚡ **МГНОВЕННОЕ УДАЛЕНИЕ СООБЩЕНИЙ**",
                parse_mode='Markdown'
            )
            creator = message.from_user
            db.add_group_admin(chat_id, creator.id, get_username(creator), SUPER_ADMIN_ID)
        
        elif settings['welcome_enabled']:
            greeting = db.get_greeting(chat_id).replace('{user}', f"@{get_username(member)}")
            bot.reply_to(message, greeting, parse_mode='Markdown')

@bot.message_handler(content_types=['text', 'photo', 'video', 'document'])
def handle_message(message):
    if message.text and message.text.startswith('/'):
        return
    
    if message.chat.type == 'private':
        bot.reply_to(message, "🦈 Добавь меня в группу!")
        return
    
    is_allowed, warning = spam_filter.check_message(message)
    
    if not is_allowed:
        # Мгновенное удаление через очередь
        delete_queue.put((message.chat.id, message.message_id))
        
        # Если есть предупреждение, отправляем его
        if warning:
            bot.send_message(message.chat.id, warning)

# ============================================
# ЗАПУСК НА RENDER
# ============================================
@app.route('/')
def home():
    return "🦈 SHARKYSPAM БОТ РАБОТАЕТ! ⚡ МГНОВЕННОЕ УДАЛЕНИЕ", 200

@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    return 'Wrong content type', 403

def set_webhook():
    print("🔄 Настройка вебхука...")
    RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
    if not RENDER_URL:
        print("❌ Нет RENDER_EXTERNAL_URL!")
        return False
    
    webhook_url = f"{RENDER_URL}/{TOKEN}"
    bot.remove_webhook()
    time.sleep(1)
    bot.set_webhook(url=webhook_url)
    
    me = bot.get_me()
    print(f"✅ Бот @{me.username} запущен! ⚡ Режим мгновенного удаления активен")
    return True

if __name__ == '__main__':
    print("🔥 ЗАПУСК SHARKYSPAM БОТА")
    print("⚡ Режим мгновенного удаления активирован")
    set_webhook()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)