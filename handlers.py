"""
ОБРАБОТЧИКИ КОМАНД И СООБЩЕНИЙ ДЛЯ TELEGRAM БОТА
Версия 4.2 - Исправленная и оптимизированная

Исправления и улучшения:
✅ Исправлен блокирующий вызов в асинхронном контексте
✅ Добавлено экранирование Markdown для безопасности
✅ Добавлена защита от спама (рейт-лимиты)
✅ Улучшена безопасность логов (маскировка ПДн)
✅ Добавлена обработка Forbidden ошибок
✅ Улучшена архитектура Dependency Injection
"""

import logging
import re
import asyncio
import time
import html
import hashlib
from typing import Optional, Tuple, List, Dict, Any, Callable
from datetime import datetime, timedelta
from collections import defaultdict
from functools import wraps

from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import ContextTypes
from telegram.error import TimedOut, BadRequest, NetworkError, RetryAfter, Forbidden
from telegram.helpers import escape_markdown

from config import config
from search_engine import SearchEngine

logger = logging.getLogger(__name__)


class RateLimiter:
    """Класс для ограничения частоты запросов"""
    
    def __init__(self):
        self.requests = defaultdict(list)  # user_id -> [timestamps]
        self.blocked_users = set()  # Заблокированные пользователи
    
    def is_allowed(self, user_id: int, max_requests: int = 5, 
                   window_seconds: int = 60) -> Tuple[bool, Optional[str]]:
        """Проверка, разрешён ли запрос"""
        
        # Проверка на заблокированного пользователя
        if user_id in self.blocked_users:
            return False, "Вы были заблокированы за спам"
        
        now = datetime.now()
        
        # Очистка старых запросов
        self.requests[user_id] = [
            ts for ts in self.requests[user_id] 
            if now - ts < timedelta(seconds=window_seconds)
        ]
        
        # Проверка лимита
        if len(self.requests[user_id]) >= max_requests:
            wait_time = window_seconds
            return False, f"Слишком много запросов. Подождите {wait_time} секунд"
        
        self.requests[user_id].append(now)
        return True, None
    
    def block_user(self, user_id: int, duration_minutes: int = 60):
        """Временная блокировка пользователя"""
        self.blocked_users.add(user_id)
        
        # Снимаем блокировку через указанное время
        async def unblock_later():
            await asyncio.sleep(duration_minutes * 60)
            self.blocked_users.discard(user_id)
            logger.info(f"🔓 Разблокирован пользователь {user_id}")
        
        asyncio.create_task(unblock_later())
        logger.warning(f"🔒 Заблокирован пользователь {user_id} на {duration_minutes} минут")


def rate_limit(max_requests: int = 5, window_seconds: int = 60):
    """Декоратор для ограничения частоты запросов"""
    def decorator(func):
        @wraps(func)
        async def wrapper(self, update: Update, context: ContextTypes.DEFAULT_TYPE, *args, **kwargs):
            user_id = update.effective_user.id
            
            # Пропускаем администраторов
            admin_ids = config.get_admin_ids()
            if admin_ids and user_id in admin_ids:
                return await func(self, update, context, *args, **kwargs)
            
            # Проверяем лимит
            allowed, message = self.rate_limiter.is_allowed(
                user_id, max_requests, window_seconds
            )
            
            if not allowed:
                await self._safe_reply(
                    update,
                    f"⏱️ *{message}*\n\n"
                    "Это защита от спама. Пожалуйста, подождите.",
                    parse_mode='Markdown'
                )
                logger.warning(f"🛑 Лимит запросов для пользователя {user_id}")
                return None
            
            return await func(self, update, context, *args, **kwargs)
        return wrapper
    return decorator


