"""
Модуль для работы с базой данных Supabase (PostgreSQL)
Версия 2.12 – добавлена поддержка fallback-режима и обработка недоступности БД
"""
import os
import asyncio
import asyncpg
import logging
from datetime import datetime, timedelta, date
from typing import List, Dict, Optional, Any, Tuple, Set

logger = logging.getLogger(__name__)

# Строка подключения к базе данных
DATABASE_URL = os.getenv('DATABASE_URL')
if not DATABASE_URL:
    raise RuntimeError("❌ DATABASE_URL не установлен! Проверьте переменные окружения на Render.")

# Пул соединений (глобальный)
_pool: Optional[asyncpg.Pool] = None
_pool_lock: Optional[asyncio.Lock] = None
POOL_MIN_SIZE = 2
POOL_MAX_SIZE = 10
POOL_TIMEOUT = 5.0

# Флаг доступности БД (устанавливается из bot.py)
_db_available = True

VALID_DAILY_FIELDS = {
    'messages', 'commands', 'searches', 'users_count',
    'feedback_count', 'ratings_helpful', 'ratings_unhelpful',
    'avg_response_time', 'total_response_time', 'response_count'
}

# ------------------------------------------------------------
#  УПРАВЛЕНИЕ ПУЛОМ
# ------------------------------------------------------------
async def get_pool() -> asyncpg.Pool:
    """Возвращает глобальный пул соединений (создаёт при первом вызове)."""
    global _pool, _pool_lock
    if _pool_lock is None:
        _pool_lock = asyncio.Lock()
    if _pool is None:
        async with _pool_lock:
            if _pool is None:
                logger.info("🔄 Ожидание полной инициализации сети Render (3 сек)...")
                await asyncio.sleep(3.0)

                max_retries = 12
                for attempt in range(max_retries):
                    try:
                        logger.info(f"🔄 Попытка {attempt+1}/{max_retries} создания пула соединений...")
                        _pool = await asyncpg.create_pool(
                            DATABASE_URL,
                            min_size=POOL_MIN_SIZE,
                            max_size=POOL_MAX_SIZE,
                            command_timeout=POOL_TIMEOUT,
                            max_queries=50000,
                            max_inactive_connection_lifetime=300,
                            statement_cache_size=0
                        )
                        logger.info(f"✅ Пул соединений создан (min={POOL_MIN_SIZE}, max={POOL_MAX_SIZE})")
                        break
                    except (OSError, asyncpg.exceptions.PostgresError, asyncio.TimeoutError) as e:
                        error_msg = str(e)
                        if "Network is unreachable" in error_msg or "Temporary failure in name resolution" in error_msg:
                            logger.warning(f"⚠️ Сеть ещё не готова: {error_msg}")
                        else:
                            logger.warning(f"⚠️ Ошибка создания пула: {error_msg}")

                        if attempt == max_retries - 1:
                            logger.critical(f"❌ Не удалось создать пул после {max_retries} попыток.")
                            raise

                        wait = min(15.0, 0.5 * (2 ** attempt))
                        logger.warning(f"⏳ Повтор через {wait:.1f}с...")
                        await asyncio.sleep(wait)
    return _pool

async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("✅ Пул соединений закрыт")

def set_db_available(available: bool):
    """Устанавливает флаг доступности БД (вызывается из bot.py)."""
    global _db_available
    _db_available = available
    logger.info(f"🔄 Статус БД изменён: {'доступна' if available else 'недоступна (fallback)'}")

def is_db_available() -> bool:
    """Проверяет доступность БД."""
    return _db_available

# ------------------------------------------------------------
#  УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ДЛЯ ВЫПОЛНЕНИЯ ЗАПРОСОВ С ПОВТОРАМИ
# ------------------------------------------------------------
async def _execute_with_retry(coro, max_retries=3, timeout=5.0):
    if not _db_available:
        raise ConnectionError("❌ БД недоступна (fallback-режим)")
    
    for attempt in range(max_retries):
        try:
            return await asyncio.wait_for(coro, timeout=timeout)
        except asyncpg.exceptions.TooManyConnectionsError:
            logger.error("❌ Превышен лимит соединений к БД (TooManyConnectionsError)")
            raise
        except (asyncio.TimeoutError,
                asyncpg.exceptions.ConnectionDoesNotExistError,
                asyncpg.exceptions.ConnectionFailureError,
                asyncpg.exceptions.InterfaceError) as e:
            if attempt == max_retries - 1:
                logger.error(f"❌ Запрос не выполнен после {max_retries} попыток: {e}")
                raise
            wait = 0.5 * (attempt + 1)
            logger.warning(f"⚠️ Ошибка БД, повтор через {wait:.1f}с (попытка {attempt+2}/{max_retries})")
            await asyncio.sleep(wait)
        except Exception as e:
            logger.error(f"❌ Ошибка выполнения запроса: {e}")
            raise

