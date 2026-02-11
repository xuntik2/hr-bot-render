#!/usr/bin/env python3
"""
Telegram-бот для HR-отдела компании "Мечел"
Версия 12.16 (Render-Ultimate) — исправлен синтаксис global,
полный класс статистики, адаптер для внешних модулей.
"""

import os
import sys
import asyncio
import logging
import json
import time
import functools
import hashlib
import re
import io
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

import psutil

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
    WEBHOOK_SECRET = 'mechel_hr_dev_' + hashlib.md5(BOT_TOKEN.encode()).hexdigest()[:16]
    if RENDER:
        logger.warning("⚠️ WEBHOOK_SECRET сгенерирован автоматически. Установите вручную для продакшена.")

WEBHOOK_PATH = f"/webhook/{WEBHOOK_SECRET}"
WEBHOOK_URL = os.getenv('WEBHOOK_URL', '')

if RENDER and not WEBHOOK_URL:
    logger.critical("❌ На Render WEBHOOK_URL обязателен")
    sys.exit(1)

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
    optional_files = ['search_engine.py', 'faq_data.py']
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
#  ВСТРОЕННЫЙ ПОИСКОВЫЙ ДВИЖОК (ЭТАЛОННЫЙ)
# ------------------------------------------------------------
class BuiltinSearchEngine:
    def __init__(self, max_cache_size: int = 1000):
        self.max_cache_size = max_cache_size
        self.cache = {}
        self.cache_ttl = {}
        self.faq_data = self._load_faq_data()
        self.stop_words = {
            'как', 'что', 'где', 'когда', 'почему', 'зачем', 'сколько', 'чей',
            'а', 'и', 'но', 'или', 'если', 'то', 'же', 'бы', 'в', 'на', 'с', 'по',
            'о', 'об', 'от', 'до', 'для', 'из', 'у', 'не', 'нет', 'да', 'это',
            'тот', 'этот', 'такой', 'какой', 'все', 'всё', 'его', 'ее', 'их'
        }
        logger.info(f"✅ Загружено {len(self.faq_data)} вопросов во встроенный поиск")

    # --------------------------------------------------------
    #  НОРМАЛИЗАЦИЯ FAQ (ЕДИНЫЙ ФОРМАТ СЛОВАРЕЙ)
    # --------------------------------------------------------
    def _normalize_faq_item(self, item: Any) -> Dict[str, Any]:
        """Преобразует элемент FAQ в словарь независимо от исходного типа."""
        if isinstance(item, dict):
            return {
                'question': item.get('question', ''),
                'answer': item.get('answer', ''),
                'category': item.get('category', 'Без категории'),
                'keywords': item.get('keywords', [])
            }
        # Предполагаем, что это объект с атрибутами
        return {
            'question': getattr(item, 'question', ''),
            'answer': getattr(item, 'answer', ''),
            'category': getattr(item, 'category', 'Без категории'),
            'keywords': getattr(item, 'keywords', [])
        }

    def _load_faq_data(self) -> List[Dict[str, Any]]:
        """Загружает FAQ, нормализуя к единому формату словарей."""
        data = []
        try:
            from faq_data import get_faq_data
            raw_data = get_faq_data()
            for item in raw_data:
                data.append(self._normalize_faq_item(item))
            logger.info(f"✅ Загружено {len(data)} вопросов через get_faq_data()")
            return data
        except ImportError:
            try:
                from faq_data import FAQ_QUESTIONS
                raw_data = FAQ_QUESTIONS
                for item in raw_data:
                    data.append(self._normalize_faq_item(item))
                logger.info(f"✅ Загружено {len(data)} вопросов через FAQ_QUESTIONS")
                return data
            except ImportError:
                logger.warning("⚠️ Файл faq_data.py не найден, используются резервные вопросы")
                return self._get_backup_questions()

    def _get_backup_questions(self) -> List[Dict[str, Any]]:
        return [
            {
                "question": "Как получить справку о заработной плате?",
                "answer": "Справку можно получить в отделе кадров (каб. 205) или через корпоративный портал.",
                "category": "Документы",
                "keywords": ["справка", "зарплата", "заработная", "плата"]
            },
            {
                "question": "Как оформить отпуск?",
                "answer": "Заявление в портале → согласование с руководителем → отдел кадров → приказ.",
                "category": "Отпуск",
                "keywords": ["отпуск", "оформить", "заявление", "отдых"]
            }
        ]

    def _normalize_query(self, query: str) -> str:
        query = query.lower().strip()
        query = re.sub(r'[^\w\s]', ' ', query)
        words = [w for w in query.split() if w not in self.stop_words and len(w) > 2]
        norm = []
        for w in words:
            if w.endswith('ться'): w = w[:-4] + 'ть'
            elif w.endswith('тся'): w = w[:-3] + 'ться'
            elif w.endswith('ать') and len(w) > 4: w = w[:-3]
            elif w.endswith('ить') and len(w) > 4: w = w[:-3]
            elif w.endswith('еть') and len(w) > 4: w = w[:-3]
            elif w.endswith('ый') or w.endswith('ий') or w.endswith('ой'): w = w[:-2]
            elif w.endswith('ая') or w.endswith('яя'): w = w[:-2]
            elif w.endswith('ое') or w.endswith('ее'): w = w[:-2]
            norm.append(w)
        return ' '.join(norm)

    def _calc_score(self, query: str, text: str) -> float:
        if not query or not text:
            return 0.0
        q_words = set(query.split())
        t_words = set(text.split())
        if not q_words:
            return 0.0
        common = q_words.intersection(t_words)
        return len(common) / len(q_words)

    def search(self, query: str, category: Optional[str] = None, top_k: int = 5) -> List[Tuple[str, str, float]]:
        """Основной метод поиска с поддержкой top_k."""
        cache_key = f"{query}_{category}_{top_k}"
        if cache_key in self.cache and datetime.now() < self.cache_ttl.get(cache_key, datetime.now()):
            return self.cache[cache_key]

        norm_q = self._normalize_query(query)
        results = []
        for item in self.faq_data:
            if category and item.get('category') != category:
                continue
            q_score = self._calc_score(norm_q, self._normalize_query(item['question']))
            kw_score = 0
            for kw in item.get('keywords', []):
                kw_score += self._calc_score(norm_q, self._normalize_query(kw))
            a_score = self._calc_score(norm_q, self._normalize_query(item['answer'])) * 0.5
            total = q_score * 2 + kw_score * 1.5 + a_score
            if total > 0.3:
                results.append((item['question'], item['answer'], total))

        results.sort(key=lambda x: x[2], reverse=True)
        top = results[:top_k]

        if len(self.cache) >= self.max_cache_size:
            oldest = next(iter(self.cache_ttl))
            del self.cache[oldest]
            del self.cache_ttl[oldest]
        self.cache[cache_key] = top
        self.cache_ttl[cache_key] = datetime.now() + timedelta(hours=1)
        return top

