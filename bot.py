#!/usr/bin/env python3
"""
Telegram-бот для HR-отдела компании "Мечел"
Версия 12.3 (ультимативная, Render-ready) — промышленный стандарт
"""

import os
import sys
import asyncio
import logging
import traceback
import json
import time
import functools
import hashlib
import re
import secrets
import io
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Set
from collections import defaultdict, deque
from urllib.parse import quote_plus

# ------------------------------------------------------------
#  ПРОВЕРКА КРИТИЧЕСКИХ ЗАВИСИМОСТЕЙ (ИСПРАВЛЕНО!)
# ------------------------------------------------------------
def check_critical_dependencies():
    """Проверка наличия критических зависимостей через importlib.metadata"""
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:
        # Для очень старых версий Python (на Render не актуально)
        try:
            from importlib_metadata import version, PackageNotFoundError
        except ImportError:
            print("❌ Не удалось импортировать importlib.metadata", file=sys.stderr)
            print("Установите importlib-metadata: pip install importlib-metadata", file=sys.stderr)
            sys.exit(1)

    critical_deps = ['quart', 'python-telegram-bot', 'hypercorn']
    missing = []

    for dep in critical_deps:
        try:
            ver = version(dep)
            print(f"✅ {dep} версия {ver} установлена")
        except PackageNotFoundError:
            missing.append(dep)

    if missing:
        print(f"❌ Отсутствуют критические зависимости: {', '.join(missing)}", file=sys.stderr)
        print(f"Установите их: pip install {' '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    print("✅ Все критические зависимости установлены")

# Выполняем проверку немедленно
check_critical_dependencies()

# ------------------------------------------------------------
#  ПРОВЕРКА КОНФИГУРАЦИОННЫХ ФАЙЛОВ
# ------------------------------------------------------------
def check_config_files():
    """Проверка наличия обязательных файлов конфигурации и модулей"""
    required_files = ['config.py', 'search_engine.py', 'bot_handlers.py']
    missing = [f for f in required_files if not os.path.exists(f)]

    if missing:
        print(f"❌ Отсутствуют обязательные файлы: {', '.join(missing)}", file=sys.stderr)
        print("Бот не может быть запущен без этих файлов.", file=sys.stderr)
        sys.exit(1)
    print("✅ Все обязательные файлы присутствуют")

check_config_files()

# ------------------------------------------------------------
#  ИМПОРТЫ ПОСЛЕ ПРОВЕРОК
# ------------------------------------------------------------
from quart import Quart, request, jsonify, send_file
import hypercorn
from hypercorn.config import Config
from hypercorn.asyncio import serve

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    CallbackQueryHandler,
    filters,
    ContextTypes,
    ApplicationBuilder
)
from telegram.error import TelegramError

import pandas as pd
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
from dotenv import load_dotenv

# ------------------------------------------------------------
#  НАСТРОЙКА ЛОГИРОВАНИЯ
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
#  ЗАГРУЗКА ПЕРЕМЕННЫХ ОКРУЖЕНИЯ
# ------------------------------------------------------------
load_dotenv()

# ------------------------------------------------------------
#  ФУНКЦИЯ ВАЛИДАЦИИ ТОКЕНА
# ------------------------------------------------------------
def validate_token(token: str) -> bool:
    """Валидация формата токена бота"""
    if not token:
        return False
    return len(token) > 30 and ':' in token

# ------------------------------------------------------------
#  КОНФИГУРАЦИЯ И ВАЛИДАЦИЯ
# ------------------------------------------------------------
BOT_TOKEN = os.getenv('TELEGRAM_BOT_TOKEN', '')

if not validate_token(BOT_TOKEN):
    logger.critical("❌ TELEGRAM_BOT_TOKEN не установлен или имеет неверный формат!")
    sys.exit(1)

RENDER = os.getenv('RENDER', 'false').lower() == 'true'
PORT = int(os.getenv('PORT', 8080))

# WEBHOOK конфигурация
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')
if not WEBHOOK_SECRET:
    # Генерация секрета на основе токена (для разработки)
    WEBHOOK_SECRET = 'mechel_hr_bot_secret_' + hashlib.md5(BOT_TOKEN.encode()).hexdigest()[:16]
    if RENDER:
        logger.warning(
            "⚠️ Используется сгенерированный секретный ключ. "
            "Рекомендуется установить вручную через переменную окружения WEBHOOK_SECRET"
        )

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')

if RENDER and not WEBHOOK_URL:
    logger.critical("❌ WEBHOOK_URL должен быть установлен в Render!")
    sys.exit(1)

ADMIN_IDS = []
try:
    admin_ids_str = os.getenv('ADMIN_IDS', '')
    if admin_ids_str:
        ADMIN_IDS = [int(id_str.strip()) for id_str in admin_ids_str.split(',')]
except ValueError as e:
    logger.error(f"❌ Ошибка парсинга ADMIN_IDS: {e}")

# ------------------------------------------------------------
#  ГЛОБАЛЬНЫЕ ОБЪЕКТЫ
# ------------------------------------------------------------
application: Optional[Application] = None
search_engine = None
bot_stats = None

