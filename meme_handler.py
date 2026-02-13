"""
Модуль обработки мемов для бота
Работает на бесплатном тарифе Render
Версия 9.2 — исправлены все синтаксические ошибки, добавлен tzinfo для МСК,
мониторинг источников, батчинг рассылки, улучшенный фильтр мата.
Полная совместимость с bot.py версии 12.40
"""
import asyncio
import aiohttp
import json
import os
import random
import re
from datetime import datetime, timedelta, time
from typing import Optional, Dict, List, Tuple
from telegram import Update
from telegram.ext import ContextTypes, JobQueue
import logging

# Импорт для работы с часовым поясом
try:
    from zoneinfo import ZoneInfo  # Python 3.9+
except ImportError:
    try:
        import pytz  # Альтернатива для более старых версий
        ZoneInfo = pytz.timezone
    except ImportError:
        ZoneInfo = None

logger = logging.getLogger(__name__)

# ============================================================
#  ФИЛЬТР МАТА - УЛУЧШЕННЫЙ СПИСОК С ТОЧНОЙ ПРОВЕРКОЙ ГРАНИЦ СЛОВ
# ============================================================
RUSSIAN_BAD_WORDS = {
    'хуй', 'хуи', 'хуя', 'хуе', 'хуё', 'хуёв', 'хуев', 'хую',
    'пизда', 'пиздец', 'пизд', 'пиздюк', 'пиздюки', 'пиздюли',
    'ебать', 'ебу', 'ебет', 'ебёт', 'ебем', 'ебём', 'ебете', 'ебёте', 'ебут',
    'ебли', 'ебля', 'ебаный', 'ебанный', 'ебанная', 'ебанное', 'ебанные', 'ебаная', 'ебано', 'ебаное', 'ебаные',
    'блядь', 'бляди', 'блядина', 'блядки', 'блядство',
    'сука', 'сучка', 'сучонок', 'сучара', 'суки',
    'нахуй', 'нахуя', 'нахуе', 'нахуё', 'нахуев', 'нахуёв',
    'похуй', 'похуя', 'похуе', 'похуё', 'похуев', 'похуёв',
    'охуел', 'охуела', 'охуело', 'охуели', 'охуевший', 'охуевшая',
    'заебал', 'заебала', 'заебали', 'заебись', 'заебистый', 'заебистая',
    'наебал', 'наебала', 'наебали', 'наебать',
    'выебал', 'выебала', 'выебали', 'выебать',
    'доебал', 'доебала', 'доебали', 'доебаться',
    'ебал', 'ебала', 'ебали', 'ебальник', 'ебальщица',
    'ебануться', 'ебанулся', 'ебанулась', 'ебанулись',
    'ебло', 'ебливый', 'ебливая', 'ебливое', 'ебливые',
    'ебнуть', 'ебни', 'ебануть', 'ебану', 'ебанем', 'ебанём',
    'ебашить', 'ебашу', 'ебашит', 'ебашат',
    'уебал', 'уебала', 'уебали', 'уебан', 'уебанка', 'уебище',
    'ебучий', 'ебучая', 'ебучее', 'ебучие',
    'залупа', 'залупой', 'залупу', 'залупы',
    'мудак', 'мудаки', 'мудило', 'мудила',
    'гандон', 'гандоны', 'гандоном', 'гандону',
    'пидор', 'пидорас', 'пидорасы', 'пидорасом', 'пидорасу',
    'педик', 'педики', 'педрила', 'педрилы',
    'шлюха', 'шлюхи', 'шлюхой', 'шлюху',
    'блядун', 'блядунья',
    'сучий', 'сучья', 'сучье', 'сучьи',
    'хуесос', 'хуесосы', 'хуесосом', 'хуесосу',
    'хуёвый', 'хуевый', 'хуёвая', 'хуевая', 'хуёвое', 'хуевое', 'хуёвые', 'хуевые',
    'хуярить', 'хуярю', 'хуярит', 'хуярят',
    'хуячить', 'хуячу', 'хуячит', 'хуячат',
    'ебало', 'ебалом', 'ебалу',
    'пиздобол', 'пиздоболы', 'пиздоболом', 'пиздоболу',
    'пиздатый', 'пиздатая', 'пиздатое', 'пиздатые',
    'похуист', 'похуисты', 'похуистом', 'похуисту',
    'ахуел', 'ахуела', 'ахуело', 'ахуели',
    'ахуеть', 'ахуевший', 'ахуевшая',
    'охуеть', 'охуевший', 'охуевшая',
    'нахуячить', 'нахуячу', 'нахуячит', 'нахуячат',
    'уебать', 'уебу', 'уебет', 'уебёт', 'уебем', 'уебём', 'уебете', 'уебёте', 'уебут',
    'выебать', 'выебу', 'выебет', 'выебёт', 'выебем', 'выебём', 'выебете', 'выебёте', 'выебут',
    'доебать', 'доебу', 'доебет', 'доебёт', 'доебем', 'доебём', 'доебете', 'доебёте', 'доебут',
    'наебать', 'наебу', 'наебет', 'наебёт', 'наебем', 'наебём', 'наебете', 'наебёте', 'наебут',
    'заебать', 'заебу', 'заебет', 'заебёт', 'заебем', 'заебём', 'заебете', 'заебёте', 'заебут',
    'ебануть', 'ебану', 'ебанет', 'ебанёт', 'ебанем', 'ебанём', 'ебанете', 'ебанёте', 'ебанут',
    'ебнуть', 'ебну', 'ебнет', 'ебнёт', 'ебнем', 'ебнём', 'ебнете', 'ебнёте', 'ебнут',
    'ебашить', 'ебашу', 'ебашит', 'ебашат',
    'ебло', 'еблом', 'еблу',
    'ебальник', 'ебальщица',
    'ебаный', 'ебанный', 'ебаная', 'ебаное', 'ебаные',
    'ебучий', 'ебучая', 'ебучее', 'ебучие',
}

