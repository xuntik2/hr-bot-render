#!/usr/bin/env python3
"""
Telegram-бот для HR-отдела компании "Мечел"
Версия 12.67 — добавлена функция shutdown() для корректного завершения
"""
import os
import sys
import asyncio
import logging
import json
import time
import hashlib
import re
import inspect
import signal
from datetime import datetime, timedelta
from typing import Dict, List, Optional, Any, Tuple, Union
from collections import defaultdict, deque

# ------------------------------------------------------------
#  ПРОВЕРКА КРИТИЧЕСКИХ ЗАВИСИМОСТЕЙ
# ------------------------------------------------------------
def check_critical_dependencies():
    try:
        from importlib.metadata import version, PackageNotFoundError
    except ImportError:
        try:
            from importlib_metadata import version, PackageNotFoundError
        except ImportError:
            print("❌ Не удалось импортировать importlib.metadata", file=sys.stderr)
            sys.exit(1)
    critical_packages = ['quart', 'python-telegram-bot', 'hypercorn', 'pandas', 'openpyxl']
    missing = []
    for pkg in critical_packages:
        try:
            ver = version(pkg)
            print(f"✅ {pkg} версия {ver} установлена")
        except PackageNotFoundError:
            missing.append(pkg)
    if missing:
        print(f"❌ Отсутствуют критические зависимости: {', '.join(missing)}", file=sys.stderr)
        sys.exit(1)
    print("✅ Все критические зависимости установлены")

check_critical_dependencies()

# ------------------------------------------------------------
#  ИМПОРТЫ
# ------------------------------------------------------------
from quart import Quart, request, jsonify
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
import pandas as pd
from dotenv import load_dotenv

# ------------------------------------------------------------
#  ИМПОРТ МОДУЛЯ МЕМОВ
# ------------------------------------------------------------
try:
    from meme_handler import (
        init_meme_handler,
        close_meme_handler,
        meme_command,
        meme_subscribe_command,
        meme_unsubscribe_command,
        get_meme_handler
    )
    MEME_MODULE_AVAILABLE = True
    print("✅ Модуль мемов загружен")
except ImportError:
    MEME_MODULE_AVAILABLE = False
    print("⚠️ Модуль мемов не найден, команды /мем и подписки будут недоступны")
    # Заглушки
    async def init_meme_handler(*args, **kwargs): pass
    async def close_meme_handler(): pass
    async def meme_command(*args, **kwargs): pass
    async def meme_subscribe_command(*args, **kwargs): pass
    async def meme_unsubscribe_command(*args, **kwargs): pass
    def get_meme_handler(): return None

# ------------------------------------------------------------
#  ИМПОРТ МОДУЛЕЙ ПРОЕКТА
# ------------------------------------------------------------
from stats import BotStatistics, generate_excel_report
from utils import is_greeting, truncate_question, parse_period_argument
from web_panel import register_web_routes

# ------------------------------------------------------------
#  СОЗДАНИЕ QUART ПРИЛОЖЕНИЯ
# ------------------------------------------------------------
app = Quart(__name__)

# Флаги инициализации
_bot_initialized = False
_bot_initializing = False
_bot_init_lock = asyncio.Lock()
_routes_registered = False

# ------------------------------------------------------------
#  ФУНКЦИЯ ЛЕВЕНШТЕЙНА
# ------------------------------------------------------------
def levenshtein_distance(s1: str, s2: str) -> int:
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

# ------------------------------------------------------------
#  КОНФИГУРАЦИЯ ЛОГИРОВАНИЯ (только stdout)
# ------------------------------------------------------------
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# ------------------------------------------------------------
#  ПЕРЕМЕННЫЕ ОКРУЖЕНИЯ
# ------------------------------------------------------------
load_dotenv()

def get_bot_token() -> str:
    token = os.getenv('TELEGRAM_BOT_TOKEN')
    if token:
        return token
    token = os.getenv('BOT_TOKEN')
    if token:
        logger.warning("⚠️ Используется устаревшее имя переменной BOT_TOKEN. Рекомендуется переименовать в TELEGRAM_BOT_TOKEN.")
        return token
    return ''

def validate_token(token: str) -> bool:
    return bool(token and len(token) > 30 and ':' in token)

BOT_TOKEN = get_bot_token()
if not validate_token(BOT_TOKEN):
    logger.critical("❌ TELEGRAM_BOT_TOKEN (или BOT_TOKEN) не установлен или неверный формат")
    sys.exit(1)

RENDER = os.getenv('RENDER', 'false').lower() == 'true'
PORT = int(os.getenv('PORT', 8080))
WEBHOOK_SECRET = os.getenv('WEBHOOK_SECRET', '')
if not WEBHOOK_SECRET:
    WEBHOOK_SECRET = 'mechel_hr_prod_' + hashlib.md5(BOT_TOKEN.encode()).hexdigest()[:16]
    if RENDER:
        logger.warning("⚠️ WEBHOOK_SECRET сгенерирован автоматически. Установите вручную для продакшена.")

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')
if RENDER and not WEBHOOK_URL:
    logger.critical("❌ На Render WEBHOOK_URL обязателен")
    sys.exit(1)

BASE_URL = f"http://localhost:{PORT}" if not RENDER else WEBHOOK_URL.rstrip('/')

ADMIN_IDS = []
try:
    admin_str = os.getenv('ADMIN_IDS', '')
    if admin_str:
        ADMIN_IDS = [int(x.strip()) for x in admin_str.split(',') if x.strip().isdigit()]
    logger.info(f"✅ Администраторы: {ADMIN_IDS}")
except Exception as e:
    logger.error(f"❌ Ошибка парсинга ADMIN_IDS: {e}")

# ------------------------------------------------------------
#  НЕФАТАЛЬНАЯ ПРОВЕРКА ОПЦИОНАЛЬНЫХ ФАЙЛОВ
# ------------------------------------------------------------
def check_optional_files():
    optional_files = ['search_engine.py']
    missing = []
    for file in optional_files:
        if not os.path.exists(file):
            missing.append(file)
    if missing:
        logger.warning(f"⚠️ Отсутствуют файлы: {', '.join(missing)}")
        logger.warning("⚠️ Будет использован встроенный функционал")
    else:
        logger.info("✅ Все опциональные файлы присутствуют")