# ------------------------------------------------------------
#  КЛАСС СТАТИСТИКИ (с метриками времени ответа)
# ------------------------------------------------------------
class BotStatistics:
    """Класс для сбора статистики и метрик производительности"""

    def __init__(self, max_history_days: int = 90):
        self.start_time = datetime.now()
        self.user_stats = defaultdict(lambda: {
            'messages': 0,
            'commands': 0,
            'searches': 0,
            'last_active': None,
            'first_seen': None,
            'feedback_count': 0
        })
        self.daily_stats = defaultdict(lambda: {
            'messages': 0,
            'commands': 0,
            'searches': 0,
            'users': set(),
            'feedback': 0,
            'response_times': []
        })
        self.command_stats = defaultdict(int)
        self.feedback_list = []
        self.error_log = deque(maxlen=1000)
        self.response_times = deque(maxlen=100)
        self.cache = {}
        self.cache_ttl = {}
        self.max_history_days = max_history_days
        self._last_cleanup = datetime.now()

    def track_response_time(self, response_time: float):
        self.response_times.append({
            'timestamp': datetime.now(),
            'response_time': response_time
        })
        date_key = datetime.now().strftime("%Y-%m-%d")
        self.daily_stats[date_key]['response_times'].append(response_time)

    def get_avg_response_time(self) -> float:
        if not self.response_times:
            return 0.0
        times = [rt['response_time'] for rt in self.response_times]
        return sum(times) / len(times)

    def get_response_time_status(self) -> Tuple[str, str]:
        avg_time = self.get_avg_response_time()
        if avg_time < 1.0:
            return "Хорошо", "green"
        elif avg_time < 3.0:
            return "Нормально", "yellow"
        else:
            return "Медленно", "red"

    def _cleanup_old_data(self):
        now = datetime.now()
        if (now - self._last_cleanup).seconds < 3600:
            return
        cutoff_date = (now - timedelta(days=self.max_history_days)).strftime("%Y-%m-%d")
        for date_key in list(self.daily_stats.keys()):
            if date_key < cutoff_date:
                del self.daily_stats[date_key]
        expired_keys = [k for k, t in self.cache_ttl.items() if now > t]
        for key in expired_keys:
            self.cache.pop(key, None)
            self.cache_ttl.pop(key, None)
        self._last_cleanup = now

    def log_message(self, user_id: int, username: str, message_type: str, text: str = ""):
        self._cleanup_old_data()
        now = datetime.now()
        date_key = now.strftime("%Y-%m-%d")

        if self.user_stats[user_id]['first_seen'] is None:
            self.user_stats[user_id]['first_seen'] = now
        self.user_stats[user_id]['last_active'] = now

        if message_type == 'command':
            self.user_stats[user_id]['commands'] += 1
            self.command_stats[text] = self.command_stats.get(text, 0) + 1
            self.daily_stats[date_key]['commands'] += 1
        elif message_type == 'message':
            self.user_stats[user_id]['messages'] += 1
            self.daily_stats[date_key]['messages'] += 1
        elif message_type == 'search':
            self.user_stats[user_id]['searches'] += 1
            self.daily_stats[date_key]['searches'] += 1
        elif message_type == 'feedback':
            self.user_stats[user_id]['feedback_count'] += 1
            self.daily_stats[date_key]['feedback'] += 1
            self.feedback_list.append({
                'user_id': user_id,
                'username': username,
                'text': text,
                'timestamp': now
            })

        self.daily_stats[date_key]['users'].add(user_id)

    def log_error(self, error_type: str, error_msg: str, user_id: int = None):
        self.error_log.append({
            'timestamp': datetime.now(),
            'type': error_type,
            'message': error_msg,
            'user_id': user_id
        })

    def get_summary_stats(self) -> Dict[str, Any]:
        self._cleanup_old_data()
        total_users = len(self.user_stats)
        active_users_24h = sum(1 for user_data in self.user_stats.values()
                               if user_data['last_active'] and
                               datetime.now() - user_data['last_active'] < timedelta(hours=24))

        days_stats = {}
        for date_key in sorted(self.daily_stats.keys(), reverse=True)[:30]:
            days_stats[date_key] = {
                'messages': self.daily_stats[date_key]['messages'],
                'commands': self.daily_stats[date_key]['commands'],
                'searches': self.daily_stats[date_key]['searches'],
                'users': len(self.daily_stats[date_key]['users']),
                'feedback': self.daily_stats[date_key]['feedback'],
                'avg_response_time': (
                    sum(self.daily_stats[date_key]['response_times']) /
                    len(self.daily_stats[date_key]['response_times'])
                    if self.daily_stats[date_key]['response_times'] else 0
                )
            }

        avg_response_time = self.get_avg_response_time()
        status, color = self.get_response_time_status()

        return {
            'uptime': str(datetime.now() - self.start_time),
            'start_time': self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            'total_users': total_users,
            'active_users_24h': active_users_24h,
            'total_messages': sum(u['messages'] for u in self.user_stats.values()),
            'total_commands': sum(u['commands'] for u in self.user_stats.values()),
            'total_searches': sum(u['searches'] for u in self.user_stats.values()),
            'total_feedback': len(self.feedback_list),
            'avg_response_time': avg_response_time,
            'response_time_status': status,
            'response_time_color': color,
            'daily_stats': days_stats,
            'top_commands': dict(sorted(self.command_stats.items(), key=lambda x: x[1], reverse=True)[:10]),
            'cache_size': len(self.cache),
            'error_count': len(self.error_log)
        }