# ============================================================
#  ИСТОЧНИКИ МЕМОВ (БЕЗ ПРОБЕЛОВ В КОНЦЕ — КРИТИЧЕСКИ ВАЖНО!)
# ============================================================
MEME_SOURCES = [
    {
        'name': 'Reddit r/PrequelMemes',
        'url': 'https://meme-api.com/gimme/PrequelMemes',
        'timeout': 5,
        'retries': 2
    },
    {
        'name': 'Reddit r/wholesomememes',
        'url': 'https://meme-api.com/gimme/wholesomememes',
        'timeout': 5,
        'retries': 2
    },
    {
        'name': 'Reddit r/memes',
        'url': 'https://meme-api.com/gimme/memes',
        'timeout': 5,
        'retries': 2
    },
    {
        'name': 'Reddit r/dankmemes',
        'url': 'https://meme-api.com/gimme/dankmemes',
        'timeout': 5,
        'retries': 2
    }
]

# Резервные каналы с русскими мемами (для ссылок в fallback)
FALLBACK_RUSSIAN_CHANNELS = [
    "@pikabumemes",
    "@russianmemes",
    "@memes_ru"
]


class MemeStorage:
    """Хранилище для данных о мемах (работает в памяти + файл)"""

    def __init__(self, file_path: str = 'meme_data.json'):
        self.file_path = file_path
        self.last_meme_time: Dict[int, datetime] = {}  # user_id -> время последнего мема
        self.last_request_time: Dict[int, datetime] = {}  # user_id -> время последнего запроса (защита от спама)
        self.subscribers: set = set()  # user_id подписчиков
        self._lock = asyncio.Lock()
        self._load_data()

    def _load_data(self):
        """Загрузка данных из файла"""
        try:
            if os.path.exists(self.file_path):
                with open(self.file_path, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Загружаем время последнего мема
                    self.last_meme_time = {
                        int(user_id): datetime.fromisoformat(timestamp)
                        for user_id, timestamp in data.get('last_meme_time', {}).items()
                    }
                    # Загружаем подписчиков
                    self.subscribers = set(data.get('subscribers', []))
                logger.info(f"✅ Загружено {len(self.subscribers)} подписчиков из {self.file_path}")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки данных мемов: {e}")

    async def _save_data(self):
        """Асинхронное сохранение данных в файл"""
        async with self._lock:
            try:
                data = {
                    'last_meme_time': {
                        str(user_id): dt.isoformat()
                        for user_id, dt in self.last_meme_time.items()
                    },
                    'subscribers': list(self.subscribers)
                }
                with open(self.file_path, 'w', encoding='utf-8') as f:
                    json.dump(data, f, ensure_ascii=False, indent=2)
                logger.debug(f"💾 Сохранено {len(self.subscribers)} подписчиков")
            except Exception as e:
                logger.error(f"❌ Ошибка сохранения данных мемов: {e}")

    async def can_get_meme(self, user_id: int) -> Tuple[bool, Optional[str]]:
        """Проверяет, может ли пользователь получить мем сейчас (1 раз в 24 часа)"""
        now = datetime.now()
        last_time = self.last_meme_time.get(user_id)

        if last_time is None:
            return True, None

        if (now - last_time).total_seconds() >= 86400:  # 24 часа
            return True, None

        remaining = 86400 - (now - last_time).total_seconds()
        hours = int(remaining // 3600)
        minutes = int((remaining % 3600) // 60)

        return False, (
            f"😅 Вы уже получали мем сегодня!\n"
            f"Следующий мем будет доступен через {hours}ч {minutes}мин"
        )

    async def is_spamming(self, user_id: int) -> bool:
        """Проверяет, не спамит ли пользователь (защита от флуда)"""
        now = datetime.now()
        last_request = self.last_request_time.get(user_id)
        
        if last_request is None:
            self.last_request_time[user_id] = now
            return False
        
        # Минимум 3 секунды между запросами
        if (now - last_request).total_seconds() < 3:
            return True
        
        self.last_request_time[user_id] = now
        return False

    async def record_meme_usage(self, user_id: int):
        """Записывает время получения мема пользователем"""
        self.last_meme_time[user_id] = datetime.now()
        await self._save_data()

    async def subscribe(self, user_id: int) -> bool:
        """Подписывает пользователя на рассылку мемов"""
        if user_id in self.subscribers:
            return False
        self.subscribers.add(user_id)
        await self._save_data()
        return True

    async def unsubscribe(self, user_id: int) -> bool:
        """Отписывает пользователя от рассылки мемов"""
        if user_id not in self.subscribers:
            return False
        self.subscribers.remove(user_id)
        await self._save_data()
        return True

    def is_subscribed(self, user_id: int) -> bool:
        """Проверяет, подписан ли пользователь"""
        return user_id in self.subscribers

    def get_subscribers_count(self) -> int:
        """Возвращает количество подписчиков"""
        return len(self.subscribers)


class ContentFilter:
    """Улучшенный фильтр контента для мемов"""

    @staticmethod
    def normalize_text(text: str) -> str:
        """Нормализует текст: замена латиницы на кириллицу и удаление спецсимволов"""
        # Замена латиницы на кириллицу (защита от обхода мата)
        replacements = {
            'a': 'а', 'e': 'е', 'o': 'о', 'p': 'р', 'c': 'с', 'x': 'х',
            'y': 'у', 'k': 'к', 'm': 'м', 't': 'т', 'b': 'в', 'n': 'п',
            '3': 'з', '0': 'о', '@': 'а', '$': 'с', '*': ''
        }
        text = text.lower()
        for lat, cyr in replacements.items():
            text = text.replace(lat, cyr)
        # Удаление всех не-буквенных символов, кроме пробелов
        text = re.sub(r'[^а-яё\s]', '', text)
        return text

    @staticmethod
    def has_bad_words(text: str) -> bool:
        """Проверяет наличие мата в тексте с точной проверкой границ слов"""
        if not text:
            return False

        # Нормализуем текст
        text_clean = ContentFilter.normalize_text(text)
        words = text_clean.split()

        # Точная проверка по словам (не подстрокам)
        for word in words:
            if word in RUSSIAN_BAD_WORDS:
                return True
        return False

    @staticmethod
    def is_safe_meme(meme_data: dict) -> bool:
        """Проверяет, безопасен ли мем для показа"""
        # Проверяем заголовок и описание
        for field in ['name', 'title', 'description']:
            text = meme_data.get(field, '')
            if ContentFilter.has_bad_words(text):
                logger.warning(f"🚫 Мем отфильтрован из-за мата в {field}: {text[:50]}")
                return False

        # Проверяем категорию
        category = meme_data.get('category', '').lower()
        unsafe_categories = ['nsfw', 'porn', 'sex', 'adult', 'xxx']
        if any(unsafe_cat in category for unsafe_cat in unsafe_categories):
            logger.warning(f"🚫 Мем отфильтрован из-за небезопасной категории: {category}")
            return False

        return True


class MemeFetcher:
    """Загрузчик мемов из различных источников с кэшированием"""

    def __init__(self, session: Optional[aiohttp.ClientSession] = None):
        self.session = session
        self.content_filter = ContentFilter()
        self._cache = {}  # Кэш для избежания повторных запросов
        self._cache_ttl = {}  # Время жизни кэша

    async def fetch_meme(self) -> Optional[dict]:
        """Получает случайный мем из доступных источников с кэшированием"""
        # Проверяем кэш (5 минут)
        now = datetime.now()
        if 'cached_meme' in self._cache and now < self._cache_ttl.get('cached_meme', now):
            logger.info("📦 Используем кэшированный мем")
            return self._cache['cached_meme']

        # Перемешиваем источники для случайности
        sources = MEME_SOURCES.copy()
        random.shuffle(sources)
        failed_sources = []

        for source in sources:
            try:
                meme = await self._fetch_from_source(source)
                if meme and self.content_filter.is_safe_meme(meme):
                    logger.info(f"✅ Получен мем из {source['name']}: {meme.get('title', 'Без названия')[:50]}")
                    # Сохраняем в кэш на 5 минут
                    self._cache['cached_meme'] = meme
                    self._cache_ttl['cached_meme'] = now + timedelta(minutes=5)
                    return meme
            except Exception as e:
                failed_sources.append(source['name'])
                logger.warning(f"⚠️ Ошибка получения мема из {source['name']}: {e}")
                continue

        logger.error(f"❌ Все источники не сработали: {', '.join(failed_sources)}")
        return None

    async def _fetch_from_source(self, source: dict) -> Optional[dict]:
        """Получает мем из конкретного источника"""
        url = source['url']
        timeout = aiohttp.ClientTimeout(total=source.get('timeout', 5))

        for attempt in range(source.get('retries', 2) + 1):
            try:
                async with self.session.get(url, timeout=timeout) as response:
                    if response.status == 200:
                        data = await response.json()

                        # Обработка стандартного формата
                        meme_url = data.get('url') or data.get('image_url')
                        meme_title = data.get('title') or data.get('name') or 'Мем дня'

                        if meme_url:
                            return {
                                'url': meme_url,
                                'title': meme_title,
                                'source': source['name'],
                                'description': data.get('description', ''),
                                'category': data.get('category', '')
                            }
                    else:
                        logger.warning(f"⚠️ {source['name']} вернул статус {response.status}")
            except asyncio.TimeoutError:
                logger.warning(f"⚠️ Таймаут при запросе к {source['name']} (попытка {attempt + 1})")
                continue
            except Exception as e:
                logger.warning(f"⚠️ Ошибка при запросе к {source['name']}: {e}")
                continue

        return None

    async def check_source_availability(self, source: dict) -> Tuple[str, bool]:
        """Проверяет доступность одного источника (быстрый HEAD-запрос или малый GET)"""
        try:
            timeout = aiohttp.ClientTimeout(total=3)
            async with self.session.get(source['url'], timeout=timeout) as response:
                if response.status == 200:
                    return source['name'], True
                else:
                    return source['name'], False
        except Exception:
            return source['name'], False

    async def check_all_sources(self) -> Dict[str, bool]:
        """Проверяет доступность всех источников параллельно"""
        tasks = [self.check_source_availability(src) for src in MEME_SOURCES]
        results = await asyncio.gather(*tasks, return_exceptions=True)
        status = {}
        for i, res in enumerate(results):
            if isinstance(res, tuple):
                name, ok = res
                status[name] = ok
            else:
                status[MEME_SOURCES[i]['name']] = False
        return status


class MemeHandler:
    """Основной класс для обработки команд мемов"""

    def __init__(self):
        self.storage = MemeStorage()
        self.session: Optional[aiohttp.ClientSession] = None
        self.job_queue: Optional[JobQueue] = None
        self._daily_job = None
        self._sources_job = None  # Задача периодической проверки источников
        self._sources_status = {
            'last_check': None,
            'available': False,
            'details': {}
        }
        # Настройка часового пояса для МСК
        try:
            self.moscow_tz = ZoneInfo("Europe/Moscow") if ZoneInfo else pytz.timezone("Europe/Moscow")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось установить часовой пояс: {e}. Используется системное время.")
            self.moscow_tz = None

    def set_job_queue(self, job_queue: JobQueue):
        """Устанавливает очередь задач для планирования рассылки"""
        self.job_queue = job_queue
        logger.info("✅ JobQueue установлен для рассылки мемов")

    def get_fetcher(self) -> MemeFetcher:
        """Возвращает загрузчик мемов с общей сессией (без утечки ресурсов)"""
        if self.session is None or self.session.closed:
            self.session = aiohttp.ClientSession()
        return MemeFetcher(self.session)

    async def close_session(self):
        """Закрывает сессию при остановке бота"""
        if self.session and not self.session.closed:
            await self.session.close()
            logger.info("✅ Сессия aiohttp закрыта")

    async def _get_meme_from_fallback(self, context: ContextTypes.DEFAULT_TYPE, chat_id: int) -> bool:
        """Отправляет ссылку на резервный канал с русскими мемами (когда API недоступно)"""
        for channel in FALLBACK_RUSSIAN_CHANNELS:
            try:
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=f"😅 Основные источники мемов временно недоступны.\n"
                         f"Свежие русские мемы можно посмотреть здесь: {channel}",
                    parse_mode='HTML'
                )
                logger.info(f"🔄 Отправлена ссылка на резервный канал {channel}")
                return True
            except Exception as e:
                logger.warning(f"⚠️ Не удалось отправить ссылку на {channel}: {e}")
                continue
        return False

    async def handle_meme_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /мем с защитой от спама и fallback"""
        user = update.effective_user
        user_id = user.id

        try:
            # Защита от спама (минимум 3 сек между запросами)
            if await self.storage.is_spamming(user_id):
                await update.message.reply_text(
                    "⏳ Подождите немного перед следующим запросом мема!",
                    parse_mode='HTML'
                )
                return

            # Проверка лимита 1 мем/сутки
            can_get, message = await self.storage.can_get_meme(user_id)
            if not can_get:
                await update.message.reply_text(message, parse_mode='HTML')
                return

            # Получаем мем из API
            fetcher = self.get_fetcher()
            meme = await fetcher.fetch_meme()

            if meme and meme.get('url'):
                await update.message.reply_photo(
                    photo=meme['url'],
                    caption=f"😄 {meme.get('title', 'Мем дня')}\n"
                            f"Источник: {meme.get('source', 'Неизвестно')}",
                    parse_mode='HTML'
                )
                await self.storage.record_meme_usage(user_id)
                logger.info(f"📨 Мем отправлен пользователю {user_id} (@{user.username})")
                return

            # Fallback: отправляем ссылку на канал с русскими мемами
            if not await self._get_meme_from_fallback(context, user_id):
                await update.message.reply_text(
                    "😅 К сожалению, не удалось найти мем прямо сейчас.\n"
                    "Попробуйте позже!",
                    parse_mode='HTML'
                )

        except Exception as e:
            logger.error(f"❌ Ошибка в /мем: {e}", exc_info=True)
            await update.message.reply_text(
                "😅 Упс! Что-то пошло не так. Мы уже работаем над этим!",
                parse_mode='HTML'
            )

    async def handle_subscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /мемподписка"""
        user = update.effective_user
        user_id = user.id

        try:
            if await self.storage.subscribe(user_id):
                await update.message.reply_text(
                    "✅ Отлично! Вы подписались на ежедневную рассылку мемов!\n"
                    "📅 Каждый день в 09:30 по МСК вы будете получать свежий мем.\n"
                    "Чтобы отписаться, используйте команду /мемотписка",
                    parse_mode='HTML'
                )
                logger.info(f"🔔 Пользователь {user_id} (@{user.username}) подписался на рассылку")
            else:
                await update.message.reply_text(
                    "ℹ️ Вы уже подписаны на рассылку мемов!",
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"❌ Ошибка в /мемподписка: {e}", exc_info=True)
            await update.message.reply_text(
                "😅 Упс! Что-то пошло не так. Мы уже работаем над этим!",
                parse_mode='HTML'
            )

    async def handle_unsubscribe_command(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработчик команды /мемотписка"""
        user = update.effective_user
        user_id = user.id

        try:
            if await self.storage.unsubscribe(user_id):
                await update.message.reply_text(
                    "✅ Вы отписались от рассылки мемов.\n"
                    "Мемы больше не будут приходить вам.",
                    parse_mode='HTML'
                )
                logger.info(f"🔕 Пользователь {user_id} (@{user.username}) отписался от рассылки")
            else:
                await update.message.reply_text(
                    "ℹ️ Вы не подписаны на рассылку мемов.",
                    parse_mode='HTML'
                )
        except Exception as e:
            logger.error(f"❌ Ошибка в /мемотписка: {e}", exc_info=True)
            await update.message.reply_text(
                "😅 Упс! Что-то пошло не так. Мы уже работаем над этим!",
                parse_mode='HTML'
            )

    async def send_daily_meme(self, context: ContextTypes.DEFAULT_TYPE):
        """Отправляет ежедневный мем всем подписчикам с батчингом"""
        try:
            subscribers = list(self.storage.subscribers)
            if not subscribers:
                logger.info("📭 Нет подписчиков для ежедневной рассылки")
                return

            logger.info(f"📬 Начинаю рассылку мемов {len(subscribers)} подписчикам")

            # Получаем мем
            fetcher = self.get_fetcher()
            meme = await fetcher.fetch_meme()

            if not meme or not meme.get('url'):
                logger.error("❌ Не удалось получить мем для рассылки")
                return

            sent_count = 0
            failed_count = 0

            # Батчинг по 25 сообщений для избежания перегрузки
            batch_size = 25
            for i in range(0, len(subscribers), batch_size):
                batch = subscribers[i:i + batch_size]
                
                for user_id in batch:
                    try:
                        await context.bot.send_photo(
                            chat_id=user_id,
                            photo=meme['url'],
                            caption=f"🌅 Доброе утро! Вот ваш мем на сегодня:\n"
                                    f"😄 {meme.get('title', 'Мем дня')}\n"
                                    f"Источник: {meme.get('source', 'Неизвестно')}",
                            parse_mode='HTML'
                        )
                        sent_count += 1
                        await self.storage.record_meme_usage(user_id)
                        await asyncio.sleep(0.3)  # Небольшая задержка между отправками
                    except Exception as e:
                        logger.error(f"❌ Ошибка отправки мема пользователю {user_id}: {e}")
                        failed_count += 1
                        await asyncio.sleep(0.5)  # Длинная пауза при ошибках
                
                # Пауза между батчами для избежания лимитов
                if i + batch_size < len(subscribers):
                    await asyncio.sleep(1.0)

            logger.info(f"✅ Рассылка завершена: отправлено {sent_count}, ошибок {failed_count}")

        except Exception as e:
            logger.error(f"❌ Критическая ошибка в ежедневной рассылке: {e}", exc_info=True)

    def schedule_daily_meme(self):
        """Настраивает ежедневную рассылку мемов в 09:30 МСК + пробуждение бота"""
        if not self.job_queue:
            logger.error("❌ JobQueue не установлен!")
            return

        # Удаляем старую задачу, если она есть
        if self._daily_job:
            self._daily_job.schedule_removal()

        # Настройка времени 09:30 МСК с учётом часового пояса
        if self.moscow_tz:
            target_time = time(hour=9, minute=30, tzinfo=self.moscow_tz)
            wake_up_time = time(hour=9, minute=25, tzinfo=self.moscow_tz)
            logger.info("⏰ Время рассылки задано в часовом поясе Москвы")
        else:
            target_time = time(hour=9, minute=30)
            wake_up_time = time(hour=9, minute=25)
            logger.warning("⚠️ Часовой пояс не определён, используется локальное время сервера")
        
        # Основная рассылка в 09:30 МСК
        self._daily_job = self.job_queue.run_daily(
            callback=self.send_daily_meme,
            time=target_time,
            days=(0, 1, 2, 3, 4, 5, 6),
            name='daily_meme_broadcast'
        )
        logger.info("⏰ Ежедневная рассылка мемов настроена на 09:30 МСК")

        # Задача пробуждения бота за 5 минут до рассылки (для бесплатного тарифа Render)
        self.job_queue.run_daily(
            callback=lambda ctx: None,  # Пустая задача для пробуждения процесса
            time=wake_up_time,
            name='wake_up_before_meme'
        )
        logger.info("⏰ Задача пробуждения бота настроена на 09:25 МСК (для бесплатного тарифа)")

    async def update_sources_status(self):
        """Обновляет статус доступности источников мемов (вызывается по расписанию)"""
        fetcher = self.get_fetcher()
        try:
            details = await fetcher.check_all_sources()
            available = any(details.values())
            self._sources_status = {
                'last_check': datetime.now(),
                'available': available,
                'details': details
            }
            logger.info(f"📊 Статус источников мемов обновлён: доступно {sum(details.values())}/{len(details)}")
        except Exception as e:
            logger.error(f"❌ Ошибка при проверке источников мемов: {e}")
            self._sources_status = {
                'last_check': datetime.now(),
                'available': False,
                'details': {src['name']: False for src in MEME_SOURCES}
            }

    async def periodic_sources_check(self, context: ContextTypes.DEFAULT_TYPE):
        """Периодическая задача для проверки источников (вызывается по расписанию)"""
        await self.update_sources_status()

    def schedule_sources_check(self, interval_hours: int = 1):
        """Запускает периодическую проверку источников (раз в час)"""
        if not self.job_queue:
            logger.error("❌ JobQueue не установлен!")
            return
        # Удаляем старую задачу, если она есть
        if self._sources_job:
            self._sources_job.schedule_removal()
        self._sources_job = self.job_queue.run_repeating(
            callback=self.periodic_sources_check,
            interval=interval_hours * 3600,
            first=10,  # первый запуск через 10 секунд
            name='sources_status_check'
        )
        logger.info(f"⏰ Периодическая проверка источников мемов запущена (интервал {interval_hours} ч)")

    def get_sources_status(self) -> dict:
        """Возвращает статус источников мемов для веб-панели"""
        return self._sources_status

    def get_stats(self) -> dict:
        """Возвращает статистику по мемам"""
        return {
            'subscribers_count': self.storage.get_subscribers_count(),
            'last_meme_usage': len(self.storage.last_meme_time),
            'total_requests_today': len([dt for dt in self.storage.last_meme_time.values() 
                                        if (datetime.now() - dt).days == 0])
        }


# ============================================================
#  ГЛОБАЛЬНЫЕ ФУНКЦИИ ДЛЯ ИНТЕГРАЦИИ С БОТОМ
# ============================================================
_meme_handler: Optional[MemeHandler] = None


def get_meme_handler() -> MemeHandler:
    """Возвращает глобальный экземпляр обработчика мемов"""
    global _meme_handler
    if _meme_handler is None:
        _meme_handler = MemeHandler()
    return _meme_handler


async def init_meme_handler(job_queue: JobQueue):
    """
    Инициализирует обработчик мемов при запуске бота
    """
    handler = get_meme_handler()
    handler.set_job_queue(job_queue)
    handler.schedule_daily_meme()
    handler.schedule_sources_check(interval_hours=1)  # проверка каждый час
    # Первоначальная проверка
    await handler.update_sources_status()
    logger.info("✅ Модуль мемов инициализирован")


async def close_meme_handler():
    """Закрывает обработчик мемов при остановке бота (очищает ресурсы)"""
    handler = get_meme_handler()
    await handler.close_session()
    logger.info("✅ Модуль мемов закрыт")


# ============================================================
#  ЭКСПОРТИРУЕМЫЕ ОБРАБОТЧИКИ ДЛЯ bot.py
# ============================================================
async def meme_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /мем — получение одного мема в сутки"""
    handler = get_meme_handler()
    await handler.handle_meme_command(update, context)


async def meme_subscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /мемподписка — подписка на ежедневную рассылку"""
    handler = get_meme_handler()
    await handler.handle_subscribe_command(update, context)


async def meme_unsubscribe_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
    """Команда /мемотписка — отписка от ежедневной рассылки"""
    handler = get_meme_handler()
    await handler.handle_unsubscribe_command(update, context)


# ============================================================
#  ТЕСТЫ (опционально)
# ============================================================
async def test_meme_fetcher():
    """Тестирование загрузчика мемов"""
    handler = get_meme_handler()
    fetcher = handler.get_fetcher()
    meme = await fetcher.fetch_meme()
    if meme:
        print(f"✅ Получен мем: {meme.get('title')}")
        print(f"   URL: {meme.get('url')}")
        print(f"   Источник: {meme.get('source')}")
    else:
        print("❌ Не удалось получить мем")


if __name__ == "__main__":
    asyncio.run(test_meme_fetcher())