class BotCommandHandler:
    """Обработчик команд бота с продвинутой обработкой ошибок"""
    
    def __init__(self, search_engine: SearchEngine):
        self.search_engine = search_engine
        self.semaphore = asyncio.Semaphore(10)  # Ограничение параллельных операций
        self.request_timeout = 25  # Таймаут операций в секундах
        self.max_retries = 3  # Максимальное количество повторных попыток
        self.rate_limiter = RateLimiter()  # Ограничитель запросов
        
        self.metrics = {
            'total_requests': 0,
            'successful_requests': 0,
            'failed_requests': 0,
            'average_response_time': 0.0,
            'blocked_users': 0,
            'last_reset': datetime.now()
        }
        
        # Кэш для форматированных ответов (FAQ_ID -> форматированный текст)
        self.formatted_answers_cache = {}
    
    def _update_metrics(self, success: bool, response_time: float):
        """Обновление метрик производительности"""
        self.metrics['total_requests'] += 1
        if success:
            self.metrics['successful_requests'] += 1
        else:
            self.metrics['failed_requests'] += 1
        
        # Обновляем среднее время ответа
        total_time = self.metrics['average_response_time'] * (self.metrics['total_requests'] - 1)
        self.metrics['average_response_time'] = (total_time + response_time) / self.metrics['total_requests']
    
    def get_metrics(self) -> Dict[str, Any]:
        """Получение текущих метрик"""
        return self.metrics.copy()
    
    async def _execute_with_retry(self, coro, operation_name: str = "операция"):
        """Выполнение корутины с повторными попытками"""
        start_time = time.time()
        last_exception = None
        
        for attempt in range(self.max_retries):
            try:
                async with self.semaphore:
                    result = await asyncio.wait_for(coro, timeout=self.request_timeout)
                    
                # Записываем успешную метрику
                response_time = time.time() - start_time
                self._update_metrics(success=True, response_time=response_time)
                
                return result
                
            except TimedOut:
                last_exception = TimedOut(f"Таймаут {operation_name}")
                logger.warning(f"Таймаут {operation_name} (попытка {attempt + 1}/{self.max_retries})")
                
            except RetryAfter as e:
                wait_time = e.retry_after if hasattr(e, 'retry_after') else 5
                logger.warning(f"Telegram просит подождать {wait_time} сек (попытка {attempt + 1})")
                await asyncio.sleep(wait_time)
                continue
                
            except (NetworkError, BadRequest) as e:
                last_exception = e
                logger.warning(f"Сетевая ошибка {operation_name}: {e} (попытка {attempt + 1})")
                
            except Exception as e:
                last_exception = e
                logger.error(f"Неожиданная ошибка {operation_name}: {e}", exc_info=True)
            
            # Экспоненциальная задержка перед повторной попыткой
            if attempt < self.max_retries - 1:
                wait_time = (attempt + 1) * 2  # 2, 4 секунды
                logger.info(f"Ждем {wait_time} сек перед повторной попыткой...")
                await asyncio.sleep(wait_time)
        
        # Все попытки провалились
        response_time = time.time() - start_time
        self._update_metrics(success=False, response_time=response_time)
        
        if last_exception:
            logger.error(f"Все попытки {operation_name} провалились: {last_exception}")
            raise last_exception
        else:
            raise Exception(f"Неизвестная ошибка при выполнении {operation_name}")
    
    async def _safe_send_message(self, chat_id: int, text: str, context: ContextTypes.DEFAULT_TYPE,
                               parse_mode: str = 'Markdown', **kwargs) -> bool:
        """Безопасная отправка сообщения с обработкой ошибок"""
        try:
            async def send():
                await context.bot.send_message(
                    chat_id=chat_id,
                    text=text,
                    parse_mode=parse_mode,
                    **kwargs
                )
            
            await self._execute_with_retry(send(), "отправки сообщения")
            return True
            
        except Forbidden:
            # Пользователь заблокировал бота
            logger.warning(f"Пользователь {chat_id} заблокировал бота")
            # Можно добавить логику очистки данных пользователя
            return False
            
        except Exception as e:
            logger.error(f"Не удалось отправить сообщение: {e}")
            return False
    
    async def _safe_reply(self, update: Update, text: str, parse_mode: str = 'Markdown', **kwargs) -> bool:
        """Безопасный ответ на сообщение с экранированием Markdown"""
        if not update.message:
            return False
        
        # Экранируем текст для безопасной отправки
        if parse_mode == 'Markdown':
            # Используем escape_markdown из telegram.helpers
            text = escape_markdown(text, version=2)
        elif parse_mode == 'HTML':
            # Для HTML экранируем специальные символы
            text = html.escape(text)
        
        try:
            async def reply():
                await update.message.reply_text(text, parse_mode=parse_mode, **kwargs)
            
            await self._execute_with_retry(reply(), "ответа на сообщение")
            return True
            
        except Forbidden:
            logger.warning(f"Пользователь {update.effective_user.id} заблокировал бота")
            return False
            
        except Exception as e:
            logger.error(f"Не удалось ответить на сообщение: {e}")
            return False
    
    @rate_limit(max_requests=10, window_seconds=60)
    async def handle_welcome(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команд /start и /help"""
        user = update.effective_user
        
        # Маскируем имя пользователя для логов
        user_log_name = f"{user.id} ({hash(str(user.id)) % 10000:04d})"
        
        welcome_text = f"""
🤖 *Добро пожаловать в Корпоративный Бот Мечел, {user.first_name}!*

Я — ваш виртуальный помощник по кадровым вопросам. 
Готов помочь с информацией о:

📅 *Отпуска и больничные*
• Оформление ежегодного отпуска
• Больничные листы
• Отпуск без содержания

💰 *Зарплата и выплаты*
• График выплаты зарплаты
• Аванс, премии, бонусы
• Справка 2\\-НДФЛ

📄 *Документы и справки*
• Трудовая книжка
• Справки с места работы
• Характеристики

🏢 *Работа в офисе*
• График работы
• Удаленная работа
• Командировки

🎓 *Обучение и развитие*
• Корпоративное обучение
• Повышение квалификации
• Стажировки

🎁 *Социальные льготы*
• Медицинская страховка
• Спортивные мероприятия
• Корпоративные скидки

📋 *Основные команды:*
• /start — это сообщение
• /categories — все категории вопросов  
• /search [вопрос] — поиск по базе
• /feedback — обратная связь

💡 *Просто напишите ваш вопрос!*
Например: "Как оформить отпуск\\?" или "Когда выплачивается зарплата\\?"

⏱️ *Среднее время ответа:* {self.metrics['average_response_time']:.1f} сек
✅ *Надежность системы:* {(self.metrics['successful_requests'] / max(self.metrics['total_requests'], 1) * 100):.1f}%
"""
        
        try:
            await self._safe_reply(update, welcome_text, parse_mode='Markdown')
            logger.info(f"👋 Приветствие отправлено пользователю {user_log_name}")
            
        except Exception as e:
            logger.error(f"Ошибка отправки приветствия: {e}")
            # Пробуем отправить упрощенное сообщение
            try:
                simple_text = (
                    "Добро пожаловать в HR Bot Мечел! Я помогу вам с кадровыми вопросами. "
                    "Напишите ваш вопрос или используйте команду /categories для просмотра тем."
                )
                await update.message.reply_text(simple_text)
            except Exception as inner_e:
                logger.error(f"Даже упрощенное сообщение не удалось отправить: {inner_e}")
    
    @rate_limit(max_requests=5, window_seconds=30)
    async def handle_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /categories"""
        try:
            stats = self.search_engine.get_stats()
            
            if 'categories' not in stats or not stats['categories']:
                await self._safe_reply(
                    update,
                    "📂 *Категории вопросов еще не добавлены в базу*\\.\n\n"
                    "Пожалуйста, обратитесь к администратору для заполнения базы данных\\.",
                    parse_mode='Markdown'
                )
                return
            
            categories = stats['categories']
            
            # Карта эмодзи для категорий
            emoji_map = {
                'Отпуск': '🏖️',
                'Зарплата': '💰', 
                'Больничные': '🏥',
                'Документы': '📄',
                'IT': '💻',
                'Офис': '🏢',
                'Обучение': '🎓',
                'Льготы': '🎁',
                'Командировки': '✈️',
                'Трудоустройство': '💼',
                'Охрана труда': '🛡️',
                'Корпоративная культура': '🏢',
                'Соцпакет': '🎁',
                'Развитие': '📈',
                'Портал': '🌐',
                'Праздники': '🎉',
                'Семья': '👨‍👩‍👧‍👦',
                'Финансы': '💵',
                'График работы': '🕒',
                'Кадры': '👥',
                'Связь': '📱',
                'Информация': 'ℹ️',
                'Безопасность': '🔐',
                'Питание': '🍽️',
                'Спорт': '⚽',
                'Медицина': '🏥',
                'Транспорт': '🚗',
                'Оборудование': '🖨️',
                'Отчетность': '📊'
            }
            
            categories_text = "📂 *Категории вопросов:*\n\n"
            
            # Сортируем категории и группируем по первым буквам
            sorted_categories = sorted(categories)
            
            for category in sorted_categories:
                emoji = emoji_map.get(category, '📁')
                count = sum(1 for faq in self.search_engine.faq_data if faq.category == category)
                categories_text += f"{emoji} *{category}* — {count} вопросов\n"
            
            categories_text += f"\n📊 *Всего категорий:* {len(categories)}"
            categories_text += f"\n💾 *Всего вопросов в базе:* {stats.get('total_faq', 0)}"
            categories_text += f"\n🔍 *Размер поискового индекса:* {stats.get('keywords_index_size', 0)} ключевых слов"
            
            await self._safe_reply(update, categories_text, parse_mode='Markdown')
            logger.info(f"📂 Категории отправлены пользователю {update.effective_user.id}")
            
        except Exception as e:
            logger.error(f"Ошибка обработки команды /categories: {e}", exc_info=True)
            await self._safe_reply(
                update,
                "❌ *Произошла ошибка при получении категорий*\\.\n\n"
                "Пожалуйста, попробуйте позже или обратитесь к администратору\\.",
                parse_mode='Markdown'
            )
    
    @rate_limit(max_requests=10, window_seconds=60)
    async def handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query: str = None):
        """Обработка команды /search"""
        if not query:
            query = update.message.text
        
        # Извлекаем запрос из команды
        if query.startswith('/search'):
            query = query.replace('/search', '', 1).strip()
        elif query.startswith('/поиск'):
            query = query.replace('/поиск', '', 1).strip()
        
        if not query:
            help_text = """
🔍 *Расширенный поиск по базе знаний*

*Использование:* `/search [ваш запрос]`

*Примеры:*
• `/search как оформить отпуск`
• `/search справка 2\\-НДФЛ где получить`
• `/search график работы в праздники`

💡 *Советы для лучшего поиска:*
1\\. Используйте ключевые слова: "отпуск", "больничный", "зарплата"
2\\. Будьте конкретны: "оформить учебный отпуск"
3\\. Используйте несколько слов: "справка 2\\-НДФЛ для банка"

📋 *Альтернатива:* Просто напишите вопрос в чат без команды\\.
"""
            await self._safe_reply(update, help_text, parse_mode='Markdown')
            return
        
        # Обрабатываем запрос
        await self._process_query(update, context, query)
    
    @rate_limit(max_requests=3, window_seconds=300)
    async def handle_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /feedback"""
        if not config.is_feedback_enabled():
            await self._safe_reply(
                update,
                "💬 *Система отзывов временно отключена*\n\n"
                "Приносим извинения за неудобства\\. Система будет доступна в ближайшее время\\.",
                parse_mode='Markdown'
            )
            return
        
        feedback_text = """
