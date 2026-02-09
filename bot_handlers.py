"""
ПРОСТЫЕ ОБРАБОТЧИКИ ДЛЯ БОТА С УЛУЧШЕННОЙ ЗАЩИТОЙ ОТ СПАМА
"""

import logging
import time
from typing import Optional, Tuple
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes
from telegram.helpers import escape_markdown

from config import config
from search_engine import SearchEngine

logger = logging.getLogger(__name__)

class BotCommandHandler:
    """Обработчик команд с улучшенной защитой от спама"""
    
    def __init__(self, search_engine: SearchEngine):
        self.search_engine = search_engine
        self.user_requests = {}  # user_id -> [timestamps]
        self.max_requests_per_minute = 10
    
    async def handle_welcome(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка /start и /help"""
        user = update.effective_user
        
        welcome_text = f"""
🤖 *Добро пожаловать в HR Bot Мечел, {user.first_name}!*

Я помогу вам с кадровыми вопросами:

📅 *Отпуска и больничные*
💰 *Зарплата и выплаты*
📄 *Документы и справки*
🏢 *Работа в офисе*

💡 *Просто напишите ваш вопрос!*

*Примеры:*
• Как оформить отпуск?
• Когда выплачивается зарплата?
• Где получить справку 2-НДФЛ?
"""
        
        await update.message.reply_text(welcome_text, parse_mode='Markdown')
    
    async def handle_categories(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка /categories"""
        try:
            stats = self.search_engine.get_stats()
            
            # Получаем категории
            categories = set()
            for faq in self.search_engine.faq_data:
                if faq.category:
                    categories.add(faq.category)
            
            if not categories:
                await update.message.reply_text("📂 Категории еще не добавлены")
                return
            
            categories_text = "📂 *Категории вопросов:*\n\n"
            for category in sorted(categories):
                count = sum(1 for faq in self.search_engine.faq_data if faq.category == category)
                categories_text += f"• {category} — {count} вопросов\n"
            
            categories_text += f"\n📊 Всего категорий: {len(categories)}"
            
            await update.message.reply_text(categories_text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Ошибка /categories: {e}")
            await update.message.reply_text("❌ Ошибка при получении категорий")
    
    async def handle_search(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка /search"""
        query = update.message.text.replace('/search', '', 1).strip()
        
        if not query:
            help_text = """
🔍 *Поиск по базе знаний*

*Использование:* `/search [ваш вопрос]`

*Пример:* `/search как оформить отпуск`
"""
            await update.message.reply_text(help_text, parse_mode='Markdown')
            return
        
        await self._process_query(update, context, query)
    
    async def handle_feedback(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка /feedback"""
        feedback_text = """
📝 *Обратная связь*

Ваше мнение важно для нас!

*Что можно отправить:*
• Предложения по улучшению
• Сообщения об ошибках
• Оценку качества ответов

💡 *Просто напишите ваш отзыв в следующем сообщении!*
"""
        await update.message.reply_text(feedback_text, parse_mode='Markdown')
    
    async def handle_stats(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка /stats (только для админов)"""
        admin_ids = config.get_admin_ids()
        if not admin_ids or update.effective_user.id not in admin_ids:
            await update.message.reply_text("❌ Эта команда только для администраторов")
            return
        
        try:
            stats = self.search_engine.get_stats()
            
            stats_text = f"""
📊 *Статистика бота:*

• FAQ в базе: {stats['faq_count']}
• Категорий: {stats['categories']}
• Размер кэша: {stats['cache_size']}
• Всего поисков: {stats['total_searches']}
• Эффективность кэша: {stats['cache_hit_rate']}%
• Время: {datetime.now().strftime('%H:%M:%S')}
"""
            await update.message.reply_text(stats_text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Ошибка /stats: {e}")
            await update.message.reply_text("❌ Ошибка при получении статистики")
    
    async def handle_text_message(self, update: Update, context: ContextTypes.DEFAULT_TYPE):
        """Обработка обычных сообщений"""
        text = update.message.text.strip()
        
        if not text or len(text) < 2:
            return
        
        # Если это команда, но не распознана
        if text.startswith('/'):
            await update.message.reply_text(
                "❌ Неизвестная команда. Используйте /help для списка команд"
            )
            return
        
        # Обрабатываем как запрос
        await self._process_query(update, context, text)
    
    async def _process_query(self, update: Update, context: ContextTypes.DEFAULT_TYPE, query: str):
        """Обработка запроса пользователя с улучшенной защитой от спама"""
        user_id = update.effective_user.id
        current_time = time.time()
        
        # Очистка старых записей (старше 1 минуты)
        self.user_requests = {
            uid: timestamps for uid, timestamps in self.user_requests.items()
            if any(current_time - ts < 60 for ts in timestamps)
        }
        
        # Получаем запросы пользователя за последнюю минуту
        user_timestamps = self.user_requests.get(user_id, [])
        user_timestamps = [ts for ts in user_timestamps if current_time - ts < 60]
        
        if len(user_timestamps) >= self.max_requests_per_minute:
            await update.message.reply_text("⏱️ Слишком много запросов. Подождите минуту.")
            return
        
        # Обновляем список запросов пользователя
        user_timestamps.append(current_time)
        self.user_requests[user_id] = user_timestamps
        
        # Показываем "печатает"
        await context.bot.send_chat_action(
            chat_id=update.effective_chat.id,
            action='typing'
        )
        
        # Ищем ответ
        try:
            result = self.search_engine.search(query)
            
            if result:
                await self._send_result(update, result)
            else:
                await self._send_no_result(update, query)
                
        except Exception as e:
            logger.error(f"Ошибка поиска: {e}")
            await update.message.reply_text("❌ Ошибка при поиске ответа")
    
    async def _send_result(self, update: Update, result: Tuple):
        """Отправка найденного ответа"""
        try:
            faq_id, question, answer, category, score = result
            
            response = f"""
✅ *Найден ответ!* ({min(score, 100)}%)

*Вопрос:* {escape_markdown(question, version=2)}
*Категория:* {escape_markdown(category, version=2)}

*Ответ:*
{escape_markdown(answer, version=2)}
"""
            await update.message.reply_text(response, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Ошибка отправки результата: {e}")
            await update.message.reply_text("✅ Найден ответ! (не удалось отформатировать)")
    
    async def _send_no_result(self, update: Update, query: str):
        """Сообщение если ничего не найдено"""
        response = f"""
❓ *По запросу "{escape_markdown(query[:50], version=2)}" не найдено точного ответа*

💡 *Что можно сделать:*
• Уточните формулировку
• Используйте ключевые слова
• Посмотрите категории: /categories
• Обратитесь в HR-отдел напрямую
"""
        await update.message.reply_text(response, parse_mode='Markdown')