check_optional_files()

# ------------------------------------------------------------
#  ВСТРОЕННЫЙ ПОИСКОВЫЙ ДВИЖОК (исправлен: возвращает id)
# ------------------------------------------------------------
class BuiltinSearchEngine:
    def __init__(self, max_cache_size: int = 500):
        self.faq_data = []
        self.cache = {}
        self.max_cache_size = max_cache_size
        self._load_data()

    def _load_data(self):
        try:
            with open('faq.json', 'r', encoding='utf-8') as f:
                self.faq_data = json.load(f)
            logger.info(f"✅ Загружено {len(self.faq_data)} записей из faq.json")
        except FileNotFoundError:
            logger.warning("⚠️ faq.json не найден, база знаний пуста")
            self.faq_data = []
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки faq.json: {e}")
            self.faq_data = []

    def refresh_data(self):
        self._load_data()
        self.cache.clear()

    def search(self, query: str, category: str = None, top_k: int = 5) -> List[Tuple[int, str, str, float]]:
        cache_key = f"{query}:{category}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        results = []
        query_lower = query.lower()

        for item in self.faq_data:
            if category and item.get('category') != category:
                continue
            question = item.get('question', '')
            answer = item.get('answer', '')
            faq_id = item.get('id')
            if not question or not answer or faq_id is None:
                continue
            score = self._calculate_score(query_lower, question.lower())
            if score > 0.3:
                results.append((faq_id, question, answer, score))

        results.sort(key=lambda x: x[3], reverse=True)
        top_results = results[:top_k]

        if len(self.cache) >= self.max_cache_size:
            self.cache.clear()
        self.cache[cache_key] = top_results
        return top_results

    def _calculate_score(self, query: str, text: str) -> float:
        if query in text:
            return 1.0
        query_words = set(query.split())
        text_words = set(text.split())
        if not query_words:
            return 0.0
        match_count = len(query_words & text_words)
        return match_count / len(query_words)

    def suggest_correction(self, query: str, top_k: int = 3) -> List[str]:
        suggestions = set()
        query_lower = query.lower()
        for item in self.faq_data:
            question = item.get('question', '')
            if not question:
                continue
            if levenshtein_distance(query_lower, question.lower()) <= 3:
                suggestions.add(question)
                if len(suggestions) >= top_k:
                    break
        return list(suggestions)

# ------------------------------------------------------------
#  АДАПТЕР ДЛЯ ВНЕШНЕГО SEARCH ENGINE
# ------------------------------------------------------------
class ExternalSearchEngineAdapter:
    def __init__(self, engine):
        self.engine = engine
        self.cache = {}

    def search(self, query: str, category: str = None, top_k: int = 5) -> List[Tuple[int, str, str, float]]:
        cache_key = f"{query}:{category}"
        if cache_key in self.cache:
            return self.cache[cache_key]

        try:
            if hasattr(self.engine, 'search'):
                sig = inspect.signature(self.engine.search)
                params = sig.parameters
                if 'category' in params:
                    results = self.engine.search(query, category=category, top_k=top_k)
                else:
                    results = self.engine.search(query, top_k=top_k)

                normalized = []
                for r in results:
                    if isinstance(r, tuple) and len(r) >= 3:
                        q, a, s = r[0], r[1], r[2]
                        fake_id = hash(q) % 1000000
                        normalized.append((fake_id, q, a, s))
                    elif isinstance(r, dict):
                        q = r.get('question', r.get('Question', ''))
                        a = r.get('answer', r.get('Answer', ''))
                        s = r.get('score', r.get('Score', 0.0))
                        fid = r.get('id', hash(q) % 1000000)
                        normalized.append((fid, q, a, float(s)))
                    elif hasattr(r, 'question') and hasattr(r, 'answer'):
                        q = r.question
                        a = r.answer
                        s = getattr(r, 'score', 0.0)
                        fid = getattr(r, 'id', hash(q) % 1000000)
                        normalized.append((fid, q, a, float(s)))
                normalized.sort(key=lambda x: x[3], reverse=True)
                self.cache[cache_key] = normalized[:top_k]
                return normalized[:top_k]
        except Exception as e:
            logger.error(f"Ошибка поиска во внешнем движке: {e}")
            return []

    def suggest_correction(self, query: str, top_k: int = 3):
        try:
            if hasattr(self.engine, 'suggest_correction'):
                return self.engine.suggest_correction(query, top_k=top_k)
        except Exception as e:
            logger.error(f"Ошибка предложения во внешнем движке: {e}")
        return []

    def refresh_data(self):
        if hasattr(self.engine, 'refresh_data'):
            self.engine.refresh_data()

    @property
    def faq_data(self):
        if hasattr(self.engine, 'faq_data'):
            return self.engine.faq_data
        return []

# ------------------------------------------------------------
#  СИСТЕМА ПОДПИСОК
# ------------------------------------------------------------
SUBSCRIBERS_FILE = 'subscribers.json'
subscribers_lock = asyncio.Lock()
_subscribers_cache = None
_subscribers_cache_loaded = False

async def load_subscribers():
    global _subscribers_cache, _subscribers_cache_loaded
    if _subscribers_cache_loaded:
        return _subscribers_cache or []

    try:
        async with subscribers_lock:
            if os.path.exists(SUBSCRIBERS_FILE):
                with open(SUBSCRIBERS_FILE, 'r', encoding='utf-8') as f:
                    _subscribers_cache = json.load(f)
                    _subscribers_cache_loaded = True
                    return _subscribers_cache
    except Exception as e:
        logger.error(f"Ошибка загрузки подписчиков: {e}")

    _subscribers_cache = []
    _subscribers_cache_loaded = True
    return []

async def save_subscribers(subscribers: List[int]):
    global _subscribers_cache
    try:
        async with subscribers_lock:
            with open(SUBSCRIBERS_FILE, 'w', encoding='utf-8') as f:
                json.dump(subscribers, f, ensure_ascii=False, indent=2)
            _subscribers_cache = subscribers.copy()
    except Exception as e:
        logger.error(f"Ошибка сохранения подписчиков: {e}")

