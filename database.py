# database.py
"""
Модуль для работы с базой данных Supabase (PostgreSQL)
Версия 2.7 – добавлены функции очистки старых данных
"""
import os
import asyncio
import asyncpg
import logging
from datetime import datetime, timedelta
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
                # Дополнительная задержка перед первой попыткой (важно для Render Free)
                logger.info("🔄 Ожидание полной инициализации сети Render (3 сек)...")
                await asyncio.sleep(3.0)

                max_retries = 12  # Увеличено для бесплатного тарифа
                for attempt in range(max_retries):
                    try:
                        logger.info(f"🔄 Попытка {attempt+1}/{max_retries} создания пула соединений...")
                        _pool = await asyncpg.create_pool(
                            DATABASE_URL,
                            min_size=POOL_MIN_SIZE,
                            max_size=POOL_MAX_SIZE,
                            command_timeout=POOL_TIMEOUT,
                            max_queries=50000,
                            max_inactive_connection_lifetime=300
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
                            logger.critical(f"❌ Не удалось создать пул после {max_retries} попыток. "
                                          f"Проверьте: 1) DATABASE_URL в переменных окружения, "
                                          f"2) Доступность Supabase, 3) Лимиты бесплатного тарифа.")
                            raise

                        # Экспоненциальная задержка с ограничением 15 секунд
                        wait = min(15.0, 0.5 * (2 ** attempt))  # 0.5, 1.0, 2.0, 4.0, 8.0, 15.0...
                        logger.warning(f"⏳ Повтор через {wait:.1f}с...")
                        await asyncio.sleep(wait)
    return _pool

async def close_pool():
    global _pool
    if _pool:
        await _pool.close()
        _pool = None
        logger.info("✅ Пул соединений закрыт")

# ------------------------------------------------------------
#  УНИВЕРСАЛЬНАЯ ФУНКЦИЯ ДЛЯ ВЫПОЛНЕНИЯ ЗАПРОСОВ С ПОВТОРАМИ
# ------------------------------------------------------------
async def _execute_with_retry(coro, max_retries=3, timeout=5.0):
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

# ------------------------------------------------------------
#  ПОДПИСЧИКИ НА РАССЫЛКУ
# ------------------------------------------------------------
async def get_subscribers() -> List[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _execute_with_retry(conn.fetch('SELECT user_id FROM subscribers'))
        return [r['user_id'] for r in rows]

async def get_subscribers_batch(offset: int = 0, limit: int = 1000) -> List[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _execute_with_retry(
            conn.fetch('SELECT user_id FROM subscribers ORDER BY user_id OFFSET $1 LIMIT $2', offset, limit)
        )
        return [r['user_id'] for r in rows]

async def count_subscribers() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await _execute_with_retry(conn.fetchval('SELECT COUNT(*) FROM subscribers'))

async def add_subscriber(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('''
            INSERT INTO subscribers (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        ''', user_id))

async def remove_subscriber(user_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('DELETE FROM subscribers WHERE user_id = $1', user_id))

async def ensure_subscribed(user_id: int):
    await add_subscriber(user_id)

# ------------------------------------------------------------
#  ПОДПИСЧИКИ НА МЕМЫ
# ------------------------------------------------------------
async def get_all_meme_subscribers() -> List[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _execute_with_retry(conn.fetch('SELECT user_id FROM meme_subscribers'))
        return [r['user_id'] for r in rows]

async def get_meme_subscribers_batch(offset: int = 0, limit: int = 500) -> List[int]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _execute_with_retry(
            conn.fetch('SELECT user_id FROM meme_subscribers ORDER BY user_id OFFSET $1 LIMIT $2', offset, limit)
        )
        return [r['user_id'] for r in rows]

async def count_meme_subscribers() -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await _execute_with_retry(conn.fetchval('SELECT COUNT(*) FROM meme_subscribers'))

async def add_meme_subscriber(user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('''
            INSERT INTO meme_subscribers (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        ''', user_id))
        return True

async def remove_meme_subscriber(user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('DELETE FROM meme_subscribers WHERE user_id = $1', user_id))
        return True

async def is_meme_subscribed(user_id: int) -> bool:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await _execute_with_retry(conn.fetchrow('SELECT 1 FROM meme_subscribers WHERE user_id = $1', user_id))
        return row is not None

# ------------------------------------------------------------
#  СИСТЕМНЫЕ СООБЩЕНИЯ
# ------------------------------------------------------------
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
    "help": "📚 <b>Доступные команды:</b>\n\n/start - начать работу с ботом\n/help - показать эту справку\n/categories - показать категории вопросов\n/feedback - оставить отзыв или предложение\n/subscribe - подписаться на рассылку\n/unsubscribe - отписаться от рассылки\n/whatcanido - что я умею",
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

async def save_message(key: str, text: str, title: str = ''):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('''
            INSERT INTO messages (key, text, title) VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE SET text = $2, title = $3
        ''', key, text, title))

async def load_all_messages() -> Dict:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _execute_with_retry(conn.fetch('SELECT key, text, title FROM messages'))
        return {r['key']: {'text': r['text'], 'title': r['title']} for r in rows}

# ------------------------------------------------------------
#  РАБОТА С FAQ
# ------------------------------------------------------------
async def load_all_faq() -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _execute_with_retry(conn.fetch('''
            SELECT id, priority, question, answer, keywords, category
            FROM faq ORDER BY id
        '''))
        return [dict(r) for r in rows]

async def get_faq_by_id(faq_id: int) -> Optional[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        row = await _execute_with_retry(conn.fetchrow('''
            SELECT id, priority, question, answer, keywords, category
            FROM faq WHERE id = $1
        ''', faq_id))
        return dict(row) if row else None

async def add_faq(question: str, answer: str, category: str, keywords: str = '', priority: int = 0) -> int:
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

async def update_faq(faq_id: int, question: str, answer: str, category: str, keywords: str = '', priority: int = 0):
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

async def delete_faq(faq_id: int):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('DELETE FROM faq WHERE id = $1', faq_id))

# ------------------------------------------------------------
#  ИСТОРИЯ МЕМОВ
# ------------------------------------------------------------
async def add_meme_history(user_id: int, meme_path: str = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('''
            INSERT INTO meme_history (user_id, meme_path) VALUES ($1, $2)
        ''', user_id, meme_path))

async def get_meme_count_last_24h(user_id: int) -> int:
    pool = await get_pool()
    async with pool.acquire() as conn:
        return await _execute_with_retry(conn.fetchval('''
            SELECT COUNT(*) FROM meme_history
            WHERE user_id = $1 AND sent_at > NOW() - INTERVAL '24 hours'
        ''', user_id))

# ------------------------------------------------------------
#  ОТЗЫВЫ
# ------------------------------------------------------------
async def save_feedback(user_id: int, username: str, text: str):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('''
            INSERT INTO feedback (user_id, username, text) VALUES ($1, $2, $3)
        ''', user_id, username, text))

async def get_all_feedback(limit: int = 1000) -> List[Dict]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _execute_with_retry(conn.fetch('''
            SELECT id, user_id, username, text, created_at
            FROM feedback
            ORDER BY created_at DESC
            LIMIT $1
        ''', limit))
        return [dict(r) for r in rows]

# ------------------------------------------------------------
#  ОЦЕНКИ ОТВЕТОВ
# ------------------------------------------------------------
async def save_rating(faq_id: int, user_id: int, is_helpful: bool):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('''
            INSERT INTO faq_ratings (faq_id, user_id, is_helpful) VALUES ($1, $2, $3)
        ''', faq_id, user_id, is_helpful))

async def get_rating_stats() -> Dict[str, Any]:
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

# ------------------------------------------------------------
#  СТАТИСТИКА (daily_stats, response_times, error_log)
# ------------------------------------------------------------
async def log_daily_stat(date: str, field: str, increment: int = 1):
    if field not in VALID_DAILY_FIELDS:
        raise ValueError(f"Invalid field for daily_stats: {field}")
    pool = await get_pool()
    async with pool.acquire() as conn:
        query = f'''
            INSERT INTO daily_stats (date, {field})
            VALUES ($1, $2)
            ON CONFLICT (date)
            DO UPDATE SET {field} = daily_stats.{field} + EXCLUDED.{field}
        '''
        await _execute_with_retry(conn.execute(query, date, increment))

async def add_response_time(response_time: float):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('INSERT INTO response_times (response_time) VALUES ($1)', response_time))
        await _execute_with_retry(conn.execute('''
            DELETE FROM response_times
            WHERE id <= (SELECT id FROM response_times ORDER BY id DESC LIMIT 1 OFFSET 100)
        '''))
        today = datetime.now().strftime('%Y-%m-%d')
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