# ------------------------------------------------------------
#  ДЕКОРАТОР ИЗМЕРЕНИЯ ВРЕМЕНИ ОТВЕТА
# ------------------------------------------------------------
def measure_response_time(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start_time = time.time()
        try:
            result = await func(*args, **kwargs)
            response_time = time.time() - start_time
            if bot_stats:
                bot_stats.track_response_time(response_time)
            return result
        except Exception as e:
            response_time = time.time() - start_time
            if bot_stats:
                bot_stats.track_response_time(response_time)
            raise e
    return wrapper

# ------------------------------------------------------------
#  ЛОКАЛЬНЫЙ ПОИСКОВЫЙ ДВИЖОК (РЕЗЕРВНЫЙ)
# ------------------------------------------------------------
class SearchEngine:
    """Встроенный поисковый движок с нормализацией запросов (резервный)"""

    def __init__(self, max_cache_size: int = 1000):
        self.max_cache_size = max_cache_size
        self.cache = {}
        self.cache_ttl = {}
        self.faq_data = self._load_faq_data()
        logger.info(f"✅ Загружено {len(self.faq_data)} вопросов во встроенный поисковый движок")

        self.stop_words = {
            'как', 'что', 'где', 'когда', 'почему', 'зачем', 'сколько', 'чей', 'чье',
            'а', 'и', 'но', 'или', 'если', 'то', 'же', 'бы', 'в', 'на', 'с', 'по',
            'о', 'об', 'от', 'до', 'для', 'из', 'у', 'не', 'нет', 'да', 'это', 'тот',
            'этот', 'такой', 'какой', 'все', 'всё', 'его', 'ее', 'их', 'им', 'ними'
        }

    def _normalize_query(self, query: str) -> str:
        query = query.lower().strip()
        query = re.sub(r'[^\w\s]', ' ', query)
        words = [w for w in query.split() if w not in self.stop_words and len(w) > 2]

        normalized_words = []
        for word in words:
            if word.endswith('ться'):
                word = word[:-4] + 'ть'
            elif word.endswith('тся'):
                word = word[:-3] + 'ться'
            elif word.endswith('ать') and len(word) > 4:
                word = word[:-3]
            elif word.endswith('ить') and len(word) > 4:
                word = word[:-3]
            elif word.endswith('еть') and len(word) > 4:
                word = word[:-3]
            elif word.endswith('ый') or word.endswith('ий') or word.endswith('ой'):
                word = word[:-2]
            elif word.endswith('ая') or word.endswith('яя'):
                word = word[:-2]
            elif word.endswith('ое') or word.endswith('ее'):
                word = word[:-2]
            normalized_words.append(word)

        return ' '.join(normalized_words)

    def _load_faq_data(self) -> List[Dict[str, Any]]:
        if not os.path.exists('faq_data.py'):
            logger.warning("⚠️ Файл faq_data.py не найден, используются резервные вопросы")
            return self._get_backup_questions()

        try:
            from faq_data import get_faq_data
            data = get_faq_data()
            logger.info("✅ Загружены данные через get_faq_data()")
            return data
        except ImportError:
            try:
                from faq_data import FAQ_QUESTIONS
                logger.info("✅ Загружены данные через FAQ_QUESTIONS")
                return FAQ_QUESTIONS
            except ImportError:
                logger.warning("⚠️ Не удалось импортировать данные из faq_data.py, используются резервные вопросы")
                return self._get_backup_questions()

    def _get_backup_questions(self) -> List[Dict[str, Any]]:
        return [
            {
                "question": "Как получить справку о заработной плате?",
                "answer": "Справку о заработной плате можно получить в отделе кадров (каб. 205) или через корпоративный портал в разделе 'Документы'.",
                "category": "Документы",
                "keywords": ["справка", "зарплата", "заработная", "плата", "документ"]
            },
            {
                "question": "Как оформить отпуск?",
                "answer": "Для оформления отпуска необходимо:\n1. Заполнить заявление в корпоративном портале\n2. Согласовать с руководителем отдела\n3. Получить визу в отделе кадров\n4. Подписать приказ",
                "category": "Отпуск",
                "keywords": ["отпуск", "оформить", "заявление", "отдых", "каникулы"]
            }
        ]

    def search(self, query: str, category: Optional[str] = None) -> List[Dict[str, Any]]:
        cache_key = f"{query}_{category}"
        if cache_key in self.cache:
            if datetime.now() < self.cache_ttl.get(cache_key, datetime.now()):
                return self.cache[cache_key]
            else:
                del self.cache[cache_key]
                del self.cache_ttl[cache_key]

        normalized_query = self._normalize_query(query)
        results = []

        for item in self.faq_data:
            if category and item.get('category') != category:
                continue

            question_score = self._calculate_score(normalized_query, item['question'].lower())
            keyword_score = 0
            for kw in item.get('keywords', []):
                keyword_score += self._calculate_score(normalized_query, kw.lower())
            answer_score = self._calculate_score(normalized_query, item['answer'].lower()) * 0.5

            total_score = question_score * 2 + keyword_score * 1.5 + answer_score
            if total_score > 0.3:
                results.append({
                    **item,
                    'score': total_score,
                    'matched_query': normalized_query
                })

        results.sort(key=lambda x: x['score'], reverse=True)

        if len(self.cache) >= self.max_cache_size:
            oldest_key = next(iter(self.cache_ttl))
            del self.cache[oldest_key]
            del self.cache_ttl[oldest_key]

        self.cache[cache_key] = results
        self.cache_ttl[cache_key] = datetime.now() + timedelta(hours=1)

        return results[:5]

    def _calculate_score(self, query: str, text: str) -> float:
        if not query or not text:
            return 0.0
        if query in text:
            return 1.0
        query_words = set(query.split())
        text_words = set(text.split())
        if not query_words:
            return 0.0
        common = query_words.intersection(text_words)
        return len(common) / len(query_words)

# ------------------------------------------------------------
#  ФУНКЦИЯ POST_INIT (определена ДО использования)
# ------------------------------------------------------------
async def post_init(application: Application):
    """Вызывается после инициализации приложения Telegram"""
    logger.info("✅ Приложение Telegram готово")

# ------------------------------------------------------------
#  ОБРАБОТЧИКИ КОМАНД И СООБЩЕНИЙ
# ------------------------------------------------------------
@measure_response_time
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    logger.info(f"👋 Пользователь {user.id} ({user.username}) запустил бота")
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/start')
    else:
        logger.warning("⚠️ bot_stats не инициализирован")

    welcome_text = (
        f"Привет, {user.first_name}! 👋\n\n"
        "Я HR-бот компании <b>Мечел</b>. Я помогу вам получить ответы на кадровые вопросы:\n\n"
        "📋 <b>Основные возможности:</b>\n"
        "• Ответы на вопросы по кадровой политике\n"
        "• Информация о документах и справках\n"
        "• Консультации по отпускам и больничным\n"
        "• Связь с HR-отделом\n\n"
        "💡 <b>Просто напишите ваш вопрос</b>, и я постараюсь найти на него ответ!\n\n"
        "⚙️ <b>Доступные команды:</b>\n"
        "/help - Получить справку по использованию\n"
        "/categories - Показать категории вопросов\n"
        "/feedback - Оставить отзыв\n"
    )
    if user.id in ADMIN_IDS:
        welcome_text += "\n👑 <b>Административные команды:</b>\n"
        welcome_text += "/stats - Статистика бота\n"
        welcome_text += "/export - Экспорт данных в Excel\n"

    await update.message.reply_text(welcome_text, parse_mode='HTML')

@measure_response_time
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/help')
    help_text = (
        "❓ <b>Как пользоваться ботом:</b>\n\n"
        "1. <b>Задайте вопрос</b> в свободной форме\n"
        "   Пример: \"Как оформить отпуск?\"\n"
        "   Пример: \"Нужна справка о зарплате\"\n\n"
        "2. <b>Используйте категории</b>\n"
        "   Команда /categories покажет темы, по которым я могу помочь\n\n"
        "3. <b>Обратная связь</b>\n"
        "   Если ответ был неполным, используйте /feedback\n\n"
        "4. <b>Поиск</b>\n"
        "   Я ищу по ключевым словам, попробуйте перефразировать вопрос\n\n"
        "📞 <b>Контакты HR-отдела:</b>\n"
        "• Телефон: +7 (3519) 25-60-00\n"
        "• Email: hr@mechel.ru\n"
        "• Кабинет: 205, главный офис\n"
    )
    await update.message.reply_text(help_text, parse_mode='HTML')

@measure_response_time
async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/categories')

    if search_engine is None:
        await update.message.reply_text(
            "⚠️ В настоящий момент категории недоступны. Пожалуйста, попробуйте позже.",
            parse_mode='HTML'
        )
        return

    categories = {}
    for item in search_engine.faq_data:
        cat = item.get('category', 'Без категории')
        categories[cat] = categories.get(cat, 0) + 1

    text = "📂 <b>Доступные категории вопросов:</b>\n\n"
    for cat, cnt in sorted(categories.items()):
        text += f"• <b>{cat}</b> ({cnt} вопросов)\n"

    text += "\n💡 <b>Вы можете:</b>\n"
    text += "1. Написать вопрос, и я найду нужную категорию\n"
    text += "2. Указать категорию в вопросе\n"
    text += "   Пример: \"отпуск: как перенести отпуск?\""

    keyboard = []
    for cat in sorted(categories.keys()):
        if cat != 'Без категории':
            keyboard.append([InlineKeyboardButton(cat, callback_data=f"cat_{cat}")])

    reply_markup = InlineKeyboardMarkup(keyboard) if keyboard else None
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

@measure_response_time
async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/feedback')

    context.user_data['awaiting_feedback'] = True
    text = (
        "💬 <b>Обратная связь</b>\n\n"
        "Пожалуйста, опишите:\n"
        "1. Ваш вопрос или проблему\n"
        "2. Полученный ответ (если был)\n"
        "3. Что можно улучшить\n\n"
        "Ваше мнение поможет сделать бота лучше!"
    )
    await update.message.reply_text(text, parse_mode='HTML')

@measure_response_time
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Эта команда доступна только администраторам.")
        return

    # Проверка состояния приложения
    if application is None or application.bot is None:
        await update.message.reply_text("⚠️ Бот временно недоступен. Пожалуйста, попробуйте позже.")
        return

    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/stats')

    stats = bot_stats.get_summary_stats() if bot_stats else {}
    status_text = "✅ Бот работает нормально" if application else "⚠️ Бот инициализируется"

    text = (
        f"📊 <b>Статистика бота</b>\n"
        f"<i>Обновлено: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}</i>\n\n"
        f"🟢 <b>Статус:</b> {status_text}\n"
        f"⏱️ <b>Время работы:</b> {stats.get('uptime', 'N/A')}\n"
        f"🕒 <b>Запущен:</b> {stats.get('start_time', 'N/A')}\n\n"
        f"👥 <b>Пользователи:</b>\n"
        f"• Всего: {stats.get('total_users', 0)}\n"
        f"• Активные (24ч): {stats.get('active_users_24h', 0)}\n\n"
        f"📨 <b>Активность:</b>\n"
        f"• Сообщения: {stats.get('total_messages', 0)}\n"
        f"• Команды: {stats.get('total_commands', 0)}\n"
        f"• Поиски: {stats.get('total_searches', 0)}\n"
        f"• Отзывы: {stats.get('total_feedback', 0)}\n\n"
        f"⚡ <b>Производительность:</b>\n"
        f"• Ср. время ответа: <b>{stats.get('avg_response_time', 0):.2f}с</b>\n"
        f"• Статус: <span style='color:{stats.get('response_time_color', 'gray')};'>"
        f"{stats.get('response_time_status', 'N/A')}</span>\n"
        f"• Размер кэша: {stats.get('cache_size', 0)}\n"
        f"• Ошибок: {stats.get('error_count', 0)}\n"
    )

    base_url = f"http://localhost:{PORT}" if not RENDER else WEBHOOK_URL.replace('/webhook/', '/')
    keyboard = [
        [InlineKeyboardButton("📊 Веб-статистика", url=base_url)],
        [InlineKeyboardButton("📁 Экспорт в Excel", callback_data="export_excel")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)

@measure_response_time
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("❌ Эта команда доступна только администраторам.")
        return
    await export_to_excel(update, context)

async def export_to_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Экспорт данных в Excel (статистика, время ответа, FAQ, пользователи)"""
    user = update.effective_user

    # Проверка bot_stats и ранний возврат
    if bot_stats is None:
        logger.warning("⚠️ bot_stats не инициализирован при экспорте")
        await update.message.reply_text("⚠️ Экспорт временно недоступен. Статистика не инициализирована.")
        return

    bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/export')

    try:
        output = io.BytesIO()
        workbook = Workbook()

        # ----- Лист 1: Общая статистика -----
        ws1 = workbook.active
        ws1.title = "Общая статистика"
        stats = bot_stats.get_summary_stats()

        ws1['A1'] = "Статистика HR-бота Мечел"
        ws1['A1'].font = Font(bold=True, size=14)
        ws1.merge_cells('A1:D1')

        ws1['A3'] = "Показатель"
        ws1['B3'] = "Значение"
        ws1['A3'].font = ws1['B3'].font = Font(bold=True)

        rows = [
            ("Дата экспорта", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("Время работы", stats.get('uptime', 'N/A')),
            ("Запущен", stats.get('start_time', 'N/A')),
            ("Всего пользователей", stats.get('total_users', 0)),
            ("Активные (24ч)", stats.get('active_users_24h', 0)),
            ("Всего сообщений", stats.get('total_messages', 0)),
            ("Всего команд", stats.get('total_commands', 0)),
            ("Всего поисков", stats.get('total_searches', 0)),
            ("Всего отзывов", stats.get('total_feedback', 0)),
            ("Ср. время ответа", f"{stats.get('avg_response_time', 0):.2f} сек"),
            ("Статус времени ответа", stats.get('response_time_status', 'N/A')),
            ("Размер кэша", stats.get('cache_size', 0)),
            ("Количество ошибок", stats.get('error_count', 0))
        ]

        for i, (label, value) in enumerate(rows, start=4):
            ws1[f'A{i}'] = label
            ws1[f'B{i}'] = value

        # ----- Лист 2: Время ответа -----
        ws2 = workbook.create_sheet("Время ответа")
        ws2['A1'] = "История времени ответа"
        ws2['A1'].font = Font(bold=True, size=14)
        ws2.merge_cells('A1:C1')

        ws2['A3'] = "Время"
        ws2['B3'] = "Время ответа (сек)"
        ws2['C3'] = "Статус"
        for cell in ['A3', 'B3', 'C3']:
            ws2[cell].font = Font(bold=True)

        if hasattr(bot_stats, 'response_times'):
            for i, rt in enumerate(bot_stats.response_times, start=4):
                ws2[f'A{i}'] = rt['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
                ws2[f'B{i}'] = rt['response_time']
                ws2[f'C{i}'] = (
                    "Хорошо" if rt['response_time'] < 1.0 else
                    "Нормально" if rt['response_time'] < 3.0 else
                    "Медленно"
                )

        # ----- Лист 3: FAQ -----
        ws3 = workbook.create_sheet("FAQ База")
        ws3['A1'] = "База знаний FAQ"
        ws3['A1'].font = Font(bold=True, size=14)
        ws3.merge_cells('A1:D1')

        headers = ["Категория", "Вопрос", "Ответ", "Ключевые слова"]
        for col, h in enumerate(headers, start=1):
            cell = ws3.cell(row=3, column=col)
            cell.value = h
            cell.font = Font(bold=True)

        faq_source = search_engine.faq_data if search_engine else []
        if not faq_source:
            ws3.cell(row=4, column=1, value="Нет данных FAQ (поисковый движок недоступен)")
        else:
            for i, item in enumerate(faq_source, start=4):
                ws3.cell(row=i, column=1, value=item.get('category', 'Без категории'))
                ws3.cell(row=i, column=2, value=item.get('question', ''))
                ws3.cell(row=i, column=3, value=item.get('answer', ''))
                ws3.cell(row=i, column=4, value=', '.join(item.get('keywords', [])))

        # ----- Лист 4: Пользователи -----
        ws4 = workbook.create_sheet("Пользователи")
        ws4['A1'] = "Статистика пользователей"
        ws4['A1'].font = Font(bold=True, size=14)
        ws4.merge_cells('A1:G1')

        headers2 = ["ID", "Имя", "Сообщения", "Команды", "Поиски", "Отзывы", "Последняя активность"]
        for col, h in enumerate(headers2, start=1):
            cell = ws4.cell(row=3, column=col)
            cell.value = h
            cell.font = Font(bold=True)

        for i, (uid, udata) in enumerate(bot_stats.user_stats.items(), start=4):
            ws4.cell(row=i, column=1, value=uid)
            ws4.cell(row=i, column=2, value=f"Пользователь {uid}")
            ws4.cell(row=i, column=3, value=udata.get('messages', 0))
            ws4.cell(row=i, column=4, value=udata.get('commands', 0))
            ws4.cell(row=i, column=5, value=udata.get('searches', 0))
            ws4.cell(row=i, column=6, value=udata.get('feedback_count', 0))
            last = udata.get('last_active')
            ws4.cell(row=i, column=7, value=last.strftime("%Y-%m-%d %H:%M:%S") if last else '')

        # Автоширина колонок
        for ws in [ws1, ws2, ws3, ws4]:
            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    try:
                        if cell.value and len(str(cell.value)) > max_len:
                            max_len = len(str(cell.value))
                    except:
                        pass
                ws.column_dimensions[col_letter].width = min(max_len + 2, 50)

        workbook.save(output)
        output.seek(0)

        filename = f"mechel_hr_bot_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await update.message.reply_document(
            document=output.getvalue(),
            filename=filename,
            caption=f"📊 Экспорт данных от {datetime.now().strftime('%d.%m.%Y %H:%M')}\n"
                    f"Файл содержит: статистику, FAQ, пользователей и время ответа."
        )
        logger.info(f"✅ Пользователь {user.id} экспортировал данные в Excel")

    except Exception as e:
        logger.error(f"❌ Ошибка экспорта в Excel: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка при экспорте в Excel: {str(e)}")

@measure_response_time
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()

    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'message')

    if context.user_data.get('awaiting_feedback'):
        context.user_data['awaiting_feedback'] = False
        if bot_stats:
            bot_stats.log_message(user.id, user.username or "Unknown", 'feedback', text)
        await update.message.reply_text("🙏 Спасибо за ваш отзыв! Он будет учтен для улучшения бота.")
        return

    if text.lower() in ['статистика', 'stats'] and user.id in ADMIN_IDS:
        await stats_command(update, context)
        return

    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'search')

    if search_engine is None:
        await update.message.reply_text(
            "⚠️ В настоящий момент поиск временно недоступен. Пожалуйста, попробуйте позже.",
            parse_mode='HTML'
        )
        return

    category = None
    if ':' in text:
        parts = text.split(':', 1)
        potential = parts[0].strip().lower()
        categories_set = {item.get('category') for item in search_engine.faq_data}
        for cat in categories_set:
            if cat and potential in cat.lower():
                category = cat
                text = parts[1].strip()
                break

    results = search_engine.search(text, category)

    if results:
        best = results[0]
        response = f"<b>{best['question']}</b>\n\n{best['answer']}\n\n"
        if best.get('category'):
            response += f"📂 <i>Категория: {best['category']}</i>\n"
        response += f"🎯 <i>Релевантность: {best['score']:.0%}</i>"

        if len(results) > 1:
            response += "\n\n🔍 <b>Возможно, вас также заинтересует:</b>\n"
            for i, res in enumerate(results[1:4], 1):
                response += f"{i}. {res['question']}\n"

            keyboard = []
            for i, res in enumerate(results[1:4], 1):
                keyboard.append([InlineKeyboardButton(f"📌 {res['question'][:30]}...", callback_data=f"result_{i}")])
            reply_markup = InlineKeyboardMarkup(keyboard)
        else:
            reply_markup = None

        await update.message.reply_text(response, parse_mode='HTML', reply_markup=reply_markup)
    else:
        not_found = (
            "😕 <b>Не удалось найти ответ на ваш вопрос</b>\n\n"
            "Попробуйте:\n"
            "1. Переформулировать вопрос\n"
            "2. Использовать /categories для выбора темы\n"
            "3. Указать категорию через двоеточие\n"
            "   Пример: <i>отпуск: как перенести отпуск?</i>\n\n"
            "Если вопрос срочный, свяжитесь с HR-отделом:\n"
            "📞 +7 (3519) 25-60-00\n"
            "📧 hr@mechel.ru"
        )
        await update.message.reply_text(not_found, parse_mode='HTML')

@measure_response_time
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user = query.from_user
    data = query.data

    logger.info(f"🔘 Callback от {user.id}: {data}")

    if data.startswith('cat_'):
        category = data[4:]
        await query.edit_message_text(
            f"📂 Вы выбрали категорию: <b>{category}</b>\n\n"
            f"Теперь задайте вопрос по этой теме.",
            parse_mode='HTML'
        )

    elif data.startswith('result_'):
        if search_engine is None:
            await query.edit_message_text(
                "⚠️ Поисковый движок временно недоступен.",
                parse_mode='HTML'
            )
            return
        idx = int(data[7:]) - 1
        if 0 <= idx < len(search_engine.faq_data):
            res = search_engine.faq_data[idx]
            text = f"<b>{res['question']}</b>\n\n{res['answer']}\n\n"
            if res.get('category'):
                text += f"📂 <i>Категория: {res['category']}</i>"
            await query.edit_message_text(text, parse_mode='HTML')

    elif data == 'export_excel':
        if user.id in ADMIN_IDS:
            await export_to_excel(update, context)
        else:
            await query.answer("❌ Эта функция доступна только администраторам", show_alert=True)

async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    logger.error(f"❌ Ошибка: {type(error).__name__}: {error}", exc_info=True)
    if bot_stats:
        user_id = update.effective_user.id if update and update.effective_user else None
        bot_stats.log_error(type(error).__name__, str(error), user_id)

    if ADMIN_IDS:
        err_text = (
            f"⚠️ <b>Произошла ошибка в боте</b>\n\n"
            f"<b>Тип:</b> {type(error).__name__}\n"
            f"<b>Ошибка:</b> {str(error)[:200]}\n"
            f"<b>Время:</b> {datetime.now().strftime('%H:%M:%S')}"
        )
        for admin_id in ADMIN_IDS:
            try:
                await context.bot.send_message(admin_id, err_text, parse_mode='HTML')
            except:
                pass

# ------------------------------------------------------------
#  ВЕБ-ИНТЕРФЕЙС (Quart) — с улучшенной метрикой времени ответа
# ------------------------------------------------------------
app = Quart(__name__)

@app.route('/')
async def index():
    if not bot_stats:
        return "Бот инициализируется...", 503

    stats = bot_stats.get_summary_stats()
    page_start = time.time()

    html_template = '''
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>HR-бот Мечел - Статистика</title>
        <style>
            :root {
                --primary: #2c3e50;
                --secondary: #3498db;
                --success: #27ae60;
                --warning: #f39c12;
                --danger: #e74c3c;
                --light: #ecf0f1;
                --dark: #2c3e50;
            }
            * { margin:0; padding:0; box-sizing:border-box; }
            body {
                font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif;
                line-height:1.6;
                color:#333;
                background:linear-gradient(135deg, #667eea 0%, #764ba2 100%);
                min-height:100vh;
                padding:20px;
            }
            .container { max-width:1200px; margin:0 auto; }
            .header { text-align:center; margin-bottom:30px; color:white; }
            .header h1 { font-size:2.5rem; margin-bottom:10px; text-shadow:2px 2px 4px rgba(0,0,0,0.3); }
            .status-badge { display:inline-block; padding:5px 15px; border-radius:20px; font-weight:bold; margin:10px 0; }
            .status-online { background:var(--success); color:white; }
            .status-offline { background:var(--danger); color:white; }
            .stats-grid {
                display:grid;
                grid-template-columns:repeat(auto-fit, minmax(300px, 1fr));
                gap:20px;
                margin-bottom:30px;
            }
            .stat-card {
                background:white;
                border-radius:10px;
                padding:20px;
                box-shadow:0 10px 30px rgba(0,0,0,0.1);
                transition:transform 0.3s ease;
            }
            .stat-card:hover { transform:translateY(-5px); }
            .stat-card h3 { color:var(--primary); margin-bottom:15px; display:flex; align-items:center; gap:10px; }
            .stat-value { font-size:2rem; font-weight:bold; margin:10px 0; }
            .metric-badge { display:inline-block; padding:3px 10px; border-radius:15px; font-size:0.8rem; margin-left:10px; }
            .metric-good { background:#d4edda; color:#155724; }
            .metric-warning { background:#fff3cd; color:#856404; }
            .metric-bad { background:#f8d7da; color:#721c24; }
            .info-grid {
                display:grid;
                grid-template-columns:repeat(auto-fit, minmax(200px, 1fr));
                gap:10px;
                margin-top:15px;
            }
            .info-item { padding:10px; background:var(--light); border-radius:5px; }
            .footer { text-align:center; margin-top:30px; color:white; font-size:0.9rem; opacity:0.8; }

            .metric {
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 15px;
                background: linear-gradient(145deg, #ffffff, #f5f7fa);
                border-radius: 12px;
                box-shadow: 0 4px 15px rgba(0,0,0,0.05);
                margin-bottom: 10px;
            }
            .metric-label {
                font-size: 0.9rem;
                text-transform: uppercase;
                letter-spacing: 1px;
                color: var(--primary);
                opacity: 0.8;
            }
            .metric-value {
                font-size: 2.5rem;
                font-weight: 700;
                color: var(--dark);
                line-height: 1.2;
            }
            .metric-subvalue {
                font-size: 1rem;
                color: var(--secondary);
                font-weight: 500;
            }
            @media (max-width:768px) {
                .stats-grid { grid-template-columns:1fr; }
                .header h1 { font-size:2rem; }
            }
        </style>
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 HR-бот компании "Мечел"</h1>
                <div class="status-badge status-online">✅ Онлайн</div>
                <p>Система автоматических ответов на кадровые вопросы</p>
                <p><a href="/export/excel" style="color:white; background:rgba(255,255,255,0.2); padding:8px 16px; border-radius:20px; text-decoration:none;">📥 Скачать Excel</a></p>
            </div>
            <div class="stats-grid">
                <div class="stat-card">
                    <h3>📊 Общая статистика</h3>
                    <div class="info-grid">
                        <div class="info-item"><strong>Время работы:</strong><br>{{ stats.uptime }}</div>
                        <div class="info-item"><strong>Запущен:</strong><br>{{ stats.start_time }}</div>
                        <div class="info-item"><strong>Пользователи:</strong><br>{{ stats.total_users }} всего</div>
                        <div class="info-item"><strong>Активные (24ч):</strong><br>{{ stats.active_users_24h }}</div>
                    </div>
                </div>
                <div class="stat-card">
                    <h3>⚙️ Производительность</h3>
                    <div class="metric">
                        <div class="metric-label">Время ответа</div>
                        <div class="metric-value">{{ "%.2f"|format(stats.avg_response_time) }}с</div>
                        <div class="metric-subvalue">
                            <span class="metric-badge {{ 'metric-good' if stats.avg_response_time < 1 else 'metric-warning' if stats.avg_response_time < 3 else 'metric-bad' }}">
                                {{ stats.response_time_status }}
                            </span>
                        </div>
                    </div>
                    <p>Кэш статистики: {{ stats.cache_size }}</p>
                    <p>Запущен: {{ stats.start_time[:10] }}</p>
                </div>
                <div class="stat-card">
                    <h3>📈 Активность</h3>
                    <div class="info-grid">
                        <div class="info-item"><strong>Сообщения:</strong><br>{{ stats.total_messages }}</div>
                        <div class="info-item"><strong>Команды:</strong><br>{{ stats.total_commands }}</div>
                        <div class="info-item"><strong>Поиски:</strong><br>{{ stats.total_searches }}</div>
                        <div class="info-item"><strong>Отзывы:</strong><br>{{ stats.total_feedback }}</div>
                    </div>
                </div>
            </div>
            <div class="stat-card">
                <h3>📋 Дневная статистика (последние 7 дней)</h3>
                <div style="overflow-x:auto;">
                    <table style="width:100%; border-collapse:collapse;">
                        <thead>
                            <tr style="background:var(--light);">
                                <th style="padding:10px; text-align:left;">Дата</th>
                                <th style="padding:10px; text-align:left;">Пользователи</th>
                                <th style="padding:10px; text-align:left;">Сообщения</th>
                                <th style="padding:10px; text-align:left;">Команды</th>
                                <th style="padding:10px; text-align:left;">Поиски</th>
                                <th style="padding:10px; text-align:left;">Время ответа</th>
                            </tr>
                        </thead>
                        <tbody>
                            {% for date, day_stats in stats.daily_stats.items()|sort(reverse=True)|list[:7] %}
                            <tr style="border-bottom:1px solid #ddd;">
                                <td style="padding:10px;">{{ date }}</td>
                                <td style="padding:10px;">{{ day_stats.users }}</td>
                                <td style="padding:10px;">{{ day_stats.messages }}</td>
                                <td style="padding:10px;">{{ day_stats.commands }}</td>
                                <td style="padding:10px;">{{ day_stats.searches }}</td>
                                <td style="padding:10px;">{{ "%.2f"|format(day_stats.avg_response_time) }}с</td>
                            </tr>
                            {% endfor %}
                        </tbody>
                    </table>
                </div>
            </div>
            <div class="footer">
                <p>Страница сгенерирована за {{ "%.3f"|format(time.time() - page_start) }} сек</p>
                <p>HR-бот Мечел • Версия 12.3 • {{ now.strftime('%Y-%m-%d %H:%M:%S') }}</p>
            </div>
        </div>
    </body>
    </html>
    '''

    from jinja2 import Template
    template = Template(html_template)
    html_content = template.render(
        stats=stats,
        now=datetime.now(),
        page_start=page_start,
        time=time
    )
    return html_content

@app.route('/health')
async def health_check():
    return jsonify({
        "status": "healthy",
        "timestamp": datetime.now().isoformat(),
        "bot_initialized": application is not None,
        "search_engine_ready": search_engine is not None
    }), 200

# Маршрут для экспорта Excel через веб-интерфейс
@app.route('/export/excel')
async def export_excel_web():
    """Скачивание Excel-файла со статистикой через веб-браузер"""
    if bot_stats is None:
        return jsonify({"error": "Статистика не инициализирована"}), 500

    try:
        output = io.BytesIO()
        workbook = Workbook()

        # ---- повторно используем логику формирования Excel ----
        stats = bot_stats.get_summary_stats()

        # Лист 1: Общая статистика
        ws1 = workbook.active
        ws1.title = "Общая статистика"
        ws1['A1'] = "Статистика HR-бота Мечел"
        ws1['A1'].font = Font(bold=True, size=14)
        ws1.merge_cells('A1:D1')
        ws1['A3'] = "Показатель"
        ws1['B3'] = "Значение"
        ws1['A3'].font = ws1['B3'].font = Font(bold=True)

        rows = [
            ("Дата экспорта", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
            ("Время работы", stats.get('uptime', 'N/A')),
            ("Запущен", stats.get('start_time', 'N/A')),
            ("Всего пользователей", stats.get('total_users', 0)),
            ("Активные (24ч)", stats.get('active_users_24h', 0)),
            ("Всего сообщений", stats.get('total_messages', 0)),
            ("Всего команд", stats.get('total_commands', 0)),
            ("Всего поисков", stats.get('total_searches', 0)),
            ("Всего отзывов", stats.get('total_feedback', 0)),
            ("Ср. время ответа", f"{stats.get('avg_response_time', 0):.2f} сек"),
            ("Статус времени ответа", stats.get('response_time_status', 'N/A')),
            ("Размер кэша", stats.get('cache_size', 0)),
            ("Количество ошибок", stats.get('error_count', 0))
        ]
        for i, (label, value) in enumerate(rows, start=4):
            ws1[f'A{i}'] = label
            ws1[f'B{i}'] = value

        # Лист 2: Время ответа
        ws2 = workbook.create_sheet("Время ответа")
        ws2['A1'] = "История времени ответа"
        ws2['A1'].font = Font(bold=True, size=14)
        ws2.merge_cells('A1:C1')
        ws2['A3'] = "Время"
        ws2['B3'] = "Время ответа (сек)"
        ws2['C3'] = "Статус"
        for cell in ['A3', 'B3', 'C3']:
            ws2[cell].font = Font(bold=True)
        if hasattr(bot_stats, 'response_times'):
            for i, rt in enumerate(bot_stats.response_times, start=4):
                ws2[f'A{i}'] = rt['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
                ws2[f'B{i}'] = rt['response_time']
                ws2[f'C{i}'] = (
                    "Хорошо" if rt['response_time'] < 1.0 else
                    "Нормально" if rt['response_time'] < 3.0 else
                    "Медленно"
                )

        # Лист 3: FAQ
        ws3 = workbook.create_sheet("FAQ База")
        ws3['A1'] = "База знаний FAQ"
        ws3['A1'].font = Font(bold=True, size=14)
        ws3.merge_cells('A1:D1')
        headers = ["Категория", "Вопрос", "Ответ", "Ключевые слова"]
        for col, h in enumerate(headers, start=1):
            cell = ws3.cell(row=3, column=col)
            cell.value = h
            cell.font = Font(bold=True)

        faq_source = search_engine.faq_data if search_engine else []
        if not faq_source:
            ws3.cell(row=4, column=1, value="Нет данных FAQ (поисковый движок недоступен)")
        else:
            for i, item in enumerate(faq_source, start=4):
                ws3.cell(row=i, column=1, value=item.get('category', 'Без категории'))
                ws3.cell(row=i, column=2, value=item.get('question', ''))
                ws3.cell(row=i, column=3, value=item.get('answer', ''))
                ws3.cell(row=i, column=4, value=', '.join(item.get('keywords', [])))

        # Лист 4: Пользователи
        ws4 = workbook.create_sheet("Пользователи")
        ws4['A1'] = "Статистика пользователей"
        ws4['A1'].font = Font(bold=True, size=14)
        ws4.merge_cells('A1:G1')
        headers2 = ["ID", "Имя", "Сообщения", "Команды", "Поиски", "Отзывы", "Последняя активность"]
        for col, h in enumerate(headers2, start=1):
            cell = ws4.cell(row=3, column=col)
            cell.value = h
            cell.font = Font(bold=True)

        for i, (uid, udata) in enumerate(bot_stats.user_stats.items(), start=4):
            ws4.cell(row=i, column=1, value=uid)
            ws4.cell(row=i, column=2, value=f"Пользователь {uid}")
            ws4.cell(row=i, column=3, value=udata.get('messages', 0))
            ws4.cell(row=i, column=4, value=udata.get('commands', 0))
            ws4.cell(row=i, column=5, value=udata.get('searches', 0))
            ws4.cell(row=i, column=6, value=udata.get('feedback_count', 0))
            last = udata.get('last_active')
            ws4.cell(row=i, column=7, value=last.strftime("%Y-%m-%d %H:%M:%S") if last else '')

        # Автоширина
        for ws in [ws1, ws2, ws3, ws4]:
            for col in ws.columns:
                max_len = 0
                col_letter = get_column_letter(col[0].column)
                for cell in col:
                    try:
                        if cell.value and len(str(cell.value)) > max_len:
                            max_len = len(str(cell.value))
                    except:
                        pass
                ws.column_dimensions[col_letter].width = min(max_len + 2, 50)

        workbook.save(output)
        output.seek(0)

        filename = f"mechel_hr_bot_export_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        return await send_file(
            output,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=filename,
            as_attachment=True
        )
    except Exception as e:
        logger.error(f"❌ Ошибка веб-экспорта в Excel: {e}", exc_info=True)
        return jsonify({"error": str(e)}), 500

@app.route(WEBHOOK_PATH, methods=['POST'])
async def webhook():
    """Обработка входящих вебхуков от Telegram"""
    if request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET:
        return 'Forbidden', 403

    try:
        data = await request.get_json()
        if not data:
            logger.error("Получен пустой запрос вебхука")
            return 'Bad Request', 400

        if data and application and application.bot:
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
    except Exception as e:
        logger.error(f"Ошибка в webhook: {e}", exc_info=True)
        return 'Error', 500

    return 'OK', 200

# ------------------------------------------------------------
#  ИНИЦИАЛИЗАЦИЯ БОТА (с максимальной защитой от сбоев)
# ------------------------------------------------------------
async def init_bot():
    """Инициализация всех компонентов"""
    global application, search_engine, bot_stats

    logger.info("🚀 Инициализация HR-бота Мечел...")

    # Повторная валидация токена
    if not validate_token(BOT_TOKEN):
        logger.error("❌ Неверный формат токена бота (повторная проверка)")
        return False

    # Корректное завершение предыдущего экземпляра
    if application:
        try:
            logger.info("🔄 Завершение предыдущего экземпляра приложения...")
            await application.stop()
            await application.shutdown()
            logger.info("✅ Предыдущий экземпляр приложения завершён")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка при завершении предыдущего приложения: {e}")

    try:
        # 1. Поисковый движок — сначала пробуем внешний, потом локальный
        try:
            from search_engine import SearchEngine as ExternalSearchEngine
            search_engine = ExternalSearchEngine()
            logger.info("✅ Загружен внешний поисковый движок (search_engine.py)")
        except ImportError:
            # Используем локальный класс как резервный вариант
            search_engine = SearchEngine()  # локальный класс из этого файла
            logger.warning("⚠️ Используется встроенный поисковый движок (резервный)")

        # 2. Статистика
        bot_stats = BotStatistics()

        # 3. Приложение Telegram
        application = (
            ApplicationBuilder()
            .token(BOT_TOKEN)
            .post_init(post_init)
            .build()
        )

        # 4. Регистрация обработчиков
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("categories", categories_command))
        application.add_handler(CommandHandler("feedback", feedback_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("export", export_command))

        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        application.add_error_handler(error_handler)

        # 5. Настройка вебхука / поллинга
        if RENDER:
            webhook_url = f"{WEBHOOK_URL}{WEBHOOK_PATH}"
            await application.bot.set_webhook(
                url=webhook_url,
                secret_token=WEBHOOK_SECRET,
                drop_pending_updates=True
            )
            logger.info(f"✅ Вебхук установлен: {webhook_url}")
        else:
            await application.bot.delete_webhook(drop_pending_updates=True)
            logger.info("✅ Режим поллинга (polling) активирован")

        logger.info("✅ Бот успешно инициализирован!")
        return True

    except Exception as e:
        logger.critical(f"❌ Ошибка инициализации бота: {e}", exc_info=True)
        # Останавливаем приложение, если оно было частично инициализировано
        if application:
            try:
                await application.stop()
                await application.shutdown()
            except:
                pass
        return False

# ------------------------------------------------------------
#  ОСНОВНАЯ ФУНКЦИЯ ЗАПУСКА
# ------------------------------------------------------------
async def main():
    """Точка входа"""
    success = await init_bot()
    if not success:
        logger.critical("Не удалось инициализировать бота. Завершение работы.")
        sys.exit(1)

    if RENDER:
        config = Config()
        config.bind = [f"0.0.0.0:{PORT}"]
        config.worker_class = "asyncio"
        logger.info(f"🌐 Запуск веб-сервера на порту {PORT}")
        await serve(app, config)
    else:
        logger.info(f"🌐 Запуск веб-интерфейса на http://localhost:{PORT}")
        logger.info("🤖 Запуск бота в режиме поллинга...")

        # Чисто асинхронный запуск polling (без threading)
        polling_task = asyncio.create_task(
            application.run_polling(allowed_updates=Update.ALL_TYPES)
        )

        config = Config()
        config.bind = [f"0.0.0.0:{PORT}"]
        await serve(app, config)

        await polling_task

if __name__ == '__main__':
    asyncio.run(main())