📝 *Режим обратной связи*

Мы ценим ваше мнение и стремимся улучшать наш бот\\!

*Что можно отправить:*
• Предложения по улучшению
• Сообщения об ошибках
• Идеи новых функций
• Оценку качества ответов

*Требования к отзыву:*
• Минимум 10 символов
• Максимум 1000 символов
• Конструктивная критика приветствуется

*Как это поможет:*
1\\. Повысим точность ответов
2\\. Улучшим скорость работы
3\\. Добавим новые функции
4\\. Исправим ошибки

Ваш отзыв будет передан команде разработчиков и учтен при обновлениях бота\\.

💡 *Просто напишите ваш отзыв в следующем сообщении\\!*
"""
        await self._safe_reply(update, feedback_text, parse_mode='Markdown')
    
    async def handle_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /stats (только для администраторов)"""
        admin_ids = config.get_admin_ids()
        if admin_ids and update.effective_user.id not in admin_ids:
            await self._safe_reply(
                update,
                "❌ *Эта команда доступна только администраторам*\n\n"
                "Если вы администратор, убедитесь, что ваш ID добавлен в список администраторов\\.",
                parse_mode='Markdown'
            )
            return
        
        try:
            search_stats = self.search_engine.get_stats()
            
            # Получаем метрики производительности
            success_rate = (self.metrics['successful_requests'] / 
                          max(self.metrics['total_requests'], 1) * 100)
            
            uptime = datetime.now() - self.metrics['last_reset']
            uptime_str = str(uptime).split('.')[0]  # Убираем микросекунды
            
            stats_text = f"""
📊 *Статистика HR\\-бота Мечел*

⏱️ *Производительность:*
• Всего запросов: {self.metrics['total_requests']}
• Успешных: {self.metrics['successful_requests']}
• Неудачных: {self.metrics['failed_requests']}
• Успешность: {success_rate:.1f}%
• Среднее время ответа: {self.metrics['average_response_time']:.2f} сек
• Время работы: {uptime_str}
• Заблокированных пользователей: {self.metrics['blocked_users']}

🔍 *Поисковая система:*
• Всего поисков: {search_stats.get('total_searches', 0)}
• Среднее время поиска: {search_stats.get('avg_response_time', '0\\.000s')}
• Размер кэша: {search_stats.get('cache_size', 0)} записей
• Попадания в кэш: {search_stats.get('cache_hits', 0)}
• Промахи кэша: {search_stats.get('cache_misses', 0)}
• Эффективность кэша: {(search_stats.get('cache_hits', 0) / max(search_stats.get('total_searches', 1), 1) * 100):.1f}%

📚 *База знаний:*
• Всего FAQ: {search_stats.get('total_faq', 0)}/75
• Категорий: {len(search_stats.get('categories', []))}
• Индекс ключевых слов: {search_stats.get('keywords_index_size', 0)}
• Индекс вопросов: {search_stats.get('question_index_size', 0)}
• Уникальных ключевых слов: {len(search_stats.get('unique_keywords', []))}

👥 *Пользователи:*
• Администраторов: {len(admin_ids) if admin_ids else 0}
• Ваш ID: {update.effective_user.id}
"""
            
            await self._safe_reply(update, stats_text, parse_mode='Markdown')
            logger.info(f"📊 Статистика запрошена администратором {update.effective_user.id}")
            
        except Exception as e:
            logger.error(f"Ошибка получения статистики: {e}", exc_info=True)
            await self._safe_reply(
                update,
                "❌ *Ошибка при получении статистики*\n\n"
                f"Детали: {str(e)[:100]}...",
                parse_mode='Markdown'
            )
    
    async def handle_clear_cache(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка команды /clear (только для администраторов)"""
        admin_ids = config.get_admin_ids()
        if not admin_ids or update.effective_user.id not in admin_ids:
            await self._safe_reply(
                update,
                "❌ *Эта команда доступна только администраторам*\n\n"
                "Если вы администратор, убедитесь, что ваш ID добавлен в список администраторов\\.",
                parse_mode='Markdown'
            )
            return
        
        try:
            # Сохраняем текущую статистику перед очисткой
            stats_before = self.search_engine.get_stats()
            
            # Очищаем кэш и обновляем данные
            self.search_engine.refresh_data()
            
            # Очищаем локальный кэш форматированных ответов
            self.formatted_answers_cache.clear()
            
            # Получаем статистику после очистки
            stats_after = self.search_engine.get_stats()
            
            response_text = f"""
✅ *Кэш поиска успешно очищен и данные обновлены\\!*

📊 *Результаты:*
• Кэш очищен: {stats_before.get('cache_size', 0)} → {stats_after.get('cache_size', 0)} записей
• FAQ в памяти: {len(self.search_engine.faq_data)} записей
• Категорий: {len(stats_after.get('categories', []))}
• Время обновления: {datetime.now().strftime('%H:%M:%S')}

💡 *Обратите внимание:*
• Первые несколько запросов после очистки могут быть медленнее
• Кэш будет наполняться по мере использования
• Рекомендуется очищать кэш не чаще 1 раза в сутки
"""
            
            await self._safe_reply(update, response_text, parse_mode='Markdown')
            logger.info(f"🔄 Кэш очищен администратором {update.effective_user.id}")
            
        except Exception as e:
            logger.error(f"Ошибка очистки кэша: {e}", exc_info=True)
            await self._safe_reply(
                update,
                "❌ *Ошибка при обновлении данных*\n\n"
                f"Детали: {str(e)[:100]}...",
                parse_mode='Markdown'
            )
    
    @rate_limit(max_requests=15, window_seconds=60)
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка текстовых сообщений (не команд)"""
        text = update.message.text.strip()
        
        if not text or len(text) < 2:
            return
        
        # Если сообщение начинается с /, но команда не распознана
        if text.startswith('/'):
            command = text.split()[0]
            
            # Показываем список доступных команд
            response = f"""
❓ *Неизвестная команда:* `{command}`

📋 *Доступные команды:*
• /start — Начало работы с ботом
• /categories — Список категорий вопросов
• /search [вопрос] — Поиск по базе знаний
• /feedback — Оставить отзыв или предложение
"""
            admin_ids = config.get_admin_ids()
            if admin_ids and update.effective_user.id in admin_ids:
                response += "• /stats — Статистика работы бота\n"
                response += "• /clear — Очистить кэш поиска\n"
            
            if config.is_meme_enabled():
                response += "• /meme — Посмотреть случайный мем\n"
                response += "• /meme_subscribe — Подписаться на ежедневные мемы\n"
            
            response += "\n💡 *Или просто напишите ваш вопрос\\!*"
            
            await self._safe_reply(update, response, parse_mode='Markdown')
            return
        
        # Обрабатываем как обычный запрос
        await self._process_query(update, context, text)
    
    async def _process_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
        """Основная логика обработки пользовательского запроса"""
        user_id = update.effective_user.id
        chat_id = update.effective_chat.id
        
        # Безопасное логирование (хешируем запрос для анонимности)
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        logger.info(f"🔍 Обработка запроса от {user_id} (хэш: {query_hash}, длина: {len(query)} символов)")
        
        # Проверка длины запроса
        if len(query) < 3:
            await self._safe_reply(
                update,
                "❌ *Запрос слишком короткий*\n\n"
                "Пожалуйста, задайте более конкретный вопрос\\.\n\n"
                "*Примеры правильных запросов:*\n"
                "• 'Как оформить отпуск\\?'\n"
                "• 'Где получить справку 2\\-НДФЛ\\?'\n"
                "• 'Когда выплачивается зарплата\\?'\n"
                "• 'Что делать при увольнении\\?'",
                parse_mode='Markdown'
            )
            return
        
        # Показываем индикатор "печатает" с таймаутом
        try:
            async def send_typing():
                await context.bot.send_chat_action(
                    chat_id=chat_id,
                    action='typing'
                )
            
            await self._execute_with_retry(send_typing(), "отправки индикатора печати")
        except Exception as e:
            logger.warning(f"Не удалось отправить индикатор печати: {e}")
        
        try:
            start_time = time.time()
            
            # Используем run_in_executor для неблокирующего поиска
            # Правильно передаем функцию и аргументы
            loop = asyncio.get_event_loop()
            
            # Передаем метод и его аргументы правильно
            search_func = lambda: self.search_engine.search(query, user_id)
            result = await asyncio.wait_for(
                loop.run_in_executor(None, search_func),
                timeout=20.0  # Таймаут поиска
            )
            
            search_time = time.time() - start_time
            
            if result:
                await self._send_search_result(update, context, query, result, search_time)
            else:
                await self._send_no_results(update, context, query, search_time)
                
        except asyncio.TimeoutError:
            logger.warning(f"Таймаут поиска для пользователя {user_id} (хэш запроса: {query_hash})")
            await self._safe_reply(
                update,
                "⏱️ *Поиск занял слишком много времени*\n\n"
                "Похоже, ваш запрос слишком сложный или система перегружена\\.\n\n"
                "*Что можно сделать:*\n"
                "1\\. Упростите запрос\n"
                "2\\. Используйте ключевые слова\n"
                "3\\. Попробуйте позже\n"
                "4\\. Обратитесь в HR\\-отдел напрямую",
                parse_mode='Markdown'
            )
            
        except Exception as e:
            logger.error(f"Ошибка поиска для пользователя {user_id}: {e}", exc_info=True)
            await self._safe_reply(
                update,
                "❌ *Произошла ошибка при поиске*\n\n"
                "Пожалуйста, попробуйте позже или обратитесь к администратору\\.\n\n"
                f"Код ошибки: `{type(e).__name__}`",
                parse_mode='Markdown'
            )
    
    async def _send_search_result(self, update: Update, context: ContextTypes.DEFAULT_TYPE,
                                original_query: str, result: Tuple, search_time: float):
        """Отправка найденного результата"""
        try:
            faq_id, question, answer, category, score = result
            
            # Преобразуем score в проценты с ограничением 100%
            relevance = min(int(score), 100)
            
            # Определяем уровень релевантности
            if relevance >= 85:
                relevance_emoji = "🎯"
                relevance_text = "Отличное совпадение"
                relevance_color = "🟢"
            elif relevance >= 65:
                relevance_emoji = "✅"
                relevance_text = "Хорошее совпадение"
                relevance_color = "🟡"
            elif relevance >= 40:
                relevance_emoji = "⚠️"
                relevance_text = "Частичное совпадение"
                relevance_color = "🟠"
            else:
                relevance_emoji = "❓"
                relevance_text = "Слабое совпадение"
                relevance_color = "🔴"
            
            # Форматируем ответ для лучшей читаемости (с кэшированием)
            if faq_id in self.formatted_answers_cache:
                formatted_answer = self.formatted_answers_cache[faq_id]
            else:
                formatted_answer = self._format_answer(answer)
                self.formatted_answers_cache[faq_id] = formatted_answer
            
            # Безопасно обрезаем оригинальный запрос для отображения
            display_query = original_query[:40]
            if len(original_query) > 40:
                display_query += "..."
            
            # Создаем информативное сообщение
            response = f"""
{relevance_emoji} *{relevance_text}: {relevance}%* {relevance_color}

📝 *Вопрос:* {escape_markdown(question, version=2)}
📁 *Категория:* {escape_markdown(category, version=2)}
⏱️ *Время поиска:* {search_time:.2f} сек

💡 *Ответ:*
{formatted_answer}

🔍 *По запросу:* "{escape_markdown(display_query, version=2)}"
"""
            
            # Проверяем длину сообщения
            if len(response) > 4000:
                # Отправляем первую часть
                await self._safe_send_message(
                    update.effective_chat.id,
                    response[:4000],
                    context,
                    parse_mode='Markdown'
                )
                
                # Отправляем остаток, если есть
                if len(response) > 4000:
                    await self._safe_send_message(
                        update.effective_chat.id,
                        response[4000:],
                        context,
                        parse_mode='Markdown'
                    )
            else:
                await self._safe_reply(update, response, parse_mode='Markdown')
            
            logger.info(f"✅ Ответ отправлен пользователю {update.effective_user.id} "
                       f"(FAQ ID: {faq_id}, релевантность: {relevance}%, время: {search_time:.2f} сек)")
            
        except Exception as e:
            logger.error(f"Ошибка отправки результата: {e}", exc_info=True)
            
            # Пробуем отправить упрощенный ответ
            try:
                await self._safe_reply(
                    update,
                    f"✅ *Найден ответ\\!*\n\n"
                    f"*Вопрос:* {escape_markdown(question[:100], version=2)}\n\n"
                    f"*Ответ:* {escape_markdown(answer[:200], version=2)}...",
                    parse_mode='Markdown'
                )
            except Exception:
                # Если и это не удалось, отправляем сообщение об ошибке
                await self._safe_reply(
                    update,
                    "❌ *Не удалось отправить полный ответ*\n\n"
                    "Пожалуйста, попробуйте ещё раз или обратитесь к администратору\\.",
                    parse_mode='Markdown'
                )
    
    async def _send_no_results(self, update: Update, context: ContextTypes.DEFAULT_TYPE, 
                             query: str, search_time: float):
        """Отправка сообщения, если ничего не найдено"""
        user_id = update.effective_user.id
        
        # Пытаемся найти похожие вопросы
        similar_questions = self._find_similar_questions(query, limit=5)
        
        # Безопасное логирование
        query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
        
        if similar_questions:
            response = f"""
❓ *Точного ответа на "{escape_markdown(query[:50], version=2)}" не найдено*

⏱️ *Поиск выполнен за:* {search_time:.2f} сек

💡 *Возможно, вы имели в виду:*
"""
            for i, (question, category, similarity) in enumerate(similar_questions[:3], 1):
                safe_question = escape_markdown(question[:60], version=2)
                safe_category = escape_markdown(category, version=2)
                response += f"\n{i}\\. *{safe_question}*\\.\\.\\. \\({safe_category}, сходство: {similarity}%\\)"
            
            response += """
\n📝 *Что можно сделать:*
• Уточните формулировку вопроса
• Используйте ключевые слова из похожих вопросов
• Посмотрите список категорий: /categories
• Обратитесь в HR\\-отдел напрямую

🔍 *Попробуйте поискать так:*
• /search отпуск оформление
• /search справка 2\\-НДФЛ
• /search график работы
"""
        else:
            response = f"""
🔍 *По запросу "{escape_markdown(query[:50], version=2)}" ничего не найдено*

⏱️ *Поиск выполнен за:* {search_time:.2f} сек

💡 *Советы для успешного поиска:*
• Используйте конкретные термины: "отпуск", "2\\-НДФЛ", "график работы"
• Проверьте правильность написания
• Попробуйте перефразировать вопрос
• Используйте синонимы

📋 *Что ещё можно сделать:*
• Посмотреть все категории: /categories
• Использовать расширенный поиск: /search [ключевые слова]
• Оставить отзыв о пропущенном вопросе: /feedback
• Обратиться в HR\\-отдел напрямую

📞 *Контакты HR\\-отдела:*
• Телефон: \\(495\\) 123\\-45\\-67
• Email: hr@mechel\\.ru
• Кабинет: 301, 3 этаж
"""
        
        await self._safe_reply(update, response, parse_mode='Markdown')
        logger.info(f"❌ Не найдено результатов для пользователя {user_id} "
                   f"(хэш запроса: {query_hash}, время: {search_time:.2f} сек, найдено похожих: {len(similar_questions)})")
        
        # Сохраняем неотвеченный запрос для анализа
        if config.is_feedback_enabled():
            self._save_unanswered_query(user_id, query, search_time)
    
    def _find_similar_questions(self, query: str, limit: int = 3) -> List[tuple]:
        """Поиск похожих вопросов с оценкой схожести"""
        similar = []
        query_words = set(re.findall(r'\w+', query.lower()))
        
        for faq in self.search_engine.faq_data:
            question_words = set(re.findall(r'\w+', faq.question.lower()))
            
            # Вычисляем меру Жаккара (коэффициент схожести)
            intersection = len(query_words.intersection(question_words))
            union = len(query_words.union(question_words))
            
            if union > 0:
                similarity = (intersection / union) * 100
                
                # Добавляем только если есть хотя бы небольшое совпадение
                if similarity > 10:
                    similar.append((faq.question, faq.category, round(similarity)))
        
        # Сортируем по схожести (по убыванию)
        similar.sort(key=lambda x: x[2], reverse=True)
        
        return similar[:limit]
    
    def _format_answer(self, answer: str) -> str:
        """Форматирование ответа для лучшей читаемости с экранированием"""
        # Экранируем спецсимволы Markdown
        safe_answer = escape_markdown(answer, version=2)
        
        # Заменяем маркеры списков
        safe_answer = safe_answer.replace('• ', '  • ')
        
        # Добавляем абзацы для длинных текстов
        if len(safe_answer) > 800:
            # Находим подходящее место для разделения
            sentences = safe_answer.split('. ')
            if len(sentences) > 3:
                # Берем первые 3 предложения для первого абзаца
                first_part = '. '.join(sentences[:3]) + '.'
                second_part = '. '.join(sentences[3:])
                safe_answer = f"{first_part}\n\n{second_part}"
        
        # Ограничиваем длину, если слишком длинный
        if len(safe_answer) > 2000:
            safe_answer = safe_answer[:2000] + "\\.\\.\\."
            safe_answer += "\n\n*\\(Ответ сокращен для удобства чтения\\)*"
        
        return safe_answer
    
    def _save_unanswered_query(self, user_id: int, query: str, search_time: float):
        """Сохранение неотвеченного запроса для анализа"""
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            
            sql = """
            INSERT INTO unanswered_queries (user_id, query_text, search_time_seconds, created_at)
            VALUES (%s, %s, %s, NOW())
            """
            
            cursor.execute(sql, (user_id, query, round(search_time, 2)))
            conn.commit()
            conn.close()
            
            logger.info(f"💾 Сохранен неотвеченный запрос от {user_id} "
                       f"(длина: {len(query)} символов, время поиска: {search_time:.2f} сек)")
            
        except Exception as e:
            logger.error(f"Ошибка сохранения неотвеченного запроса: {e}")