async def add_subscriber(user_id: int):
    subscribers = await load_subscribers()
    if user_id not in subscribers:
        subscribers.append(user_id)
        await save_subscribers(subscribers)
        return True
    return False

async def remove_subscriber(user_id: int):
    subscribers = await load_subscribers()
    if user_id in subscribers:
        subscribers.remove(user_id)
        await save_subscribers(subscribers)
        return True
    return False

async def get_subscribers() -> List[int]:
    return await load_subscribers()

async def ensure_subscribed(user_id: int):
    await add_subscriber(user_id)

# ------------------------------------------------------------
#  ПЕРИОДИЧЕСКОЕ СОХРАНЕНИЕ ПОДПИСЧИКОВ
# ------------------------------------------------------------
async def periodic_subscriber_save():
    while True:
        await asyncio.sleep(300)
        try:
            subscribers = await load_subscribers()
            await save_subscribers(subscribers)
        except Exception as e:
            logger.error(f"Ошибка периодического сохранения: {e}")

# ------------------------------------------------------------
#  ФУНКЦИЯ АВТОРИЗАЦИИ (поддержка Bearer токена)
# ------------------------------------------------------------
def is_authorized(request, WEBHOOK_SECRET: str) -> bool:
    """Проверяет авторизацию по заголовку X-Secret-Key, параметру key или Bearer токену."""
    secret = WEBHOOK_SECRET
    if request.headers.get('X-Secret-Key') == secret:
        return True
    if request.args.get('key') == secret:
        return True
    auth_header = request.headers.get('Authorization', '')
    if auth_header.startswith('Bearer '):
        token = auth_header[7:]
        if token == secret:
            return True
    return False

# ------------------------------------------------------------
#  СИСТЕМНЫЕ СООБЩЕНИЯ
# ------------------------------------------------------------
MESSAGES_FILE = 'messages.json'
messages_lock = asyncio.Lock()

DEFAULT_MESSAGES = {
    "welcome": (
        "🦸‍♂️ <b>Привет, {first_name}!</b>\n\n"
        "Я — официальный HR-помощник компании <b>«Мечел»</b>.\n\n"
        "🤖 <b>Что я умею:</b>\n"
        "• Отвечать на вопросы по отпускам, зарплате, ДМС и документам\n"
        "• Показывать категории вопросов для быстрого поиска\n"
        "• Присылать мемы для поднятия настроения 😄\n"
        "• Принимать ваши предложения по улучшению базы знаний\n\n"
        "👇 Нажмите кнопку ниже, чтобы начать!"
    ),
    "main_menu": (
        "📋 <b>Главное меню</b>\n\n"
        "Выберите категорию или задайте вопрос текстом.\n\n"
        "<i>Пример: «Как оформить отпуск?» или «Справка 2-НДФЛ»</i>"
    ),
    "help": "📚 <b>Доступные команды:</b>\n\n/start - начать работу с ботом\\n/help - показать эту справку\\n/categories - показать категории вопросов\\n/feedback - оставить отзыв или предложение\\n/subscribe - подписаться на рассылку\\n/unsubscribe - отписаться от рассылки\\n/whatcanido - что я умею",
    "greeting_response": "👋 Здравствуйте! Чем могу помочь?",
    "subscribe_success": "✅ Вы успешно подписались на рассылку!",
    "already_subscribed": "ℹ️ Вы уже подписаны на рассылку.",
    "unsubscribe_success": "✅ Вы успешно отписались от рассылки.",
    "not_subscribed": "ℹ️ Вы не подписаны на рассылку.",
    "feedback_ack": "✅ Спасибо за ваш отзыв! Мы обязательно учтём ваши предложения.",
    "suggestions": "🤔 Возможно, вы имели в виду:\n\n{suggestions}\n\nПопробуйте уточнить ваш запрос.",
    "no_results": "😔 К сожалению, я не нашёл ответ на ваш вопрос. Попробуйте переформулировать или напишите /feedback с вашим предложением добавить этот вопрос в базу знаний."
}

async def load_messages():
    try:
        async with messages_lock:
            if os.path.exists(MESSAGES_FILE):
                with open(MESSAGES_FILE, 'r', encoding='utf-8') as f:
                    return json.load(f)
    except Exception as e:
        logger.error(f"Ошибка загрузки сообщений: {e}")
    return DEFAULT_MESSAGES.copy()

async def save_messages(messages: Dict):
    try:
        async with messages_lock:
            with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
                json.dump(messages, f, ensure_ascii=False, indent=2)
    except Exception as e:
        logger.error(f"Ошибка сохранения сообщений: {e}")

async def get_message(key: str, **kwargs) -> str:
    messages = await load_messages()
    text = messages.get(key, DEFAULT_MESSAGES.get(key, ''))
    if not text:
        text = f'⚠️ Сообщение "{key}" не найдено'
    try:
        return text.format(**kwargs)
    except KeyError:
        return text

# ------------------------------------------------------------
#  ГЛОБАЛЬНЫЕ ОБЪЕКТЫ
# ------------------------------------------------------------
application: Optional[Application] = None
search_engine: Optional[Union[BuiltinSearchEngine, ExternalSearchEngineAdapter]] = None
bot_stats: Optional[BotStatistics] = None

# ------------------------------------------------------------
#  БЛОКИРОВКИ ДЛЯ РАБОТЫ С JSON
# ------------------------------------------------------------
faq_lock = asyncio.Lock()

# ------------------------------------------------------------
#  ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ------------------------------------------------------------
async def _reply_or_edit(update: Update, text: str, parse_mode: str = 'HTML', reply_markup=None):
    if update.message:
        return await update.message.reply_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
    elif update.callback_query:
        await update.callback_query.edit_message_text(text, parse_mode=parse_mode, reply_markup=reply_markup)
        return None
    else:
        logger.error("Не удалось определить тип update для отправки сообщения")
        return None

