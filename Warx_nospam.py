import telebot
from telebot import types
import time
from collections import defaultdict
import re
import random
import sqlite3
import os
from datetime import datetime
from flask import Flask, request
import threading

# ============================================
# НАСТРОЙКИ (берутся из переменных окружения на Render)
# ============================================
TOKEN = os.environ.get('BOT_TOKEN')  # Токен бота (обязательно)
if not TOKEN:
    raise ValueError("❌ Нет токена! Добавь BOT_TOKEN в переменные окружения!")

SUPER_ADMIN_ID = int(os.environ.get('SUPER_ADMIN_ID', 123456789))  # Твой ID

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ============================================
# БАЗА ДАННЫХ
# ============================================
class Database:
    def __init__(self):
        # Используем /tmp для временных файлов на Render
        db_path = os.path.join('/tmp', 'antispam.db')
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
    
    def create_tables(self):
        # Таблица настроек групп
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT 1,
                max_messages INTEGER DEFAULT 4,
                time_window INTEGER DEFAULT 3,
                caps_limit INTEGER DEFAULT 50,
                emoji_limit INTEGER DEFAULT 5,
                min_length INTEGER DEFAULT 2,
                max_length INTEGER DEFAULT 1000,
                warn_limit INTEGER DEFAULT 5
            )
        ''')
        
        # Таблица админов групп (МОЖНО НЕСКОЛЬКО!)
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
        
        self.conn.commit()
    
    def get_group_settings(self, chat_id):
        self.cursor.execute('SELECT * FROM group_settings WHERE chat_id = ?', (chat_id,))
        result = self.cursor.fetchone()
        
        if not result:
            self.cursor.execute('''
                INSERT INTO group_settings (chat_id, max_messages, time_window) 
                VALUES (?, 4, 3)
            ''', (chat_id,))
            self.conn.commit()
            
            self.cursor.execute('SELECT * FROM group_settings WHERE chat_id = ?', (chat_id,))
            result = self.cursor.fetchone()
        
        columns = [description[0] for description in self.cursor.description]
        return dict(zip(columns, result))
    
    def update_setting(self, chat_id, setting, value):
        self.cursor.execute(f'UPDATE group_settings SET {setting} = ? WHERE chat_id = ?', (value, chat_id))
        self.conn.commit()
    
    def is_group_admin(self, chat_id, user_id):
        if user_id == SUPER_ADMIN_ID:
            return True
        
        self.cursor.execute('SELECT * FROM group_admins WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        return self.cursor.fetchone() is not None
    
    def get_group_admins(self, chat_id):
        self.cursor.execute('SELECT * FROM group_admins WHERE chat_id = ?', (chat_id,))
        result = self.cursor.fetchall()
        columns = [description[0] for description in self.cursor.description]
        return [dict(zip(columns, row)) for row in result]
    
    def add_group_admin(self, chat_id, user_id, username, added_by):
        self.cursor.execute('''
            INSERT OR REPLACE INTO group_admins (chat_id, user_id, username, added_by, date_added)
            VALUES (?, ?, ?, ?, ?)
        ''', (chat_id, user_id, username, added_by, datetime.now()))
        self.conn.commit()
    
    def remove_group_admin(self, chat_id, user_id):
        self.cursor.execute('DELETE FROM group_admins WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        self.conn.commit()
    
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
    
    def add_warn(self, chat_id, user_id, username):
        offender = self.get_offender(chat_id, user_id)
        
        if offender:
            self.cursor.execute('''
                UPDATE offenders 
                SET warns = warns + 1, last_offense = ?, username = ?
                WHERE chat_id = ? AND user_id = ?
            ''', (datetime.now(), username, chat_id, user_id))
        else:
            self.cursor.execute('''
                INSERT INTO offenders (chat_id, user_id, username, warns, last_offense)
                VALUES (?, ?, ?, 1, ?)
            ''', (chat_id, user_id, username, datetime.now()))
        
        self.conn.commit()
        return self.get_offender(chat_id, user_id)
    
    def get_offender(self, chat_id, user_id):
        self.cursor.execute('SELECT * FROM offenders WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        result = self.cursor.fetchone()
        
        if result:
            columns = [description[0] for description in self.cursor.description]
            return dict(zip(columns, result))
        return None
    
    def reset_warns(self, chat_id, user_id):
        self.cursor.execute('DELETE FROM offenders WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        self.conn.commit()
    
    def get_top_offenders(self, chat_id, limit=10):
        self.cursor.execute('''
            SELECT username, warns FROM offenders 
            WHERE chat_id = ? 
            ORDER BY warns DESC 
            LIMIT ?
        ''', (chat_id, limit))
        return self.cursor.fetchall()
    
    def get_stats(self, chat_id):
        self.cursor.execute('SELECT COUNT(*) FROM group_admins WHERE chat_id = ?', (chat_id,))
        admins = self.cursor.fetchone()[0]
        
        self.cursor.execute('SELECT COUNT(*) FROM offenders WHERE chat_id = ?', (chat_id,))
        offenders = self.cursor.fetchone()[0]
        
        self.cursor.execute('SELECT COUNT(*) FROM ban_words WHERE chat_id = ?', (chat_id,))
        banwords = self.cursor.fetchone()[0]
        
        return {'admins': admins, 'offenders': offenders, 'banwords': banwords}

# Инициализация базы данных
db = Database()

# ============================================
# КЛАСС АНТИСПАМА
# ============================================
class GroupAntiSpam:
    def __init__(self):
        self.user_messages = defaultdict(list)
        
        # Имбовые предупреждения
        self.warning_messages = [
            "🚫 **4 СООБЩЕНИЯ ЗА 3 СЕКУНДЫ?** ЭТО ФЛУД!",
            "🤬 **ТЫ ЧЕ ТАК ЧАСТО ПИШЕШЬ?**",
            "💀 **СПАМ-ФИЛЬТР УНИЧТОЖИЛ СООБЩЕНИЕ!**",
            "👿 **ФЛУД НЕ ПРОЙДЕТ!**",
            "⚡ **4/3 - НОРМА ПРЕВЫШЕНА!**",
            "🔥 **ФЛУД ДЕТЕКТЕД! УДАЛЕНО!**",
            "🎯 **ТОЧНОЕ ПОПАДАНИЕ! 4 ЗА 3 СЕК!**",
            "🚔 **ПОЛИЦИЯ АНТИСПАМА НА МЕСТЕ!**"
        ]
        
        self.caps_warnings = [
            "🔇 **ХВАТИТ ОРАТЬ КАПСОМ!**",
            "👂 **УШИ ЗАВЯЛИ!**",
            "📢 **СДЕЛАЙ ТИШЕ!**"
        ]
        
        self.emoji_warnings = [
            "🎭 **ХВАТИТ СПАМИТЬ ЭМОДЗИ!**",
            "🎪 **ЦИРК УЕХАЛ!**",
            "🎨 **УБЕРИ СМАЙЛИКИ!**"
        ]
    
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
        text = message.text
        
        settings = db.get_group_settings(chat_id)
        
        if not settings['enabled']:
            return True, None
        
        if db.is_group_admin(chat_id, user_id):
            return True, None
        
        current_time = time.time()
        key = f"{chat_id}:{user_id}"
        
        self.user_messages[key] = [
            msg for msg in self.user_messages[key] 
            if current_time - msg['time'] < 60
        ]
        
        # 1. ПРОВЕРКА НА ФЛУД (4 за 3 секунды)
        recent_messages = [
            msg for msg in self.user_messages[key] 
            if current_time - msg['time'] < settings['time_window']
        ]
        
        if len(recent_messages) >= settings['max_messages']:
            offender = db.add_warn(chat_id, user_id, username)
            warns = offender['warns']
            
            warning_text = f"⚡ **ФЛУД! {settings['max_messages']} СООБЩЕНИЙ ЗА {settings['time_window']} СЕКУНДЫ!**\n"
            
            if warns >= settings['warn_limit']:
                warning_text += f"💀 **ПРЕВЫШЕН ЛИМИТ ПРЕДУПРЕЖДЕНИЙ!**\n"
            
            warning_text += f"👤 @{username}\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
            
            return False, warning_text
        
        # 2. ПРОВЕРКА НА ДЛИНУ
        if len(text) < settings['min_length']:
            return False, f"⚠️ Слишком коротко! (мин. {settings['min_length']} симв.)"
        
        if len(text) > settings['max_length']:
            return False, f"⚠️ Слишком длинно! (макс. {settings['max_length']} симв.)"
        
        # 3. ПРОВЕРКА НА КАПС
        if len(text) > 5:
            upper_count = sum(1 for c in text if c.isupper())
            upper_percent = (upper_count / len(text)) * 100
            
            if upper_percent > settings['caps_limit']:
                offender = db.add_warn(chat_id, user_id, username)
                warns = offender['warns']
                return False, random.choice(self.caps_warnings) + f"\n👤 @{username}\n⚠️ {warns}/{settings['warn_limit']}"
        
        # 4. ПРОВЕРКА НА ЭМОДЗИ
        emoji_count = self.count_emojis(text)
        if emoji_count > settings['emoji_limit']:
            offender = db.add_warn(chat_id, user_id, username)
            warns = offender['warns']
            return False, random.choice(self.emoji_warnings) + f"\n👤 @{username}\n😊 {emoji_count} эмодзи\n⚠️ {warns}/{settings['warn_limit']}"
        
        # 5. ПРОВЕРКА НА ЗАПРЕЩЕННЫЕ СЛОВА
        ban_words = db.get_ban_words(chat_id)
        text_lower = text.lower()
        for word in ban_words:
            if word in text_lower:
                offender = db.add_warn(chat_id, user_id, username)
                warns = offender['warns']
                return False, f"🚫 **ЗАПРЕЩЕННОЕ СЛОВО!**\n👤 @{username}\n🔴 {word}\n⚠️ {warns}/{settings['warn_limit']}"
        
        # 6. ПРОВЕРКА НА ПОВТОРЫ
        if len(self.user_messages[key]) >= 3:
            last_texts = [msg['text'] for msg in self.user_messages[key][-3:]]
            if all(t == text for t in last_texts):
                offender = db.add_warn(chat_id, user_id, username)
                warns = offender['warns']
                return False, f"🔄 **ПОВТОР СООБЩЕНИЯ!**\n👤 @{username}\n⚠️ {warns}/{settings['warn_limit']}"
        
        self.user_messages[key].append({
            'text': text,
            'time': current_time
        })
        
        return True, None

spam_filter = GroupAntiSpam()

# ============================================
# КОМАНДЫ БОТА
# ============================================
def is_admin(chat_id, user_id):
    return db.is_group_admin(chat_id, user_id)

@bot.message_handler(commands=['start'])
def start(message):
    if message.chat.type == 'private':
        text = """
🔥 **MEGA ANTISPAM ДЛЯ ГРУПП** 🔥

**🤖 Правила по умолчанию:**
• **4 СООБЩЕНИЯ ЗА 3 СЕКУНДЫ** = ФЛУД!
• Защита от капса
• Лимит эмодзи
• Бан-слова
• Система предупреждений

👑 **Можно добавить НЕСКОЛЬКО АДМИНОВ!**

📌 **Добавь меня в группу и дай права админа!**
        """
        bot.reply_to(message, text, parse_mode='Markdown')
    else:
        bot.reply_to(message, 
            "🤖 **Антиспам бот активирован!**\n"
            "📌 **Правило: 4 сообщения за 3 секунды = флуд!**\n"
            "👑 /help - список команд",
            parse_mode='Markdown'
        )

@bot.message_handler(commands=['help'])
def help_command(message):
    if message.chat.type == 'private':
        start(message)
        return
    
    text = """
📚 **КОМАНДЫ ДЛЯ АДМИНОВ:**

🛡️ **Управление:**
/antispam_on - ✅ Включить
/antispam_off - ❌ Выключить
/settings - ⚙️ Настройки
/stats - 📊 Статистика

👥 **Админы (МОЖНО НЕСКОЛЬКО):**
/add_admin - ✅ Добавить админа
/remove_admin - ❌ Удалить админа
/admins - 👥 Список админов

🚫 **Нарушители:**
/warns - 📝 Предупреждения
/reset_warns - 🔄 Сбросить варны
/top - 🏆 Топ нарушителей

📝 **Бан-слова:**
/add_banword слово - ✅ Добавить
/remove_banword слово - ❌ Удалить
/banwords - 📋 Все слова

⚙️ **Настройка лимитов:**
/set_max_msgs 4 - Макс сообщений
/set_time 3 - Временное окно (сек)
/set_caps 50 - Лимит капса (%)
/set_emoji 5 - Лимит эмодзи
/set_warn_limit 5 - Лимит варнов
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['antispam_on'])
def antispam_on(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!** /admins", parse_mode='Markdown')
        return
    
    db.update_setting(chat_id, 'enabled', 1)
    bot.reply_to(message, "🛡️ **АНТИСПАМ ВКЛЮЧЕН!**\n📌 4 сообщения за 3 секунды = флуд!", parse_mode='Markdown')

@bot.message_handler(commands=['antispam_off'])
def antispam_off(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    db.update_setting(chat_id, 'enabled', 0)
    bot.reply_to(message, "💤 **АНТИСПАМ ВЫКЛЮЧЕН!**", parse_mode='Markdown')

@bot.message_handler(commands=['settings'])
def settings_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    settings = db.get_group_settings(chat_id)
    
    text = f"""
⚙️ **ТЕКУЩИЕ НАСТРОЙКИ:**

📌 **ГЛАВНОЕ ПРАВИЛО:**
• **{settings['max_messages']} сообщений за {settings['time_window']} секунд = ФЛУД!**

🛡️ **Остальные лимиты:**
• Капс: >{settings['caps_limit']}% = предупреждение
• Эмодзи: >{settings['emoji_limit']} = предупреждение
• Длина: {settings['min_length']}-{settings['max_length']} симв.
• Лимит варнов: {settings['warn_limit']}

📝 **Команды для изменения:**
/set_max_msgs [число] - макс сообщений
/set_time [сек] - временное окно
/set_caps [%] - лимит капса
/set_emoji [число] - лимит эмодзи
/set_warn_limit [число] - лимит варнов
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['set_max_msgs'])
def set_max_msgs(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        return
    
    try:
        value = int(message.text.split()[1])
        if 1 <= value <= 10:
            db.update_setting(chat_id, 'max_messages', value)
            bot.reply_to(message, f"✅ **Макс сообщений:** {value}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Использование: /set_max_msgs [1-10]", parse_mode='Markdown')

@bot.message_handler(commands=['set_time'])
def set_time(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        return
    
    try:
        value = int(message.text.split()[1])
        if 1 <= value <= 10:
            db.update_setting(chat_id, 'time_window', value)
            bot.reply_to(message, f"✅ **Временное окно:** {value} сек", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Использование: /set_time [1-10]", parse_mode='Markdown')

@bot.message_handler(commands=['set_caps'])
def set_caps(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        return
    
    try:
        value = int(message.text.split()[1])
        if 0 <= value <= 100:
            db.update_setting(chat_id, 'caps_limit', value)
            bot.reply_to(message, f"✅ **Лимит капса:** {value}%", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Использование: /set_caps [0-100]", parse_mode='Markdown')

@bot.message_handler(commands=['set_emoji'])
def set_emoji(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        return
    
    try:
        value = int(message.text.split()[1])
        if 0 <= value <= 20:
            db.update_setting(chat_id, 'emoji_limit', value)
            bot.reply_to(message, f"✅ **Лимит эмодзи:** {value}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Использование: /set_emoji [0-20]", parse_mode='Markdown')

@bot.message_handler(commands=['set_warn_limit'])
def set_warn_limit(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        return
    
    try:
        value = int(message.text.split()[1])
        if 1 <= value <= 10:
            db.update_setting(chat_id, 'warn_limit', value)
            bot.reply_to(message, f"✅ **Лимит предупреждений:** {value}", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Использование: /set_warn_limit [1-10]", parse_mode='Markdown')

@bot.message_handler(commands=['add_admin'])
def add_admin(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы могут добавлять админов!**", parse_mode='Markdown')
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ **Ответь на сообщение пользователя!**", parse_mode='Markdown')
        return
    
    new_admin = message.reply_to_message.from_user
    db.add_group_admin(chat_id, new_admin.id, new_admin.username or "NoName", user_id)
    
    bot.reply_to(message, f"✅ **@{new_admin.username or 'NoName'} теперь админ!**\n👥 Все админы: /admins", parse_mode='Markdown')

@bot.message_handler(commands=['remove_admin'])
def remove_admin(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ **Ответь на сообщение пользователя!**", parse_mode='Markdown')
        return
    
    admin_to_remove = message.reply_to_message.from_user
    
    if admin_to_remove.id == user_id:
        bot.reply_to(message, "❌ **Нельзя удалить самого себя!**", parse_mode='Markdown')
        return
    
    db.remove_group_admin(chat_id, admin_to_remove.id)
    bot.reply_to(message, f"✅ **@{admin_to_remove.username or 'NoName'} больше не админ**", parse_mode='Markdown')

@bot.message_handler(commands=['admins'])
def list_admins(message):
    chat_id = message.chat.id
    
    admins = db.get_group_admins(chat_id)
    
    if not admins:
        bot.reply_to(message, "📝 **Админов пока нет**\nДобавьте первого админа через /add_admin", parse_mode='Markdown')
        return
    
    text = "👑 **АДМИНЫ ГРУППЫ:**\n\n"
    for admin in admins:
        text += f"• @{admin['username']} (ID: {admin['user_id']})\n"
    
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['add_banword'])
def add_banword(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    try:
        word = message.text.split()[1].lower()
        db.add_ban_word(chat_id, word, user_id)
        bot.reply_to(message, f"🚫 **Слово '{word}' добавлено в бан-лист!**", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Использование: /add_banword слово", parse_mode='Markdown')

@bot.message_handler(commands=['remove_banword'])
def remove_banword(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    try:
        word = message.text.split()[1].lower()
        db.remove_ban_word(chat_id, word)
        bot.reply_to(message, f"✅ **Слово '{word}' удалено из бан-листа!**", parse_mode='Markdown')
    except:
        bot.reply_to(message, "❌ Использование: /remove_banword слово", parse_mode='Markdown')

@bot.message_handler(commands=['banwords'])
def banwords(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    words = db.get_ban_words(chat_id)
    
    if words:
        text = "🚫 **ЗАПРЕЩЕННЫЕ СЛОВА:**\n\n" + "\n".join([f"• {word}" for word in words])
    else:
        text = "📝 **Запрещенных слов пока нет**"
    
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['warns'])
def warns_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ **Ответь на сообщение пользователя!**", parse_mode='Markdown')
        return
    
    target = message.reply_to_message.from_user
    offender = db.get_offender(chat_id, target.id)
    settings = db.get_group_settings(chat_id)
    
    warns = offender['warns'] if offender else 0
    
    text = f"""
📝 **ПРЕДУПРЕЖДЕНИЯ**
👤 @{target.username or 'NoName'}
⚠️ **{warns}/{settings['warn_limit']}**
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['reset_warns'])
def reset_warns(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ **Ответь на сообщение пользователя!**", parse_mode='Markdown')
        return
    
    target = message.reply_to_message.from_user
    db.reset_warns(chat_id, target.id)
    
    bot.reply_to(message, f"✅ **Предупреждения сброшены для @{target.username or 'NoName'}**", parse_mode='Markdown')

@bot.message_handler(commands=['top'])
def top_offenders(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    top = db.get_top_offenders(chat_id)
    
    if not top:
        bot.reply_to(message, "🏆 **Пока нет нарушителей!** Чат чист! 🎉", parse_mode='Markdown')
        return
    
    text = "🏆 **ТОП НАРУШИТЕЛЕЙ:**\n\n"
    for i, (username, warns) in enumerate(top, 1):
        text += f"{i}. @{username} - {warns} предупреждений\n"
    
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['stats'])
def stats_command(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ **Только админы!**", parse_mode='Markdown')
        return
    
    settings = db.get_group_settings(chat_id)
    stats = db.get_stats(chat_id)
    
    status = "🟢 ВКЛ" if settings['enabled'] else "🔴 ВЫКЛ"
    
    text = f"""
📊 **СТАТИСТИКА ГРУППЫ**

🛡️ **Статус:** {status}
📌 **Правило:** {settings['max_messages']} за {settings['time_window']}с

👥 **Админов:** {stats['admins']}
🚫 **Нарушителей:** {stats['offenders']}
📝 **Бан-слов:** {stats['banwords']}

⚙️ **Настройки:**
• Капс: >{settings['caps_limit']}%
• Эмодзи: >{settings['emoji_limit']}
• Длина: {settings['min_length']}-{settings['max_length']}
• Лимит варнов: {settings['warn_limit']}
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(content_types=['new_chat_members'])
def welcome_new(message):
    for member in message.new_chat_members:
        if member.id == bot.get_me().id:
            bot.reply_to(message, 
                "🤖 **Всем привет! Я антиспам бот!**\n\n"
                "📌 **ПРАВИЛО: 4 СООБЩЕНИЯ ЗА 3 СЕКУНДЫ = ФЛУД!**\n"
                "👑 Админы: используйте /help\n"
                "👥 Можно добавить несколько админов через /add_admin",
                parse_mode='Markdown'
            )
            
            try:
                creator = message.from_user
                db.add_group_admin(message.chat.id, creator.id, creator.username or "Creator", SUPER_ADMIN_ID)
            except:
                pass

@bot.message_handler(func=lambda message: True)
def handle_message(message):
    if message.text.startswith('/'):
        return
    
    if message.chat.type == 'private':
        bot.reply_to(message, "🤖 **Добавь меня в группу!** /start", parse_mode='Markdown')
        return
    
    is_allowed, warning = spam_filter.check_message(message)
    
    if not is_allowed and warning:
        try:
            bot.delete_message(message.chat.id, message.message_id)
            bot.send_message(message.chat.id, warning)
        except:
            pass

# ============================================
# ЗАПУСК НА RENDER (ЧЕРЕЗ ВЕБХУКИ - САМЫЙ НАДЕЖНЫЙ)
# ============================================

import os
import time
from flask import Flask, request

app = Flask(__name__)

# --- Главная страница для проверки ---
@app.route('/')
def home():
    return "🔥 Антиспам бот работает! 🔥", 200

@app.route('/health')
def health():
    return "OK", 200

# --- Эндпоинт для вебхуков Telegram ---
@app.route(f'/{TOKEN}', methods=['POST'])
def webhook():
    """Принимает обновления от Telegram"""
    if request.headers.get('content-type') == 'application/json':
        json_string = request.get_data().decode('utf-8')
        update = telebot.types.Update.de_json(json_string)
        bot.process_new_updates([update])
        return '', 200
    else:
        return 'Wrong content type', 403

# --- Функция настройки вебхука ---
def set_webhook():
    """Устанавливает вебхук для бота"""
    print("="*50)
    print("🔄 Настройка вебхука...")
    RENDER_URL = os.environ.get('RENDER_EXTERNAL_URL')
    if not RENDER_URL:
        print("❌ Переменная RENDER_EXTERNAL_URL не найдена!")
        return False
    
    webhook_url = f"{RENDER_URL}/{TOKEN}"
    print(f"📌 URL вебхука: {webhook_url}")
    
    try:
        # Удаляем старый вебхук
        bot.remove_webhook()
        time.sleep(1)
        # Устанавливаем новый
        bot.set_webhook(url=webhook_url)
        print("✅ Вебхук успешно установлен!")
        
        # Проверяем информацию о боте
        me = bot.get_me()
        print(f"✅ Бот @{me.username} (ID: {me.id}) подключен к Telegram API.")
        return True
    except Exception as e:
        print(f"❌ Ошибка установки вебхука: {e}")
        return False

# --- Главный блок запуска ---
if __name__ == '__main__':
    print("="*50)
    print("🔥 ЗАПУСК АНТИСПАМ-БОТА (ЧЕРЕЗ ВЕБХУКИ)")
    print("="*50)
    print(f"👑 Супер админ ID: {SUPER_ADMIN_ID}")
    
    # Устанавливаем вебхук перед запуском Flask
    if set_webhook():
        print("✅ Бот готов к работе через вебхуки!")
    else:
        print("⚠️ Не удалось установить вебхук, но Flask все равно запустится.")
    
    # Запускаем Flask
    port = int(os.environ.get('PORT', 10000))
    print(f"🚀 Запуск Flask сервера на порту {port}...")
    print("="*50)
    app.run(host='0.0.0.0', port=port, debug=False)