# ------------------------------------------------------------
#  ИНИЦИАЛИЗАЦИЯ ТАБЛИЦ
# ------------------------------------------------------------
async def init_db():
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS subscribers (
                    user_id BIGINT PRIMARY KEY,
                    subscribed_at TIMESTAMPTZ DEFAULT NOW()
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS messages (
                    key TEXT PRIMARY KEY,
                    text TEXT NOT NULL,
                    title TEXT
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS faq (
                    id SERIAL PRIMARY KEY,
                    priority INTEGER DEFAULT 0,
                    question TEXT NOT NULL,
                    answer TEXT NOT NULL,
                    keywords TEXT,
                    norm_keywords TEXT,
                    norm_question TEXT,
                    category TEXT NOT NULL,
                    usage_count INTEGER DEFAULT 0
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS meme_history (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    meme_path TEXT,
                    sent_at TIMESTAMPTZ DEFAULT NOW()
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS meme_subscribers (
                    user_id BIGINT PRIMARY KEY,
                    subscribed_at TIMESTAMPTZ DEFAULT NOW()
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS feedback (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT NOT NULL,
                    username TEXT,
                    text TEXT NOT NULL,
                    created_at TIMESTAMPTZ DEFAULT NOW()
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS faq_ratings (
                    id SERIAL PRIMARY KEY,
                    faq_id INTEGER NOT NULL,
                    user_id BIGINT,
                    is_helpful BOOLEAN NOT NULL,
                    rated_at TIMESTAMPTZ DEFAULT NOW()
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS daily_stats (
                    date DATE PRIMARY KEY,
                    messages INTEGER DEFAULT 0,
                    commands INTEGER DEFAULT 0,
                    searches INTEGER DEFAULT 0,
                    users_count INTEGER DEFAULT 0,
                    feedback_count INTEGER DEFAULT 0,
                    ratings_helpful INTEGER DEFAULT 0,
                    ratings_unhelpful INTEGER DEFAULT 0,
                    avg_response_time FLOAT DEFAULT 0,
                    total_response_time FLOAT DEFAULT 0,
                    response_count INTEGER DEFAULT 0
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS response_times (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    response_time FLOAT NOT NULL
                )
            '''))
            await _execute_with_retry(conn.execute('''
                CREATE TABLE IF NOT EXISTS error_log (
                    id SERIAL PRIMARY KEY,
                    timestamp TIMESTAMPTZ DEFAULT NOW(),
                    error_type TEXT,
                    error_message TEXT,
                    user_id BIGINT
                )
            '''))
            logger.info("✅ Таблицы в Supabase созданы или уже существуют.")
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации БД: {e}")
        raise

# ------------------------------------------------------------
#  ПОДПИСЧИКИ НА РАССЫЛКУ
# ------------------------------------------------------------
async def get_subscribers() -> List[int]:
    if not _db_available:
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(conn.fetch('SELECT user_id FROM subscribers'))
            return [r['user_id'] for r in rows]
    except Exception as e:
        logger.error(f"❌ Ошибка получения подписчиков: {e}")
        return []

async def get_subscribers_batch(offset: int = 0, limit: int = 1000) -> List[int]:
    if not _db_available:
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(
                conn.fetch('SELECT user_id FROM subscribers ORDER BY user_id OFFSET $1 LIMIT $2', offset, limit)
            )
            return [r['user_id'] for r in rows]
    except Exception as e:
        logger.error(f"❌ Ошибка получения подписчиков (batch): {e}")
        return []

async def count_subscribers() -> int:
    if not _db_available:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await _execute_with_retry(conn.fetchval('SELECT COUNT(*) FROM subscribers'))
    except Exception as e:
        logger.error(f"❌ Ошибка подсчёта подписчиков: {e}")
        return 0

async def add_subscriber(user_id: int):
    if not _db_available:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('''
                INSERT INTO subscribers (user_id) VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id))
    except Exception as e:
        logger.error(f"❌ Ошибка добавления подписчика: {e}")

async def remove_subscriber(user_id: int):
    if not _db_available:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('DELETE FROM subscribers WHERE user_id = $1', user_id))
    except Exception as e:
        logger.error(f"❌ Ошибка удаления подписчика: {e}")

async def ensure_subscribed(user_id: int):
    await add_subscriber(user_id)

# ------------------------------------------------------------
#  ПОДПИСЧИКИ НА МЕМЫ
# ------------------------------------------------------------
async def get_all_meme_subscribers() -> List[int]:
    if not _db_available:
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(conn.fetch('SELECT user_id FROM meme_subscribers'))
            return [r['user_id'] for r in rows]
    except Exception as e:
        logger.error(f"❌ Ошибка получения подписчиков на мемы: {e}")
        return []

async def get_meme_subscribers_batch(offset: int = 0, limit: int = 500) -> List[int]:
    if not _db_available:
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(
                conn.fetch('SELECT user_id FROM meme_subscribers ORDER BY user_id OFFSET $1 LIMIT $2', offset, limit)
            )
            return [r['user_id'] for r in rows]
    except Exception as e:
        logger.error(f"❌ Ошибка получения подписчиков на мемы (batch): {e}")
        return []

async def count_meme_subscribers() -> int:
    if not _db_available:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await _execute_with_retry(conn.fetchval('SELECT COUNT(*) FROM meme_subscribers'))
    except Exception as e:
        logger.error(f"❌ Ошибка подсчёта подписчиков на мемы: {e}")
        return 0

async def add_meme_subscriber(user_id: int) -> bool:
    if not _db_available:
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('''
                INSERT INTO meme_subscribers (user_id) VALUES ($1)
                ON CONFLICT (user_id) DO NOTHING
            ''', user_id))
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка добавления подписчика на мемы: {e}")
        return False

async def remove_meme_subscriber(user_id: int) -> bool:
    if not _db_available:
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('DELETE FROM meme_subscribers WHERE user_id = $1', user_id))
            return True
    except Exception as e:
        logger.error(f"❌ Ошибка удаления подписчика на мемы: {e}")
        return False

async def is_meme_subscribed(user_id: int) -> bool:
    if not _db_available:
        return False
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await _execute_with_retry(conn.fetchrow('SELECT 1 FROM meme_subscribers WHERE user_id = $1', user_id))
            return row is not None
    except Exception as e:
        logger.error(f"❌ Ошибка проверки подписки на мемы: {e}")
        return False

# ------------------------------------------------------------
#  СИСТЕМНЫЕ СООБЩЕНИЯ
# ------------------------------------------------------------
DEFAULT_MESSAGES = {
    "welcome": (
        "🦸‍♂️ <b>Привет, {first_name}!</b>\n\n"
        "Я — официальный HR-помощник корпоративного офиса ПАО «Мечел».\n\n"
        "🤖 <b>Что я умею:</b>\n\n"
        "📌 <b>1. Отвечать на HR-вопросы</b>\n"
        "   Отпуска, зарплата, ДМС, документы, больничные\n"
        "   <i>Пример: «Как оформить отпуск?» или «Когда выплата зарплаты?»</i>\n\n"
        "📂 <b>2. Категории вопросов</b>\n"
        "   Быстрый поиск по темам: /categories\n\n"
        "😄 <b>3. Мемы для настроения</b>\n"
        "   /mem — получить случайный мем\n"
        "   /memsub — подписаться на ежедневную рассылку мемов\n"
        "   /memunsub — отписаться от рассылки мемов\n\n"
        "💬 <b>4. Обратная связь</b>\n"
        "   /feedback — оставить предложение по улучшению бота\n\n"
        "📋 <b>Другие команды:</b>\n"
        "/help — подробная справка\n"
        "/whatcanido — все возможности бота\n"
        "/subscribe — подписаться на HR-рассылку\n"
        "/unsubscribe — отписаться от HR-рассылки\n\n"
        "👇 <b>Нажмите кнопку ниже, чтобы начать!</b>"
    ),
    "welcome_admin": (
        "🦸‍♂️ <b>Привет, {first_name}!</b>\n\n"
        "Я — официальный HR-помощник корпоративного офиса ПАО «Мечел».\n\n"
        "🤖 <b>Что я умею:</b>\n\n"
        "📌 <b>1. Отвечать на HR-вопросы</b>\n"
        "   Отпуска, зарплата, ДМС, документы, больничные\n"
        "   <i>Пример: «Как оформить отпуск?» или «Когда выплата зарплаты?»</i>\n\n"
        "📂 <b>2. Категории вопросов</b>\n"
        "   Быстрый поиск по темам: /categories\n\n"
        "😄 <b>3. Мемы для настроения</b>\n"
        "   /mem — получить случайный мем\n"
        "   /memsub — подписаться на ежедневную рассылку мемов\n"
        "   /memunsub — отписаться от рассылки мемов\n\n"
        "💬 <b>4. Обратная связь</b>\n"
        "   /feedback — оставить предложение по улучшению бота\n\n"
        "📋 <b>Команды для всех:</b>\n"
        "/help — подробная справка\n"
        "/whatcanido — все возможности бота\n"
        "/subscribe — подписаться на HR-рассылку\n"
        "/unsubscribe — отписаться от HR-рассылки\n\n"
        "👑 <b>Админ-команды:</b>\n"
        "/stats [период] — статистика использования (day/week/month)\n"
        "/export — выгрузка статистики в Excel\n"
        "/feedbacks — выгрузка всех отзывов в Excel\n"
        "/broadcast — рассылка сообщения подписчикам\n"
        "/save — принудительное сохранение данных\n"
        "/status — состояние системы и лимиты БД\n"
        "/cleanup — очистка старых данных\n"
        "/admin — админ-панель\n\n"
        "🌐 <b>Веб-интерфейс:</b> {base_url}\n\n"
        "👇 <b>Нажмите кнопку ниже, чтобы начать!</b>"
    ),
    "main_menu": (
        "📋 <b>Главное меню</b>\n\n"
        "Выберите категорию или задайте вопрос текстом.\n\n"
        "<i>Пример: «Как оформить отпуск?» или «Справка 2-НДФЛ»</i>"
    ),
    "help": (
        "📚 <b>Доступные команды:</b>\n\n"
        "/start — начать работу с ботом\n"
        "/help — показать эту справку\n"
        "/categories — показать категории вопросов\n"
        "/feedback — оставить отзыв или предложение\n"
        "/subscribe — подписаться на рассылку\n"
        "/unsubscribe — отписаться от рассылки\n"
        "/whatcanido — что я умею\n"
        "/mem — получить мем\n"
        "/memsub — подписка на мемы\n"
        "/memunsub — отписка от мемов"
    ),
    "greeting_response": "👋 Здравствуйте! Чем могу помочь?",
    "subscribe_success": "✅ Вы успешно подписались на рассылку!",
    "already_subscribed": "ℹ️ Вы уже подписаны на рассылку.",
    "unsubscribe_success": "✅ Вы успешно отписались от рассылки.",
    "not_subscribed": "ℹ️ Вы не подписаны на рассылку.",
    "feedback_ack": "✅ Спасибо за ваш отзыв! Мы обязательно учтём ваши предложения.",
    "suggestions": "🤔 Возможно, вы имели в виду:\n\n{suggestions}\n\nПопробуйте уточнить ваш запрос.",
    "no_results": "😔 К сожалению, я не нашёл ответ на ваш вопрос. Попробуйте переформулировать или напишите /feedback с вашим предложением добавить этот вопрос в базу знаний."
}

async def get_message(key: str, **kwargs) -> str:
    """Получает сообщение из БД или возвращает значение по умолчанию."""
    # В fallback-режиме возвращаем DEFAULT_MESSAGES напрямую
    if not _db_available:
        text = DEFAULT_MESSAGES.get(key, f'⚠️ Сообщение "{key}" не найдено')
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await _execute_with_retry(conn.fetchrow('SELECT text FROM messages WHERE key = $1', key))
            if row:
                text = row['text']
            else:
                text = DEFAULT_MESSAGES.get(key, f'⚠️ Сообщение "{key}" не найдено')
            try:
                return text.format(**kwargs)
            except KeyError:
                return text
    except Exception as e:
        logger.error(f"❌ Ошибка получения сообщения {key}: {e}")
        text = DEFAULT_MESSAGES.get(key, f'⚠️ Сообщение "{key}" не найдено')
        try:
            return text.format(**kwargs)
        except KeyError:
            return text

async def save_message(key: str, text: str, title: str = ''):
    if not _db_available:
        logger.warning(f"⚠️ Не удалось сохранить сообщение {key} (БД недоступна)")
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('''
                INSERT INTO messages (key, text, title) VALUES ($1, $2, $3)
                ON CONFLICT (key) DO UPDATE SET text = $2, title = $3
            ''', key, text, title))
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения сообщения {key}: {e}")

async def load_all_messages() -> Dict:
    if not _db_available:
        return {k: {'text': v, 'title': ''} for k, v in DEFAULT_MESSAGES.items()}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(conn.fetch('SELECT key, text, title FROM messages'))
            return {r['key']: {'text': r['text'], 'title': r['title']} for r in rows}
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки сообщений: {e}")
        return {k: {'text': v, 'title': ''} for k, v in DEFAULT_MESSAGES.items()}

# ------------------------------------------------------------
#  РАБОТА С FAQ
# ------------------------------------------------------------
async def load_all_faq() -> List[Dict]:
    if not _db_available:
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(conn.fetch('''
                SELECT id, priority, question, answer, keywords, category
                FROM faq ORDER BY id
            '''))
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"❌ Ошибка загрузки FAQ: {e}")
        return []

async def get_faq_by_id(faq_id: int) -> Optional[Dict]:
    if not _db_available:
        return None
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            row = await _execute_with_retry(conn.fetchrow('''
                SELECT id, priority, question, answer, keywords, category
                FROM faq WHERE id = $1
            ''', faq_id))
            return dict(row) if row else None
    except Exception as e:
        logger.error(f"❌ Ошибка получения FAQ по ID: {e}")
        return None

async def add_faq(question: str, answer: str, category: str, keywords: str = '', priority: int = 0) -> int:
    if not _db_available:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            norm_question = ' '.join(question.lower().split())
            norm_keywords = ' '.join(keywords.lower().split()) if keywords else ''
            new_id = await _execute_with_retry(conn.fetchval('''
                INSERT INTO faq (priority, question, answer, keywords, norm_keywords, norm_question, category)
                VALUES ($1, $2, $3, $4, $5, $6, $7)
                RETURNING id
            ''', priority, question, answer, keywords, norm_keywords, norm_question, category))
            return new_id
    except Exception as e:
        logger.error(f"❌ Ошибка добавления FAQ: {e}")
        return 0

async def update_faq(faq_id: int, question: str, answer: str, category: str, keywords: str = '', priority: int = 0):
    if not _db_available:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            norm_question = ' '.join(question.lower().split())
            norm_keywords = ' '.join(keywords.lower().split()) if keywords else ''
            await _execute_with_retry(conn.execute('''
                UPDATE faq SET
                    priority = $1,
                    question = $2,
                    answer = $3,
                    keywords = $4,
                    norm_keywords = $5,
                    norm_question = $6,
                    category = $7
                WHERE id = $8
            ''', priority, question, answer, keywords, norm_keywords, norm_question, category, faq_id))
    except Exception as e:
        logger.error(f"❌ Ошибка обновления FAQ: {e}")

async def delete_faq(faq_id: int):
    if not _db_available:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('DELETE FROM faq WHERE id = $1', faq_id))
    except Exception as e:
        logger.error(f"❌ Ошибка удаления FAQ: {e}")