async def get_recent_response_times(limit: int = 100) -> List[float]:
    pool = await get_pool()
    async with pool.acquire() as conn:
        rows = await _execute_with_retry(conn.fetch('SELECT response_time FROM response_times ORDER BY id DESC LIMIT $1', limit))
        return [r['response_time'] for r in rows]

async def log_error(error_type: str, error_message: str, user_id: int = None):
    pool = await get_pool()
    async with pool.acquire() as conn:
        await _execute_with_retry(conn.execute('''
            INSERT INTO error_log (error_type, error_message, user_id) VALUES ($1, $2, $3)
        ''', error_type, error_message, user_id))

async def get_daily_stats_for_last_days(days: int = 7) -> Dict[str, Dict]:
    """Загружает daily_stats за последние N дней (для инициализации буфера статистики)."""
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

# ------------------------------------------------------------
#  ФУНКЦИИ ОЧИСТКИ СТАРЫХ ДАННЫХ
# ------------------------------------------------------------
async def cleanup_old_errors(days: int = 30):
    """Удаляет записи из error_log старше указанного количества дней."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await _execute_with_retry(conn.execute('''
            DELETE FROM error_log
            WHERE timestamp < NOW() - INTERVAL '1 day' * $1
        ''', days))
        logger.info(f"✅ Очищено {result} старых записей из error_log")

async def cleanup_old_feedback(days: int = 90):
    """Удаляет записи из feedback старше указанного количества дней."""
    pool = await get_pool()
    async with pool.acquire() as conn:
        result = await _execute_with_retry(conn.execute('''
            DELETE FROM feedback
            WHERE created_at < NOW() - INTERVAL '1 day' * $1
        ''', days))
        logger.info(f"✅ Очищено {result} старых записей из feedback")

# ------------------------------------------------------------
#  ЗАВЕРШЕНИЕ РАБОТЫ
# ------------------------------------------------------------
async def shutdown_db():
    await close_pool()
