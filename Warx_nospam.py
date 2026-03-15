import telebot
from telebot import types
import time
from collections import defaultdict, Counter
import re
import random
import sqlite3
import os
from datetime import datetime, timedelta
from flask import Flask, request
import json
import hashlib
import urllib.parse

# ============================================
# НАСТРОЙКИ
# ============================================
TOKEN = os.environ.get('BOT_TOKEN')
if not TOKEN:
    raise ValueError("❌ Нет токена! Добавь BOT_TOKEN в переменные окружения!")

SUPER_ADMIN_ID = int(os.environ.get('SUPER_ADMIN_ID', 6647021953))

bot = telebot.TeleBot(TOKEN)
app = Flask(__name__)

# ============================================
# НАСТРОЙКИ СИСТЕМ
# ============================================
REPORT_LIMIT = 3
REPORT_MUTE_MINUTES = 10
reported_messages = defaultdict(set)

# Система уровней
LEVELS = {
    1: 0,
    2: 100,
    3: 300,
    4: 600,
    5: 1000,
    6: 1500,
    7: 2100,
    8: 2800,
    9: 3600,
    10: 4500
}

# Достижения
ACHIEVEMENTS = {
    'first_message': {'name': 'Первые шаги', 'desc': 'Отправить первое сообщение', 'emoji': '👶'},
    '100_messages': {'name': 'Болтун', 'desc': 'Отправить 100 сообщений', 'emoji': '🗣️'},
    '1000_messages': {'name': 'Легенда чата', 'desc': 'Отправить 1000 сообщений', 'emoji': '👑'},
    'helper': {'name': 'Помощник', 'desc': 'Помочь другому участнику', 'emoji': '🦸'},
    'reporter': {'name': 'Борец со спамом', 'desc': 'Пожаловаться на спам 10 раз', 'emoji': '🛡️'},
    'old_timer': {'name': 'Старожил', 'desc': 'Быть в чате больше года', 'emoji': '⏳'},
    'game_master': {'name': 'Игрок', 'desc': 'Сыграть в 10 игр', 'emoji': '🎮'},
    'night_owl': {'name': 'Ночная сова', 'desc': 'Писать после полуночи', 'emoji': '🦉'},
}