# ------------------------------------------------------------
#  АДАПТЕР ДЛЯ ВНЕШНЕГО SEARCH ENGINE
# ------------------------------------------------------------
class ExternalSearchEngineAdapter:
    """Адаптирует внешний SearchEngine к нашему интерфейсу."""
    def __init__(self, external_engine):
        self._engine = external_engine
        # Пытаемся определить, поддерживает ли внешний движок top_k
        self._supports_top_k = self._check_top_k_support()
        logger.info(f"🔧 Внешний поисковый движок {'' if self._supports_top_k else 'НЕ '}поддерживает top_k")

    def _check_top_k_support(self) -> bool:
        """Проверяет, принимает ли внешний метод search параметр top_k."""
        import inspect
        sig = inspect.signature(self._engine.search)
        return 'top_k' in sig.parameters

    def search(self, query: str, category: Optional[str] = None, top_k: int = 5) -> List[Tuple[str, str, float]]:
        """Унифицированный метод поиска."""
        try:
            if self._supports_top_k:
                result = self._engine.search(query, category, top_k=top_k)
            else:
                result = self._engine.search(query, category)
                if isinstance(result, list):
                    result = result[:top_k]
            return self._normalize_result(result)
        except Exception as e:
            logger.error(f"❌ Ошибка во внешнем поисковом движке: {e}. Используйте BuiltinSearchEngine.")
            return []

    def _normalize_result(self, result: Any) -> List[Tuple[str, str, float]]:
        """Приводит результат поиска к единому формату."""
        normalized = []
        if isinstance(result, list):
            for item in result:
                if isinstance(item, tuple) and len(item) >= 3:
                    normalized.append((str(item[0]), str(item[1]), float(item[2])))
                elif isinstance(item, dict):
                    question = item.get('question', item.get('Question', ''))
                    answer = item.get('answer', item.get('Answer', ''))
                    score = item.get('score', item.get('Score', 0.0))
                    normalized.append((question, answer, float(score)))
                elif hasattr(item, 'question') and hasattr(item, 'answer'):
                    score = getattr(item, 'score', getattr(item, 'Score', 0.0))
                    normalized.append((item.question, item.answer, float(score)))
        return normalized

    @property
    def cache(self):
        return getattr(self._engine, 'cache', {})

    @property
    def faq_data(self):
        if hasattr(self._engine, 'faq_data'):
            raw = self._engine.faq_data
            normalized = []
            for item in raw:
                if isinstance(item, dict):
                    normalized.append(item)
                else:
                    normalized.append({
                        'question': getattr(item, 'question', ''),
                        'answer': getattr(item, 'answer', ''),
                        'category': getattr(item, 'category', 'Без категории'),
                        'keywords': getattr(item, 'keywords', [])
                    })
            return normalized
        return []