# ------------------------------------------------------------
#  ИСТОРИЯ МЕМОВ
# ------------------------------------------------------------
async def add_meme_history(user_id: int, meme_path: str = None):
    if not _db_available:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('''
                INSERT INTO meme_history (user_id, meme_path) VALUES ($1, $2)
            ''', user_id, meme_path))
    except Exception as e:
        logger.error(f"❌ Ошибка добавления истории мемов: {e}")

async def get_meme_count_last_24h(user_id: int) -> int:
    if not _db_available:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            return await _execute_with_retry(conn.fetchval('''
                SELECT COUNT(*) FROM meme_history
                WHERE user_id = $1 AND sent_at > NOW() - INTERVAL '24 hours'
            ''', user_id))
    except Exception as e:
        logger.error(f"❌ Ошибка подсчёта мемов: {e}")
        return 0

# ------------------------------------------------------------
#  ОТЗЫВЫ
# ------------------------------------------------------------
async def save_feedback(user_id: int, username: str, text: str):
    if not _db_available:
        logger.warning(f"⚠️ Отзыв не сохранён (БД недоступна): {user_id}")
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('''
                INSERT INTO feedback (user_id, username, text) VALUES ($1, $2, $3)
            ''', user_id, username, text))
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения отзыва: {e}")

