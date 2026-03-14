import telebot
from telebot import types
import time
from collections import defaultdict
import re
import random
import sqlite3
import os
from datetime import datetime, timedelta
from flask import Flask, request

# ============================================
# НАСТРОЙКИ
# ============================================
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise ValueError("❌ Нет токена! Добавь BOT_TOKEN в переменные окружения!")

SUPER_ADMIN_ID = int(os.environ.get('SUPER_ADMIN_ID', 123456789))

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ============================================
# РАСШИРЕННАЯ БАЗА ДАННЫХ
# ============================================
class Database:
    def __init__(self):
        db_path = os.path.join('/tmp', 'antispam.db')
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        # Расширенные настройки групп
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT 1,
                
                -- ФУНКЦИИ (ВКЛ/ВЫКЛ)
                flood_enabled BOOLEAN DEFAULT 1,
                caps_enabled BOOLEAN DEFAULT 1,
                emoji_enabled BOOLEAN DEFAULT 1,
                repeat_enabled BOOLEAN DEFAULT 1,
                links_enabled BOOLEAN DEFAULT 1,
                swear_enabled BOOLEAN DEFAULT 1,
                media_enabled BOOLEAN DEFAULT 1,
                welcome_enabled BOOLEAN DEFAULT 1,
                
                -- НАСТРОЙКИ ФЛУДА
                max_messages INTEGER DEFAULT 4,
                time_window INTEGER DEFAULT 3,
                
                -- НАСТРОЙКИ КАПСА
                caps_limit INTEGER DEFAULT 50,
                
                -- НАСТРОЙКИ ЭМОДЗИ
                emoji_limit INTEGER DEFAULT 5,
                
                -- НАСТРОЙКИ ССЫЛОК
                link_kd INTEGER DEFAULT 10,  -- минут для новичков
                
                -- НАСТРОЙКИ МЕДИА
                media_limit INTEGER DEFAULT 3,  -- макс медиа за 5 сек
                
                -- СИСТЕМА НАКАЗАНИЙ
                warn_limit INTEGER DEFAULT 5,
                auto_mute BOOLEAN DEFAULT 1,
                mute_time INTEGER DEFAULT 60,  -- секунд
                
                -- ДРУГОЕ (ТОЛЬКО МАКСИМУМ)
                max_length INTEGER DEFAULT 1000
            )
        ''')
        
        # Таблица админов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_admins (
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                added_by INTEGER,
                date_added TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
        ''')
        
        # Таблица нарушителей
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS offenders (
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                warns INTEGER DEFAULT 0,
                last_offense TIMESTAMP,
                muted_until TIMESTAMP,
                join_time TIMESTAMP,
                PRIMARY KEY (chat_id, user_id)
            )
        ''')
        
        # Таблица запрещенных слов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS ban_words (
                chat_id INTEGER,
                word TEXT,
                added_by INTEGER,
                PRIMARY KEY (chat_id, word)
            )
        ''')
        
        # Таблица логов
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                action TEXT,
                reason TEXT,
                timestamp TIMESTAMP
            )
        ''')
        
        # Таблица приветствий
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS greetings (
                chat_id INTEGER PRIMARY KEY,
                message TEXT DEFAULT "👋 Добро пожаловать, {user}!"
            )
        ''')
        
        self.conn.commit()
    
    def get_group_settings(self, chat_id):
        self.cursor.execute('SELECT * FROM group_settings WHERE chat_id = ?', (chat_id,))
        result = self.cursor.fetchone()
        
        if not result:
            self.cursor.execute('''
                INSERT INTO group_settings (chat_id) VALUES (?)
            ''', (chat_id,))
            self.conn.commit()
            
            self.cursor.execute('SELECT * FROM group_settings WHERE chat_id = ?', (chat_id,))
            result = self.cursor.fetchone()
        
        columns = [description[0] for description in self.cursor.description]
        return dict(zip(columns, result))
    
    def update_setting(self, chat_id, setting, value):
        self.cursor.execute(f'UPDATE group_settings SET {setting} = ? WHERE chat_id = ?', (value, chat_id))
        self.conn.commit()
    
    def toggle_function(self, chat_id, function_name):
        """Включить/выключить функцию"""
        current = self.get_group_settings(chat_id)[function_name]
        self.update_setting(chat_id, function_name, 0 if current else 1)
        return not current
    
    def is_group_admin(self, chat_id, user_id):
        if user_id == SUPER_ADMIN_ID:
            return True
        self.cursor.execute('SELECT * FROM group_admins WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        return self.cursor.fetchone() is not None
    
    def add_group_admin(self, chat_id, user_id, username, added_by):
        self.cursor.execute('''
            INSERT OR REPLACE INTO group_admins (chat_id, user_id, username, added_by, date_added)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, user_id, username, added_by, datetime.now()))
        self.conn.commit()
    
    def remove_group_admin(self, chat_id, user_id):
        self.cursor.execute('DELETE FROM group_admins WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        self.conn.commit()
    
    def get_group_admins(self, chat_id):
        self.cursor.execute('SELECT * FROM group_admins WHERE chat_id = ?', (chat_id,))
        result = self.cursor.fetchall()
        columns = [description[0] for description in self.cursor.description]
        return [dict(zip(columns, row)) for row in result]
    
    def get_ban_words(self, chat_id):
        self.cursor.execute('SELECT word FROM ban_words WHERE chat_id = ?', (chat_id,))
        return [row[0] for row in self.cursor.fetchall()]
    
    def add_ban_word(self, chat_id, word, added_by):
        self.cursor.execute('''
            INSERT OR REPLACE INTO ban_words (chat_id, word, added_by)
            VALUES (?, ?, ?)
        ''', (chat_id, word.lower(), added_by))
        self.conn.commit()
    
    def remove_ban_word(self, chat_id, word):
        self.cursor.execute('DELETE FROM ban_words WHERE chat_id = ? AND word = ?', (chat_id, word.lower()))
        self.conn.commit()
    
    def add_warn(self, chat_id, user_id, username, reason):
        offender = self.get_offender(chat_id, user_id)
        settings = self.get_group_settings(chat_id)
        
        if offender:
            self.cursor.execute('''
                UPDATE offenders 
                SET warns = warns + 1, last_offense = ?, username = ?
                WHERE chat_id = ? AND user_id = ?
            ''', (datetime.now(), username, chat_id, user_id))
            new_warns = offender['warns'] + 1
        else:
            self.cursor.execute('''
                INSERT INTO offenders (chat_id, user_id, username, warns, last_offense, join_time)
                VALUES (?, ?, ?, 1, ?, ?)
            ''', (chat_id, user_id, username, datetime.now(), datetime.now()))
            new_warns = 1
        
        # Логируем
        self.log_action(chat_id, user_id, username, 'WARN', reason)
        self.conn.commit()
        
        # Проверяем на авто-мут
        if settings['auto_mute'] and new_warns >= settings['warn_limit']:
            mute_until = datetime.now() + timedelta(seconds=settings['mute_time'])
            self.cursor.execute('''
                UPDATE offenders SET muted_until = ? WHERE chat_id = ? AND user_id = ?
            ''', (mute_until, chat_id, user_id))
            self.conn.commit()
            return new_warns, mute_until
        
        return new_warns, None
    
    def get_offender(self, chat_id, user_id):
        self.cursor.execute('SELECT * FROM offenders WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        result = self.cursor.fetchone()
        if result:
            columns = [description[0] for description in self.cursor.description]
            return dict(zip(columns, result))
        return None
    
    def is_muted(self, chat_id, user_id):
        offender = self.get_offender(chat_id, user_id)
        if offender and offender['muted_until']:
            return datetime.now() < datetime.fromisoformat(offender['muted_until'])
        return False
    
    def reset_warns(self, chat_id, user_id):
        self.cursor.execute('DELETE FROM offenders WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        self.conn.commit()
    
    def log_action(self, chat_id, user_id, username, action, reason):
        self.cursor.execute('''
            INSERT INTO logs (chat_id, user_id, username, action, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (chat_id, user_id, username, action, reason, datetime.now()))
        self.conn.commit()
    
    def get_logs(self, chat_id, limit=20):
        self.cursor.execute('''
            SELECT * FROM logs WHERE chat_id = ? ORDER BY timestamp DESC LIMIT ?
        ''', (chat_id, limit))
        result = self.cursor.fetchall()
        columns = [description[0] for description in self.cursor.description]
        return [dict(zip(columns, row)) for row in result]
    
    def set_greeting(self, chat_id, message):
        self.cursor.execute('''
            INSERT OR REPLACE INTO greetings (chat_id, message) VALUES (?, ?)
        ''', (chat_id, message))
        self.conn.commit()
    
    def get_greeting(self, chat_id):
        self.cursor.execute('SELECT message FROM greetings WHERE chat_id = ?', (chat_id,))
        result = self.cursor.fetchone()
        return result[0] if result else "👋 Добро пожаловать, {user}!"

db = Database()

# ============================================
# КЛАСС АНТИСПАМА
# ============================================
class UltimateAntiSpam:
    def __init__(self):
        self.user_messages = defaultdict(list)
        self.user_media = defaultdict(list)
        
        # Предупреждения
        self.warnings = {
            'flood': [
                "⚡ **ФЛУД!** {msgs} за {sec} сек",
                "🤬 **ХВАТИТ СПАМИТЬ!**",
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
            ],
            'media': [
                "📸 **ХВАТИТ СПАМИТЬ МЕДИА!**",
                "🎥 **НЕ ТАК МНОГО ФОТО!**"
            ]
        }
    
    def has_link(self, text):
        """Проверяет наличие ссылки"""
        link_pattern = re.compile(r'(https?://|www\.)[^\s]+')
        return bool(link_pattern.search(text))
    
    def has_swear(self, text, ban_words):
        """Проверяет наличие мата"""
        text_lower = text.lower()
        for word in ban_words:
            if word in text_lower:
                return True, word
        return False, None
    
    def count_emojis(self, text):
        """Считает эмодзи"""
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
        
        # Проверка включен ли антиспам
        if not settings['enabled']:
            return True, None
        
        # Пропускаем админов
        if db.is_group_admin(chat_id, user_id):
            return True, None
        
        # Проверка на мут
        if db.is_muted(chat_id, user_id):
            offender = db.get_offender(chat_id, user_id)
            mute_until = datetime.fromisoformat(offender['muted_until'])
            remaining = int((mute_until - datetime.now()).total_seconds())
            return False, f"🔇 **Вы в муте!** Осталось: {remaining} сек"
        
        current_time = time.time()
        key = f"{chat_id}:{user_id}"
        
        # Получаем или создаем нарушителя
        offender = db.get_offender(chat_id, user_id)
        join_time = datetime.fromisoformat(offender['join_time']) if offender else datetime.now()
        
        # Очищаем старые сообщения
        self.user_messages[key] = [
            msg for msg in self.user_messages[key] 
            if current_time - msg['time'] < 60
        ]
        
        # ПРОВЕРКА ДЛИНЫ (ТОЛЬКО МАКСИМУМ)
        if len(text) > settings['max_length']:
            return False, f"⚠️ Слишком длинно! (макс. {settings['max_length']} симв.)"
        
        # 1. ПРОВЕРКА ФЛУДА
        if settings['flood_enabled']:
            recent_messages = [
                msg for msg in self.user_messages[key] 
                if current_time - msg['time'] < settings['time_window']
            ]
            
            if len(recent_messages) >= settings['max_messages']:
                warns, mute_until = db.add_warn(chat_id, user_id, username, f"Флуд ({len(recent_messages)} за {settings['time_window']}с)")
                warning = random.choice(self.warnings['flood']).format(msgs=settings['max_messages'], sec=settings['time_window'])
                
                if mute_until:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']} сек!**"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                
                return False, warning
        
        # 2. ПРОВЕРКА КАПСА
        if settings['caps_enabled'] and len(text) > 5:
            upper_count = sum(1 for c in text if c.isupper())
            upper_percent = (upper_count / len(text)) * 100
            
            if upper_percent > settings['caps_limit']:
                warns, mute_until = db.add_warn(chat_id, user_id, username, f"Капс ({upper_percent:.0f}%)")
                warning = random.choice(self.warnings['caps'])
                
                if mute_until:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']} сек!**"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                
                return False, warning
        
        # 3. ПРОВЕРКА ЭМОДЗИ
        if settings['emoji_enabled']:
            emoji_count = self.count_emojis(text)
            if emoji_count > settings['emoji_limit']:
                warns, mute_until = db.add_warn(chat_id, user_id, username, f"Эмодзи ({emoji_count})")
                warning = random.choice(self.warnings['emoji']) + f"\n😊 Эмодзи: {emoji_count}"
                
                if mute_until:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']} сек!**"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                
                return False, warning
        
        # 4. ПРОВЕРКА ПОВТОРОВ
        if settings['repeat_enabled'] and len(self.user_messages[key]) >= 3:
            last_texts = [msg['text'] for msg in self.user_messages[key][-3:]]
            if all(t == text for t in last_texts):
                warns, mute_until = db.add_warn(chat_id, user_id, username, "Повтор сообщения")
                warning = random.choice(self.warnings['repeat'])
                
                if mute_until:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']} сек!**"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                
                return False, warning
        
        # 5. ПРОВЕРКА ССЫЛОК
        if settings['links_enabled'] and self.has_link(text):
            # Проверка на новичков
            if offender and (datetime.now() - join_time).total_seconds() < settings['link_kd'] * 60:
                warns, mute_until = db.add_warn(chat_id, user_id, username, "Ссылка (новичок)")
                warning = random.choice(self.warnings['link']) + f"\n⏱️ Подождите {settings['link_kd']} мин"
                
                if mute_until:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']} сек!**"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                
                return False, warning
        
        # 6. ПРОВЕРКА МАТА
        if settings['swear_enabled']:
            ban_words = db.get_ban_words(chat_id)
            has_swear, found_word = self.has_swear(text, ban_words)
            if has_swear:
                warns, mute_until = db.add_warn(chat_id, user_id, username, f"Мат: {found_word}")
                warning = random.choice(self.warnings['swear']) + f"\n🔴 Слово: {found_word}"
                
                if mute_until:
                    warning += f"\n🔇 **АВТО-МУТ на {settings['mute_time']} сек!**"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                
                return False, warning
        
        # Сохраняем сообщение
        if text:
            self.user_messages[key].append({
                'text': text,
                'time': current_time
            })
        
        return True, None

spam_filter = UltimateAntiSpam()

# ============================================
# КОМАНДЫ БОТА
# ============================================
def is_admin(chat_id, user_id):
    return db.is_group_admin(chat_id, user_id)

@bot.message_handler(commands=['start'])
def start(message):
    text = """
🔥 **ULTIMATE ANTISPAM БОТ** 🔥

**🤖 Функции:**
• 🚫 Флуд (4 за 3 сек)
• 🔇 Капс (>50%)
• 😊 Эмодзи (>5)
• 🔁 Повторы
• 🔗 Ссылки для новичков
• 🤬 Бан-слова
• 📸 Медиа-флуд
• 👋 Приветствие
• 🔨 Авто-мут

**👑 Админ команды:**
/functions - вкл/выкл функции
/settings - настройки
/logs - логи нарушений
/add_admin - добавить админа
    """
    bot.reply_to(message, text, parse_mode='Markdown')

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
        types.InlineKeyboardButton(
            f"{'✅' if settings['flood_enabled'] else '❌'} Флуд", 
            callback_data=f"toggle_flood"
        ),
        types.InlineKeyboardButton(
            f"{'✅' if settings['caps_enabled'] else '❌'} Капс", 
            callback_data=f"toggle_caps"
        ),
        types.InlineKeyboardButton(
            f"{'✅' if settings['emoji_enabled'] else '❌'} Эмодзи", 
            callback_data=f"toggle_emoji"
        ),
        types.InlineKeyboardButton(
            f"{'✅' if settings['repeat_enabled'] else '❌'} Повторы", 
            callback_data=f"toggle_repeat"
        ),
        types.InlineKeyboardButton(
            f"{'✅' if settings['links_enabled'] else '❌'} Ссылки", 
            callback_data=f"toggle_links"
        ),
        types.InlineKeyboardButton(
            f"{'✅' if settings['swear_enabled'] else '❌'} Бан-слова", 
            callback_data=f"toggle_swear"
        ),
        types.InlineKeyboardButton(
            f"{'✅' if settings['media_enabled'] else '❌'} Медиа", 
            callback_data=f"toggle_media"
        ),
        types.InlineKeyboardButton(
            f"{'✅' if settings['welcome_enabled'] else '❌'} Приветствие", 
            callback_data=f"toggle_welcome"
        ),
        types.InlineKeyboardButton(
            f"{'✅' if settings['auto_mute'] else '❌'} Авто-мут", 
            callback_data=f"toggle_mute"
        ),
        types.InlineKeyboardButton("📊 Главное меню", callback_data="main_menu")
    ]
    markup.add(*buttons)
    
    bot.reply_to(message, "🔧 **УПРАВЛЕНИЕ ФУНКЦИЯМИ**\nНажми чтобы вкл/выкл:", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['settings'])
def settings_menu(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    settings = db.get_group_settings(chat_id)
    
    text = f"""
⚙️ **ТЕКУЩИЕ НАСТРОЙКИ:**

📌 **ФЛУД:** {settings['max_messages']} за {settings['time_window']}с
🔇 **КАПС:** >{settings['caps_limit']}%
😊 **ЭМОДЗИ:** >{settings['emoji_limit']}
🔗 **ССЫЛКИ:** кд {settings['link_kd']} мин
📸 **МЕДИА:** {settings['media_limit']} за 5с
⚠️ **ВАРНЫ:** {settings['warn_limit']}
🔨 **МУТ:** {settings['mute_time']} сек
📏 **МАКС ДЛИНА:** {settings['max_length']} симв.

**Команды для изменения:**
/set_max_msgs [число]
/set_time [сек]
/set_caps [%]
/set_emoji [число]
/set_link_kd [мин]
/set_media_limit [число]
/set_warn_limit [число]
/set_mute_time [сек]
/set_max_len [число]
/greeting [текст]
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['logs'])
def show_logs(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    logs = db.get_logs(chat_id, 10)
    
    if not logs:
        bot.reply_to(message, "📝 **Логов пока нет**")
        return
    
    text = "📋 **ПОСЛЕДНИЕ НАРУШЕНИЯ:**\n\n"
    for log in logs:
        text += f"• @{log['username']}: {log['reason']}\n  ({log['timestamp'][:19]})\n"
    
    bot.reply_to(message, text, parse_mode='Markdown')

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
    db.add_group_admin(chat_id, new_admin.id, new_admin.username or "NoName", user_id)
    bot.reply_to(message, f"✅ @{new_admin.username or 'NoName'} теперь админ!")

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
    bot.reply_to(message, f"✅ @{admin.username or 'NoName'} больше не админ")

@bot.message_handler(commands=['admins'])
def list_admins(message):
    chat_id = message.chat.id
    
    admins = db.get_group_admins(chat_id)
    
    if not admins:
        bot.reply_to(message, "📝 Админов пока нет")
        return
    
    text = "👑 **АДМИНЫ:**\n"
    for admin in admins:
        text += f"• @{admin['username']}\n"
    
    bot.reply_to(message, text, parse_mode='Markdown')

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
        bot.reply_to(message, f"🚫 Слово '{word}' добавлено!")
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
        bot.reply_to(message, f"✅ Слово '{word}' удалено!")
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
def set_greeting(message):
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
        bot.reply_to(message, "❌ Использование: /greeting [текст]\nИспользуй {user} для имени")

# Настройки чисел
@bot.message_handler(commands=['set_max_msgs'])
def set_max_msgs(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): return
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
    if not is_admin(chat_id, user_id): return
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
    if not is_admin(chat_id, user_id): return
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
    if not is_admin(chat_id, user_id): return
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
    if not is_admin(chat_id, user_id): return
    try:
        val = int(message.text.split()[1])
        if 0 <= val <= 60:
            db.update_setting(chat_id, 'link_kd', val)
            bot.reply_to(message, f"✅ Кд ссылок: {val} мин")
    except:
        bot.reply_to(message, "❌ /set_link_kd [0-60]")

@bot.message_handler(commands=['set_media_limit'])
def set_media_limit(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): return
    try:
        val = int(message.text.split()[1])
        if 1 <= val <= 10:
            db.update_setting(chat_id, 'media_limit', val)
            bot.reply_to(message, f"✅ Медиа лимит: {val}")
    except:
        bot.reply_to(message, "❌ /set_media_limit [1-10]")

@bot.message_handler(commands=['set_warn_limit'])
def set_warn_limit(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): return
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
    if not is_admin(chat_id, user_id): return
    try