# ------------------------------------------------------------
#  ПОЛНЫЙ КЛАСС СТАТИСТИКИ (СО ВСЕМИ МЕТОДАМИ И АТРИБУТАМИ)
# ------------------------------------------------------------
class BotStatistics:
    """Промышленный трекер статистики с автоочисткой и всеми метриками."""

    def __init__(self, max_history_days: int = 90):
        self.start_time = datetime.now()
        self.user_stats = defaultdict(lambda: {
            'messages': 0, 'commands': 0, 'searches': 0,
            'last_active': None, 'first_seen': None, 'feedback_count': 0
        })
        self.daily_stats = defaultdict(lambda: {
            'messages': 0, 'commands': 0, 'searches': 0,
            'users': set(), 'feedback': 0, 'response_times': []
        })
        self.command_stats = defaultdict(int)
        self.feedback_list = []
        self.error_log = deque(maxlen=1000)
        self.response_times = deque(maxlen=100)
        self.cache = {}
        self.cache_ttl = {}
        self.max_history_days = max_history_days
        self._last_cleanup = datetime.now()
        self._cleanup_lock = asyncio.Lock()

    async def track_user(self, user_id: int):
        """Учёт уникального пользователя."""
        date_key = datetime.now().strftime("%Y-%m-%d")
        self.daily_stats[date_key]['users'].add(user_id)
        await self._cleanup_old_data()

    def track_query(self):
        """Учёт запроса."""
        date_key = datetime.now().strftime("%Y-%m-%d")
        self.daily_stats[date_key]['queries'] += 1

    def track_feedback(self):
        """Учёт обратной связи."""
        date_key = datetime.now().strftime("%Y-%m-%d")
        self.daily_stats[date_key]['feedback'] += 1

    def track_response_time(self, response_time: float):
        """Учёт времени ответа."""
        self.response_times.append({
            'timestamp': datetime.now(),
            'response_time': response_time
        })
        date_key = datetime.now().strftime("%Y-%m-%d")
        self.daily_stats[date_key]['response_times'].append(response_time)

    async def _cleanup_old_data(self):
        """Асинхронная очистка устаревших данных (раз в час)."""
        now = datetime.now()
        if (now - self._last_cleanup).seconds < 3600:
            return

        async with self._cleanup_lock:
            cutoff_date = (now - timedelta(days=self.max_history_days)).strftime("%Y-%m-%d")
            keys_to_delete = [k for k in self.daily_stats.keys() if k < cutoff_date]
            for k in keys_to_delete:
                del self.daily_stats[k]

            expired_keys = [k for k, t in self.cache_ttl.items() if now > t]
            for k in expired_keys:
                self.cache.pop(k, None)
                self.cache_ttl.pop(k, None)

            self._last_cleanup = now

    def get_total_users(self) -> int:
        """Общее количество уникальных пользователей за всю историю."""
        all_users = set()
        for day in self.daily_stats.values():
            all_users.update(day['users'])
        return len(all_users)

    def get_avg_response_time(self) -> float:
        """Среднее время ответа (последние 100 запросов)."""
        if not self.response_times:
            return 0.0
        return sum(rt['response_time'] for rt in self.response_times) / len(self.response_times)

    def get_response_time_status(self) -> Tuple[str, str]:
        """Статус производительности с цветом."""
        avg = self.get_avg_response_time()
        if avg < 1.0:
            return "Хорошо", "green"
        elif avg < 3.0:
            return "Нормально", "yellow"
        else:
            return "Медленно", "red"

    def log_message(self, user_id: int, username: str, msg_type: str, text: str = ""):
        """Логирование сообщения (синхронная версия, вызывает очистку в фоне)."""
        # Запускаем очистку асинхронно, но не ждём её
        asyncio.create_task(self._cleanup_old_data())
        now = datetime.now()
        date_key = now.strftime("%Y-%m-%d")

        if self.user_stats[user_id]['first_seen'] is None:
            self.user_stats[user_id]['first_seen'] = now
        self.user_stats[user_id]['last_active'] = now

        if msg_type == 'command':
            self.user_stats[user_id]['commands'] += 1
            self.command_stats[text] = self.command_stats.get(text, 0) + 1
            self.daily_stats[date_key]['commands'] += 1
        elif msg_type == 'message':
            self.user_stats[user_id]['messages'] += 1
            self.daily_stats[date_key]['messages'] += 1
        elif msg_type == 'search':
            self.user_stats[user_id]['searches'] += 1
            self.daily_stats[date_key]['searches'] += 1
        elif msg_type == 'feedback':
            self.user_stats[user_id]['feedback_count'] += 1
            self.daily_stats[date_key]['feedback'] += 1
            self.feedback_list.append({
                'user_id': user_id, 'username': username,
                'text': text, 'timestamp': now
            })

        self.daily_stats[date_key]['users'].add(user_id)

    def log_error(self, error_type: str, error_msg: str, user_id: int = None):
        """Логирование ошибок."""
        self.error_log.append({
            'timestamp': datetime.now(),
            'type': error_type,
            'message': error_msg,
            'user_id': user_id
        })

    def get_summary_stats(self) -> Dict[str, Any]:
        """Полная сводка статистики."""
        total_users = len(self.user_stats)
        active_24h = sum(
            1 for u in self.user_stats.values()
            if u['last_active'] and (datetime.now() - u['last_active']) < timedelta(hours=24)
        )
        days_stats = {}
        for date in sorted(self.daily_stats.keys(), reverse=True)[:30]:
            ds = self.daily_stats[date]
            days_stats[date] = {
                'messages': ds['messages'],
                'commands': ds['commands'],
                'searches': ds['searches'],
                'users': len(ds['users']),
                'feedback': ds['feedback'],
                'avg_response_time': sum(ds['response_times']) / len(ds['response_times']) if ds['response_times'] else 0
            }
        avg_resp = self.get_avg_response_time()
        status, color = self.get_response_time_status()
        return {
            'uptime': str(datetime.now() - self.start_time),
            'start_time': self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            'total_users': total_users,
            'active_users_24h': active_24h,
            'total_messages': sum(u['messages'] for u in self.user_stats.values()),
            'total_commands': sum(u['commands'] for u in self.user_stats.values()),
            'total_searches': sum(u['searches'] for u in self.user_stats.values()),
            'total_feedback': len(self.feedback_list),
            'avg_response_time': avg_resp,
            'response_time_status': status,
            'response_time_color': color,
            'daily_stats': days_stats,
            'top_commands': dict(sorted(self.command_stats.items(), key=lambda x: x[1], reverse=True)[:10]),
            'cache_size': len(self.cache),
            'error_count': len(self.error_log)
        }