async def get_all_feedback(limit: int = 1000) -> List[Dict]:
    if not _db_available:
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(conn.fetch('''
                SELECT id, user_id, username, text, created_at
                FROM feedback
                ORDER BY created_at DESC
                LIMIT $1
            ''', limit))
            return [dict(r) for r in rows]
    except Exception as e:
        logger.error(f"❌ Ошибка получения отзывов: {e}")
        return []

# ------------------------------------------------------------
#  ОЦЕНКИ ОТВЕТОВ
# ------------------------------------------------------------
async def save_rating(faq_id: int, user_id: int, is_helpful: bool):
    if not _db_available:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('''
                INSERT INTO faq_ratings (faq_id, user_id, is_helpful) VALUES ($1, $2, $3)
            ''', faq_id, user_id, is_helpful))
    except Exception as e:
        logger.error(f"❌ Ошибка сохранения оценки: {e}")

async def get_rating_stats() -> Dict[str, Any]:
    if not _db_available:
        return {
            'total_ratings': 0,
            'helpful': 0,
            'unhelpful': 0,
            'satisfaction_rate': 0,
        }
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            total = await _execute_with_retry(conn.fetchval('SELECT COUNT(*) FROM faq_ratings'))
            helpful = await _execute_with_retry(conn.fetchval('SELECT COUNT(*) FROM faq_ratings WHERE is_helpful = true'))
            unhelpful = total - helpful
            return {
                'total_ratings': total,
                'helpful': helpful,
                'unhelpful': unhelpful,
                'satisfaction_rate': round(helpful / total * 100, 2) if total > 0 else 0,
            }
    except Exception as e:
        logger.error(f"❌ Ошибка получения статистики оценок: {e}")
        return {
            'total_ratings': 0,
            'helpful': 0,
            'unhelpful': 0,
            'satisfaction_rate': 0,
        }

