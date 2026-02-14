#!/usr/bin/env python3
"""
Telegram-бот для HR-отдела компании "Мечел"
Версия 12.45 — архитектурная рефакторинг:
• Вынесены utils.py, web_panel.py, stats.py
• bot.py содержит только Telegram-логику и инициализацию
• Полная совместимость с search_engine.py v5.2 и meme_handler.py v9.2
• Оптимизация для Render Free
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
#  ИМПОРТЫ (без psutil и TelegramError — не используются)
# ------------------------------------------------------------
from quart import Quart, request, jsonify, make_response, render_template_string
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
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter
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
#  ФУНКЦИЯ ЛЕВЕНШТЕЙНА (ДЛЯ ВСТРОЕННОГО ДВИЖКА)
# ------------------------------------------------------------
def levenshtein_distance(s1: str, s2: str) -> int:
    """Вычисляет расстояние Левенштейна между двумя строками."""
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
    WEBHOOK_SECRET = 'mechel_hr_dev_' + hashlib.md5(BOT_TOKEN.encode()).hexdigest()[:16]
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
#  ВСТРОЕННЫЙ ПОИСКОВЫЙ ДВИЖОК (С ОПТИМИЗАЦИЕЙ И ПРЕДЛОЖЕНИЯМИ)
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
        logger.info(f"✅ BuiltinSearchEngine: загружено {len(self.faq_data)} вопросов (оптимизированный нечёткий поиск)")

    def _normalize_faq_item(self, item: Any) -> Dict[str, Any]:
        if isinstance(item, dict):
            return {
                'id': item.get('id', hash(item.get('question', '')) % 1000000),
                'question': item.get('question', ''),
                'answer': item.get('answer', ''),
                'category': item.get('category', 'Без категории'),
                'keywords': item.get('keywords', [])
            }
        return {
            'id': getattr(item, 'id', hash(getattr(item, 'question', '')) % 1000000),
            'question': getattr(item, 'question', ''),
            'answer': getattr(item, 'answer', ''),
            'category': getattr(item, 'category', 'Без категории'),
            'keywords': getattr(item, 'keywords', [])
        }

    def _load_faq_data(self) -> List[Dict[str, Any]]:
        data = []
        try:
            with open('faq.json', 'r', encoding='utf-8') as f:
                raw_data = json.load(f)
                for idx, item in enumerate(raw_data, start=1):
                    normalized = self._normalize_faq_item(item)
                    if not normalized.get('id'):
                        normalized['id'] = idx
                    data.append(normalized)
            logger.info(f"✅ Загружено {len(data)} вопросов из faq.json")
            return data
        except FileNotFoundError:
            logger.warning("⚠️ Файл faq.json не найден, используются резервные вопросы")
        except json.JSONDecodeError as e:
            logger.error(f"⚠️ Ошибка парсинга faq.json: {e}. Используются резервные вопросы")
        return self._get_backup_questions()

    def _get_backup_questions(self) -> List[Dict[str, Any]]:
        return [
            {
                "id": 1,
                "question": "Как получить справку о заработной плате?",
                "answer": "Справку можно получить в отделе кадров (каб. 205) или через корпоративный портал.",
                "category": "Документы",
                "keywords": ["справка", "зарплата", "заработная", "плата"]
            },
            {
                "id": 2,
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
            elif w.endswith('ять') and len(w) > 4: w = w[:-3]
            elif w.endswith('ить') and len(w) > 4: w = w[:-3]
            elif w.endswith('еть') and len(w) > 4: w = w[:-3]
            elif w.endswith('ый') or w.endswith('ий') or w.endswith('ой'): w = w[:-2]
            elif w.endswith('ая') or w.endswith('яя'): w = w[:-2]
            elif w.endswith('ое') or w.endswith('ее'): w = w[:-2]
            norm.append(w)
        return ' '.join(norm)

    def _quick_match(self, norm_query: str, item: Dict[str, Any]) -> bool:
        if not norm_query:
            return False
        q_words = set(norm_query.split())
        norm_question = self._normalize_query(item['question'])
        q_words_question = set(norm_question.split())
        if q_words.intersection(q_words_question):
            return True
        norm_keywords = ' '.join(item.get('keywords', [])).lower()
        if norm_keywords:
            q_words_keywords = set(norm_keywords.split())
            if q_words.intersection(q_words_keywords):
                return True
        return False

    def _calculate_full_score(self, norm_query: str, item: Dict[str, Any]) -> float:
        score = 0.0
        norm_question = self._normalize_query(item['question'])
        norm_answer = self._normalize_query(item['answer'])
        norm_keywords = ' '.join(item.get('keywords', [])).lower()
        q_words = set(norm_query.split())

        if norm_query == norm_question:
            return 100.0
        if norm_query in norm_question:
            score += 50.0
        if len(norm_query) >= 4 and norm_question:
            lev_dist = levenshtein_distance(norm_query, norm_question)
            if lev_dist == 0:
                return 100.0
            elif lev_dist <= 2:
                score += 40.0
            elif lev_dist <= 4:
                score += 20.0
        if norm_keywords:
            kw_lev = levenshtein_distance(norm_query, norm_keywords[:len(norm_query)+5])
            if kw_lev <= 2:
                score += 30.0

        q_words_question = set(norm_question.split())
        common_q = q_words.intersection(q_words_question)
        score += len(common_q) * 12.0

        if norm_keywords:
            kw_words = set(norm_keywords.split())
            common_kw = q_words.intersection(kw_words)
            score += len(common_kw) * 20.0

        for word in q_words:
            if len(word) > 3:
                if word in norm_question:
                    score += 3.0
                if norm_keywords and word in norm_keywords:
                    score += 5.0

        if norm_answer:
            a_score = self._calc_score_simple(norm_query, norm_answer) * 0.5
            score += a_score

        return score

    def _calc_score_simple(self, query: str, text: str) -> float:
        if not query or not text:
            return 0.0
        q_words = set(query.split())
        t_words = set(text.split())
        if not q_words:
            return 0.0
        common = q_words.intersection(t_words)
        return len(common) / len(q_words)

    def search(self, query: str, category: Optional[str] = None, top_k: int = 5) -> List[Tuple[str, str, float]]:
        cache_key = f"{query}_{category}_{top_k}"
        if cache_key in self.cache and datetime.now() < self.cache_ttl.get(cache_key, datetime.now()):
            return self.cache[cache_key]

        norm_q = self._normalize_query(query)
        if not norm_q:
            return []

        filtered = self.faq_data
        if category:
            filtered = [item for item in self.faq_data if item.get('category') == category]

        preliminary = []
        for item in filtered:
            if self._quick_match(norm_q, item):
                preliminary.append(item)
        if not preliminary:
            preliminary = filtered[:20]

        candidates = []
        for item in preliminary[:20]:
            q_words = set(norm_q.split())
            norm_question = self._normalize_query(item['question'])
            q_words_question = set(norm_question.split())
            common = q_words.intersection(q_words_question)
            base_score = len(common) * 12.0
            norm_keywords = ' '.join(item.get('keywords', [])).lower()
            if norm_keywords:
                kw_words = set(norm_keywords.split())
                common_kw = q_words.intersection(kw_words)
                base_score += len(common_kw) * 20.0
            candidates.append((item, base_score))

        candidates.sort(key=lambda x: x[1], reverse=True)
        top_candidates = [item for item, _ in candidates[:10]]

        results = []
        for item in top_candidates:
            score = self._calculate_full_score(norm_q, item)
            if score > 0:
                results.append((item['question'], item['answer'], min(score, 100.0)))

        results.sort(key=lambda x: x[2], reverse=True)
        top = results[:top_k]

        if len(self.cache) >= self.max_cache_size:
            oldest = next(iter(self.cache_ttl))
            del self.cache[oldest]
            del self.cache_ttl[oldest]

        self.cache[cache_key] = top
        self.cache_ttl[cache_key] = datetime.now() + timedelta(hours=1)
        return top

    def suggest_correction(self, query: str, top_k: int = 3) -> List[str]:
        if not query or not self.faq_data:
            return []
        norm_query = self._normalize_query(query)
        if not norm_query or len(norm_query) < 3:
            return []
        candidates = []
        for item in self.faq_data[:50]:
            norm_question = self._normalize_query(item['question'])
            if norm_question:
                dist = levenshtein_distance(norm_query, norm_question)
                if dist <= 5:
                    candidates.append((item['question'], dist))
        candidates.sort(key=lambda x: x[1])
        return [q for q, _ in candidates[:top_k]]

    def refresh_data(self):
        self.faq_data = self._load_faq_data()
        self.cache.clear()
        self.cache_ttl.clear()
        logger.info("🔄 BuiltinSearchEngine: данные обновлены")

# ------------------------------------------------------------
#  АДАПТЕР ДЛЯ ВНЕШНЕГО SEARCH ENGINE (С АНАЛИЗОМ СИГНАТУРЫ!)
# ------------------------------------------------------------
class ExternalSearchEngineAdapter:
    def __init__(self, external_engine):
        self._engine = external_engine
        self._search_method = getattr(external_engine, 'search', None)
        if not self._search_method:
            raise AttributeError("Внешний движок не имеет метода search")

        # 🔥 АНАЛИЗ СИГНАТУРЫ — ОСТАВЛЕН ДЛЯ СОВМЕСТИМОСТИ
        sig = inspect.signature(self._search_method)
        self._has_category = 'category' in sig.parameters
        self._supports_top_k = 'top_k' in sig.parameters
        logger.info(f"🔧 Внешний движок: поддержка category={self._has_category}, top_k={self._supports_top_k}")

    def search(self, query: str, category: Optional[str] = None, top_k: int = 5) -> List[Tuple[str, str, float]]:
        try:
            kwargs = {'query': query}
            if self._supports_top_k:
                kwargs['top_k'] = top_k
            if self._has_category and category is not None:
                kwargs['category'] = category

            result = self._search_method(**kwargs)

            if isinstance(result, list):
                result = result[:top_k]
            return self._normalize_result(result)
        except Exception as e:
            logger.error(f"❌ Ошибка во внешнем поисковом движке: {e}")
            return []

    def _normalize_result(self, result: Any) -> List[Tuple[str, str, float]]:
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
                    norm_item = {
                        'id': item.get('id', hash(item.get('question', '')) % 1000000),
                        'question': item.get('question', ''),
                        'answer': item.get('answer', ''),
                        'category': item.get('category', 'Без категории'),
                        'keywords': item.get('keywords', []) if isinstance(item.get('keywords'), list) else str(item.get('keywords', '')).split(', ')
                    }
                else:
                    norm_item = {
                        'id': getattr(item, 'id', hash(getattr(item, 'question', '')) % 1000000),
                        'question': getattr(item, 'question', ''),
                        'answer': getattr(item, 'answer', ''),
                        'category': getattr(item, 'category', 'Без категории'),
                        'keywords': getattr(item, 'keywords', []) if isinstance(getattr(item, 'keywords', []), list) else str(getattr(item, 'keywords', '')).split(', ')
                    }
                normalized.append(norm_item)
            return normalized
        return []

    def suggest_correction(self, query: str, top_k: int = 3) -> List[str]:
        if hasattr(self._engine, 'suggest_correction'):
            return self._engine.suggest_correction(query, top_k)
        return []

    def refresh_data(self):
        if hasattr(self._engine, 'refresh_data'):
            self._engine.refresh_data()
            logger.info("🔄 ExternalSearchEngineAdapter: данные обновлены во внешнем движке")

# ------------------------------------------------------------
#  СИСТЕМА ПОДПИСОК (с кэшированием)
# ------------------------------------------------------------
SUBSCRIBERS_FILE = 'subscribers.json'
subscribers_lock = asyncio.Lock()
_subscribers_cache = None
_subscribers_cache_loaded = False

async def load_subscribers():
    global _subscribers_cache, _subscribers_cache_loaded
    if _subscribers_cache_loaded:
        return _subscribers_cache
    try:
        async with subscribers_lock:
            with open(SUBSCRIBERS_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                _subscribers_cache = data.get('subscribers', [])
    except FileNotFoundError:
        _subscribers_cache = []
    except Exception as e:
        logger.error(f"Ошибка загрузки подписчиков: {e}")
        _subscribers_cache = []
    _subscribers_cache_loaded = True
    return _subscribers_cache

async def save_subscribers(subscribers: List[int]):
    global _subscribers_cache
    async with subscribers_lock:
        with open(SUBSCRIBERS_FILE, 'w', encoding='utf-8') as f:
            json.dump({'subscribers': subscribers, 'updated': datetime.now().isoformat()}, f, ensure_ascii=False, indent=2)
        _subscribers_cache = subscribers

async def add_subscriber(user_id: int):
    subs = await load_subscribers()
    if user_id not in subs:
        subs.append(user_id)
        await save_subscribers(subs)
        return True
    return False

async def remove_subscriber(user_id: int):
    subs = await load_subscribers()
    if user_id in subs:
        subs.remove(user_id)
        await save_subscribers(subs)
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
            subs = await load_subscribers()
            await save_subscribers(subs)
            logger.info(f"✅ Периодическое сохранение подписчиков: {len(subs)} записей")
        except Exception as e:
            logger.error(f"❌ Ошибка периодического сохранения подписчиков: {e}")

# ------------------------------------------------------------
#  СИСТЕМНЫЕ СООБЩЕНИЯ (EDITABLE)
# ------------------------------------------------------------
MESSAGES_FILE = 'messages.json'
messages_lock = asyncio.Lock()
DEFAULT_MESSAGES = {
    "welcome": {
        "title": "Приветствие",
        "text": "👋 Привет, {first_name}!\n"
                "Я HR-бот компании <b>Мечел</b>. Помогу с кадровыми вопросами.\n"
                "📌 Просто напишите вопрос — я поищу в базе знаний.\n"
                "/help — подсказки\n"
                "/categories — категории вопросов\n"
                "/feedback — отзыв / предложения\n"
                "💬 Можно также использовать русские команды:\n"
                "/старт, /помощь, /категории, /предложения"
    },
    "help": {
        "title": "Помощь",
        "text": "❓ <b>Как пользоваться:</b>\n"
                "1. Задайте вопрос своими словами.\n"
                "2. Можно указать категорию через двоеточие, например:\n"
                "<i>отпуск: как перенести?</i>\n"
                "3. Используйте /categories для выбора темы.\n"
                "📞 HR: +7 (3519) 25-60-00, hr@mechel.ru"
    },
    "no_results": {
        "title": "Ничего не найдено",
        "text": "😕 Не нашёл ответ. Попробуйте переформулировать, использовать /categories для выбора категории или /feedback /предложения."
    },
    "suggestions": {
        "title": "Предложения по исправлению",
        "text": "😕 Не нашёл точного совпадения для «{query}».\n"
                "Возможно, вы имели в виду:\n"
                "{suggestions}\n"
                "Попробуйте переформулировать или /feedback /предложения."
    },
    "feedback_ack": {
        "title": "Спасибо за отзыв / предложение",
        "text": "🙏 Спасибо за ваше предложение! Мы обязательно его рассмотрим."
    },
    "greeting_response": {
        "title": "Ответ на приветствие",
        "text": "👋 Привет! Я HR-бот Мечел. Чем могу помочь?"
    },
    "subscribe_success": {
        "title": "Подписка оформлена",
        "text": "✅ Вы подписались на рассылку новостей! Теперь вы будете получать уведомления от администрации."
    },
    "unsubscribe_success": {
        "title": "Подписка отменена",
        "text": "✅ Вы отписались от рассылки. Если передумаете, всегда можете подписаться снова командой /subscribe."
    },
    "already_subscribed": {
        "title": "Уже подписаны",
        "text": "ℹ️ Вы уже подписаны на рассылку."
    },
    "not_subscribed": {
        "title": "Не подписаны",
        "text": "ℹ️ Вы не подписаны на рассылку. Чтобы подписаться, используйте /subscribe."
    }
}

async def load_messages():
    try:
        async with messages_lock:
            with open(MESSAGES_FILE, 'r', encoding='utf-8') as f:
                data = json.load(f)
                for key, default in DEFAULT_MESSAGES.items():
                    if key not in data:
                        data[key] = default
                    else:
                        if 'text' not in data[key]:
                            data[key]['text'] = default.get('text', '')
                        if 'title' not in data[key]:
                            data[key]['title'] = default.get('title', key)
                return data
    except FileNotFoundError:
        async with messages_lock:
            with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
                json.dump(DEFAULT_MESSAGES, f, ensure_ascii=False, indent=2)
            return DEFAULT_MESSAGES.copy()
    except Exception as e:
        logger.error(f"Ошибка загрузки сообщений: {e}")
        return DEFAULT_MESSAGES.copy()

async def save_messages(messages: Dict):
    async with messages_lock:
        with open(MESSAGES_FILE, 'w', encoding='utf-8') as f:
            json.dump(messages, f, ensure_ascii=False, indent=2)

async def get_message(key: str, **kwargs) -> str:
    msgs = await load_messages()
    entry = msgs.get(key, DEFAULT_MESSAGES.get(key, {}))
    template = entry.get('text', '')
    if kwargs and template:
        try:
            return template.format(**kwargs)
        except KeyError:
            return template
    return template

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
#  КОМАНДЫ /ЧТО_МОГУ И /АДМИН
# ------------------------------------------------------------
async def what_can_i_do(update: Update, context: ContextTypes.DEFAULT_TYPE):
    user = update.effective_user
    text = (
        "📋 <b>Что я умею:</b>\n"
        "• Отвечать на HR-вопросы (просто напишите)\n"
        "• Показывать категории: /categories\n"
        "• Принимать предложения: /feedback\n"
        "• Присылать мемы: /мем\n"
        "• Подписаться на рассылку: /subscribe\n\n"
        "💡 Совет: можно писать «отпуск: как перенести?» — я найду точнее!"
    )
    await update.message.reply_text(text, parse_mode='HTML')

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
        "• Веб-интерфейс: " + BASE_URL
    )
    await update.message.reply_text(text, parse_mode='HTML')

# ------------------------------------------------------------
#  ИНИЦИАЛИЗАЦИЯ БОТА
# ------------------------------------------------------------
async def init_bot():
    global application, search_engine, bot_stats
    logger.info("🚀 Инициализация бота версии 12.45...")
    try:
        use_builtin = False
        try:
            from search_engine import EnhancedSearchEngine
            ext_engine = EnhancedSearchEngine(max_cache_size=1000)
            search_engine = ExternalSearchEngineAdapter(ext_engine)
            test_result = search_engine.search("тест", top_k=1)
            if test_result is not None:
                logger.info("✅ Загружен EnhancedSearchEngine из search_engine.py (оптимизированный нечёткий поиск)")
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
                    logger.info("✅ Загружен SearchEngine из search_engine.py (оптимизированный нечёткий поиск)")
                else:
                    raise ImportError("Тест не пройден")
            except (ImportError, Exception) as e2:
                logger.debug(f"Внешний SearchEngine не подходит: {e2}")
                use_builtin = True

        if use_builtin:
            search_engine = BuiltinSearchEngine()
            logger.info("✅ Используется встроенный BuiltinSearchEngine (оптимизированный нечёткий поиск)")

        bot_stats = BotStatistics()
        logger.info("✅ Инициализирован модуль статистики")

        builder = ApplicationBuilder().token(BOT_TOKEN).post_init(post_init)
        application = builder.build()

        # --- Инициализация модуля мемов ---
        if MEME_MODULE_AVAILABLE:
            await init_meme_handler(application.job_queue, admin_ids=ADMIN_IDS)
            logger.info("✅ Модуль мемов инициализирован")
        else:
            logger.warning("⚠️ Модуль мемов не загружен, команды /мем, /мемподписка, /мемотписка недоступны")

        # --- АНГЛИЙСКИЕ КОМАНДЫ (включая мемы) ---
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
        application.add_handler(CommandHandler("что_могу", what_can_i_do))
        application.add_handler(CommandHandler("админ", admin_panel))

        # Английские команды для мемов
        if MEME_MODULE_AVAILABLE:
            application.add_handler(CommandHandler("mem", meme_command))
            application.add_handler(CommandHandler("memsub", meme_subscribe_command))
            application.add_handler(CommandHandler("memunsub", meme_unsubscribe_command))

        # --- РУССКИЕ КОМАНДЫ ЧЕРЕЗ MessageHandler ---
        async def russian_command_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
            text = update.message.text.lower()
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
            elif text.startswith('/мем') and MEME_MODULE_AVAILABLE:
                await meme_command(update, context)
            elif text.startswith('/мемподписка') and MEME_MODULE_AVAILABLE:
                await meme_subscribe_command(update, context)
            elif text.startswith('/мемотписка') and MEME_MODULE_AVAILABLE:
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

        await application.initialize()

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
                    return False
            else:
                logger.error("❌ Не удалось установить вебхук")
                return False
        else:
            await application.bot.delete_webhook(drop_pending_updates=True)
            logger.info("✅ Режим поллинга (локальная разработка)")

        asyncio.create_task(periodic_subscriber_save())
        logger.info("✅ Запущена задача периодического сохранения подписчиков")

        # --- РЕГИСТРАЦИЯ ВЕБ-МАРШРУТОВ ---
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

        logger.info("✅ Бот полностью инициализирован и готов к работе")
        return True

    except Exception as e:
        logger.critical(f"❌ Ошибка инициализации бота: {e}", exc_info=True)
        return False

# ... (остальные обработчики команд, сообщений, веб-эндпоинты — без изменений, как в предыдущем полном коде) ...

# ------------------------------------------------------------
#  MAIN
# ------------------------------------------------------------
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

def shutdown_signal(sig):
    logger.info(f"Получен сигнал {sig}, инициируем завершение...")
    loop = asyncio.get_event_loop()
    loop.create_task(shutdown())

if __name__ == '__main__':
    loop = asyncio.get_event_loop()
    for sig in (signal.SIGTERM, signal.SIGINT):
        loop.add_signal_handler(sig, lambda s=sig: shutdown_signal(s))
    asyncio.run(main())