# ------------------------------------------------------------
#  ДЕКОРАТОР ИЗМЕРЕНИЯ ВРЕМЕНИ
# ------------------------------------------------------------
def measure_response_time(func):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        start = time.time()
        try:
            result = await func(*args, **kwargs)
            resp_time = time.time() - start
            if bot_stats:
                bot_stats.track_response_time(resp_time)
            return result
        except Exception as e:
            resp_time = time.time() - start
            if bot_stats:
                bot_stats.track_response_time(resp_time)
            raise e
    return wrapper

# ------------------------------------------------------------
#  ГЛОБАЛЬНЫЕ ОБЪЕКТЫ
# ------------------------------------------------------------
application: Optional[Application] = None
search_engine: Optional[Union[BuiltinSearchEngine, ExternalSearchEngineAdapter]] = None
bot_stats: Optional[BotStatistics] = None

# ------------------------------------------------------------
#  POST_INIT
# ------------------------------------------------------------
async def post_init(application: Application):
    logger.info("✅ Приложение Telegram полностью готово и запущено")

# ------------------------------------------------------------
#  ИНИЦИАЛИЗАЦИЯ БОТА (С АВТОВЫБОРОМ ДВИЖКА)
# ------------------------------------------------------------
async def init_bot():
    global application, search_engine, bot_stats  # ⬅️ ЕДИНСТВЕННОЕ объявление global
    logger.info("🚀 Инициализация бота версии 12.16...")

    try:
        # 1. ИНИЦИАЛИЗАЦИЯ ПОИСКОВОГО ДВИЖКА С АВТОВЫБОРОМ
        use_builtin = False
        try:
            from search_engine import EnhancedSearchEngine
            ext_engine = EnhancedSearchEngine(max_cache_size=1000)
            search_engine = ExternalSearchEngineAdapter(ext_engine)
            test_result = search_engine.search("тест", top_k=1)
            if test_result is not None:
                logger.info("✅ Загружен EnhancedSearchEngine из search_engine.py (адаптирован)")
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
                    logger.info("✅ Загружен SearchEngine из search_engine.py (адаптирован)")
                else:
                    raise ImportError("Тест не пройден")
            except (ImportError, Exception) as e2:
                logger.debug(f"Внешний SearchEngine не подходит: {e2}")
                use_builtin = True

        if use_builtin:
            search_engine = BuiltinSearchEngine()
            logger.info("✅ Используется встроенный BuiltinSearchEngine")

        # 2. ИНИЦИАЛИЗАЦИЯ СТАТИСТИКИ
        bot_stats = BotStatistics()
        logger.info("✅ Инициализирован модуль статистики")

        # 3. ПРИЛОЖЕНИЕ TELEGRAM
        builder = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init)
        application = builder.build()

        # 4. РЕГИСТРАЦИЯ ОБРАБОТЧИКОВ
        application.add_handler(CommandHandler("start", start_command))
        application.add_handler(CommandHandler("help", help_command))
        application.add_handler(CommandHandler("categories", categories_command))
        application.add_handler(CommandHandler("feedback", feedback_command))
        application.add_handler(CommandHandler("stats", stats_command))
        application.add_handler(CommandHandler("export", export_command))
        application.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_message))
        application.add_handler(CallbackQueryHandler(handle_callback_query))
        application.add_error_handler(error_handler)

        # 5. ИНИЦИАЛИЗАЦИЯ И WEBHOOK
        await application.initialize()
        if RENDER:
            webhook_url = WEBHOOK_URL + WEBHOOK_PATH
            logger.info(f"🔄 Установка webhook на {webhook_url}...")
            result = await application.bot.set_webhook(
                url=webhook_url,
                secret_token=WEBHOOK_SECRET,
                drop_pending_updates=True,
                max_connections=40
            )
            if result:
                logger.info(f"✅ Webhook успешно установлен на {webhook_url}")
                info = await application.bot.get_webhook_info()
                if info.url == webhook_url:
                    logger.info("✅ Webhook подтверждён")
                else:
                    logger.error(f"❌ Webhook не совпадает: {info.url}")
                    return False
            else:
                logger.error("❌ Не удалось установить webhook")
                return False
        else:
            await application.bot.delete_webhook(drop_pending_updates=True)
            logger.info("✅ Режим поллинга (локальная разработка)")

        logger.info("✅ Бот полностью инициализирован и готов к работе")
        return True

    except Exception as e:
        logger.critical(f"❌ Ошибка инициализации бота: {e}", exc_info=True)
        # ❗ НЕ ПОВТОРЯЕМ global application — оно уже объявлено в начале функции
        application = None
        return False

# ------------------------------------------------------------
#  ОБРАБОТЧИКИ КОМАНД
# ------------------------------------------------------------
@measure_response_time
async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/start')
    text = (
        f"👋 Привет, {user.first_name}!\n\n"
        "Я HR-бот компании <b>Мечел</b>. Помогу с кадровыми вопросами.\n\n"
        "📌 Просто напишите вопрос — я поищу в базе знаний.\n"
        "/help — подсказки\n"
        "/categories — категории вопросов\n"
        "/feedback — отзыв\n"
    )
    if user.id in ADMIN_IDS:
        text += "\n👑 Админ-команды:\n/stats — статистика\n/export — Excel"
    await update.message.reply_text(text, parse_mode='HTML')

