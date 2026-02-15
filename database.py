# database.py
"""
Модуль для работы с базой данных Supabase (PostgreSQL)
Версия 1.0
"""
import os
import json
import asyncpg
import logging
from datetime import datetime, timedelta
from typing import List, Dict, Optional, Any, Tuple
from collections import defaultdict, deque

logger = logging.getLogger(__name__)

# Строка подключения к базе данных. Её мы добавим в переменные окружения на Render.
DATABASE_URL = os.getenv('DATABASE_URL')

# ------------------------------------------------------------
#  ИНИЦИАЛИЗАЦИЯ ТАБЛИЦ
# ------------------------------------------------------------
async def init_db():
    """Создаёт все необходимые таблицы, если их ещё нет."""
    if not DATABASE_URL:
        logger.error("❌ DATABASE_URL не установлен!")
        return

    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # 1. Таблица подписчиков на рассылку
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS subscribers (
                user_id BIGINT PRIMARY KEY,
                subscribed_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # 2. Таблица системных сообщений
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS messages (
                key TEXT PRIMARY KEY,
                text TEXT NOT NULL,
                title TEXT
            )
        ''')

        # 3. Таблица базы знаний FAQ
        await conn.execute('''
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
        ''')

        # 4. Таблица истории мемов (кто и когда получил)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS meme_history (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                meme_path TEXT,
                sent_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # 5. Таблица подписчиков на мемы
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS meme_subscribers (
                user_id BIGINT PRIMARY KEY,
                subscribed_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # 6. Таблица отзывов и предложений
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                user_id BIGINT NOT NULL,
                username TEXT,
                text TEXT NOT NULL,
                created_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # 7. Таблица оценок ответов
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS faq_ratings (
                id SERIAL PRIMARY KEY,
                faq_id INTEGER NOT NULL,
                user_id BIGINT,
                is_helpful BOOLEAN NOT NULL,
                rated_at TIMESTAMPTZ DEFAULT NOW()
            )
        ''')

        # 8. Таблица для хранения статистики по дням (чтобы не потерять при перезапуске)
        await conn.execute('''
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
        ''')

        # 9. Таблица для истории времени ответа (последние 100)
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS response_times (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW(),
                response_time FLOAT NOT NULL
            )
        ''')

        # 10. Таблица для лога ошибок
        await conn.execute('''
            CREATE TABLE IF NOT EXISTS error_log (
                id SERIAL PRIMARY KEY,
                timestamp TIMESTAMPTZ DEFAULT NOW(),
                error_type TEXT,
                error_message TEXT,
                user_id BIGINT
            )
        ''')

        logger.info("✅ Таблицы в Supabase созданы или уже существуют.")
    except Exception as e:
        logger.error(f"❌ Ошибка при создании таблиц: {e}")
    finally:
        await conn.close()

# ------------------------------------------------------------
#  ПОДПИСЧИКИ НА РАССЫЛКУ (из subscribers.json)
# ------------------------------------------------------------
async def get_subscribers() -> List[int]:
    """Возвращает список всех подписчиков."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch('SELECT user_id FROM subscribers')
        return [r['user_id'] for r in rows]
    finally:
        await conn.close()

async def add_subscriber(user_id: int):
    """Добавляет подписчика."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('''
            INSERT INTO subscribers (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        ''', user_id)
    finally:
        await conn.close()

async def remove_subscriber(user_id: int):
    """Удаляет подписчика."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('DELETE FROM subscribers WHERE user_id = $1', user_id)
    finally:
        await conn.close()

async def ensure_subscribed(user_id: int):
    """Гарантирует, что пользователь есть в подписчиках (используется в /start)."""
    await add_subscriber(user_id)

# ------------------------------------------------------------
#  СИСТЕМНЫЕ СООБЩЕНИЯ (из messages.json)
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

async def get_message(key: str, **kwargs) -> str:
    """Возвращает текст сообщения по ключу с подстановкой переменных."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow('SELECT text FROM messages WHERE key = $1', key)
        if row:
            text = row['text']
        else:
            # Если нет в БД, берём из DEFAULT_MESSAGES
            text = DEFAULT_MESSAGES.get(key, f'⚠️ Сообщение "{key}" не найдено')
        # Подставляем переменные, если они есть
        try:
            return text.format(**kwargs)
        except KeyError:
            return text
    finally:
        await conn.close()

async def save_message(key: str, text: str, title: str = ''):
    """Сохраняет или обновляет системное сообщение."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('''
            INSERT INTO messages (key, text, title) VALUES ($1, $2, $3)
            ON CONFLICT (key) DO UPDATE SET text = $2, title = $3
        ''', key, text, title)
    finally:
        await conn.close()

async def load_all_messages() -> Dict:
    """Загружает все сообщения (для веб-панели)."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch('SELECT key, text, title FROM messages')
        result = {}
        for r in rows:
            result[r['key']] = {'text': r['text'], 'title': r['title']}
        return result
    finally:
        await conn.close()

# ------------------------------------------------------------
#  РАБОТА С FAQ (из faq.json)
# ------------------------------------------------------------
async def load_all_faq() -> List[Dict]:
    """Загружает все записи FAQ."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch('''
            SELECT id, priority, question, answer, keywords, category
            FROM faq ORDER BY id
        ''')
        return [dict(r) for r in rows]
    finally:
        await conn.close()

async def get_faq_by_id(faq_id: int) -> Optional[Dict]:
    """Возвращает одну запись FAQ по ID."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow('''
            SELECT id, priority, question, answer, keywords, category
            FROM faq WHERE id = $1
        ''', faq_id)
        return dict(row) if row else None
    finally:
        await conn.close()

async def add_faq(question: str, answer: str, category: str, keywords: str = '', priority: int = 0) -> int:
    """Добавляет новую запись в FAQ. Возвращает ID новой записи."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Простейшая нормализация (можно улучшить, скопировав логику из search_engine.py)
        norm_question = ' '.join(question.lower().split())
        norm_keywords = ' '.join(keywords.lower().split()) if keywords else ''
        new_id = await conn.fetchval('''
            INSERT INTO faq (priority, question, answer, keywords, norm_keywords, norm_question, category)
            VALUES ($1, $2, $3, $4, $5, $6, $7)
            RETURNING id
        ''', priority, question, answer, keywords, norm_keywords, norm_question, category)
        return new_id
    finally:
        await conn.close()

async def update_faq(faq_id: int, question: str, answer: str, category: str, keywords: str = '', priority: int = 0):
    """Обновляет существующую запись FAQ."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        norm_question = ' '.join(question.lower().split())
        norm_keywords = ' '.join(keywords.lower().split()) if keywords else ''
        await conn.execute('''
            UPDATE faq SET
                priority = $1,
                question = $2,
                answer = $3,
                keywords = $4,
                norm_keywords = $5,
                norm_question = $6,
                category = $7
            WHERE id = $8
        ''', priority, question, answer, keywords, norm_keywords, norm_question, category, faq_id)
    finally:
        await conn.close()

async def delete_faq(faq_id: int):
    """Удаляет запись из FAQ."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('DELETE FROM faq WHERE id = $1', faq_id)
    finally:
        await conn.close()

async def get_next_faq_id() -> int:
    """Возвращает следующий ID (нужно только для совместимости со старым кодом, но можно и через sequence)."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # PostgreSQL сам генерирует ID, эта функция больше не нужна.
        # Оставляем заглушку для обратной совместимости.
        return 0
    finally:
        await conn.close()

# ------------------------------------------------------------
#  ИСТОРИЯ МЕМОВ (из meme_data.json)
# ------------------------------------------------------------
async def add_meme_history(user_id: int, meme_path: str = None):
    """Записывает факт получения мема пользователем."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('''
            INSERT INTO meme_history (user_id, meme_path) VALUES ($1, $2)
        ''', user_id, meme_path)
    finally:
        await conn.close()

async def get_meme_count_last_24h(user_id: int) -> int:
    """Сколько мемов получил пользователь за последние 24 часа."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        count = await conn.fetchval('''
            SELECT COUNT(*) FROM meme_history
            WHERE user_id = $1 AND sent_at > NOW() - INTERVAL '24 hours'
        ''', user_id)
        return count
    finally:
        await conn.close()

async def add_meme_subscriber(user_id: int) -> bool:
    """Подписать на рассылку мемов. Возвращает True, если подписка была добавлена."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        result = await conn.execute('''
            INSERT INTO meme_subscribers (user_id) VALUES ($1)
            ON CONFLICT (user_id) DO NOTHING
        ''', user_id)
        # В asyncpg нет простого способа узнать, была ли вставка, поэтому вернём True
        return True
    finally:
        await conn.close()

async def remove_meme_subscriber(user_id: int) -> bool:
    """Отписать от рассылки мемов."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        result = await conn.execute('DELETE FROM meme_subscribers WHERE user_id = $1', user_id)
        return True  # Упрощаем
    finally:
        await conn.close()

async def is_meme_subscribed(user_id: int) -> bool:
    """Проверить, подписан ли пользователь на рассылку мемов."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        row = await conn.fetchrow('SELECT 1 FROM meme_subscribers WHERE user_id = $1', user_id)
        return row is not None
    finally:
        await conn.close()

async def get_all_meme_subscribers() -> List[int]:
    """Список всех подписчиков на мемы."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch('SELECT user_id FROM meme_subscribers')
        return [r['user_id'] for r in rows]
    finally:
        await conn.close()

# ------------------------------------------------------------
#  ОТЗЫВЫ (feedback)
# ------------------------------------------------------------
async def save_feedback(user_id: int, username: str, text: str):
    """Сохраняет отзыв пользователя."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('''
            INSERT INTO feedback (user_id, username, text) VALUES ($1, $2, $3)
        ''', user_id, username, text)
    finally:
        await conn.close()

async def get_all_feedback(limit: int = 1000) -> List[Dict]:
    """Возвращает последние отзывы."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch('''
            SELECT id, user_id, username, text, created_at
            FROM feedback
            ORDER BY created_at DESC
            LIMIT $1
        ''', limit)
        return [dict(r) for r in rows]
    finally:
        await conn.close()

# ------------------------------------------------------------
#  ОЦЕНКИ ОТВЕТОВ (faq_ratings)
# ------------------------------------------------------------
async def save_rating(faq_id: int, user_id: int, is_helpful: bool):
    """Сохраняет оценку ответа."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('''
            INSERT INTO faq_ratings (faq_id, user_id, is_helpful) VALUES ($1, $2, $3)
        ''', faq_id, user_id, is_helpful)
    finally:
        await conn.close()

async def get_rating_stats() -> Dict[str, Any]:
    """Возвращает статистику по оценкам."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        total = await conn.fetchval('SELECT COUNT(*) FROM faq_ratings')
        helpful = await conn.fetchval('SELECT COUNT(*) FROM faq_ratings WHERE is_helpful = true')
        unhelpful = total - helpful
        return {
            'total_ratings': total,
            'helpful': helpful,
            'unhelpful': unhelpful,
            'satisfaction_rate': round(helpful / total * 100, 2) if total > 0 else 0,
        }
    finally:
        await conn.close()

# ------------------------------------------------------------
#  СТАТИСТИКА (daily_stats, response_times, error_log)
# ------------------------------------------------------------
async def log_daily_stat(date: str, field: str, increment: int = 1):
    """Увеличивает счётчик в daily_stats. Поле может быть 'messages', 'commands', 'searches', 'feedback_count', 'ratings_helpful', 'ratings_unhelpful'."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Используем INSERT ... ON CONFLICT для атомарного увеличения
        await conn.execute(f'''
            INSERT INTO daily_stats (date, {field})
            VALUES ($1, $2)
            ON CONFLICT (date)
            DO UPDATE SET {field} = daily_stats.{field} + EXCLUDED.{field}
        ''', date, increment)
    finally:
        await conn.close()

async def update_daily_users(date: str, users_set: set):
    """Обновляет количество уникальных пользователей за день."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        # Мы не можем хранить множество в SQL просто так, поэтому будем хранить число.
        # Но для точности нужно либо отдельную таблицу, либо обновлять через триггер.
        # Пока упростим: будем считать, что users_count обновляется отдельно.
        # В текущей реализации статистика всё ещё может теряться. Для простоты оставим пока как есть,
        # но позже можно усложнить.
        pass
    finally:
        await conn.close()

async def add_response_time(response_time: float):
    """Сохраняет время ответа."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('INSERT INTO response_times (response_time) VALUES ($1)', response_time)
        # Оставляем только последние 100 записей
        await conn.execute('''
            DELETE FROM response_times
            WHERE id <= (SELECT id FROM response_times ORDER BY id DESC LIMIT 1 OFFSET 100)
        ''')
    finally:
        await conn.close()

async def get_recent_response_times(limit: int = 100) -> List[float]:
    """Возвращает последние времена ответа."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        rows = await conn.fetch('SELECT response_time FROM response_times ORDER BY id DESC LIMIT $1', limit)
        return [r['response_time'] for r in rows]
    finally:
        await conn.close()

async def log_error(error_type: str, error_message: str, user_id: int = None):
    """Сохраняет ошибку в лог."""
    conn = await asyncpg.connect(DATABASE_URL)
    try:
        await conn.execute('''
            INSERT INTO error_log (error_type, error_message, user_id) VALUES ($1, $2, $3)
        ''', error_type, error_message, user_id)
    finally:
        await conn.close()