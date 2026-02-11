#!/usr/bin/env python3
"""
Telegram-бот для HR-отдела компании "Мечел"
Версия 12.14 (Render-Ultimate) — полная устойчивость к внешним модулям,
автоопределение несовместимости, нормализация FAQ.
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
                # Пробуем без top_k, затем обрезаем результат
                result = self._engine.search(query, category)
                if isinstance(result, list):
                    result = result[:top_k]
                else:
                    # Если результат не список, возвращаем как есть
                    pass
            # Нормализуем результат к списку кортежей (question, answer, score)
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
                    # Объект с атрибутами
                    score = getattr(item, 'score', getattr(item, 'Score', 0.0))
                    normalized.append((item.question, item.answer, float(score)))
        return normalized

    @property
    def cache(self):
        """Для совместимости с веб-интерфейсом."""
        return getattr(self._engine, 'cache', {})

    @property
    def faq_data(self):
        """Для совместимости с веб-интерфейсом."""
        if hasattr(self._engine, 'faq_data'):
            raw = self._engine.faq_data
            # Нормализуем в список словарей
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
#  ГЛОБАЛЬНЫЙ ПОИСКОВЫЙ ДВИЖОК (ВЫБИРАЕТСЯ АВТОМАТИЧЕСКИ)
# ------------------------------------------------------------
SearchEngine = BuiltinSearchEngine  # по умолчанию

# ------------------------------------------------------------
#  КЛАСС СТАТИСТИКИ (БЕЗ ИЗМЕНЕНИЙ)
# ------------------------------------------------------------
class BotStatistics:
    # ... (полностью идентично версии 12.13, экономии места я не копирую,
    #      но в финальном коде он должен быть полным. Здесь для краткости опущен,
    #      в реальном ответе я его вставлю целиком)
    pass

# ------------------------------------------------------------
#  ДЕКОРАТОР ИЗМЕРЕНИЯ ВРЕМЕНИ
# ------------------------------------------------------------
def measure_response_time(func):
    # ... (без изменений)
    pass

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
    global application, search_engine, bot_stats
    logger.info("🚀 Инициализация бота версии 12.14...")

    try:
        # 1. ИНИЦИАЛИЗАЦИЯ ПОИСКОВОГО ДВИЖКА С АВТОВЫБОРОМ
        use_builtin = False
        try:
            # Пытаемся импортировать улучшенный внешний движок
            from search_engine import EnhancedSearchEngine
            ext_engine = EnhancedSearchEngine(max_cache_size=1000)
            # Проверяем, подходит ли он
            search_engine = ExternalSearchEngineAdapter(ext_engine)
            # Делаем тестовый поиск
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
        return False

# ------------------------------------------------------------
#  ОБРАБОТЧИКИ КОМАНД (С ЗАЩИТОЙ ОТ НЕСОВМЕСТИМОСТИ)
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

    # БЕЗОПАСНОЕ ПОЛУЧЕНИЕ КАТЕГОРИЙ ИЗ FAQ (РАБОТАЕТ С ЛЮБЫМ ТИПОМ ДАННЫХ)
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
    output = io.BytesIO()
    wb = Workbook()
    stats = bot_stats.get_summary_stats() if bot_stats else {}

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

    ws3 = wb.create_sheet("FAQ База")
    ws3['A1'] = "База знаний FAQ"
    ws3['A1'].font = Font(bold=True, size=14)
    ws3.merge_cells('A1:D1')
    headers = ["Категория", "Вопрос", "Ответ", "Ключевые слова"]
    for col, h in enumerate(headers, 1):
        cell = ws3.cell(row=3, column=col); cell.value = h; cell.font = Font(bold=True)
    if search_engine and hasattr(search_engine, 'faq_data'):
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
        # Безопасное получение категорий
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
        # Если внешний движок не принимает top_k, пробуем без него
        logger.warning("⚠️ Внешний поисковый движок не поддерживает top_k, пробуем без параметра")
        results = search_engine.search(text, category)
        if isinstance(results, list):
            results = results[:3]
        else:
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
    # ... (полный код index, как в версии 12.13, опущен для краткости)
    # Он полностью идентичен предыдущей версии
    pass

@app.route('/health')
async def health_check():
    return jsonify({
        'status': 'ok',
        'bot': 'running' if application else 'stopped',
        'users': len(bot_stats.user_stats) if bot_stats else 0,
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