@measure_response_time
async def help_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if bot_stats:
        bot_stats.log_message(update.effective_user.id, update.effective_user.username or "Unknown", 'command', '/help')
    text = (
        "❓ <b>Как пользоваться:</b>\n"
        "1. Задайте вопрос своими словами.\n"
        "2. Можно указать категорию через двоеточие, например:\n"
        "   <i>отпуск: как перенести?</i>\n"
        "3. Используйте /categories для выбора темы.\n\n"
        "📞 HR: +7 (3519) 25-60-00, hr@mechel.ru"
    )
    await update.message.reply_text(text, parse_mode='HTML')

@measure_response_time
async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if bot_stats:
        bot_stats.log_message(update.effective_user.id, update.effective_user.username or "Unknown", 'command', '/categories')
    if search_engine is None:
        await update.message.reply_text("⚠️ Категории временно недоступны (поисковый движок не загружен).")
        return

    categories = {}
    for item in search_engine.faq_data:
        if isinstance(item, dict):
            cat = item.get('category', 'Без категории')
        else:
            cat = getattr(item, 'category', 'Без категории')
        categories[cat] = categories.get(cat, 0) + 1

    if not categories:
        await update.message.reply_text("📂 Категории временно недоступны.")
        return
    text = "📂 <b>Категории:</b>\n"
    for cat, cnt in sorted(categories.items()):
        text += f"• {cat} ({cnt})\n"
    text += "\nУкажите категорию в вопросе через двоеточие."
    await update.message.reply_text(text, parse_mode='HTML')

@measure_response_time
async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if bot_stats:
        bot_stats.log_message(update.effective_user.id, update.effective_user.username or "Unknown", 'command', '/feedback')
    context.user_data['awaiting_feedback'] = True
    await update.message.reply_text(
        "💬 Напишите ваш отзыв или предложение.",
        parse_mode='HTML'
    )

@measure_response_time
async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет прав.")
        return
    if bot_stats is None:
        await update.message.reply_text("⚠️ Статистика временно недоступна.")
        return
    bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/stats')
    s = bot_stats.get_summary_stats()
    avg = s['avg_response_time']
    status, color = s['response_time_status'], s['response_time_color']
    text = (
        f"📊 <b>Статистика</b>\n"
        f"👥 Всего: {s['total_users']}, 24ч: {s['active_users_24h']}\n"
        f"📨 Сообщ: {s['total_messages']}, Команд: {s['total_commands']}\n"
        f"🔍 Поисков: {s['total_searches']}, Отзывов: {s['total_feedback']}\n"
        f"⚡ Время ответа: <b>{avg:.2f}с</b> ({status})\n"
        f"📦 Кэш: {s['cache_size']}\n"
        f"⏱ Uptime: {s['uptime']}\n"
    )
    base = f"http://localhost:{PORT}" if not RENDER else WEBHOOK_URL.replace('/webhook/', '/')
    keyboard = [
        [InlineKeyboardButton("📊 Веб-статистика", url=base)],
        [InlineKeyboardButton("📁 Excel", callback_data="export_excel")]
    ]
    await update.message.reply_text(text, parse_mode='HTML', reply_markup=InlineKeyboardMarkup(keyboard))

@measure_response_time
async def export_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if update.effective_user.id not in ADMIN_IDS:
        await update.message.reply_text("⛔ Нет прав.")
        return
    await export_to_excel(update, context)

async def export_to_excel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    if bot_stats is None:
        await update.message.reply_text("⚠️ Экспорт временно недоступен (статистика не инициализирована).")
        return
    bot_stats.log_message(user.id, user.username or "Unknown", 'command', '/export')
    try:
        output = await generate_excel_report()
        filename = f"mechel_bot_{datetime.now().strftime('%Y%m%d_%H%M%S')}.xlsx"
        await update.message.reply_document(
            document=output.getvalue(),
            filename=filename,
            caption=f"📊 Экспорт от {datetime.now().strftime('%d.%m.%Y %H:%M')}"
        )
        logger.info(f"✅ Экспорт выполнен пользователем {user.id}")
    except Exception as e:
        logger.error(f"❌ Ошибка экспорта: {e}", exc_info=True)
        await update.message.reply_text(f"❌ Ошибка: {str(e)}")