# ============================================
# РАСШИРЕННАЯ БАЗА ДАННЫХ
# ============================================
class Database:
    def __init__(self):
        db_path = os.path.join('/tmp', 'antispam.db')
        self.conn = sqlite3.connect(db_path, check_same_thread=False)
        self.cursor = self.conn.cursor()
        self.create_tables()
        print("✅ База данных подключена")
    
    def create_tables(self):
        # Таблица настроек групп
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS group_settings (
                chat_id INTEGER PRIMARY KEY,
                enabled BOOLEAN DEFAULT 1,
                flood_enabled BOOLEAN DEFAULT 1,
                caps_enabled BOOLEAN DEFAULT 1,
                emoji_enabled BOOLEAN DEFAULT 1,
                repeat_enabled BOOLEAN DEFAULT 1,
                links_enabled BOOLEAN DEFAULT 1,
                swear_enabled BOOLEAN DEFAULT 1,
                media_enabled BOOLEAN DEFAULT 1,
                welcome_enabled BOOLEAN DEFAULT 1,
                max_messages INTEGER DEFAULT 4,
                time_window INTEGER DEFAULT 3,
                caps_limit INTEGER DEFAULT 50,
                emoji_limit INTEGER DEFAULT 5,
                link_kd INTEGER DEFAULT 10,
                media_limit INTEGER DEFAULT 3,
                warn_limit INTEGER DEFAULT 5,
                auto_mute BOOLEAN DEFAULT 1,
                mute_time INTEGER DEFAULT 60,
                max_length INTEGER DEFAULT 1000,
                warn_reset_time INTEGER DEFAULT 24,
                games_enabled BOOLEAN DEFAULT 1,
                stats_enabled BOOLEAN DEFAULT 1,
                leveling_enabled BOOLEAN DEFAULT 1
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
        
        # Таблица пользователей (для системы уровней)
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS users (
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                first_name TEXT,
                messages INTEGER DEFAULT 0,
                exp INTEGER DEFAULT 0,
                level INTEGER DEFAULT 1,
                games_played INTEGER DEFAULT 0,
                games_won INTEGER DEFAULT 0,
                reports_sent INTEGER DEFAULT 0,
                helped_count INTEGER DEFAULT 0,
                join_date TIMESTAMP,
                last_active TIMESTAMP,
                achievements TEXT DEFAULT '[]',
                reputation INTEGER DEFAULT 0,
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
                total_warns INTEGER DEFAULT 0,
                last_warn_reset TIMESTAMP,
                last_reason TEXT,
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
        
        # Таблица для жалоб
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reports (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                message_id INTEGER,
                reported_user_id INTEGER,
                reported_username TEXT,
                reporter_user_id INTEGER,
                reporter_username TEXT,
                message_text TEXT,
                timestamp TIMESTAMP,
                UNIQUE(chat_id, message_id, reporter_user_id)
            )
        ''')
        
        # Таблица для событий
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                title TEXT,
                description TEXT,
                event_date TIMESTAMP,
                created_by INTEGER,
                created_at TIMESTAMP,
                participants TEXT DEFAULT '[]'
            )
        ''')
        
        # Таблица для напоминаний
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS reminders (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                reminder_text TEXT,
                reminder_time TIMESTAMP,
                created_at TIMESTAMP,
                is_done BOOLEAN DEFAULT 0
            )
        ''')
        
        # Таблица для игровой статистики
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS game_stats (
                chat_id INTEGER,
                user_id INTEGER,
                username TEXT,
                game_type TEXT,
                games_played INTEGER DEFAULT 0,
                games_won INTEGER DEFAULT 0,
                total_score INTEGER DEFAULT 0,
                PRIMARY KEY (chat_id, user_id, game_type)
            )
        ''')
        
        # Таблица для викторин
        self.cursor.execute('''
            CREATE TABLE IF NOT EXISTS quiz_questions (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                chat_id INTEGER,
                question TEXT,
                answer TEXT,
                options TEXT,
                added_by INTEGER,
                category TEXT DEFAULT 'general'
            )
        ''')
        
        self.conn.commit()
        print("✅ Все таблицы созданы")
    
    # ========== МЕТОДЫ ДЛЯ ПОЛЬЗОВАТЕЛЕЙ ==========
    def get_user(self, chat_id, user_id):
        self.cursor.execute('SELECT * FROM users WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
        result = self.cursor.fetchone()
        
        if not result:
            self.cursor.execute('''
                INSERT INTO users (chat_id, user_id, username, first_name, join_date, last_active)
                VALUES (?, ?, ?, ?, ?, ?)
            ''', (chat_id, user_id, '', '', datetime.now(), datetime.now()))
            self.conn.commit()
            self.cursor.execute('SELECT * FROM users WHERE chat_id = ? AND user_id = ?', (chat_id, user_id))
            result = self.cursor.fetchone()
        
        columns = [description[0] for description in self.cursor.description]
        return dict(zip(columns, result))
    
    def update_user_activity(self, chat_id, user_id, username, first_name):
        user = self.get_user(chat_id, user_id)
        
        self.cursor.execute('''
            UPDATE users 
            SET messages = messages + 1, 
                exp = exp + 1,
                last_active = ?,
                username = ?,
                first_name = ?
            WHERE chat_id = ? AND user_id = ?
        ''', (datetime.now(), username, first_name, chat_id, user_id))
        self.conn.commit()
        
        # Проверка на повышение уровня
        user = self.get_user(chat_id, user_id)
        new_level = user['level']
        
        for level, exp_needed in LEVELS.items():
            if user['exp'] >= exp_needed and level > new_level:
                new_level = level
        
        if new_level > user['level']:
            self.cursor.execute('''
                UPDATE users SET level = ? WHERE chat_id = ? AND user_id = ?
            ''', (new_level, chat_id, user_id))
            self.conn.commit()
            return new_level
        return None
    
    def add_achievement(self, chat_id, user_id, achievement_key):
        user = self.get_user(chat_id, user_id)
        achievements = json.loads(user['achievements']) if user['achievements'] else []
        
        if achievement_key not in achievements:
            achievements.append(achievement_key)
            self.cursor.execute('''
                UPDATE users SET achievements = ? WHERE chat_id = ? AND user_id = ?
            ''', (json.dumps(achievements), chat_id, user_id))
            self.conn.commit()
            return True
        return False
    
    def get_top_users(self, chat_id, limit=10):
        self.cursor.execute('''
            SELECT username, messages, exp, level FROM users 
            WHERE chat_id = ? 
            ORDER BY exp DESC LIMIT ?
        ''', (chat_id, limit))
        result = self.cursor.fetchall()
        return [{'username': r[0], 'messages': r[1], 'exp': r[2], 'level': r[3]} for r in result]
    
    # ========== МЕТОДЫ ДЛЯ СОБЫТИЙ ==========
    def add_event(self, chat_id, title, description, event_date, created_by):
        self.cursor.execute('''
            INSERT INTO events (chat_id, title, description, event_date, created_by, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (chat_id, title, description, event_date, created_by, datetime.now()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_events(self, chat_id, upcoming=True):
        now = datetime.now()
        if upcoming:
            self.cursor.execute('''
                SELECT * FROM events 
                WHERE chat_id = ? AND event_date > ? 
                ORDER BY event_date ASC
            ''', (chat_id, now))
        else:
            self.cursor.execute('''
                SELECT * FROM events 
                WHERE chat_id = ? 
                ORDER BY event_date DESC LIMIT 10
            ''', (chat_id,))
        
        result = self.cursor.fetchall()
        columns = [description[0] for description in self.cursor.description]
        return [dict(zip(columns, row)) for row in result]
    
    def add_participant(self, event_id, user_id, username):
        self.cursor.execute('SELECT participants FROM events WHERE id = ?', (event_id,))
        result = self.cursor.fetchone()
        if result:
            participants = json.loads(result[0]) if result[0] else []
            if user_id not in [p['user_id'] for p in participants]:
                participants.append({'user_id': user_id, 'username': username, 'joined_at': str(datetime.now())})
                self.cursor.execute('UPDATE events SET participants = ? WHERE id = ?', 
                                  (json.dumps(participants), event_id))
                self.conn.commit()
                return True
        return False
    
    # ========== МЕТОДЫ ДЛЯ НАПОМИНАНИЙ ==========
    def add_reminder(self, chat_id, user_id, username, text, reminder_time):
        self.cursor.execute('''
            INSERT INTO reminders (chat_id, user_id, username, reminder_text, reminder_time, created_at)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (chat_id, user_id, username, text, reminder_time, datetime.now()))
        self.conn.commit()
        return self.cursor.lastrowid
    
    def get_due_reminders(self):
        now = datetime.now()
        self.cursor.execute('''
            SELECT * FROM reminders 
            WHERE reminder_time <= ? AND is_done = 0
        ''', (now,))
        result = self.cursor.fetchall()
        columns = [description[0] for description in self.cursor.description]
        return [dict(zip(columns, row)) for row in result]
    
    def mark_reminder_done(self, reminder_id):
        self.cursor.execute('UPDATE reminders SET is_done = 1 WHERE id = ?', (reminder_id,))
        self.conn.commit()
    
    # ========== МЕТОДЫ ДЛЯ ИГР ==========
    def update_game_stats(self, chat_id, user_id, username, game_type, won=False, score=0):
        self.cursor.execute('''
            SELECT * FROM game_stats WHERE chat_id = ? AND user_id = ? AND game_type = ?
        ''', (chat_id, user_id, game_type))
        result = self.cursor.fetchone()
        
        if result:
            if won:
                self.cursor.execute('''
                    UPDATE game_stats 
                    SET games_played = games_played + 1, 
                        games_won = games_won + 1,
                        total_score = total_score + ?,
                        username = ?
                    WHERE chat_id = ? AND user_id = ? AND game_type = ?
                ''', (score, username, chat_id, user_id, game_type))
            else:
                self.cursor.execute('''
                    UPDATE game_stats 
                    SET games_played = games_played + 1,
                        total_score = total_score + ?,
                        username = ?
                    WHERE chat_id = ? AND user_id = ? AND game_type = ?
                ''', (score, username, chat_id, user_id, game_type))
        else:
            self.cursor.execute('''
                INSERT INTO game_stats (chat_id, user_id, username, game_type, games_played, games_won, total_score)
                VALUES (?, ?, ?, ?, 1, ?, ?)
            ''', (chat_id, user_id, username, game_type, 1 if won else 0, score))
        
        self.conn.commit()
    
    def get_game_leaderboard(self, chat_id, game_type=None, limit=10):
        if game_type:
            self.cursor.execute('''
                SELECT username, games_played, games_won, total_score FROM game_stats 
                WHERE chat_id = ? AND game_type = ?
                ORDER BY games_won DESC, total_score DESC LIMIT ?
            ''', (chat_id, game_type, limit))
        else:
            self.cursor.execute('''
                SELECT username, SUM(games_played) as total_played, 
                       SUM(games_won) as total_won, SUM(total_score) as total_score 
                FROM game_stats 
                WHERE chat_id = ? 
                GROUP BY user_id 
                ORDER BY total_won DESC, total_score DESC LIMIT ?
            ''', (chat_id, limit))
        
        result = self.cursor.fetchall()
        return result
    
    # ========== МЕТОДЫ ДЛЯ ВИКТОРИН ==========
    def add_quiz_question(self, chat_id, question, answer, options, added_by, category='general'):
        self.cursor.execute('''
            INSERT INTO quiz_questions (chat_id, question, answer, options, added_by, category)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (chat_id, question, answer, json.dumps(options), added_by, category))
        self.conn.commit()
    
    def get_random_question(self, chat_id, category=None):
        if category:
            self.cursor.execute('''
                SELECT * FROM quiz_questions 
                WHERE chat_id = ? AND category = ?
                ORDER BY RANDOM() LIMIT 1
            ''', (chat_id, category))
        else:
            self.cursor.execute('''
                SELECT * FROM quiz_questions 
                WHERE chat_id = ?
                ORDER BY RANDOM() LIMIT 1
            ''', (chat_id,))
        
        result = self.cursor.fetchone()
        if result:
            columns = [description[0] for description in self.cursor.description]
            question = dict(zip(columns, result))
            question['options'] = json.loads(question['options'])
            return question
        return None
    
    # ========== СУЩЕСТВУЮЩИЕ МЕТОДЫ ==========
    def get_group_settings(self, chat_id):
        self.cursor.execute('SELECT * FROM group_settings WHERE chat_id = ?', (chat_id,))
        result = self.cursor.fetchone()
        
        if not result:
            self.cursor.execute('INSERT INTO group_settings (chat_id) VALUES (?)', (chat_id,))
            self.conn.commit()
            self.cursor.execute('SELECT * FROM group_settings WHERE chat_id = ?', (chat_id,))
            result = self.cursor.fetchone()
        
        columns = [description[0] for description in self.cursor.description]
        return dict(zip(columns, result))
    
    def update_setting(self, chat_id, setting, value):
        self.cursor.execute(f'UPDATE group_settings SET {setting} = ? WHERE chat_id = ?', (value, chat_id))
        self.conn.commit()
    
    def toggle_function(self, chat_id, function_name):
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
        print(f"✅ Админ {username} добавлен")
    
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
                SET warns = warns + 1, last_offense = ?, username = ?, total_warns = total_warns + 1, last_reason = ?
                WHERE chat_id = ? AND user_id = ?
            ''', (datetime.now(), username, reason, chat_id, user_id))
            new_warns = offender['warns'] + 1
        else:
            self.cursor.execute('''
                INSERT INTO offenders (chat_id, user_id, username, warns, last_offense, join_time, total_warns, last_warn_reset, last_reason)
                VALUES (?, ?, ?, 1, ?, ?, 1, ?, ?)
            ''', (chat_id, user_id, username, datetime.now(), datetime.now(), datetime.now(), reason))
            new_warns = 1
        
        self.log_action(chat_id, user_id, username, 'WARN', reason)
        self.conn.commit()
        
        if settings['auto_mute'] and new_warns >= settings['warn_limit']:
            mute_until = datetime.now() + timedelta(seconds=settings['mute_time'])
            self.cursor.execute('UPDATE offenders SET muted_until = ? WHERE chat_id = ? AND user_id = ?', 
                              (mute_until, chat_id, user_id))
            self.conn.commit()
            return new_warns, mute_until
        
        return new_warns, None
    
    def mute_user(self, chat_id, user_id, username, minutes, reason="Ручной мут"):
        seconds = minutes * 60
        mute_until = datetime.now() + timedelta(seconds=seconds)
        
        offender = self.get_offender(chat_id, user_id)
        if offender:
            self.cursor.execute('''
                UPDATE offenders SET muted_until = ?, last_offense = ?, username = ?, last_reason = ?
                WHERE chat_id = ? AND user_id = ?
            ''', (mute_until, datetime.now(), username, reason, chat_id, user_id))
        else:
            self.cursor.execute('''
                INSERT INTO offenders (chat_id, user_id, username, warns, last_offense, join_time, muted_until, last_reason)
                VALUES (?, ?, ?, 0, ?, ?, ?, ?)
            ''', (chat_id, user_id, username, datetime.now(), datetime.now(), mute_until, reason))
        
        self.log_action(chat_id, user_id, username, 'MUTE', f"{reason} на {minutes} мин")
        self.conn.commit()
        return mute_until
    
    def unmute_user(self, chat_id, user_id):
        self.cursor.execute('UPDATE offenders SET muted_until = NULL WHERE chat_id = ? AND user_id = ?', 
                          (chat_id, user_id))
        self.log_action(chat_id, user_id, "unknown", 'UNMUTE', "Снятие мута")
        self.conn.commit()
    
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
        self.cursor.execute('UPDATE offenders SET warns = 0, last_warn_reset = ? WHERE chat_id = ? AND user_id = ?', 
                          (datetime.now(), chat_id, user_id))
        self.unmute_user(chat_id, user_id)
        self.conn.commit()
    
    def log_action(self, chat_id, user_id, username, action, reason):
        self.cursor.execute('''
            INSERT INTO logs (chat_id, user_id, username, action, reason, timestamp)
            VALUES (?, ?, ?, ?, ?, ?)
        ''', (chat_id, user_id, username, action, reason, datetime.now()))
        self.conn.commit()
        print(f"📝 Лог: {action} - {username} - {reason}")
    
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
class AntiSpam:
    def __init__(self):
        self.user_messages = defaultdict(list)
        
        self.warnings = {
            'flood': [
                "⚡ ФЛУД! {msgs} за {sec} сек",
                "🤬 ХВАТИТ СПАМИТЬ!",
                "🚫 ФЛУД-КОНТРОЛЬ!"
            ],
            'caps': [
                "🔇 ХВАТИТ ОРАТЬ!",
                "👂 УШИ ЗАВЯЛИ!",
                "📢 СДЕЛАЙ ТИШЕ!"
            ],
            'emoji': [
                "🎭 ХВАТИТ СПАМИТЬ ЭМОДЗИ!",
                "🎪 ЦИРК УЕХАЛ!"
            ],
            'repeat': [
                "🔄 ХВАТИТ ПОВТОРЯТЬСЯ!",
                "🔁 ПОВТОР СООБЩЕНИЯ!"
            ],
            'link': [
                "🔗 НОВИЧКАМ НЕЛЬЗЯ ССЫЛКИ!",
                "🚫 ССЫЛКИ ЗАПРЕЩЕНЫ!"
            ],
            'swear': [
                "🤬 НЕ МАТЕРЬСЯ!",
                "🚫 ПЛОХИЕ СЛОВА ЗАПРЕЩЕНЫ!"
            ],
            'media': [
                "📸 ХВАТИТ СПАМИТЬ МЕДИА!",
                "🎥 НЕ ТАК МНОГО ФОТО!"
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
        
        if db.is_muted(chat_id, user_id):
            offender = db.get_offender(chat_id, user_id)
            mute_until = datetime.fromisoformat(offender['muted_until'])
            remaining = int((mute_until - datetime.now()).total_seconds() / 60)
            reason = offender.get('last_reason', 'неизвестно')
            return False, f"🔇 **ВЫ В МУТЕ!**\nОсталось: {remaining} мин\nПричина: {reason}"
        
        current_time = time.time()
        key = f"{chat_id}:{user_id}"
        
        offender = db.get_offender(chat_id, user_id)
        join_time = datetime.fromisoformat(offender['join_time']) if offender else datetime.now()
        
        self.user_messages[key] = [msg for msg in self.user_messages[key] if current_time - msg['time'] < 60]
        
        if len(text) > settings['max_length']:
            return False, f"⚠️ Слишком длинно! Макс: {settings['max_length']} симв."
        
        if settings['flood_enabled']:
            recent = [msg for msg in self.user_messages[key] if current_time - msg['time'] < settings['time_window']]
            if len(recent) >= settings['max_messages']:
                warns, mute = db.add_warn(chat_id, user_id, username, f"Флуд")
                warning = random.choice(self.warnings['flood']).format(msgs=settings['max_messages'], sec=settings['time_window'])
                if mute:
                    warning += f"\n🔇 АВТО-МУТ на {settings['mute_time']//60} мин"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['caps_enabled'] and len(text) > 5:
            upper = sum(1 for c in text if c.isupper()) / len(text) * 100
            if upper > settings['caps_limit']:
                warns, mute = db.add_warn(chat_id, user_id, username, f"Капс")
                warning = random.choice(self.warnings['caps'])
                if mute:
                    warning += f"\n🔇 АВТО-МУТ на {settings['mute_time']//60} мин"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['emoji_enabled']:
            emoji = self.count_emojis(text)
            if emoji > settings['emoji_limit']:
                warns, mute = db.add_warn(chat_id, user_id, username, f"Эмодзи")
                warning = random.choice(self.warnings['emoji']) + f"\n😊 Эмодзи: {emoji}"
                if mute:
                    warning += f"\n🔇 АВТО-МУТ на {settings['mute_time']//60} мин"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['repeat_enabled'] and len(self.user_messages[key]) >= 3:
            last = [msg['text'] for msg in self.user_messages[key][-3:]]
            if all(t == text for t in last):
                warns, mute = db.add_warn(chat_id, user_id, username, "Повтор")
                warning = random.choice(self.warnings['repeat'])
                if mute:
                    warning += f"\n🔇 АВТО-МУТ на {settings['mute_time']//60} мин"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['links_enabled'] and self.has_link(text):
            if offender and (datetime.now() - join_time).total_seconds() < settings['link_kd'] * 60:
                warns, mute = db.add_warn(chat_id, user_id, username, "Ссылка")
                warning = random.choice(self.warnings['link']) + f"\n⏱️ ЖДИ {settings['link_kd']} МИН"
                if mute:
                    warning += f"\n🔇 АВТО-МУТ на {settings['mute_time']//60} мин"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
                return False, warning
        
        if settings['swear_enabled']:
            ban_words = db.get_ban_words(chat_id)
            has_swear, word = self.has_swear(text, ban_words)
            if has_swear:
                warns, mute = db.add_warn(chat_id, user_id, username, f"Мат")
                warning = random.choice(self.warnings['swear']) + f"\n🔴 Слово: {word}"
                if mute:
                    warning += f"\n🔇 АВТО-МУТ на {settings['mute_time']//60} мин"
                else:
                    warning += f"\n⚠️ Предупреждение: {warns}/{settings['warn_limit']}"
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
# СИСТЕМА ЖАЛОБ
# ============================================
@bot.message_handler(commands=['report'])
def report_message(message):
    """Пожаловаться на сообщение"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = get_username(message.from_user)
    
    if not message.reply_to_message:
        bot.reply_to(message, "❌ Ответь на сообщение, на которое хочешь пожаловаться!")
        return
    
    reported_msg = message.reply_to_message
    reported_user = reported_msg.from_user
    
    if db.is_group_admin(chat_id, reported_user.id):
        bot.reply_to(message, "👑 На админов жаловаться нельзя!")
        return
    
    if reported_user.id == user_id:
        bot.reply_to(message, "🤔 На себя жаловаться? Серьезно?")
        return
    
    message_key = (chat_id, reported_msg.message_id)
    
    if user_id in reported_messages[message_key]:
        bot.reply_to(message, "⚠️ Ты уже жаловался на это сообщение!")
        return
    
    reported_messages[message_key].add(user_id)
    report_count = len(reported_messages[message_key])
    
    bot.reply_to(message, f"✅ Жалоба отправлена! ({report_count}/{REPORT_LIMIT})")
    
    db.log_action(chat_id, user_id, username, 'REPORT', 
                  f"Пожаловался на @{get_username(reported_user)}")
    
    # Обновляем статистику пользователя
    user = db.get_user(chat_id, user_id)
    if user['reports_sent'] + 1 >= 10:
        if db.add_achievement(chat_id, user_id, 'reporter'):
            bot.send_message(chat_id, f"🏆 @{username} получил достижение: {ACHIEVEMENTS['reporter']['emoji']} {ACHIEVEMENTS['reporter']['name']}!")
    
    if report_count >= REPORT_LIMIT:
        mute_until = db.mute_user(
            chat_id, 
            reported_user.id, 
            get_username(reported_user), 
            REPORT_MUTE_MINUTES,
            f"Авто-мут по жалобам ({REPORT_LIMIT} чел.)"
        )
        
        mute_time_str = mute_until.strftime("%H:%M")
        bot.send_message(
            chat_id,
            f"🔇 **Пользователь @{get_username(reported_user)} получил мут на {REPORT_MUTE_MINUTES} мин**\n"
            f"Причина: {REPORT_LIMIT} жалобы на сообщение\n"
            f"⏱️ До: {mute_time_str}"
        )
        
        reported_messages.pop(message_key, None)
        
        try:
            bot.delete_message(chat_id, reported_msg.message_id)
        except:
            pass

@bot.message_handler(commands=['reports_clear'])
def clear_reports(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    keys_to_remove = [key for key in reported_messages if key[0] == chat_id]
    for key in keys_to_remove:
        reported_messages.pop(key, None)
    
    bot.reply_to(message, "✅ Все жалобы в чате очищены!")

# ============================================
# СИСТЕМА УРОВНЕЙ И СТАТИСТИКИ
# ============================================
@bot.message_handler(commands=['profile'])
def show_profile(message):
    """Показать профиль пользователя"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = get_username(message.from_user)
    
    user = db.get_user(chat_id, user_id)
    achievements = json.loads(user['achievements']) if user['achievements'] else []
    
    next_level_exp = LEVELS.get(user['level'] + 1, LEVELS[user['level']] * 2)
    exp_needed = next_level_exp - user['exp']
    
    text = f"""
👤 **ПРОФИЛЬ @{username}**

📊 **УРОВЕНЬ:** {user['level']}
✨ **ОПЫТ:** {user['exp']} / {next_level_exp}
📈 **ДО СЛЕДУЮЩЕГО УРОВНЯ:** {exp_needed} опыта

📝 **СТАТИСТИКА:**
• Сообщений: {user['messages']}
• Игр сыграно: {user['games_played']}
• Побед в играх: {user['games_won']}
• Жалоб отправлено: {user['reports_sent']}
• Репутация: {user['reputation']}

🏆 **ДОСТИЖЕНИЯ ({len(achievements)}/{len(ACHIEVEMENTS)}):**
"""
    
    if achievements:
        for ach in achievements:
            if ach in ACHIEVEMENTS:
                text += f"\n{ACHIEVEMENTS[ach]['emoji']} {ACHIEVEMENTS[ach]['name']} — {ACHIEVEMENTS[ach]['desc']}"
    else:
        text += "\nПока нет достижений. Будь активнее!"
    
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['top'])
def show_top(message):
    """Показать топ пользователей"""
    chat_id = message.chat.id
    
    top_users = db.get_top_users(chat_id, 10)
    
    if not top_users:
        bot.reply_to(message, "📊 Статистика пока пуста")
        return
    
    text = "🏆 **ТОП-10 ПОЛЬЗОВАТЕЛЕЙ**\n\n"
    for i, user in enumerate(top_users, 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
        text += f"{medal} {i}. @{user['username']}\n"
        text += f"   Уровень {user['level']} | Опыт: {user['exp']} | Сообщений: {user['messages']}\n"
    
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['stats'])
def show_stats(message):
    """Показать статистику чата"""
    chat_id = message.chat.id
    
    # Получаем общую статистику
    db.cursor.execute('SELECT COUNT(*) FROM users WHERE chat_id = ?', (chat_id,))
    total_users = db.cursor.fetchone()[0]
    
    db.cursor.execute('SELECT SUM(messages) FROM users WHERE chat_id = ?', (chat_id,))
    total_messages = db.cursor.fetchone()[0] or 0
    
    db.cursor.execute('SELECT AVG(messages) FROM users WHERE chat_id = ?', (chat_id,))
    avg_messages = int(db.cursor.fetchone()[0] or 0)
    
    db.cursor.execute('SELECT COUNT(*) FROM offenders WHERE chat_id = ?', (chat_id,))
    total_offenders = db.cursor.fetchone()[0]
    
    db.cursor.execute('SELECT COUNT(*) FROM logs WHERE chat_id = ? AND action = "WARN"', (chat_id,))
    total_warns = db.cursor.fetchone()[0]
    
    text = f"""
📊 **СТАТИСТИКА ЧАТА**

👥 **УЧАСТНИКИ:**
• Всего в базе: {total_users}
• Активных сегодня: ?

💬 **СООБЩЕНИЯ:**
• Всего: {total_messages}
• В среднем на пользователя: {avg_messages}

⚠️ **НАРУШЕНИЯ:**
• Нарушителей: {total_offenders}
• Всего предупреждений: {total_warns}

🎮 **ИГРЫ:**
• Сыграно игр: ?
• Побед: ?

🏆 **ТОП-3 АКТИВНЫХ:"""
    
    top_users = db.get_top_users(chat_id, 3)
    for i, user in enumerate(top_users, 1):
        text += f"\n{i}. @{user['username']} — {user['messages']} сообщ."
    
    bot.reply_to(message, text, parse_mode='Markdown')

# ============================================
# СИСТЕМА СОБЫТИЙ И НАПОМИНАНИЙ
# ============================================
@bot.message_handler(commands=['event'])
def event_command(message):
    """Создание и управление событиями"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы могут создавать события!")
        return
    
    parts = message.text.split(maxsplit=3)
    if len(parts) < 4:
        bot.reply_to(message, "❌ Использование: /event создать | Название | ДД.ММ ЧЧ:ММ | Описание")
        return
    
    action = parts[1].lower()
    if action == "создать":
        try:
            title = parts[2]
            date_str = parts[3].split('|')[0].strip()
            description = parts[3].split('|')[1].strip() if '|' in parts[3] else ""
            
            event_date = datetime.strptime(date_str, "%d.%m %H:%M")
            event_date = event_date.replace(year=datetime.now().year)
            
            event_id = db.add_event(chat_id, title, description, event_date, user_id)
            bot.reply_to(message, f"✅ Событие создано!\nID: {event_id}\nНазвание: {title}\n📅 {event_date.strftime('%d.%m.%Y %H:%M')}")
        except Exception as e:
            bot.reply_to(message, f"❌ Ошибка: {e}")
    
    elif action == "список":
        events = db.get_events(chat_id)
        if not events:
            bot.reply_to(message, "📅 Нет предстоящих событий")
            return
        
        text = "📅 **ПРЕДСТОЯЩИЕ СОБЫТИЯ:**\n\n"
        for event in events:
            participants = json.loads(event['participants']) if event['participants'] else []
            text += f"🔹 **{event['title']}**\n"
            text += f"   ID: {event['id']} | 📅 {datetime.fromisoformat(event['event_date']).strftime('%d.%m %H:%M')}\n"
            text += f"   👥 Участников: {len(participants)}\n"
            if event['description']:
                text += f"   📝 {event['description']}\n"
            text += "\n"
        
        bot.reply_to(message, text, parse_mode='Markdown')
    
    elif action == "идти":
        try:
            event_id = int(parts[2])
            username = get_username(message.from_user)
            if db.add_participant(event_id, user_id, username):
                bot.reply_to(message, f"✅ Ты записан на событие!")
            else:
                bot.reply_to(message, "⚠️ Ты уже записан или событие не найдено")
        except:
            bot.reply_to(message, "❌ Использование: /event идти [ID]")

@bot.message_handler(commands=['remind'])
def remind_command(message):
    """Создание напоминания"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = get_username(message.from_user)
    
    parts = message.text.split(maxsplit=2)
    if len(parts) < 3:
        bot.reply_to(message, "❌ Использование: /remind завтра 15:00 Купить молоко")
        return
    
    time_str = parts[1]
    text = parts[2]
    
    try:
        # Простой парсинг времени
        if time_str.lower() == "завтра":
            reminder_time = datetime.now() + timedelta(days=1)
            reminder_time = reminder_time.replace(hour=12, minute=0, second=0, microsecond=0)
        elif ":" in time_str:
            # Сегодня в указанное время
            hour, minute = map(int, time_str.split(':'))
            reminder_time = datetime.now().replace(hour=hour, minute=minute, second=0, microsecond=0)
            if reminder_time < datetime.now():
                reminder_time += timedelta(days=1)
        else:
            # Через N минут/часов
            if 'ч' in time_str:
                hours = int(time_str.replace('ч', ''))
                reminder_time = datetime.now() + timedelta(hours=hours)
            elif 'м' in time_str:
                minutes = int(time_str.replace('м', ''))
                reminder_time = datetime.now() + timedelta(minutes=minutes)
            else:
                bot.reply_to(message, "❌ Не понимаю формат времени. Используй: завтра, ЧЧ:ММ, Xч, Xм")
                return
        
        reminder_id = db.add_reminder(chat_id, user_id, username, text, reminder_time)
        bot.reply_to(message, f"✅ Напоминание создано!\nID: {reminder_id}\n⏰ {reminder_time.strftime('%d.%m %H:%M')}\n📝 {text}")
        
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

# ============================================
# ИГРЫ И РАЗВЛЕЧЕНИЯ
# ============================================
@bot.message_handler(commands=['game'])
def game_menu(message):
    """Меню игр"""
    text = """
🎮 **ИГРЫ В ЧАТЕ**

Доступные игры:
/game dice [ставка] — бросить кубик
/game coin [ставка] — орёл/решка
/game guess — угадай число (1-100)
/quiz — викторина с вопросами
/wordchain — игра в цепочку слов
/leaderboard — таблица лидеров
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['quiz'])
def quiz_command(message):
    """Викторина"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    question = db.get_random_question(chat_id)
    if not question:
        bot.reply_to(message, "❌ Вопросов пока нет. Добавьте через /quiz_add")
        return
    
    markup = types.InlineKeyboardMarkup(row_width=2)
    buttons = []
    for i, option in enumerate(question['options']):
        callback_data = f"quiz_{question['id']}_{i}"
        buttons.append(types.InlineKeyboardButton(option, callback_data=callback_data))
    markup.add(*buttons)
    
    bot.send_message(
        chat_id,
        f"❓ **{question['question']}**\n\nВыбери правильный ответ:",
        reply_markup=markup,
        parse_mode='Markdown'
    )

@bot.message_handler(commands=['quiz_add'])
def quiz_add_command(message):
    """Добавить вопрос в викторину (только для админов)"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы могут добавлять вопросы!")
        return
    
    parts = message.text.split('|')
    if len(parts) < 3:
        bot.reply_to(message, "❌ Использование: /quiz_add Вопрос | Правильный ответ | Вариант1 | Вариант2 | ...")
        return
    
    question = parts[0].replace('/quiz_add', '').strip()
    answer = parts[1].strip()
    options = [opt.strip() for opt in parts[2:]]
    
    if answer not in options:
        options.append(answer)
        random.shuffle(options)
    
    db.add_quiz_question(chat_id, question, answer, options, user_id)
    bot.reply_to(message, "✅ Вопрос добавлен в викторину!")

@bot.message_handler(commands=['leaderboard'])
def leaderboard_command(message):
    """Таблица лидеров в играх"""
    chat_id = message.chat.id
    
    leaders = db.get_game_leaderboard(chat_id)
    
    if not leaders:
        bot.reply_to(message, "📊 Статистика игр пока пуста")
        return
    
    text = "🎮 **ТАБЛИЦА ЛИДЕРОВ В ИГРАХ**\n\n"
    for i, (username, played, won, score) in enumerate(leaders[:10], 1):
        medal = "🥇" if i == 1 else "🥈" if i == 2 else "🥉" if i == 3 else "🔹"
        win_rate = (won / played * 100) if played > 0 else 0
        text += f"{medal} {i}. @{username}\n"
        text += f"   Игр: {played} | Побед: {won} ({win_rate:.1f}%) | Очков: {score}\n"
    
    bot.reply_to(message, text, parse_mode='Markdown')

# ============================================
# ПОЛЕЗНЫЕ УТИЛИТЫ
# ============================================
@bot.message_handler(commands=['weather'])
def weather_command(message):
    """Погода (демо-версия)"""
    city = message.text.replace('/weather', '').strip()
    if not city:
        bot.reply_to(message, "❌ Укажи город: /weather Москва")
        return
    
    # Здесь должна быть интеграция с API погоды
    bot.reply_to(message, f"🌤️ Погода в {city}:\nТемпература: +18°C\nВлажность: 65%\nВетер: 3 м/с")

@bot.message_handler(commands=['currency'])
def currency_command(message):
    """Курсы валют (демо-версия)"""
    text = """
💰 **КУРСЫ ВАЛЮТ**

USD: 450 ₸
EUR: 490 ₸
RUB: 5.2 ₸
CNY: 62 ₸
    """
    bot.reply_to(message, text, parse_mode='Markdown')

@bot.message_handler(commands=['calc'])
def calc_command(message):
    """Калькулятор"""
    expr = message.text.replace('/calc', '').strip()
    if not expr:
        bot.reply_to(message, "❌ Пример: /calc 2+2*2")
        return
    
    try:
        # Безопасное вычисление
        result = eval(expr, {"__builtins__": {}})
        bot.reply_to(message, f"📱 {expr} = {result}")
    except:
        bot.reply_to(message, "❌ Ошибка в выражении")

# ============================================
# КОМАНДЫ БОТА
# ============================================
@bot.message_handler(commands=['start'])
def start(message):
    text = """
🔥 **MEGA ULTRA IMBA BOT** 🔥

**🤖 ВОЗМОЖНОСТИ:**

🛡️ **АНТИСПАМ:**
• Флуд, капс, эмодзи, повторы, ссылки, мат
• Авто-мут после N варнов
• Ручной мут (/mute)
• Система жалоб (/report)

📊 **СТАТИСТИКА:**
/profile — твой профиль и достижения
/top — топ пользователей
/stats — статистика чата

🎮 **ИГРЫ:**
/game — меню игр
/quiz — викторина
/leaderboard — таблица лидеров

📅 **ПЛАНИРОВЩИК:**
/event — создать событие
/remind — напоминание

🛠️ **УТИЛИТЫ:**
/weather [город] — погода
/currency — курсы валют
/calc [выражение] — калькулятор

👑 **АДМИН КОМАНДЫ:**
/functions - управление функциями
/settings - настройки
/logs - логи нарушений
/add_admin - добавить админа
/quiz_add - добавить вопрос
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
        types.InlineKeyboardButton(f"{'✅' if settings['flood_enabled'] else '❌'} Флуд", callback_data="toggle_flood"),
        types.InlineKeyboardButton(f"{'✅' if settings['caps_enabled'] else '❌'} Капс", callback_data="toggle_caps"),
        types.InlineKeyboardButton(f"{'✅' if settings['emoji_enabled'] else '❌'} Эмодзи", callback_data="toggle_emoji"),
        types.InlineKeyboardButton(f"{'✅' if settings['repeat_enabled'] else '❌'} Повторы", callback_data="toggle_repeat"),
        types.InlineKeyboardButton(f"{'✅' if settings['links_enabled'] else '❌'} Ссылки", callback_data="toggle_links"),
        types.InlineKeyboardButton(f"{'✅' if settings['swear_enabled'] else '❌'} Бан-слова", callback_data="toggle_swear"),
        types.InlineKeyboardButton(f"{'✅' if settings['media_enabled'] else '❌'} Медиа", callback_data="toggle_media"),
        types.InlineKeyboardButton(f"{'✅' if settings['welcome_enabled'] else '❌'} Приветствие", callback_data="toggle_welcome"),
        types.InlineKeyboardButton(f"{'✅' if settings['auto_mute'] else '❌'} Авто-мут", callback_data="toggle_mute"),
        types.InlineKeyboardButton(f"{'✅' if settings['games_enabled'] else '❌'} Игры", callback_data="toggle_games"),
        types.InlineKeyboardButton(f"{'✅' if settings['stats_enabled'] else '❌'} Статистика", callback_data="toggle_stats"),
        types.InlineKeyboardButton(f"{'✅' if settings['leveling_enabled'] else '❌'} Уровни", callback_data="toggle_leveling"),
        types.InlineKeyboardButton("⚙️ Настройки", callback_data="settings_menu"),
        types.InlineKeyboardButton("📋 Логи", callback_data="logs_menu")
    ]
    markup.add(*buttons)
    
    bot.reply_to(message, "🔧 **УПРАВЛЕНИЕ ФУНКЦИЯМИ**\nНажми чтобы вкл/выкл:", reply_markup=markup, parse_mode='Markdown')

@bot.message_handler(commands=['settings'])
def settings_command(message):
    """Показать настройки группы"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    try:
        settings = db.get_group_settings(chat_id)
        mute_minutes = settings['mute_time'] // 60
        
        def escape_md(text):
            return str(text).replace('_', '\\_').replace('*', '\\*').replace('`', '\\`')
        
        text = f"""
⚙️ **НАСТРОЙКИ ГРУППЫ** ⚙️

📌 **ОСНОВНЫЕ:**
• Флуд: {escape_md(settings.get('max_messages', 4))} за {escape_md(settings.get('time_window', 3))} сек
• Капс: >{escape_md(settings.get('caps_limit', 50))}%
• Эмодзи: >{escape_md(settings.get('emoji_limit', 5))}
• Ссылки: кд {escape_md(settings.get('link_kd', 10))} мин
• Медиа: {escape_md(settings.get('media_limit', 3))} за 5 сек

🔨 **НАКАЗАНИЯ:**
• Лимит варнов: {escape_md(settings.get('warn_limit', 5))}
• Время мута: {escape_md(mute_minutes)} мин
• Авто-сброс варнов: через {escape_md(settings.get('warn_reset_time', 24))} ч
• Авто-мут: {'✅' if settings.get('auto_mute', True) else '❌'}

🎮 **ИГРЫ И РАЗВЛЕЧЕНИЯ:**
• Игры: {'✅' if settings.get('games_enabled', True) else '❌'}
• Статистика: {'✅' if settings.get('stats_enabled', True) else '❌'}
• Система уровней: {'✅' if settings.get('leveling_enabled', True) else '❌'}

📏 **ДРУГОЕ:**
• Макс длина: {escape_md(settings.get('max_length', 1000))} симв.
• Статус антиспама: {'✅ Вкл' if settings.get('enabled', True) else '❌ Выкл'}

📝 **СИСТЕМА ЖАЛОБ:**
• Лимит жалоб: {REPORT_LIMIT}
• Время мута: {REPORT_MUTE_MINUTES} мин
        """
        bot.reply_to(message, text, parse_mode='Markdown')
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

@bot.message_handler(commands=['logs'])
def show_logs(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    
    logs = db.get_logs(chat_id, 15)
    if not logs:
        bot.reply_to(message, "📝 Логов пока нет")
        return
    
    text = "📋 **ПОСЛЕДНИЕ ДЕЙСТВИЯ:**\n\n"
    for log in logs:
        emoji = "⚠️" if log['action'] == 'WARN' else "🔇" if log['action'] == 'MUTE' else "📢" if log['action'] == 'REPORT' else "✅"
        text += f"{emoji} @{log['username']}: {log['reason']}\n   🕒 {log['timestamp'][:19]}\n\n"
    
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
        
        mute_until = db.mute_user(chat_id, target.id, target_name, minutes, 
                                  f"Ручной мут от @{get_username(message.from_user)}")
        
        mute_time_str = mute_until.strftime("%H:%M %d.%m.%Y")
        bot.reply_to(message, f"🔇 @{target_name} замучен на {minutes} мин!\n⏱️ До: {mute_time_str}")
        
        try:
            bot.delete_message(chat_id, message.reply_to_message.message_id)
        except:
            pass
    except Exception as e:
        bot.reply_to(message, f"❌ Ошибка: {e}")

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
def list_admins(message):
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
        bot.reply_to(message, "❌ Использование: /greeting [текст] (используй {user} для имени)")

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

@bot.message_handler(commands=['set_media_limit'])
def set_media_limit(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
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
            seconds = minutes * 60
            db.update_setting(chat_id, 'mute_time', seconds)
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

@bot.message_handler(commands=['set_warn_reset'])
def set_warn_reset(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id): 
        bot.reply_to(message, "❌ Только админы!")
        return
    try:
        val = int(message.text.split()[1])
        if 1 <= val <= 168:
            db.update_setting(chat_id, 'warn_reset_time', val)
            bot.reply_to(message, f"✅ Авто-сброс варнов: через {val} ч")
    except:
        bot.reply_to(message, "❌ /set_warn_reset [1-168] (часов)")

@bot.message_handler(commands=['antispam_on'])
def antispam_on(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    db.update_setting(chat_id, 'enabled', 1)
    bot.reply_to(message, "🟢 **АНТИСПАМ ВКЛЮЧЕН!**", parse_mode='Markdown')

@bot.message_handler(commands=['antispam_off'])
def antispam_off(message):
    chat_id = message.chat.id
    user_id = message.from_user.id
    if not is_admin(chat_id, user_id):
        bot.reply_to(message, "❌ Только админы!")
        return
    db.update_setting(chat_id, 'enabled', 0)
    bot.reply_to(message, "🔴 **АНТИСПАМ ВЫКЛЮЧЕН!**", parse_mode='Markdown')

@bot.message_handler(commands=['fix'])
def fix_admin(message):
    """Принудительно сделать админом"""
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = get_username(message.from_user)
    
    db.add_group_admin(chat_id, user_id, username, SUPER_ADMIN_ID)
    bot.reply_to(message, f"✅ @{username} принудительно добавлен в админы!")

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
    
    elif call.data == "settings_menu":
        settings_command(call.message)
    
    elif call.data == "logs_menu":
        show_logs(call.message)
    
    elif call.data.startswith('quiz_'):
        parts = call.data.split('_')
        question_id = int(parts[1])
        answer_idx = int(parts[2])
        
        db.cursor.execute('SELECT answer, options FROM quiz_questions WHERE id = ?', (question_id,))
        result = db.cursor.fetchone()
        if result:
            correct_answer = result[0]
            options = json.loads(result[1])
            selected = options[answer_idx] if answer_idx < len(options) else ""
            
            if selected == correct_answer:
                bot.answer_callback_query(call.id, "✅ Правильно! +10 очков")
                
                # Обновляем статистику
                db.update_game_stats(chat_id, user_id, get_username(call.from_user), 'quiz', won=True, score=10)
                
                # Проверка на достижение
                user = db.get_user(chat_id, user_id)
                if user['games_played'] + 1 >= 10:
                    if db.add_achievement(chat_id, user_id, 'game_master'):
                        bot.send_message(chat_id, f"🏆 @{get_username(call.from_user)} получил достижение: {ACHIEVEMENTS['game_master']['emoji']} {ACHIEVEMENTS['game_master']['name']}!")
                
                bot.edit_message_text(
                    f"✅ Правильно! +10 очков\n\nВопрос: {call.message.text}",
                    chat_id,
                    call.message.message_id
                )
            else:
                bot.answer_callback_query(call.id, f"❌ Неправильно! Правильный ответ: {correct_answer}")
                bot.edit_message_text(
                    f"❌ Неправильно!\nПравильный ответ: {correct_answer}\n\n{call.message.text}",
                    chat_id,
                    call.message.message_id
                )

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
                "🤖 **MEGA ULTRA IMBA BOT АКТИВИРОВАН!**\n"
                "🛡️ Антиспам | 📊 Статистика | 🎮 Игры | 📅 События\n"
                "👑 /functions - управление\n"
                "🆘 /fix - если не работают команды",
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
        bot.reply_to(message, "🤖 Добавь меня в группу!")
        return
    
    chat_id = message.chat.id
    user_id = message.from_user.id
    username = get_username(message.from_user)
    first_name = message.from_user.first_name or ""
    
    # Обновляем активность пользователя (для системы уровней)
    settings = db.get_group_settings(chat_id)
    if settings.get('leveling_enabled', True):
        new_level = db.update_user_activity(chat_id, user_id, username, first_name)
        if new_level:
            bot.send_message(chat_id, f"🌟 @{username} достиг {new_level} уровня!")
        
        # Проверка на первые сообщения
        user = db.get_user(chat_id, user_id)
        if user['messages'] == 1:
            if db.add_achievement(chat_id, user_id, 'first_message'):
                bot.send_message(chat_id, f"🏆 @{username} получил достижение: {ACHIEVEMENTS['first_message']['emoji']} {ACHIEVEMENTS['first_message']['name']}!")
        elif user['messages'] == 100:
            if db.add_achievement(chat_id, user_id, '100_messages'):
                bot.send_message(chat_id, f"🏆 @{username} получил достижение: {ACHIEVEMENTS['100_messages']['emoji']} {ACHIEVEMENTS['100_messages']['name']}!")
        elif user['messages'] == 1000:
            if db.add_achievement(chat_id, user_id, '1000_messages'):
                bot.send_message(chat_id, f"🏆 @{username} получил достижение: {ACHIEVEMENTS['1000_messages']['emoji']} {ACHIEVEMENTS['1000_messages']['name']}!")
        
        # Проверка на ночную сову
        current_hour = datetime.now().hour
        if current_hour >= 0 and current_hour <= 5:
            if db.add_achievement(chat_id, user_id, 'night_owl'):
                bot.send_message(chat_id, f"🏆 @{username} получил достижение: {ACHIEVEMENTS['night_owl']['emoji']} {ACHIEVEMENTS['night_owl']['name']}!")
    
    # Очистка старых жалоб
    if random.randint(1, 100) == 1:
        keys_to_remove = [key for key in reported_messages if key[0] == chat_id]
        for key in keys_to_remove:
            reported_messages.pop(key, None)
    
    # Проверка на спам
    is_allowed, warning = spam_filter.check_message(message)
    
    if not is_allowed and warning:
        try:
            bot.delete_message(chat_id, message.message_id)
            bot.send_message(chat_id, warning)
        except:
            pass

# ============================================
# ФОНОВЫЙ ПРОЦЕСС ДЛЯ НАПОМИНАНИЙ
# ============================================
def check_reminders():
    """Проверка и отправка напоминаний"""
    while True:
        try:
            reminders = db.get_due_reminders()
            for reminder in reminders:
                try:
                    bot.send_message(
                        reminder['chat_id'],
                        f"⏰ **НАПОМИНАНИЕ**\n\n@{reminder['username']}, {reminder['reminder_text']}",
                        parse_mode='Markdown'
                    )
                    db.mark_reminder_done(reminder['id'])
                except:
                    pass
            time.sleep(60)  # Проверка раз в минуту
        except:
            time.sleep(60)

# Запуск фонового процесса
import threading
reminder_thread = threading.Thread(target=check_reminders, daemon=True)
reminder_thread.start()

# ============================================
# ЗАПУСК НА RENDER
# ============================================
@app.route('/')
def home():
    return "🔥 MEGA ULTRA IMBA БОТ РАБОТАЕТ! 🔥", 200

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
    print(f"✅ Бот @{me.username} запущен!")
    return True

if __name__ == '__main__':
    print("🔥 ЗАПУСК MEGA ULTRA IMBA БОТА")
    set_webhook()
    port = int(os.environ.get('PORT', 10000))
    app.run(host='0.0.0.0', port=port)