# ------------------------------------------------------------
#  СТАТИСТИКА (daily_stats, response_times, error_log)
# ------------------------------------------------------------
async def log_daily_stat(date: str, field: str, increment: int = 1):
    if not _db_available:
        return
    if field not in VALID_DAILY_FIELDS:
        raise ValueError(f"Invalid field for daily_stats: {field}")

    try:
        if isinstance(date, str):
            date_obj = datetime.strptime(date, '%Y-%m-%d').date()
        else:
            date_obj = date
    except ValueError:
        logger.warning(f"⚠️ Неверный формат даты: {date}, используется текущая дата")
        date_obj = datetime.now().date()

    pool = await get_pool()
    async with pool.acquire() as conn:
        query = f'''
            INSERT INTO daily_stats (date, {field})
            VALUES ($1, $2)
            ON CONFLICT (date)
            DO UPDATE SET {field} = daily_stats.{field} + EXCLUDED.{field}
        '''
        await _execute_with_retry(conn.execute(query, date_obj, increment))

async def add_response_time(response_time: float):
    if not _db_available:
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('INSERT INTO response_times (response_time) VALUES ($1)', response_time))
            await _execute_with_retry(conn.execute('''
                DELETE FROM response_times
                WHERE id <= (SELECT id FROM response_times ORDER BY id DESC LIMIT 1 OFFSET 100)
            '''))
            today = datetime.now().date()
            row = await _execute_with_retry(conn.fetchrow('''
                SELECT total_response_time, response_count FROM daily_stats WHERE date = $1
            ''', today))
            if row:
                total = row['total_response_time'] + response_time
                count = row['response_count'] + 1
                avg = total / count
                await _execute_with_retry(conn.execute('''
                    UPDATE daily_stats SET
                        total_response_time = $1,
                        response_count = $2,
                        avg_response_time = $3
                    WHERE date = $4
                ''', total, count, avg, today))
            else:
                await _execute_with_retry(conn.execute('''
                    INSERT INTO daily_stats (date, total_response_time, response_count, avg_response_time)
                    VALUES ($1, $2, $3, $4)
                ''', today, response_time, 1, response_time))
    except Exception as e:
        logger.error(f"❌ Ошибка добавления времени ответа: {e}")