# ------------------------------------------------------------
#  РАБОТА С FAQ.JSON (CRUD)
# ------------------------------------------------------------
async def load_faq_json():
    try:
        async with faq_lock:
            with open('faq.json', 'r', encoding='utf-8') as f:
                return json.load(f)
    except FileNotFoundError:
        return []
    except Exception as e:
        logger.error(f"Ошибка загрузки faq.json: {e}")
        return []

async def save_faq_json(data: List[Dict]):
    async with faq_lock:
        with open('faq.json', 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
    if search_engine and hasattr(search_engine, 'refresh_data'):
        search_engine.refresh_data()

async def get_next_faq_id() -> int:
    data = await load_faq_json()
    if not data:
        return 1
    max_id = max((item.get('id', 0) for item in data), default=0)
    return max_id + 1

# ------------------------------------------------------------
#  ФОНОВАЯ ОЧИСТКА СТАРЫХ ДАННЫХ
# ------------------------------------------------------------
async def periodic_cleanup():
    """Запускает очистку старых записей статистики раз в сутки."""
    while True:
        await asyncio.sleep(86400)  # 24 часа
        if bot_stats:
            bot_stats.cleanup_old_data(max_days=180)
            logger.info("✅ Плановая очистка старых данных статистики выполнена")

# ------------------------------------------------------------
#  POST_INIT
# ------------------------------------------------------------
async def post_init(application: Application):
    logger.info("✅ Приложение Telegram полностью готово и запущено")

# ------------------------------------------------------------
#  ОБРАБОТЧИКИ КОМАНД
# ------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_subscribed(user.id)
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/start')
        bot_stats.log_message(user.id, user.username or "Unknown", 'subscribe', '')
    text = await get_message('welcome', first_name=user.first_name)
    if user.id in ADMIN_IDS:
        text += "\n\n👑 Админ-команды:\n/stats [период] — статистика\n/feedbacks — отзывы (выгрузка)\n/export — Excel\n/статистика, /отзывы, /экспорт\n/subscribe /unsubscribe — подписка\n/broadcast — рассылка\n/save — принудительное сохранение данных"

    photo_path = os.path.join(os.path.dirname(__file__), 'mechel_start.png')
    if os.path.exists(photo_path):
        try:
            with open(photo_path, 'rb') as photo:
                await update.message.reply_photo(
                    photo=photo,
                    caption=text,
                    parse_mode='HTML'
                )
                return
        except Exception as e:
            logger.error(f"Ошибка отправки приветственного фото: {e}")

    await _reply_or_edit(update, text, parse_mode='HTML')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_subscribed(user.id)
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/help')
    text = await get_message('help')
    await _reply_or_edit(update, text, parse_mode='HTML')

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    added = await add_subscriber(user.id)
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'subscribe' if added else 'message')
    if added:
        text = await get_message('subscribe_success')
    else:
        text = await get_message('already_subscribed')
    await _reply_or_edit(update, text, parse_mode='HTML')

async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    removed = await remove_subscriber(user.id)
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'unsubscribe' if removed else 'message')
    if removed:
        text = await get_message('unsubscribe_success')
    else:
        text = await get_message('not_subscribed')
    await _reply_or_edit(update, text, parse_mode='HTML')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await _reply_or_edit(update, "⛔ Нет прав.", parse_mode='HTML')
        return
    if not context.args:
        await _reply_or_edit(update, "ℹ️ Использование: /broadcast <текст сообщения>", parse_mode=None)
        return
    message = ' '.join(context.args)
    subscribers = await get_subscribers()
    if not subscribers:
        await _reply_or_edit(update, "📭 Нет подписчиков для рассылки.", parse_mode='HTML')
        return
    sent = 0
    failed = 0
    status_msg = await _reply_or_edit(update, f"📨 Отправка {len(subscribers)} подписчикам...", parse_mode='HTML')
    for i, uid in enumerate(subscribers):
        try:
            await context.bot.send_message(chat_id=uid, text=message, parse_mode='HTML')
            sent += 1
            if i % 10 == 9:
                await asyncio.sleep(1.0)
            else:
                await asyncio.sleep(0.1)
        except Exception as e:
            logger.error(f"❌ Ошибка отправки рассылки пользователю {uid}: {e}")
            failed += 1
    await status_msg.edit_text(f"✅ Рассылка завершена.\n📨 Отправлено: {sent}\n❌ Ошибок: {failed}")

async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_subscribed(user.id)
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/categories')
    if search_engine is None or not search_engine.faq_data:
        await _reply_or_edit(update, "⚠️ Категории временно недоступны.", parse_mode='HTML')
        return
    categories = {}
    for item in search_engine.faq_data:
        cat = item.get('category', 'Без категории')
        categories[cat] = categories.get(cat, 0) + 1
    if not categories:
        await _reply_or_edit(update, "📂 Категории не найдены.", parse_mode='HTML')
        return
    keyboard = []
    for cat in sorted(categories.keys()):
        count = categories[cat]
        button = InlineKeyboardButton(text=f"{cat} ({count})", callback_data=f"cat_{cat}")
        keyboard.append([button])
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = "📂 <b>Выберите категорию:</b>\n\nНажмите на категорию, чтобы увидеть список вопросов."
    await _reply_or_edit(update, text, parse_mode='HTML', reply_markup=reply_markup)

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_subscribed(user.id)
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/feedback')
    context.user_data['awaiting_feedback'] = True
    await _reply_or_edit(update, "💬 Напишите ваше предложение или пожелание по работе бота.", parse_mode='HTML')

