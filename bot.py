#!/usr/bin/env python3
"""
Telegram-бот для HR-отдела компании "Мечел"
Версия 12.62 — исправлена передача ID для оценок, улучшена навигация
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
    critical_packages = ['quart', 'python-telegram-bot', 'hypercorn', 'pandas']
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
#  КОНФИГУРАЦИЯ ЛОГИРОВАНИЯ
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
        """
        Возвращает список кортежей (id, question, answer, score)
        """
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
        
        results.sort(key=lambda x: x[3], reverse=True)  # сортировка по score
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
                
                # Преобразуем результат в единый формат (id, question, answer, score)
                normalized = []
                for r in results:
                    if isinstance(r, tuple) and len(r) >= 3:
                        # если внешний движок возвращает (question, answer, score)
                        # id берём из хеша вопроса (не идеально, но лучше, чем ничего)
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
#  POST_INIT
# ------------------------------------------------------------
async def post_init(application: Application):
    logger.info("✅ Приложение Telegram полностью готово и запущено")

# ------------------------------------------------------------
#  ОБРАБОТЧИКИ КОМАНД
# ------------------------------------------------------------
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    PHOTO_PATH = os.path.join(os.path.dirname(__file__), 'mechel_start.png')
    keyboard = [
        [InlineKeyboardButton("🚀 СТАРТ", callback_data="menu_start")],
        [InlineKeyboardButton("📚 Что я умею", callback_data="menu_features")],
        [InlineKeyboardButton("📂 Категории вопросов", callback_data="menu_categories")]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    text = await get_message("welcome", first_name=user.first_name)
    
    if os.path.exists(PHOTO_PATH):
        try:
            with open(PHOTO_PATH, 'rb') as photo_file:
                await update.message.reply_photo(
                    photo=photo_file,
                    caption=text,
                    parse_mode='HTML',
                    reply_markup=reply_markup
                )
            bot_stats.log_message(user.id, user.username or "unknown", 'command_start_with_image')
            return
        except Exception as e:
            logger.warning(f"Ошибка отправки картинки: {e}")
    
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)
    bot_stats.log_message(user.id, user.username or "unknown", 'command_start')

async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = await get_message("help")
    await update.message.reply_text(text, parse_mode='HTML')
    bot_stats.log_message(update.effective_user.id, update.effective_user.username or "unknown", 'command')

async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not search_engine or not search_engine.faq_data:
        await update.message.reply_text("📂 База знаний пока пуста.", parse_mode='HTML')
        return
    categories = set(item.get('category', 'Без категории') for item in search_engine.faq_data)
    if not categories:
        await update.message.reply_text("📂 Категории не найдены.", parse_mode='HTML')
        return
    text = "📂 <b>Доступные категории:</b>\n" + "\n".join(f"• {cat}" for cat in sorted(categories))
    await update.message.reply_text(text, parse_mode='HTML')
    bot_stats.log_message(update.effective_user.id, update.effective_user.username or "unknown", 'command')

async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not context.args:
        await update.message.reply_text(
            "💬 Напишите ваш отзыв или предложение после команды, например:\n"
            "/feedback Было бы здорово добавить раздел про обучение",
            parse_mode='HTML'
        )
        return
    feedback_text = ' '.join(context.args)
    bot_stats.log_message(
        update.effective_user.id,
        update.effective_user.username or "unknown",
        'feedback',
        text=feedback_text
    )
    await update.message.reply_text(
        await get_message("feedback_ack"),
        parse_mode='HTML'
    )

async def feedbacks_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    feedbacks = bot_stats.get_feedback_list(limit=20)
    if not feedbacks:
        await update.message.reply_text("📭 Отзывов пока нет.")
        return
    text = "📝 <b>Последние отзывы:</b>\n"
    for fb in feedbacks[:10]:
        dt = fb['timestamp'].strftime("%d.%m %H:%M")
        username = fb['username'] or str(fb['user_id'])
        short_text = fb['text'][:100] + "..." if len(fb['text']) > 100 else fb['text']
        text += f"\n{dt} @{username}: {short_text}"
    await update.message.reply_text(text, parse_mode='HTML')

async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    period = 'all'
    if context.args:
        period = parse_period_argument(context.args[0])
    cache_size = len(getattr(search_engine, 'cache', {})) if search_engine else 0
    stats = bot_stats.get_summary_stats(period=period, cache_size=cache_size)
    rating_stats = bot_stats.get_rating_stats()
    text = (
        f"📊 <b>Статистика ({period})</b>\n"
        f"👥 Пользователей: {stats['total_users']}\n"
        f"💬 Сообщений: {stats['total_messages']}\n"
        f"🔍 Поисков: {stats['total_searches']}\n"
        f"📝 Отзывов: {stats['total_feedback']}\n"
        f"⭐ Оценок: {stats['total_ratings']} (полезных: {rating_stats['helpful']}, нет: {rating_stats['unhelpful']})\n"
        f"😊 Удовлетворённость: {rating_stats['satisfaction_rate']}%\n"
        f"⚡ Время ответа: {stats['avg_response_time']:.2f} сек ({stats['response_time_status']})\n"
        f"🗃️ Кэш: {stats['cache_size']}\n"
        f"⏳ Uptime: {stats['uptime']}\n"
        f"❌ Ошибок: {stats['error_count']}"
    )
    await update.message.reply_text(text, parse_mode='HTML')

async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    subscribers = await get_subscribers()
    try:
        output = await generate_excel_report(bot_stats, subscribers)
        await update.message.reply_document(
            document=output,
            filename=f"hr_bot_report_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx",
            caption="📊 Отчёт HR-бота"
        )
    except Exception as e:
        logger.error(f"Ошибка экспорта: {e}")
        await update.message.reply_text("❌ Не удалось создать отчёт.")

async def subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await add_subscriber(user_id):
        text = await get_message("subscribe_success")
        bot_stats.log_message(user_id, update.effective_user.username or "unknown", 'subscribe')
    else:
        text = await get_message("already_subscribed")
    await update.message.reply_text(text, parse_mode='HTML')

async def unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user_id = update.effective_user.id
    if await remove_subscriber(user_id):
        text = await get_message("unsubscribe_success")
        bot_stats.log_message(user_id, update.effective_user.username or "unknown", 'unsubscribe')
    else:
        text = await get_message("not_subscribed")
    await update.message.reply_text(text, parse_mode='HTML')

async def broadcast_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Доступ запрещён.")
        return
    if not context.args:
        await update.message.reply_text(
            "📢 Использование: /broadcast <текст сообщения>\n"
            "Можно использовать HTML-разметку."
        )
        return
    message = ' '.join(context.args)
    subscribers = await get_subscribers()
    if not subscribers:
        await update.message.reply_text("❌ Нет подписчиков для рассылки.")
        return
    await update.message.reply_text(f"📢 Начинаю рассылку {len(subscribers)} подписчикам...")
    sent = 0
    failed = 0
    for uid in subscribers:
        try:
            await context.bot.send_message(chat_id=uid, text=message, parse_mode='HTML')
            sent += 1
            await asyncio.sleep(0.05)
        except Exception as e:
            logger.error(f"Ошибка отправки пользователю {uid}: {e}")
            failed += 1
    await update.message.reply_text(f"✅ Рассылка завершена. Отправлено: {sent}, ошибок: {failed}")

async def what_can_i_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    text = (
        "📋 <b>Что я умею:</b>\n"
        "• Отвечать на HR-вопросы (просто напишите)\n"
        "• Показывать категории: /categories\n"
        "• Принимать предложения: /feedback\n"
        "• Присылать мемы: /мем или /mem\n"
        "• Подписаться на рассылку: /subscribe\n"
        "💡 Совет: можно писать «отпуск: как перенести?» — я найду точнее!"
    )
    await update.message.reply_text(text, parse_mode='HTML')
    bot_stats.log_message(update.effective_user.id, update.effective_user.username or "unknown", 'command')

async def admin_panel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        return
    text = (
        "👑 <b>Админ-панель</b>\n"
        "• Статистика: /stats [day|week|month]\n"
        "• Управление FAQ: /faq → веб-панель\n"
        "• Рассылка: /broadcast или /рассылка\n"
        "• Экспорт: /export\n"
        "• Отзывы: /feedbacks\n"
        "• Мемы: /memsub, /memunsub\n"
        f"• Веб-интерфейс: {BASE_URL}"
    )
    keyboard = [[InlineKeyboardButton("👑 Открыть админ-меню", callback_data="menu_admin")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=reply_markup)
    bot_stats.log_message(update.effective_user.id, update.effective_user.username or "unknown", 'command_admin')

# ------------------------------------------------------------
#  ОБРАБОТЧИК СООБЩЕНИЙ (исправлен: использует id из результата)
# ------------------------------------------------------------
async def handle_message(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    user_id = user.id
    username = user.username or "unknown"
    text = update.message.text.strip()
    
    if is_greeting(text):
        reply = await get_message("greeting_response")
        await update.message.reply_text(reply, parse_mode='HTML')
        bot_stats.log_message(user_id, username, 'message')
        return
    
    start_time = time.time()
    bot_stats.log_message(user_id, username, 'search')
    
    category = None
    query = text
    if ':' in text:
        parts = text.split(':', 1)
        category = parts[0].strip()
        query = parts[1].strip()
    
    results = search_engine.search(query, category=category, top_k=5) if search_engine else []
    response_time = time.time() - start_time
    bot_stats.track_response_time(response_time)
    
    if not results:
        suggestions = search_engine.suggest_correction(query, top_k=3) if search_engine else []
        if suggestions:
            sugg_text = "\n".join(f"• {s}" for s in suggestions)
            reply = await get_message("suggestions", query=query, suggestions=sugg_text)
        else:
            reply = await get_message("no_results")
        await update.message.reply_text(reply, parse_mode='HTML')
        return
    
    # Отправка результатов (теперь results — список (id, question, answer, score))
    for i, (faq_id, q, a, score) in enumerate(results, 1):
        short_q = truncate_question(q, 50)
        text = f"<b>{short_q}</b>\n{a}"
        if i < len(results):
            text += "\n---"
        await update.message.reply_text(text, parse_mode='HTML')
        await asyncio.sleep(0.5)
    
    # Кнопки оценки с правильным ID
    keyboard = [
        [
            InlineKeyboardButton("👍 Полезно", callback_data=f"helpful_{faq_id}"),
            InlineKeyboardButton("👎 Бесполезно", callback_data=f"unhelpful_{faq_id}")
        ] for (faq_id, _, _, _) in results
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        "Оцените ответы (по каждому вопросу):",
        reply_markup=reply_markup
    )

# ------------------------------------------------------------
#  ОБРАБОТЧИК CALLBACK (исправлен: принимает реальные ID)
# ------------------------------------------------------------
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    
    # === ГЛАВНОЕ МЕНЮ ===
    if data == "menu_start":
        if not search_engine or not search_engine.faq_data:
            text = "📂 База знаний пока пуста."
            await query.edit_message_text(text, parse_mode='HTML')
            bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", 'menu_start_empty')
            return
        
        categories = sorted(set(item.get('category', 'Без категории') for item in search_engine.faq_data))
        categories_text = "📂 <b>Доступные категории:</b>\n" + "\n".join(f"• {cat}" for cat in categories[:5])
        
        example_item = next((item for item in search_engine.faq_data if item.get('category') == categories[0]), None)
        example_text = f"\n\n💡 <b>Пример вопроса из категории «{categories[0]}»:</b>\n«{example_item['question']}»" if example_item else ""
        
        full_text = categories_text + example_text + "\n\nВыберите действие:"
        
        keyboard = [
            [InlineKeyboardButton("📂 Категории вопросов", callback_data="menu_categories")],
            [InlineKeyboardButton("📋 Что я ещё умею", callback_data="menu_features")],
            [InlineKeyboardButton("🏠 Вернуться в начало", callback_data="menu_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(full_text, parse_mode='HTML', reply_markup=reply_markup)
        bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", 'menu_start')
        return
    
    # === СПИСОК КАТЕГОРИЙ ===
    elif data == "menu_categories":
        if not search_engine or not search_engine.faq_data:
            text = "📂 База знаний пока пуста."
            await query.edit_message_text(text, parse_mode='HTML')
            bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", 'menu_categories_empty')
            return
        
        categories = sorted(set(item.get('category', 'Без категории') for item in search_engine.faq_data))
        keyboard = []
        for cat in categories[:10]:
            keyboard.append([InlineKeyboardButton(f"📁 {cat}", callback_data=f"category_{cat}")])
        keyboard.append([InlineKeyboardButton("⬅️ Назад", callback_data="menu_start")])
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        text = "📂 <b>Выберите категорию вопросов:</b>"
        if len(categories) > 10:
            text += f"\n\n(Показаны первые 10 из {len(categories)} категорий)"
        
        await query.edit_message_text(text, parse_mode='HTML', reply_markup=reply_markup)
        bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", 'menu_categories')
        return
    
    # === ВОПРОСЫ В КАТЕГОРИИ ===
    elif data.startswith("category_"):
        category = data.split("_", 1)[1]
        if not search_engine or not search_engine.faq_data:
            text = "📂 База знаний пока пуста."
            await query.edit_message_text(text, parse_mode='HTML')
            return
        
        questions = [item for item in search_engine.faq_data if item.get('category') == category][:10]
        if not questions:
            text = f"❌ В категории «{category}» пока нет вопросов."
            keyboard = [[InlineKeyboardButton("⬅️ Назад к категориям", callback_data="menu_categories")]]
            await query.edit_message_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))
            return
        
        questions_text = f"📂 <b>Вопросы в категории «{category}»:</b>\n\n"
        for i, q in enumerate(questions, 1):
            short_q = truncate_question(q['question'], 60)
            questions_text += f"{i}. {short_q}\n"
        questions_text += f"\n💡 Напишите свой вопрос или выберите из списка выше!"
        
        keyboard = [
            [InlineKeyboardButton("⬅️ Назад к категориям", callback_data="menu_categories")],
            [InlineKeyboardButton("🏠 В начало", callback_data="menu_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(questions_text, parse_mode='HTML', reply_markup=reply_markup)
        bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", f'category_view_{category}')
        return
    
    # === ДОПОЛНИТЕЛЬНЫЕ ВОЗМОЖНОСТИ ===
    elif data == "menu_features":
        features_text = (
            "📋 <b>Что я ещё умею:</b>\n\n"
            "😄 <b>Мемы</b>\n"
            "• /mem — прислать случайный мем\n"
            "• /memsub — подписаться на ежедневную мем-рассылку (в 9:30 МСК)\n"
            "• /memunsub — отписаться от рассылки мемов\n\n"
            "📚 <b>База знаний</b>\n"
            "• /categories — показать все категории вопросов\n"
            "• /feedback — предложить добавить новый вопрос в базу знаний\n\n"
            "🔔 <b>Уведомления</b>\n"
            "• /subscribe — подписаться на важные уведомления от HR\n"
            "• /unsubscribe — отписаться от уведомлений\n\n"
            "🔄 <b>Перезагрузка</b>\n"
            "• /start — начать диалог заново"
        )
        
        keyboard = [
            [InlineKeyboardButton("📂 Категории вопросов", callback_data="menu_categories")],
            [InlineKeyboardButton("🏠 Вернуться в начало", callback_data="menu_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(features_text, parse_mode='HTML', reply_markup=reply_markup)
        bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", 'menu_features')
        return
    
    # === АДМИН-МЕНЮ ===
    elif data == "menu_admin" and query.from_user.id in ADMIN_IDS:
        admin_text = (
            "👑 <b>Админ-панель</b>\n\n"
            "📊 <b>Статистика:</b>\n"
            "• /stats — общая статистика бота\n"
            "• /stats day — за сегодня\n"
            "• /stats week — за неделю\n"
            "• /stats month — за месяц\n"
            "• /feedbacks — последние отзывы пользователей\n\n"
            "⚙️ <b>Управление контентом:</b>\n"
            "• /faq — веб-панель управления базой знаний (в браузере)\n"
            "• /messages — редактирование системных сообщений\n"
            "• /export — экспорт статистики в Excel\n\n"
            "📣 <b>Рассылки:</b>\n"
            "• /broadcast текст — рассылка всем подписчикам\n"
            "• /рассылка текст — альтернативная команда на русском\n\n"
            "🤖 <b>Мемы:</b>\n"
            "• /memsub — подписка на ежедневную мем-рассылку (09:30 МСК)\n"
            "• /memunsub — отписка от мем-рассылки"
        )
        
        keyboard = [
            [InlineKeyboardButton("📂 Категории", callback_data="menu_categories")],
            [InlineKeyboardButton("🏠 В начало", callback_data="menu_start")]
        ]
        reply_markup = InlineKeyboardMarkup(keyboard)
        
        await query.edit_message_text(admin_text, parse_mode='HTML', reply_markup=reply_markup)
        bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", 'menu_admin')
        return
    
    # === ОЦЕНКИ ОТВЕТОВ (исправлено: используются реальные ID) ===
    elif data.startswith("helpful_"):
        faq_id = int(data.split("_")[1])
        bot_stats.record_rating(faq_id, True)
        bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", 'rating_helpful')
        await query.edit_message_text("✅ Спасибо за оценку! Рад был помочь!")
    elif data.startswith("unhelpful_"):
        faq_id = int(data.split("_")[1])
        bot_stats.record_rating(faq_id, False)
        bot_stats.log_message(query.from_user.id, query.from_user.username or "unknown", 'rating_unhelpful')
        await query.edit_message_text(
            "🙏 Спасибо за обратную связь! Мы улучшим ответы.\n"
            "Напишите ваш вопрос подробнее или используйте /feedback для предложения."
        )

# ------------------------------------------------------------
#  ОБРАБОТЧИК ОШИБОК
# ------------------------------------------------------------
async def error_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    logger.error(msg="Exception while handling an update:", exc_info=context.error)
    bot_stats.log_error("telegram_error", str(context.error), update.effective_user.id if update else None)

# ------------------------------------------------------------
#  ЗАВЕРШЕНИЕ РАБОТЫ
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
    logger.info("✅ Завершено.")

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
        logger.info("🚀 Инициализация бота версии 12.62...")
        
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
                filters.Regex(r'^/(старт|помощь|категории|предложения|отзывы|статистика|экспорт|подписаться|отписаться|рассылка|мем|мемподписка|мемотписка|что_могу|админ)'),
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
                    get_meme_handler=get_meme_handler
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
            
            asyncio.create_task(periodic_subscriber_save())
            
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
#  МАРШРУТ /HEALTH
# ------------------------------------------------------------
@app.route('/health', methods=['GET'])
async def health_check():
    global _bot_initialized, _bot_initializing
    status = {
        'status': 'ok' if _bot_initialized else 'initializing' if _bot_initializing else 'error',
        'bot': 'running' if _bot_initialized else 'initializing',
        'timestamp': datetime.now().isoformat(),
        'webhook_path': WEBHOOK_PATH,
        'webhook_url': WEBHOOK_URL + WEBHOOK_PATH if RENDER else 'local',
        'search_engine': 'loaded' if search_engine else 'not_loaded',
        'bot_stats': 'initialized' if bot_stats else 'not_initialized',
        'application': 'initialized' if application and _bot_initialized else 'not_initialized',
        'initialization': {'completed': _bot_initialized, 'in_progress': _bot_initializing}
    }
    return jsonify(status), 200 if _bot_initialized else 202

# ------------------------------------------------------------
#  МАРШРУТ /
# ------------------------------------------------------------
@app.route('/', methods=['GET'])
async def index():
    global _bot_initialized, _bot_initializing
    if _bot_initialized:
        status_text = "✅ Готов к работе"
        status_color = "#27ae60"
    elif _bot_initializing:
        status_text = "🔄 Инициализация..."
        status_color = "#f39c12"
    else:
        status_text = "❌ Ошибка инициализации"
        status_color = "#e74c3c"
    faq_count = len(search_engine.faq_data) if search_engine and hasattr(search_engine, 'faq_data') else 0
    html = f"""
    <!DOCTYPE html>
    <html>
    <head>
        <title>HR Bot - Мечел</title>
        <meta charset="utf-8">
        <style>
            body {{ font-family: Arial, sans-serif; padding: 20px; background: #f5f5f5; }}
            .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
            h1 {{ color: #2c3e50; }}
            .status {{ font-weight: bold; color: {status_color}; }}
            .info {{ margin: 15px 0; padding: 10px; background: #f8f9fa; border-radius: 5px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 HR Bot - Мечел</h1>
            <p class="status">{status_text}</p>
            <div class="info">
                <strong>Версия:</strong> 12.62 (Render)<br>
                <strong>Режим:</strong> {"Render (Production)" if RENDER else "Local (Development)"}<br>
                <strong>Вебхук:</strong> {WEBHOOK_URL + WEBHOOK_PATH if RENDER else "не настроен"}<br>
                <strong>База знаний:</strong> {faq_count} записей
            </div>
            <p><a href="/health">Проверить детальный статус</a></p>
            <p><a href="/wake">Разбудить бота (для UptimeRobot)</a></p>
        </div>
    </body>
    </html>
    """
    return html, 200

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