async def get_recent_response_times(limit: int = 100) -> List[float]:
    if not _db_available:
        return []
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(conn.fetch('SELECT response_time FROM response_times ORDER BY id DESC LIMIT $1', limit))
            return [r['response_time'] for r in rows]
    except Exception as e:
        logger.error(f"❌ Ошибка получения времени ответа: {e}")
        return []

async def log_error(error_type: str, error_message: str, user_id: int = None):
    if not _db_available:
        logger.warning(f"⚠️ Ошибка не записана в лог (БД недоступна): {error_type}")
        return
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            await _execute_with_retry(conn.execute('''
                INSERT INTO error_log (error_type, error_message, user_id) VALUES ($1, $2, $3)
            ''', error_type, error_message, user_id))
    except Exception as e:
        logger.error(f"❌ Ошибка записи в error_log: {e}")

async def get_daily_stats_for_last_days(days: int = 7) -> Dict[str, Dict]:
    if not _db_available:
        return {}
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            rows = await _execute_with_retry(conn.fetch('''
                SELECT date, messages, commands, searches, users_count,
                       feedback_count, ratings_helpful, ratings_unhelpful,
                       avg_response_time
                FROM daily_stats
                WHERE date > NOW() - INTERVAL '1 day' * $1
                ORDER BY date
            ''', days))
            result = {}
            for r in rows:
                date_str = r['date'].strftime("%Y-%m-%d")
                result[date_str] = {
                    'messages': r['messages'],
                    'commands': r['commands'],
                    'searches': r['searches'],
                    'users_count': r['users_count'],
                    'feedback': r['feedback_count'],
                    'response_times': [],
                    'ratings': {
                        'helpful': r['ratings_helpful'],
                        'unhelpful': r['ratings_unhelpful']
                    }
                }
            return result
    except Exception as e:
        logger.error(f"❌ Ошибка получения дневной статистики: {e}")
        return {}