async def feedbacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_subscribed(user.id)
    if user.id not in ADMIN_IDS:
        await _reply_or_edit(update, "⛔ Нет прав.", parse_mode='HTML')
        return
    if bot_stats is None:
        await _reply_or_edit(update, "⚠️ Статистика не инициализирована.", parse_mode='HTML')
        return
    try:
        output = generate_feedback_report(bot_stats)
        filename = f"feedbacks_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await update.message.reply_document(
            document=output.getvalue(),
            filename=filename,
            caption=f"📋 Отзывы и предложения от {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        logger.info(f"✅ Отзывы выгружены пользователем {user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка выгрузки отзывов: {e}")
        await _reply_or_edit(update, f"❌ Ошибка: {str(e)}", parse_mode='HTML')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_subscribed(user.id)
    if user.id not in ADMIN_IDS:
        await _reply_or_edit(update, "⛔ Нет прав.", parse_mode='HTML')
        return
    if bot_stats is None:
        await _reply_or_edit(update, "⚠️ Статистика временно недоступна.", parse_mode='HTML')
        return
    period = 'all'
    if context.args:
        period = parse_period_argument(context.args[0])
    bot_stats.log_message(user.id, user.username or "Unknown", 'command', f'/stats {period}')
    s = bot_stats.get_summary_stats(period)
    subscribers = await get_subscribers()
    faq_count = len(search_engine.faq_data) if search_engine else 0
    period_names = {
        'all': 'всё время',
        'day': 'день',
        'week': 'неделя',
        'month': 'месяц',
        'quarter': 'квартал',
        'halfyear': 'полгода',
        'year': 'год'
    }
    period_text = period_names.get(period, period)
    text = (
        f"📊 <b>Статистика за {period_text}</b>\n"
        f"👥 Пользователей: {s['total_users']}\n"
    )
    if period == 'all':
        text += f"👤 Активных (24ч): {s['active_users_24h']}\n"
    text += (
        f"📨 Сообщений: {s['total_messages']}\n"
        f"🛠 Команд: {s['total_commands']}\n"
        f"🔍 Поисков: {s['total_searches']}\n"
        f"📝 Отзывов/предложений: {s['total_feedback']}\n"
        f"👍 Полезных ответов: {s['total_ratings_helpful']}\n"
        f"👎 Бесполезных: {s['total_ratings_unhelpful']}\n"
        f"⭐ Удовлетворённость: "
    )
    if s['total_ratings'] > 0:
        satisfaction = s['total_ratings_helpful'] / s['total_ratings'] * 100
        text += f"{satisfaction:.1f}%\n"
    else:
        text += "нет оценок\n"
    text += (
        f"📦 Кэш поиска: {s['cache_size']}\n"
        f"⏱ Uptime: {s['uptime']}\n"
        f"👥 Подписчиков на рассылку: {len(subscribers)}\n"
        f"📚 Вопросов в базе знаний: {faq_count}\n"
    )
    keyboard = [
        [
            InlineKeyboardButton("День", callback_data="stats_day"),
            InlineKeyboardButton("Неделя", callback_data="stats_week"),
            InlineKeyboardButton("Месяц", callback_data="stats_month")
        ],
        [
            InlineKeyboardButton("Квартал", callback_data="stats_quarter"),
            InlineKeyboardButton("Полгода", callback_data="stats_halfyear"),
            InlineKeyboardButton("Год", callback_data="stats_year")
        ],
        [
            InlineKeyboardButton("📊 Веб-статистика", url=BASE_URL),
            InlineKeyboardButton("📁 Excel", callback_data="export_excel")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await _reply_or_edit(update, text, parse_mode='HTML', reply_markup=reply_markup)

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_subscribed(user.id)
    if user.id not in ADMIN_IDS:
        await _reply_or_edit(update, "⛔ Нет прав.", parse_mode='HTML')
        return
    await export_to_excel(update, context)

async def what_can_i_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    await ensure_subscribed(user.id)
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/whatcanido')
    text = (
        "📋 <b>Что я умею:</b>\n"
        "• Отвечать на HR-вопросы (просто напишите)\n"
        "• Показывать категории: /categories\n"
        "• Принимать предложения: /feedback\n"
        "• Присылать мемы: /мем или /mem\n"
        "• Подписаться на рассылку: /subscribe\n"
        "💡 Совет: можно писать «отпуск: как перенести?» — я найду точнее!"
    )
    await _reply_or_edit(update, text, parse_mode='HTML')

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        return
    text = (
        "👑 <b>Админ-панель</b>\n"
        "• Статистика: /stats [day|week|month]\n"
        "• Управление FAQ: /faq → веб-панель\n"
        "• Рассылка: /broadcast или /рассылка\n"
        "• Экспорт: /export\n"
        "• Отзывы: /feedbacks\n"
        "• Мемы: /memsub, /memunsub\n"
        "• Сохранить данные: /save или /сохранить\n"
        f"• Веб-интерфейс: {BASE_URL}"
    )
    keyboard = [[InlineKeyboardButton("👑 Открыть админ-меню", callback_data="menu_admin")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await _reply_or_edit(update, text, parse_mode='HTML', reply_markup=reply_markup)

async def export_to_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if bot_stats is None:
        await _reply_or_edit(update, "⚠️ Экспорт временно недоступен (статистика не инициализирована).", parse_mode='HTML')
        return
    bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/export')
    try:
        subscribers = await get_subscribers()
        # Запускаем тяжёлую синхронную функцию в отдельном потоке
        output = await asyncio.to_thread(generate_excel_report, bot_stats, subscribers, search_engine)
        filename = f"mechel_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await update.message.reply_document(
            document=output.getvalue(),
            filename=filename,
            caption=f"📊 Экспорт от {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        logger.info(f"✅ Экспорт выполнен пользователем {user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка экспорта: {e}", exc_info=True)
        await _reply_or_edit(update, f"❌ Ошибка: {str(e)}", parse_mode='HTML')

# ------------------------------------------------------------
#  КОМАНДА /save (принудительное сохранение данных)
# ------------------------------------------------------------
async def save_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await _reply_or_edit(update, "⛔ Нет прав.", parse_mode='HTML')
        return
    try:
        # Сохраняем подписчиков
        await save_subscribers(await get_subscribers())
        # Сохраняем сообщения
        await save_messages(await load_messages())
        # Сохраняем faq.json (перезаписываем текущими данными)
        faq_data = await load_faq_json()
        await save_faq_json(faq_data)
        await _reply_or_edit(update, "✅ Все данные успешно сохранены.", parse_mode='HTML')
        logger.info(f"💾 Данные сохранены пользователем {user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка при сохранении: {e}")
        await _reply_or_edit(update, f"❌ Ошибка при сохранении: {str(e)}", parse_mode='HTML')

# ------------------------------------------------------------
#  ОБРАБОТЧИК ТЕКСТОВЫХ СООБЩЕНИЙ
# ------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = update.message.text.strip()
    await ensure_subscribed(user.id)
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'message')
    if context.user_data.get('awaiting_feedback'):
        context.user_data['awaiting_feedback'] = False
        if bot_stats:
            bot_stats.log_message(user.id, user.username or "Unknown", 'feedback', text)
        await update.message.reply_text(await get_message('feedback_ack'), parse_mode='HTML')
        return
    if is_greeting(text):
        logger.info(f"Приветствие от {user.id}: '{text}'")
        greeting_text = await get_message('greeting_response')
        await update.message.reply_text(greeting_text, parse_mode='HTML')
        return
    if text.lower() in ['статистика', 'stats'] and user.id in ADMIN_IDS:
        await stats_command(update, context)
        return
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'search')
    if search_engine is None:
        await update.message.reply_text(
            "⚠️ Поиск временно недоступен. Попробуйте позже или используйте /feedback /предложения.",
            parse_mode='HTML'
        )
        return
    category = None
    search_text = text
    if ':' in text:
        parts = text.split(':', 1)
        cat_candidate = parts[0].strip().lower()
        for item in search_engine.faq_data:
            cat = item.get('category')
            if cat and cat_candidate in cat.lower():
                category = cat
                search_text = parts[1].strip()
                break
    try:
        results = search_engine.search(search_text, category, top_k=3)
        logger.info(f"Поиск по запросу '{search_text}', категория {category}, найдено {len(results)} результатов")
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}", exc_info=True)
        results = []
    if not results:
        suggestions = []
        if hasattr(search_engine, 'suggest_correction'):
            suggestions = search_engine.suggest_correction(search_text, top_k=3)
        if suggestions:
            suggestions_text = '\n'.join([f'• {s}' for s in suggestions])
            text_response = await get_message('suggestions', query=search_text, suggestions=suggestions_text)
            await update.message.reply_text(text_response, parse_mode='HTML')
        else:
            await update.message.reply_text(await get_message('no_results'), parse_mode='HTML')
        return
    for idx, (faq_id, q, a, s) in enumerate(results[:3]):
        response = f"📌 <b>Результат {idx+1}:</b>\n\n• <b>{q}</b>\n{a[:200]}...\n\n"
        keyboard = [
            [
                InlineKeyboardButton("👍 Помог", callback_data=f"rate_{faq_id}_1"),
                InlineKeyboardButton("👎 Нет", callback_data=f"rate_{faq_id}_0")
            ]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        await update.message.reply_text(response, parse_mode='HTML', reply_markup=reply_markup)
    await update.message.reply_text("🔍 /categories — все темы")

# ------------------------------------------------------------
#  ОБРАБОТЧИК INLINE-КНОПОК
# ------------------------------------------------------------
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'export_excel':
        if update.effective_user.id in ADMIN_IDS:
            await export_to_excel(update, context)
        else:
            await query.answer("⛔ Нет прав", show_alert=True)
        return
    if data.startswith('stats_'):
        period_map = {
            'stats_day': 'day', 'stats_week': 'week', 'stats_month': 'month',
            'stats_quarter': 'quarter', 'stats_halfyear': 'halfyear', 'stats_year': 'year'
        }
        period = period_map.get(data, 'all')
        context.args = [period]
        await stats_command(update, context)
        return
    if data.startswith('rate_'):
        parts = data.split('_')
        if len(parts) >= 3:
            faq_id = int(parts[1])
            is_helpful = parts[2] == '1'
            if bot_stats:
                bot_stats.record_rating(faq_id, is_helpful)
                bot_stats.log_message(
                    update.effective_user.id,
                    update.effective_user.username or "Unknown",
                    'rating_helpful' if is_helpful else 'rating_unhelpful',
                    ''
                )
            await query.edit_message_reply_markup(reply_markup=None)
            await query.answer("Спасибо за оценку! 👍", show_alert=False)
        return
    if data.startswith('cat_'):
        category_name = data[4:]
        questions = []
        question_ids = []
        for item in search_engine.faq_data:
            cat = item.get('category')
            if cat == category_name:
                questions.append(item.get('question', ''))
                question_ids.append(item.get('id', 0))
        if not questions:
            await query.edit_message_text(f"❓ В категории {category_name} нет вопросов.")
            return
        keyboard = []
        for qid, q in zip(question_ids, questions[:20]):
            short_q = truncate_question(q, 50)
            button = InlineKeyboardButton(text=short_q, callback_data=f"q_{qid}")
            keyboard.append([button])
        keyboard.append([InlineKeyboardButton("◀ Назад к категориям", callback_data="back_to_categories")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        await query.edit_message_text(
            f"📁 <b>{category_name}</b>\n\nВсего вопросов: {len(questions)}\nВыберите вопрос:",
            parse_mode='HTML',
            reply_markup=reply_markup
        )
    elif data.startswith('q_'):
        faq_id = int(data[2:])
        found = None
        for item in search_engine.faq_data:
            if item.get('id') == faq_id:
                found = item
                break
        if found:
            question = found.get('question', '')
            answer = found.get('answer', '')
            category = found.get('category', '')
            response = f"❓ <b>{question}</b>\n\n📌 <b>Ответ:</b>\n{answer}\n\n📁 Категория: {category}"
            keyboard = [[InlineKeyboardButton("◀ Назад к категории", callback_data=f"cat_{category}")]]
            reply_markup = InlineKeyboardMarkup(keyboard)
            await query.edit_message_text(response, parse_mode='HTML', reply_markup=reply_markup)
        else:
            await query.edit_message_text("❌ Вопрос не найден.")
    elif data == "back_to_categories":
        await categories_command(update, context)
    elif data == "menu_admin" and update.effective_user.id in ADMIN_IDS:
        await admin_panel(update, context)

# ------------------------------------------------------------
#  ОБРАБОТЧИК ОШИБОК
# ------------------------------------------------------------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    error = context.error
    logger.error(f"❌ Ошибка: {type(error).__name__}: {error}", exc_info=True)
    if bot_stats:
        user_id = update.effective_user.id if update and update.effective_user else None
        bot_stats.log_error(type(error).__name__, str(error), user_id)
    if ADMIN_IDS and application:
        for aid in ADMIN_IDS:
            try:
                await application.bot.send_message(
                    aid,
                    f"⚠️ <b>Ошибка</b>\n{type(error).__name__}: {str(error)[:200]}",
                    parse_mode='HTML'
                )
            except:
                pass

# ------------------------------------------------------------
#  ИНИЦИАЛИЗАЦИЯ БОТА (ВЫЗЫВАЕТСЯ ЧЕРЕЗ @app.before_serving)
# ------------------------------------------------------------
@app.before_serving
async def setup_bot():
    global application, search_engine, bot_stats, _bot_initialized, _bot_initializing, _routes_registered

    async with _bot_init_lock:
        if _bot_initialized or _bot_initializing:
            logger.info("ℹ️ Бот уже инициализируется или инициализирован")
            return

        _bot_initializing = True
        logger.info("🚀 Инициализация бота версии 12.67...")

        try:
            use_builtin = False

            # Загрузка поискового движка
            try:
                from search_engine import EnhancedSearchEngine
                ext_engine = EnhancedSearchEngine(max_cache_size=1000)
                search_engine = ExternalSearchEngineAdapter(ext_engine)
                test_result = search_engine.search("тест", top_k=1)
                if test_result is not None:
                    logger.info("✅ Загружен EnhancedSearchEngine из search_engine.py")
                else:
                    raise ImportError("Тест не пройден")
            except (ImportError, Exception) as e:
                logger.debug(f"EnhancedSearchEngine не подходит: {e}")
                try:
                    from search_engine import SearchEngine as ExternalSearchEngine
                    ext_engine = ExternalSearchEngine()
                    search_engine = ExternalSearchEngineAdapter(ext_engine)
                    test_result = search_engine.search("тест", top_k=1)
                    if test_result is not None:
                        logger.info("✅ Загружен SearchEngine из search_engine.py")
                    else:
                        raise ImportError("Тест не пройден")
                except (ImportError, Exception) as e2:
                    logger.debug(f"Внешний SearchEngine не подходит: {e2}")
                    use_builtin = True

            if use_builtin:
                search_engine = BuiltinSearchEngine()
                logger.info("✅ Используется встроенный BuiltinSearchEngine")

            bot_stats = BotStatistics()
            logger.info("✅ Инициализирован модуль статистики")

            builder = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init)
            application = builder.build()

            if MEME_MODULE_AVAILABLE:
                await init_meme_handler(application.job_queue, admin_ids=ADMIN_IDS)
                logger.info("✅ Модуль мемов инициализирован")
            else:
                logger.warning("⚠️ Модуль мемов не загружен")

            # --- ТОЛЬКО ЛАТИНСКИЕ КОМАНДЫ В CommandHandler ---
            application.add_handler(CommandHandler("start", start_command))
            application.add_handler(CommandHandler("help", help_command))
            application.add_handler(CommandHandler("categories", categories_command))
            application.add_handler(CommandHandler("faq", categories_command))
            application.add_handler(CommandHandler("feedback", feedback_command))
            application.add_handler(CommandHandler("suggestions", feedback_command))
            application.add_handler(CommandHandler("feedbacks", feedbacks_command))
            application.add_handler(CommandHandler("stats", stats_command))
            application.add_handler(CommandHandler("export", export_command))
            application.add_handler(CommandHandler("subscribe", subscribe_command))
            application.add_handler(CommandHandler("unsubscribe", unsubscribe_command))
            application.add_handler(CommandHandler("broadcast", broadcast_command))
            application.add_handler(CommandHandler("whatcanido", what_can_i_do))
            application.add_handler(CommandHandler("save", save_command))

            if MEME_MODULE_AVAILABLE:
                application.add_handler(CommandHandler("mem", meme_command))
                application.add_handler(CommandHandler("memsub", meme_subscribe_command))
                application.add_handler(CommandHandler("memunsub", meme_unsubscribe_command))

            # --- КИРИЛЛИЧЕСКИЕ КОМАНДЫ ЧЕРЕЗ MessageHandler ---
            async def russian_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
                text = update.message.text.lower().strip()
                if text.startswith('/старт'):
                    await start_command(update, context)
                elif text.startswith('/помощь'):
                    await help_command(update, context)
                elif text.startswith('/категории'):
                    await categories_command(update, context)
                elif text.startswith('/предложения'):
                    await feedback_command(update, context)
                elif text.startswith('/отзывы'):
                    await feedbacks_command(update, context)
                elif text.startswith('/статистика'):
                    await stats_command(update, context)
                elif text.startswith('/экспорт'):
                    await export_command(update, context)
                elif text.startswith('/подписаться'):
                    await subscribe_command(update, context)
                elif text.startswith('/отписаться'):
                    await unsubscribe_command(update, context)
                elif text.startswith('/рассылка'):
                    await broadcast_command(update, context)
                elif text.startswith('/сохранить'):
                    await save_command(update, context)
                elif text.startswith('/мем'):
                    if MEME_MODULE_AVAILABLE:
                        await meme_command(update, context)
                elif text.startswith('/мемподписка'):
                    if MEME_MODULE_AVAILABLE:
                        await meme_subscribe_command(update, context)
                elif text.startswith('/мемотписка'):
                    if MEME_MODULE_AVAILABLE:
                        await meme_unsubscribe_command(update, context)
                elif text.startswith('/что_могу'):
                    await what_can_i_do(update, context)
                elif text.startswith('/админ'):
                    await admin_panel(update, context)

            application.add_handler(MessageHandler(
                filters.Regex(r'^/(старт|помощь|категории|предложения|отзывы|статистика|экспорт|подписаться|отписаться|рассылка|сохранить|мем|мемподписка|мемотписка|что_могу|админ)'),
                russian_command_handler
            ))

            application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
            application.add_handler(CallbackQueryHandler(handle_callback_query))
            application.add_error_handler(error_handler)

            # === РЕГИСТРАЦИЯ ВЕБ-МАРШРУТОВ ===
            if not _routes_registered:
                register_web_routes(
                    app,
                    application=application,
                    search_engine=search_engine,
                    bot_stats=bot_stats,
                    load_faq_json=load_faq_json,
                    save_faq_json=save_faq_json,
                    get_next_faq_id=get_next_faq_id,
                    load_messages=load_messages,
                    save_messages=save_messages,
                    get_subscribers=get_subscribers,
                    WEBHOOK_SECRET=WEBHOOK_SECRET,
                    BASE_URL=BASE_URL,
                    MEME_MODULE_AVAILABLE=MEME_MODULE_AVAILABLE,
                    get_meme_handler=get_meme_handler,
                    is_authorized_func=is_authorized,
                    admin_ids=ADMIN_IDS
                )
                _routes_registered = True
                logger.info("✅ Веб-маршруты зарегистрированы один раз")
            else:
                logger.info("ℹ️ Веб-маршруты уже зарегистрированы, пропускаем повторную регистрацию")

            await application.initialize()
            await application.start()

            if RENDER:
                webhook_url = WEBHOOK_URL + WEBHOOK_PATH
                logger.info(f"🔄 Установка вебхука на {webhook_url}...")
                result = await application.bot.set_webhook(
                    url=webhook_url,
                    secret_token=WEBHOOK_SECRET,
                    drop_pending_updates=True,
                    max_connections=40
                )
                if result:
                    logger.info(f"✅ Вебхук успешно установлен на {webhook_url}")
                    info = await application.bot.get_webhook_info()
                    if info.url == webhook_url:
                        logger.info("✅ Вебхук подтверждён")
                    else:
                        logger.error(f"❌ Вебхук не совпадает: {info.url}")
                else:
                    logger.error("❌ Не удалось установить вебхук")
            else:
                await application.bot.delete_webhook(drop_pending_updates=True)
                logger.info("✅ Режим поллинга (локальная разработка)")

            # Запуск периодических задач
            asyncio.create_task(periodic_subscriber_save())
            asyncio.create_task(periodic_cleanup())

            _bot_initialized = True
            _bot_initializing = False
            logger.info("✅✅✅ Бот полностью инициализирован и готов к работе ✅✅✅")

        except Exception as e:
            _bot_initializing = False
            logger.critical(f"❌❌❌ КРИТИЧЕСКАЯ ОШИБКА ИНИЦИАЛИЗАЦИИ: {e}", exc_info=True)

# ------------------------------------------------------------
#  AFTER_SERVING
# ------------------------------------------------------------
@app.after_serving
async def cleanup():
    global _bot_initialized
    _bot_initialized = False
    logger.info("💤 Сервер останавливается, бот засыпает")

# ------------------------------------------------------------
#  ФУНКЦИЯ ЗАВЕРШЕНИЯ РАБОТЫ (ВЫЗЫВАЕТСЯ ПО СИГНАЛУ)
# ------------------------------------------------------------
async def shutdown():
    logger.info("🛑 Завершение работы...")
    global _bot_initialized
    _bot_initialized = False
    if MEME_MODULE_AVAILABLE:
        await close_meme_handler()
    if application:
        await application.stop()
        await application.shutdown()
    await save_subscribers(await get_subscribers())
    await save_messages(await load_messages())
    logger.info("✅ Завершено.")

# ------------------------------------------------------------
#  ЭНДПОИНТ /WAKE
# ------------------------------------------------------------
@app.route('/wake', methods=['GET', 'POST'])
async def wake():
    if not _bot_initialized:
        logger.info("🔄 Пробуждение: запуск инициализации")
        asyncio.create_task(setup_bot())
        return jsonify({'status': 'waking_up'}), 202
    return jsonify({'status': 'ok', 'awake': True}), 200

# ------------------------------------------------------------
#  ЭНДПОИНТ /SAVE (принудительное сохранение)
# ------------------------------------------------------------
@app.route('/save', methods=['POST'])
async def force_save():
    if not is_authorized(request, WEBHOOK_SECRET):
        return jsonify({'error': 'Forbidden'}), 403
    await save_subscribers(await get_subscribers())
    await save_messages(await load_messages())
    logger.info("💾 Принудительное сохранение выполнено")
    return jsonify({'status': 'saved'}), 200

# ------------------------------------------------------------
#  ОБРАБОТЧИК ВЕБХУКА
# ------------------------------------------------------------
@app.route(WEBHOOK_PATH, methods=['POST'])
async def telegram_webhook():
    global _bot_initialized, _bot_initializing
    timeout = 10
    start_time = time.time()
    while not _bot_initialized and _bot_initializing and (time.time() - start_time) < timeout:
        await asyncio.sleep(0.1)
    if not _bot_initialized:
        logger.warning("⚠️ Получен вебхук до завершения инициализации бота")
        return jsonify({'error': 'Bot not initialized yet'}), 503
    try:
        secret_token = request.headers.get('X-Telegram-Bot-Api-Secret-Token')
        if secret_token != WEBHOOK_SECRET:
            logger.warning(f"Неверный секретный токен: {secret_token}")
            return jsonify({'error': 'Invalid secret token'}), 403
        update_data = await request.get_json()
        if not update_data:
            return jsonify({'error': 'No data'}), 400
        update = Update.de_json(update_data, application.bot)
        await application.process_update(update)
        return jsonify({'status': 'ok'}), 200
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

# ------------------------------------------------------------
#  МАРШРУТЫ / и /health УДАЛЕНЫ (определены в web_panel.py)
# ------------------------------------------------------------

# ------------------------------------------------------------
#  MAIN (ЛОКАЛЬНЫЙ ЗАПУСК)
# ------------------------------------------------------------
async def main():
    logger.info("🔄 Локальный запуск...")
    await setup_bot()
    if not RENDER:
        polling_task = asyncio.create_task(application.start_polling(allowed_updates=Update.ALL_TYPES))
    config = Config()
    config.bind = [f"0.0.0.0:{PORT}"]
    await serve(app, config)

def shutdown_signal(sig):
    logger.info(f"Получен сигнал {sig}, инициируем завершение...")
    loop = asyncio.get_event_loop()
    loop.create_task(shutdown())

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: shutdown_signal(s))
    asyncio.run(main())