async def generate_excel_report() -> io.BytesIO:
    """Генерация Excel-отчёта (полностью защищена от None)."""
    output = io.BytesIO()
    wb = Workbook()
    stats = bot_stats.get_summary_stats() if bot_stats else {}

    # Лист 1: Общая статистика
    ws1 = wb.active
    ws1.title = "Общая статистика"
    ws1['A1'] = "Статистика HR-бота Мечел"
    ws1['A1'].font = Font(bold=True, size=14)
    ws1.merge_cells('A1:D1')
    ws1['A3'] = "Показатель"; ws1['B3'] = "Значение"
    for cell in ['A3','B3']: ws1[cell].font = Font(bold=True)
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
        ("Статус времени", stats.get('response_time_status', 'N/A')),
        ("Размер кэша", stats.get('cache_size', 0)),
        ("Количество ошибок", stats.get('error_count', 0))
    ]
    for i, (k, v) in enumerate(rows, 4):
        ws1[f'A{i}'] = k; ws1[f'B{i}'] = v

    # Лист 2: Время ответа
    ws2 = wb.create_sheet("Время ответа")
    ws2['A1'] = "История времени ответа"
    ws2['A1'].font = Font(bold=True, size=14)
    ws2.merge_cells('A1:C1')
    ws2['A3'] = "Время"; ws2['B3'] = "Ответ (сек)"; ws2['C3'] = "Статус"
    for c in ['A3','B3','C3']: ws2[c].font = Font(bold=True)
    if bot_stats:
        for i, rt in enumerate(bot_stats.response_times, 4):
            ws2[f'A{i}'] = rt['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
            ws2[f'B{i}'] = rt['response_time']
            t = rt['response_time']
            ws2[f'C{i}'] = "Хорошо" if t < 1 else "Нормально" if t < 3 else "Медленно"

    # Лист 3: FAQ База
    ws3 = wb.create_sheet("FAQ База")
    ws3['A1'] = "База знаний FAQ"
    ws3['A1'].font = Font(bold=True, size=14)
    ws3.merge_cells('A1:D1')
    headers = ["Категория", "Вопрос", "Ответ", "Ключевые слова"]
    for col, h in enumerate(headers, 1):
        cell = ws3.cell(row=3, column=col); cell.value = h; cell.font = Font(bold=True)
    if search_engine:
        for i, item in enumerate(search_engine.faq_data, 4):
            if isinstance(item, dict):
                cat = item.get('category', 'Без категории')
                q = item.get('question', '')
                a = item.get('answer', '')
                kw = ', '.join(item.get('keywords', []))
            else:
                cat = getattr(item, 'category', 'Без категории')
                q = getattr(item, 'question', '')
                a = getattr(item, 'answer', '')
                kw = ', '.join(getattr(item, 'keywords', []))
            ws3.cell(row=i, column=1, value=cat)
            ws3.cell(row=i, column=2, value=q)
            ws3.cell(row=i, column=3, value=a)
            ws3.cell(row=i, column=4, value=kw)
    else:
        ws3.cell(row=4, column=1, value="Поисковый движок недоступен")

    # Лист 4: Пользователи
    ws4 = wb.create_sheet("Пользователи")
    ws4['A1'] = "Статистика пользователей"
    ws4['A1'].font = Font(bold=True, size=14)
    ws4.merge_cells('A1:G1')
    headers2 = ["ID", "Имя", "Сообщ", "Команд", "Поиск", "Отзывы", "Посл. активность"]
    for col, h in enumerate(headers2, 1):
        cell = ws4.cell(row=3, column=col); cell.value = h; cell.font = Font(bold=True)
    if bot_stats:
        for i, (uid, udata) in enumerate(bot_stats.user_stats.items(), 4):
            ws4.cell(row=i, column=1, value=uid)
            ws4.cell(row=i, column=2, value=f"Пользователь {uid}")
            ws4.cell(row=i, column=3, value=udata['messages'])
            ws4.cell(row=i, column=4, value=udata['commands'])
            ws4.cell(row=i, column=5, value=udata['searches'])
            ws4.cell(row=i, column=6, value=udata['feedback_count'])
            last = udata['last_active']
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

    wb.save(output)
    output.seek(0)
    return output

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
        await update.message.reply_text("🙏 Спасибо за отзыв!")
        return

    if text.lower() in ['статистика', 'stats'] and user.id in ADMIN_IDS:
        await stats_command(update, context)
        return

    if bot_stats:
        bot_stats.log_message(user.id, user.username or "Unknown", 'search')

    if search_engine is None:
        await update.message.reply_text(
            "⚠️ Поиск временно недоступен. Попробуйте позже или используйте /feedback.",
            parse_mode='HTML'
        )
        return

    category = None
    if ':' in text:
        parts = text.split(':', 1)
        cat_candidate = parts[0].strip().lower()
        for item in search_engine.faq_data:
            if isinstance(item, dict):
                cat = item.get('category')
            else:
                cat = getattr(item, 'category', None)
            if cat and cat_candidate in cat.lower():
                category = cat
                text = parts[1].strip()
                break

    try:
        results = search_engine.search(text, category, top_k=3)
    except TypeError:
        # Внешний движок не поддерживает top_k
        logger.warning("⚠️ Внешний поисковый движок не поддерживает top_k, пробуем без параметра")
        try:
            results = search_engine.search(text, category)
            if isinstance(results, list):
                results = results[:3]
            else:
                results = []
        except Exception as e:
            logger.error(f"❌ Ошибка поиска: {e}", exc_info=True)
            results = []
    except Exception as e:
        logger.error(f"❌ Ошибка поиска: {e}", exc_info=True)
        results = []

    if not results:
        await update.message.reply_text(
            "😕 Не нашёл ответ. Попробуйте переформулировать или /feedback.",
            parse_mode='HTML'
        )
        return

    response = f"📌 <b>Результаты по запросу:</b>\n\n"
    for q, a, s in results[:3]:
        response += f"• <b>{q}</b>\n{a[:200]}...\n\n"
    response += "🔍 /categories — все темы"
    await update.message.reply_text(response, parse_mode='HTML')

@measure_response_time
async def handle_callback_query(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    data = query.data
    if data == 'export_excel':
        if update.effective_user.id in ADMIN_IDS:
            await export_to_excel(update, context)
        else:
            await query.answer("⛔ Нет прав", show_alert=True)

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
#  ВЕБ-ИНТЕРФЕЙС (Quart)
# ------------------------------------------------------------
app = Quart(__name__)

@app.before_serving
async def startup():
    logger.info("🔧 Запуск инициализации бота через before_serving...")
    success = await init_bot()
    if success:
        logger.info("✅ Бот успешно инициализирован через before_serving")
    else:
        logger.error("❌ Не удалось инициализировать бота")

@app.after_serving
async def shutdown():
    logger.info("🔴 Остановка бота...")
    if application:
        try:
            await application.stop()
            await application.shutdown()
            logger.info("✅ Бот остановлен")
        except Exception as e:
            logger.error(f"❌ Ошибка при остановке бота: {e}")

@app.route('/setwebhook')
async def set_webhook_manual():
    key = request.args.get('key')
    if key != WEBHOOK_SECRET:
        return jsonify({'error': 'Forbidden'}), 403
    if not application:
        return jsonify({'error': 'Bot not initialized'}), 503
    try:
        webhook_url = WEBHOOK_URL + WEBHOOK_PATH
        result = await application.bot.set_webhook(
            url=webhook_url,
            secret_token=WEBHOOK_SECRET,
            drop_pending_updates=True,
            max_connections=40
        )
        if result:
            info = await application.bot.get_webhook_info()
            return jsonify({
                'success': True,
                'message': 'Вебхук установлен',
                'url': info.url,
                'pending_update_count': info.pending_update_count
            })
        else:
            return jsonify({'success': False, 'message': 'Не удалось установить вебхук'}), 500
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/')
async def index():
    start_time = time.time()
    s = bot_stats.get_summary_stats() if bot_stats else {}
    avg = s.get('avg_response_time', 0)
    if avg < 1:
        perf_color = "metric-good"; perf_text = "Хорошо"
    elif avg < 3:
        perf_color = "metric-warning"; perf_text = "Нормально"
    else:
        perf_color = "metric-bad"; perf_text = "Медленно"

    bot_status = "🟢 Online" if application else "🔴 Offline"
    bot_status_class = "online" if application else "offline"

    total_users = s.get('total_users', 0)
    today_key = datetime.now().strftime('%Y-%m-%d')
    active_today = len(bot_stats.daily_stats.get(today_key, {}).get('users', [])) if bot_stats else 0
    total_searches = s.get('total_searches', 0)
    cache_size = len(search_engine.cache) if search_engine else 0
    admin_count = len(ADMIN_IDS)
    memory_usage = psutil.Process().memory_info().rss / 1024 / 1024
    start_time_str = bot_stats.start_time.strftime('%d.%m.%Y %H:%M') if bot_stats else 'N/A'

    html = f"""
    <!DOCTYPE html>
    <html lang="ru">
    <head>
        <meta charset="UTF-8">
        <meta name="viewport" content="width=device-width, initial-scale=1.0">
        <title>HR Бот Мечел — Метрики</title>
        <style>
            :root {{
                --bg-dark: #0B1C2F;
                --bg-card: #152A3A;
                --accent: #3E7B91;
                --good: #4CAF50;
                --warning: #FF9800;
                --bad: #F44336;
                --text-light: #E0E7F0;
            }}
            body {{
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
                background: var(--bg-dark);
                color: var(--text-light);
                margin: 0;
                padding: 2rem;
                line-height: 1.6;
            }}
            .container {{
                max-width: 1200px;
                margin: 0 auto;
            }}
            h1 {{
                font-weight: 600;
                font-size: 2.2rem;
                margin-bottom: 0.5rem;
                color: white;
            }}
            .subtitle {{
                color: #A0C0D0;
                margin-bottom: 2rem;
                font-size: 1.1rem;
            }}
            .grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 1.5rem;
                margin-bottom: 2rem;
            }}
            .card {{
                background: var(--bg-card);
                border-radius: 16px;
                padding: 1.5rem;
                box-shadow: 0 8px 24px rgba(0,0,0,0.3);
                border: 1px solid #2A4C5E;
            }}
            .stat-value {{
                font-size: 2.8rem;
                font-weight: 700;
                color: white;
                line-height: 1.2;
                margin-bottom: 0.5rem;
            }}
            .metric-badge {{
                display: inline-block;
                padding: 0.25rem 0.75rem;
                border-radius: 20px;
                font-size: 0.85rem;
                font-weight: 600;
                margin-left: 0.5rem;
            }}
            .metric-good {{ background: var(--good); color: white; }}
            .metric-warning {{ background: var(--warning); color: black; }}
            .metric-bad {{ background: var(--bad); color: white; }}
            .status-online {{ color: var(--good); font-weight: 600; }}
            .status-offline {{ color: var(--bad); font-weight: 600; }}
            .btn {{
                background: var(--accent);
                color: white;
                border: none;
                padding: 0.8rem 1.8rem;
                border-radius: 40px;
                font-size: 1rem;
                font-weight: 600;
                cursor: pointer;
                transition: 0.2s;
                text-decoration: none;
                display: inline-block;
                margin-top: 1rem;
            }}
            .btn:hover {{
                background: #4F9DB0;
                transform: translateY(-2px);
            }}
            table {{
                width: 100%;
                border-collapse: collapse;
                background: var(--bg-card);
                border-radius: 12px;
                overflow: hidden;
            }}
            th {{
                background: #1E3A47;
                padding: 0.75rem;
                text-align: left;
            }}
            td {{
                padding: 0.75rem;
                border-bottom: 1px solid #2A4C5E;
            }}
            .footer {{
                margin-top: 3rem;
                color: #809AA8;
                font-size: 0.9rem;
                text-align: center;
            }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 HR Бот «Мечел»</h1>
            <div class="subtitle">Версия 12.16 · Render-Ultimate (исправлен global)</div>

            <div class="grid">
                <div class="card">
                    <h3>⚙️ Производительность</h3>
                    <div class="stat-value">{avg:.2f}с</div>
                    <p>Ср. время ответа (100 запросов)
                        <span class="metric-badge {perf_color}">{perf_text}</span>
                    </p>
                    <p>Кэш поиска: {cache_size} записей</p>
                    <p>Запущен: {start_time_str}</p>
                </div>
                <div class="card">
                    <h3>📊 Аудитория</h3>
                    <div class="stat-value">{total_users}</div>
                    <p>Уникальных пользователей (всего)</p>
                    <p>Активных сегодня: {active_today}</p>
                    <p>Всего запросов: {total_searches}</p>
                </div>
                <div class="card">
                    <h3>🔌 Система</h3>
                    <div class="stat-value">
                        <span class="status-{bot_status_class}">{bot_status}</span>
                    </div>
                    <p>Webhook: {'✅ Активен' if WEBHOOK_URL else '⏹ Локальный'}</p>
                    <p>Администраторы: {admin_count}</p>
                    <p>Память: {memory_usage:.1f} МБ</p>
                </div>
            </div>

            <div style="display: flex; gap: 1rem; margin-bottom: 2rem;">
                <a href="/export/excel" class="btn">📥 Экспорт в Excel</a>
                <a href="/health" class="btn" style="background: #2E5C4E;">🩺 Health Check</a>
                <a href="/setwebhook?key={WEBHOOK_SECRET}" class="btn" style="background: #9C27B0;">🔧 Установить webhook</a>
            </div>

            <h2>📈 Статистика за последние 7 дней</h2>
            <table>
                <thead>
                    <tr>
                        <th>Дата</th>
                        <th>Пользователи</th>
                        <th>Сообщения</th>
                        <th>Команды</th>
                        <th>Поиски</th>
                        <th>Время ответа</th>
                    </tr>
                </thead>
                <tbody>
    """
    if bot_stats:
        for date, ds in sorted(bot_stats.daily_stats.items(), reverse=True)[:7]:
            avg_resp = sum(ds['response_times']) / len(ds['response_times']) if ds['response_times'] else 0
            html += f"""
                    <tr>
                        <td>{date}</td>
                        <td>{len(ds['users'])}</td>
                        <td>{ds['messages']}</td>
                        <td>{ds['commands']}</td>
                        <td>{ds['searches']}</td>
                        <td>{avg_resp:.2f}с</td>
                    </tr>
            """
    html += f"""
                </tbody>
            </table>
            <div class="footer">
                Время генерации: {(time.time() - start_time)*1000:.1f} мс · 
                {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
            </div>
        </div>
    </body>
    </html>
    """
    return html

@app.route('/health')
async def health_check():
    return jsonify({
        'status': 'ok',
        'bot': 'running' if application else 'stopped',
        'users': bot_stats.get_total_users() if bot_stats else 0,
        'uptime': str(datetime.now() - bot_stats.start_time) if bot_stats else 'N/A',
        'avg_response': bot_stats.get_avg_response_time() if bot_stats else 0,
        'cache_size': len(search_engine.cache) if search_engine else 0,
        'faq_count': len(search_engine.faq_data) if search_engine else 0
    })

@app.route('/export/excel')
async def export_excel_web():
    if bot_stats is None:
        return jsonify({'error': 'Статистика не инициализирована'}), 503
    try:
        excel_file = await generate_excel_report()
        return await send_file(
            excel_file,
            mimetype='application/vnd.openxmlformats-officedocument.spreadsheetml.sheet',
            download_name=f'mechel_bot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx',
            as_attachment=True
        )
    except Exception as e:
        logger.error(f"Ошибка веб-экспорта: {e}")
        return jsonify({'error': str(e)}), 500

@app.route(WEBHOOK_PATH, methods=['POST'])
async def webhook():
    if request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET:
        return 'Forbidden', 403
    if not application:
        logger.error("❌ Webhook: бот не инициализирован")
        return jsonify({'error': 'Bot not initialized'}), 503
    try:
        data = await request.get_json()
        if not data:
            logger.error("Получен пустой запрос вебхука")
            return 'Bad Request', 400
        update = Update.de_json(data, application.bot)
        await application.process_update(update)
        return 'OK', 200
    except Exception as e:
        logger.error(f"Webhook error: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

async def main():
    if not await init_bot():
        logger.critical("Не удалось инициализировать бота")
        sys.exit(1)

    if RENDER:
        logger.warning("⚠️ main() вызван на Render — используйте before_serving")
    else:
        logger.info("🔄 Запуск в режиме поллинга")
        polling_task = asyncio.create_task(application.start_polling(allowed_updates=Update.ALL_TYPES))
        config = Config()
        config.bind = [f"0.0.0.0:{PORT}"]
        await serve(app, config)
        await application.stop()
        polling_task.cancel()
        try:
            await polling_task
        except asyncio.CancelledError:
            pass

if __name__ == '__main__':
    asyncio.run(main())