# ------------------------------------------------------------
#  ФУНКЦИИ ОЧИСТКИ СТАРЫХ ДАННЫХ
# ------------------------------------------------------------
async def cleanup_old_errors(days: int = 30) -> int:
    if not _db_available:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _execute_with_retry(conn.execute('''
                DELETE FROM error_log
                WHERE timestamp < NOW() - INTERVAL '1 day' * $1
            ''', days))
            try:
                cleaned = int(result.split()[1]) if 'DELETE' in result else 0
            except:
                cleaned = 0
            logger.info(f"✅ Очищено {cleaned} старых записей из error_log")
            return cleaned
    except Exception as e:
        logger.error(f"❌ Ошибка очистки error_log: {e}")
        return 0

async def cleanup_old_feedback(days: int = 90) -> int:
    if not _db_available:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            result = await _execute_with_retry(conn.execute('''
                DELETE FROM feedback
                WHERE created_at < NOW() - INTERVAL '1 day' * $1
            ''', days))
            try:
                cleaned = int(result.split()[1]) if 'DELETE' in result else 0
            except:
                cleaned = 0
            logger.info(f"✅ Очищено {cleaned} старых записей из feedback")
            return cleaned
    except Exception as e:
        logger.error(f"❌ Ошибка очистки feedback: {e}")
        return 0

# ------------------------------------------------------------
#  ПОДСЧЁТ ОБЩЕГО КОЛИЧЕСТВА СТРОК
# ------------------------------------------------------------
async def get_total_rows_count() -> int:
    if not _db_available:
        return 0
    try:
        pool = await get_pool()
        async with pool.acquire() as conn:
            tables = [
                'subscribers', 'messages', 'faq', 'meme_history',
                'meme_subscribers', 'feedback', 'faq_ratings',
                'daily_stats', 'response_times', 'error_log'
            ]
            total = 0
            for table in tables:
                try:
                    count = await _execute_with_retry(conn.fetchval(f'SELECT COUNT(*) FROM {table}'))
                    total += count
                except Exception as e:
                    logger.warning(f"Не удалось подсчитать строки в {table}: {e}")
            return total
    except Exception as e:
        logger.error(f"❌ Ошибка подсчёта строк: {e}")
        return 0

# ------------------------------------------------------------
#  ЗАВЕРШЕНИЕ РАБОТЫ
# ------------------------------------------------------------
async def shutdown_db():
    await close_pool()
