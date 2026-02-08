"""
ОБРАБОТЧИКИ КОМАНД И СООБЩЕНИЙ
С улучшенной логикой и исправлением ошибок
"""

import logging
from datetime import datetime
from typing import Optional, Tuple
import re
from config import config
from search_engine import SearchEngine

logger = logging.getLogger(__name__)

class CommandHandler:
    """Полный обработчик команд с улучшенной логикой"""
    
    def __init__(self, search_engine: SearchEngine):
        self.search_engine = search_engine
    
    def handle_welcome(self, message, bot):
        """Обработка /start"""
        user_id = message.from_user.id
        
        welcome_text = """
🤖 *Добро пожаловать в Корпоративный Бот Мечел!*

Я помогу вам найти ответы на вопросы по:
• Отпускам и больничным
• Зарплате и выплатам  
• Документам и справкам
• Работе в офисе и на производстве
• Обучению и развитию
• Социальным льготам

📋 *Основные команды:*
• /start - это сообщение
• /категории - показать все категории вопросов
• /поиск [вопрос] - поиск по базе знаний
• /отзыв - оставить обратную связь

💡 *Просто напишите ваш вопрос!*
Например: "Как оформить отпуск?"
"""
        
        # Добавляем информацию о мемах, если они включены
        if config.is_meme_enabled():
            welcome_text += """
🎭 *Мемы для поднятия настроения:*
• /мем - посмотреть случайный мем
• /мемподписка - подписаться на ежедневные мемы
"""
        
        bot.reply_to(message, welcome_text, parse_mode='Markdown')
        logger.info(f"Пользователь {user_id} запустил бота")
    
    def handle_categories(self, message, bot):
        """Обработка /категории"""
        try:
            stats = self.search_engine.get_stats()
            
            if 'categories' not in stats or not stats['categories']:
                bot.reply_to(message, "📂 Категории вопросов еще не добавлены в базу.")
                return
            
            categories = stats['categories']
            
            categories_text = "📂 *Категории вопросов:*\n\n"
            
            # Маппинг эмодзи для категории
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
                'Трудоустройство': '💼'
            }
            
            for category in sorted(categories):
                emoji = emoji_map.get(category, '📁')
                count = sum(1 for faq in self.search_engine.faq_data if faq.category == category)
                categories_text += f"{emoji} *{category}* - {count} вопросов\n"
            
            categories_text += f"\n📊 Всего категорий: {len(categories)}"
            categories_text += f"\n💾 Всего вопросов в базе: {stats.get('total_faq', 0)}"
            
            bot.reply_to(message, categories_text, parse_mode='Markdown')
            logger.info(f"Пользователь {message.from_user.id} запросил категории")
            
        except Exception as e:
            logger.error(f"Ошибка при получении категорий: {str(e)}", exc_info=True)
            bot.reply_to(message, "❌ Ошибка при получении категорий.")
    
    def handle_search(self, message, bot):
        """Обработка /поиск"""
        query = message.text
        
        # Убираем команду
        if query.startswith('/поиск'):
            query = query.replace('/поиск', '', 1).strip()
        elif query.startswith('/search'):
            query = query.replace('/search', '', 1).strip()
        
        if not query:
            bot.reply_to(
                message,
                "🔍 *Поиск по базе знаний*\n\n"
                "Использование: /поиск [ваш запрос]\n"
                "Примеры:\n• /поиск как оформить отпуск\n• /поиск справка 2-НДФЛ\n• /поиск график работы",
                parse_mode='Markdown'
            )
            return
        
        # Обрабатываем запрос
        self._process_query(message, bot, query)
    
    def handle_feedback(self, message, bot):
        """Обработка /отзыв"""
        if not config.is_feedback_enabled():
            bot.reply_to(message, "💬 Система отзывов временно отключена.")
            return
        
        feedback_text = """
📝 *Режим обратной связи*

Пожалуйста, напишите ваш отзыв, предложение или замечание по работе бота.

Требования:
• Минимум 3 символа
• Максимум 500 символов
• Конструктивная критика приветствуется

Ваш отзыв поможет улучшить бота для всех сотрудников!
"""
        bot.reply_to(message, feedback_text, parse_mode='Markdown')
    
    def handle_stats(self, message, bot):
        """Обработка /статистика"""
        admin_ids = config.get_admin_ids()
        if admin_ids and message.from_user.id not in admin_ids:
            bot.reply_to(message, "❌ Эта команда доступна только администраторам.")
            return
        
        try:
            search_stats = self.search_engine.get_stats()
            
            stats_text = f"""
📊 *Статистика HR-бота*

🔍 *Поисковая система:*
• Всего запросов: {search_stats.get('total_searches', 0)}
• Среднее время: {search_stats.get('avg_response_time', '0.000s')}
• Размер кэша: {search_stats.get('cache_size', 0)}
• Попадания в кэш: {search_stats.get('cache_hits', 0)}
• Промахи кэша: {search_stats.get('cache_misses', 0)}

📚 *База знаний:*
• Всего FAQ: {search_stats.get('total_faq', 0)}
• Категорий: {len(search_stats.get('categories', []))}
• Индекс ключевых слов: {search_stats.get('keywords_index_size', 0)}
• Индекс вопросов: {search_stats.get('question_index_size', 0)}
"""
            bot.reply_to(message, stats_text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {str(e)}", exc_info=True)
            bot.reply_to(message, "❌ Ошибка при получении статистики.")
    
    def _process_query(self, message, bot, query: str):
        """Обработка текстового запроса пользователя"""
        user_id = message.from_user.id
        
        # Проверка длины запроса
        if len(query) < 3:
            bot.reply_to(
                message,
                "❌ *Запрос слишком короткий*\n\n"
                "Пожалуйста, задайте более конкретный вопрос.\n"
                "*Примеры:*\n"
                "• 'Как оформить отпуск?'\n"
                "• 'Где получить справку 2-НДФЛ?'\n"
                "• 'Когда выплачивается зарплата?'",
                parse_mode='Markdown'
            )
            return
        
        logger.info(f"Обработка запроса от {user_id}: '{query}'")
        
        # Показываем индикатор "печатает"
        bot.send_chat_action(message.chat.id, 'typing')
        
        try:
            result = self.search_engine.search(query, user_id)
            
            if result:
                self._send_response(message, bot, query, result)
            else:
                self._handle_no_result(message, bot, query)
                
        except Exception as e:
            logger.error(f"Ошибка при поиске: {str(e)}", exc_info=True)
            bot.reply_to(
                message,
                "❌ Произошла ошибка при поиске. Попробуйте позже.",
                parse_mode='Markdown'
            )
    
    def _send_response(self, message, bot, original_query: str, result: Tuple):
        """Отправка найденного ответа"""
        try:
            faq_id, question, answer, category, score = result
            
            relevance_percent = min(int(score * 10), 100)
            
            # Определяем эмодзи в зависимости от релевантности
            if relevance_percent >= 80:
                relevance_emoji = "🟢"
            elif relevance_percent >= 50:
                relevance_emoji = "🟡"
            else:
                relevance_emoji = "🔴"
            
            response = f"""
{relevance_emoji} *Релевантность: {relevance_percent}%*
📝 *Вопрос:* {question}
📁 *Категория:* {category}

💡 *Ответ:*
{answer}

🔍 *По запросу:* "{original_query[:50]}..."
"""
            
            bot.reply_to(message, response, parse_mode='Markdown')
            
            logger.info(f"Ответ отправлен пользователю {message.from_user.id} (FAQ ID: {faq_id}, релевантность: {relevance_percent}%)")
            
        except Exception as e:
            logger.error(f"Ошибка при отправке ответа: {str(e)}", exc_info=True)
            bot.reply_to(message, "❌ Произошла ошибка при отправке ответа.")
    
    def _handle_no_result(self, message, bot, query: str):
        """Обработка случая, когда ответ не найден"""
        user_id = message.from_user.id
        
        # Ищем похожие вопросы
        similar_questions = []
        try:
            for faq in self.search_engine.faq_data:
                query_words = set(query.lower().split())
                question_words = set(faq.question.lower().split())
                
                if query_words.intersection(question_words):
                    similar_questions.append(faq)
                    if len(similar_questions) >= 3:
                        break
        except Exception as e:
            logger.error(f"Ошибка при поиске похожих вопросов: {str(e)}")
        
        if similar_questions:
            response = f"""
❓ *Точного ответа на "{query}" не найдено*

💡 *Возможно, вы имели в виду:*
"""
            for i, faq in enumerate(similar_questions[:3], 1):
                response += f"\n{i}. *{faq.question[:60]}* ({faq.category})"
            
            response += """

📝 *Что можно сделать:*
• Уточните формулировку вопроса
• Используйте другие ключевые слова  
• Посмотрите /категории
• Обратитесь в HR-отдел напрямую
"""
        else:
            response = f"""
🔍 *По запросу "{query}" ничего не найдено*

💡 *Возможные причины:*
• Вопрос слишком общий или содержит опечатки
• Такой вопрос еще не добавлен в базу знаний
• Попробуйте перефразировать вопрос

📋 *Что можно сделать:*
• Проверьте правильность написания
• Используйте более конкретные формулировки
• Посмотрите список категорий: /категории
• Используйте поиск: /поиск [ключевые слова]
"""
        
        bot.reply_to(message, response, parse_mode='Markdown')
        
        # Сохраняем неотвеченый запрос
        if config.is_feedback_enabled():
            self._save_unanswered_query(user_id, query)
        
        logger.info(f"Неотвеченный запрос от {user_id}: {query}")
    
    def _save_unanswered_query(self, user_id: int, query: str):
        """Сохранить неотвеченный запрос для анализа"""
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            placeholder = config.get_placeholder()
            
            # ИСПРАВЛЕНИЕ: Используем прямой вызов cursor.execute
            sql = f"INSERT INTO unanswered_queries (user_id, query_text) VALUES ({placeholder}, {placeholder})"
            cursor.execute(sql, (user_id, query))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Ошибка сохранения неотвеченного запроса: {str(e)}", exc_info=True)
    
    def handle_text_message(self, message, bot):
        """Обработка текстового сообщения (не команды)"""
        text = message.text.strip()
        
        if not text:
            return
        
        # Если начинается с /, но команда не распознана
        if text.startswith('/'):
            command = text.split()[0]
            response = f"""
❓ *Неизвестная команда:* `{command}`

📋 *Доступные команды:*
• /start - Начало работы
• /категории - Список категорий
• /поиск [вопрос] - Поиск по базе
• /отзыв - Оставить отзыв
"""
            admin_ids = config.get_admin_ids()
            if admin_ids and message.from_user.id in admin_ids:
                response += "• /статистика - Статистика бота\n"
            
            bot.reply_to(message, response, parse_mode='Markdown')
            return
        
        # Обрабатываем как обычный запрос
        self._process_query(message, bot, text)
