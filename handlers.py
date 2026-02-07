"""
ПОЛНЫЕ ОБРАБОТЧИКИ КОМАНД И СООБЩЕНИЙ
С улучшенной логикой и поддержкой PostgreSQL
"""

import logging
from datetime import datetime
import sqlite3
from typing import Optional, Tuple, List, Dict
import re

from config import config
from search_engine import SearchEngine
from utils import check_spam, format_answer, is_valid_query, truncate_text, get_user_friendly_time

logger = logging.getLogger(__name__)

class CommandHandler:
    """Полный обработчик команд с улучшенной логикой"""
    
    def __init__(self, search_engine: SearchEngine):
        self.search_engine = search_engine
    
    def handle_welcome(self, message, bot):
        """Обработка /start"""
        user_id = message.from_user.id
        
        welcome_text = """
🤖 *Добро пожаловать в HR-бот компании Мечел!*

Я помогу вам с ответами на вопросы по:
• Графику работы и отпускам
• Больничным и зарплате
• Льготам и социальному пакету
• Офисной инфраструктуре
• Документам и справкам
• IT-проблемам

📋 *Основные команды:*
• /start - это сообщение
• /категории - показать все категории вопросов
• /поиск [вопрос] - поиск по базе знаний
• /отзыв - оставить обратную связь

💡 *Как пользоваться:*
Просто напишите свой вопрос, например:
• "Как оформить отпуск?"
• "Где получить справку 2-НДФЛ?"
• "Когда зарплата?"

🎯 *Умные возможности:*
• Я помню контекст наших разговоров
• Исправляю опечатки в ваших запросах
• Ищу даже если вопрос сформулирован не точно
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
                'Документы': '📄',
                'IT': '💻',
                'Общее': '📋',
                'Зарплата': '💰',
                'Больничный': '🏥',
                'Доступ': '🔑',
                'Обучение': '🎓',
                'Льготы': '🎁',
                'Пропуска': '🔐'
            }
            
            for category in sorted(categories):
                emoji = emoji_map.get(category, '📁')
                count = 0
                for faq in self.search_engine.faq_data:
                    if faq.category == category:
                        count += 1
                
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

*Требования:*
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
• Использовано контекста: {search_stats.get('context_searches', 0)}
• Среднее время: {search_stats.get('avg_response_time', '0.000s')}
• Эффективность контекста: {search_stats.get('context_usage_rate', '0%')}

💾 *Кэш и индексы:*
• Размер кэша: {search_stats.get('cache_size', 0)}
• Попадания в кэш: {search_stats.get('cache_hits', 0)}
• Промахи кэша: {search_stats.get('cache_misses', 0)}
• Индекс ключевых слов: {search_stats.get('keywords_index_size', 0)}
• Индекс вопросов: {search_stats.get('question_index_size', 0)}

📚 *База знаний:*
• Всего FAQ: {search_stats.get('total_faq', 0)}
• Категорий: {len(search_stats.get('categories', []))}
"""
            
            bot.reply_to(message, stats_text, parse_mode='Markdown')
            
        except Exception as e:
            logger.error(f"Ошибка при получении статистики: {str(e)}", exc_info=True)
            bot.reply_to(message, "❌ Ошибка при получении статистики.")
    
    def handle_clear_cache(self, message, bot):
        """Обработка /очистить - только для администраторов"""
        admin_ids = config.get_admin_ids()
        if not admin_ids or message.from_user.id not in admin_ids:
            bot.reply_to(message, "❌ Эта команда доступна только администраторов.")
            return
        
        try:
            self.search_engine.refresh_data()
            
            bot.reply_to(
                message,
                "✅ Данные поиска успешно обновлены!\n\n"
                "• Очищен кэш поиска\n"
                "• Перестроены поисковые индексы\n"
                "• Обновлены данные из БД",
                parse_mode='Markdown'
            )
            
            logger.info(f"Данные поиска обновлены администратором {message.from_user.id}")
            
        except Exception as e:
            logger.error(f"Ошибка при обновлении данных: {str(e)}", exc_info=True)
            bot.reply_to(message, "❌ Ошибка при обновлении данных.")
    
    def _is_feedback_message(self, text: str) -> bool:
        """Определить, является ли сообщение отзывом/жалобой"""
        text_lower = text.lower()
        
        feedback_keywords = [
            'бот не', 'бот глючит', 'бот тормозит', 'бот сломал',
            'команда не актив', 'не кликается', 'не нажимается',
            'ошибка бота', 'баг бота', 'глюк бота',
            'неправильно работает', 'некорректно работает',
            'исправь бота', 'почини бота', 'сломался бот',
            'интерфейс бота', 'кнопки бота', 'сообщения бота'
        ]
        
        for keyword in feedback_keywords:
            if keyword in text_lower:
                logger.debug(f"Найдено ключевое слово отзыва: '{keyword}'")
                return True
        
        bot_related = any(word in text_lower for word in ['бот', 'боту', 'бота'])
        not_question = '?' not in text_lower and not self._is_likely_question(text)
        
        if bot_related and not_question and len(text) > 10:
            return True
        
        return False
    
    def _is_likely_question(self, text: str) -> bool:
        """Определить, является ли текст вероятным вопросом"""
        text_lower = text.lower()
        
        question_patterns = [
            r'^как\s+', r'^где\s+', r'^когда\s+', r'^что\s+', r'^кто\s+', 
            r'^почему\s+', r'^зачем\s+', r'^сколько\s+', r'^какой\s+',
            r'^какую\s+', r'^какие\s+', r'^чей\s+', r'^кому\s+', r'^кого\s+',
            r'^чьи\s+', r'^насколько\s+', r'^откуда\s+', r'^куда\s+',
            r'можно ли', r'нужно ли', r'следует ли', r'возможно ли',
            r'как получить', r'как оформить', r'как подключить', r'как сделать'
        ]
        
        for pattern in question_patterns:
            if re.search(pattern, text_lower):
                return True
        
        if '?' in text:
            return True
        
        if len(text.split()) <= 4:
            common_queries = ['отпуск', 'зарплата', 'больничный', 'пропуск', 
                            'почта', 'справка', 'документ', 'обучение', 'офис',
                            'льготы', 'отпускные', 'больничный лист']
            if any(query in text_lower for query in common_queries):
                return True
        
        return False
    
    def _process_query(self, message, bot, query: str):
        """Обработка текстового запроса пользователя"""
        user_id = message.from_user.id
        
        # Проверяем валидность запроса
        if not is_valid_query(query) or len(query) < 3:
            bot.reply_to(
                message,
                "❌ *Запрос слишком короткий или неясный*\n\n"
                "Пожалуйста, задайте более конкретный вопрос.\n"
                "*Примеры:*\n"
                "• 'Как оформить отпуск?'\n"
                "• 'Где получить справку 2-НДФЛ?'\n"
                "• 'Когда выплачивается зарплата?'\n"
                "• 'Как подключить корпоративную почту?'",
                parse_mode='Markdown'
            )
            return
        
        # Проверка на спам
        is_spam, wait_time = check_spam(user_id)
        if is_spam:
            bot.reply_to(
                message,
                f"⚠️ *Слишком частые запросы!*\n\n"
                f"Пожалуйста, подождите {get_user_friendly_time(wait_time)} "
                f"перед следующим запросом.\n"
                f"(Защита от спама: {config.get_rate_limit_seconds()} сек между запросами)",
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
                "❌ Произошла ошибка при поиске. Попробуйте позже или обратитесь в IT-отдел.",
                parse_mode='Markdown'
            )
    
    def _send_response(self, message, bot, original_query: str, result: Tuple):
        """Отправка найденного ответа с проверкой релевантности"""
        try:
            faq_id, question, answer, category, score = result
            
            relevance_percent = min(int(score * 100), 100)
            
            if relevance_percent < 30:
                logger.warning(f"Низкая релевантность ({relevance_percent}%) для запроса '{original_query}'")
                
                response = f"""
⚠️ *По вашему запросу найдена информация с низкой релевантности ({relevance_percent}%)*

📝 *Возможно, вы имели в виду:* {question}
📁 *Категория:* {category}

💡 *Ответ:*
{answer}

💬 *Совет:* Попробуйте уточнить запрос или используйте /поиск [точный запрос]
"""
            else:
                relevance_emoji = "🔴" if relevance_percent < 50 else "🟡" if relevance_percent < 80 else "🟢"
                
                response = f"""
{relevance_emoji} *Релевантность: {relevance_percent}%*

📝 *Вопрос:* {question}
📁 *Категория:* {category}

💡 *Ответ:*
{answer}

🔍 *По запросу:* "{truncate_text(original_query, 50)}"
"""
            
            formatted_response = format_answer(response)
            bot.reply_to(message, formatted_response, parse_mode='Markdown')
            
            logger.info(f"Ответ отправлен пользователю {message.from_user.id} (FAQ ID: {faq_id}, релевантность: {relevance_percent}%)")
            
            if relevance_percent >= 50:
                self._show_related_questions(message, bot, category, faq_id)
            
        except Exception as e:
            logger.error(f"Ошибка при отправке ответа: {str(e)}", exc_info=True)
            bot.reply_to(message, "❌ Произошла ошибка при отправке ответа.")
    
    def _show_related_questions(self, message, bot, category: str, current_faq_id: int):
        """Показать связанные вопросы"""
        try:
            related_questions = []
            
            for faq in self.search_engine.faq_data:
                if faq.category == category and faq.id != current_faq_id:
                    related_questions.append(faq.question)
                    if len(related_questions) >= 3:
                        break
            
            if related_questions:
                response = "\n\n🤔 *Связанные вопросы:*\n"
                for i, question in enumerate(related_questions, 1):
                    response += f"{i}. {question}\n"
                
                response += "\n💡 *Совет:* Я помню контекст. Можете задать уточняющий вопрос!"
                
                bot.send_message(message.chat.id, response, parse_mode='Markdown')
                
        except Exception as e:
            logger.error(f"Ошибка при поиске связанных вопросов: {str(e)}")
    
    def _handle_no_result(self, message, bot, query: str):
        """Обработка случая, когда ответ не найден"""
        user_id = message.from_user.id
        
        similar_questions = []
        try:
            for faq in self.search_engine.faq_data:
                query_words = set(query.lower().split())
                question_words = set(faq.question.lower().split())
                
                if query_words.intersection(question_words):
                    similar_questions.append(faq)
                    if len(similar_questions) >= 5:
                        break
        except Exception as e:
            logger.error(f"Ошибка при поиске похожих вопросов: {str(e)}")
        
        if similar_questions:
            response = f"""
❓ *Точного ответа на "{query}" не найдено*

💡 *Возможно, вы имели в виду:*
"""
            
            for i, faq in enumerate(similar_questions[:3], 1):
                response += f"\n{i}. *{truncate_text(faq.question, 60)}* ({faq.category})"
            
            response += "\n\n📝 *Что можно сделать:*"
            response += "\n• Уточните формулировку вопроса"
            response += "\n• Используйте другие ключевые слова"
            response += "\n• Посмотрите /категории"
            response += "\n• Обратитесь в HR-отдел напрямую"
            
        else:
            response = f"""
🔍 *По запросу "{query}" ничего не найдено*

💡 *Возможные причины:*
• Вопрос слишком общий или содержит опечатки
• Такой вопрос еще не добавлен в базу знаний
• Попробуйте перефразировать вопрос

📋 *Что можно сделать:*
1. Проверьте правильность написания
2. Используйте более конкретные формулировки
3. Посмотрите список категорий: /категории
4. Используйте поиск: /поиск [ключевые слова]
"""
        
        bot.reply_to(message, format_answer(response), parse_mode='Markdown')
        
        # Сохраняем неотвеченный запрос
        if config.is_feedback_enabled():
            self._save_unanswered_query(user_id, query)
        
        logger.info(f"Неотвеченный запрос от {user_id}: {query}")
    
    def _save_unanswered_query(self, user_id: int, query: str):
        """Сохранить неотвеченный запрос для анализа"""
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            
            placeholder = config.get_placeholder()
            sql = f"INSERT INTO unanswered_queries (user_id, query_text) VALUES ({placeholder}, {placeholder})"
            
            # ИСПРАВЛЕНИЕ: Заменяем config.execute_query на cursor.execute
            cursor.execute(sql, (user_id, query))
            
            conn.commit()
            conn.close()
            
        except Exception as e:
            logger.error(f"Ошибка сохранения неотвеченного запроса: {str(e)}")
    
    def handle_unknown_command(self, message, bot):
        """Обработка неизвестной команда"""
        command = message.text.split()[0]
        
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
            response += "• /очистить - Обновить данные поиска\n"
        
        if config.is_meme_enabled():
            response += "• /мем - Посмотреть мем\n"
            response += "• /мемподписка - Подписаться на мемы\n"
            response += "• /мемотписка - Отписаться от мемов\n"
        
        bot.reply_to(message, response, parse_mode='Markdown')
    
    def handle_text_message(self, message, bot):
        """Обработка текстового сообщения (не команды)"""
        text = message.text.strip()
        
        if not text:
            return
        
        if text.startswith('/'):
            self.handle_unknown_command(message, bot)
            return
        
        if self._is_feedback_message(text):
            logger.info(f"Сообщение распознано как отзыв/жалоба: '{text[:50]}...'")
            
            if config.is_feedback_enabled():
                self._save_feedback(message, bot, text)
            else:
                bot.reply_to(
                    message,
                    "💬 *Спасибо за ваше сообщение!*\n\n"
                    "К сожалению, система отзывов временно отключена.\n"
                    "Если у вас есть срочная проблема, обратитесь в IT-отдел.",
                    parse_mode='Markdown'
                )
            return
        
        if not self._is_likely_question(text):
            logger.info(f"Сообщение не похоже на вопрос: '{text[:50]}...'")
            
            bot.reply_to(
                message,
                "🤔 *Не совсем понял ваш запрос*\n\n"
                "*Если у вас вопрос*, перефразируйте его, например:\n"
                "• 'Как оформить отпуск?'\n"
                "• 'Где получить справку 2-НДФЛ?'\n"
                "• 'Когда зарплата?'\n\n"
                "*Если это отзыв о работе бота*, используйте команду /отзыв\n\n"
                "*Если нужна помощь*, используйте /start или /help",
                parse_mode='Markdown'
            )
            return
        
        self._process_query(message, bot, text)
    
    def _save_feedback(self, message, bot, feedback_text: str):
        """Сохранить отзыв пользователя"""
        try:
            user_id = message.from_user.id
            
            conn = config.get_db_connection()
            cursor = conn.cursor()
            
            placeholder = config.get_placeholder()
            sql = f"INSERT INTO feedback (user_id, comment) VALUES ({placeholder}, {placeholder})"
            
            # ИСПРАВЛЕНИЕ: Заменяем config.execute_query на cursor.execute
            cursor.execute(sql, (user_id, feedback_text))
            
            conn.commit()
            conn.close()
            
            bot.reply_to(
                message,
                "✅ *Спасибо за ваш отзыв!*\n\n"
                "Ваше мнение очень важно для нас и поможет улучшить бота.",
                parse_mode='Markdown'
            )
            
            logger.info(f"Получен отзыв от {user_id}")
            
        except Exception as e:
            logger.error(f"Ошибка сохранения отзыва: {str(e)}", exc_info=True)
            bot.reply_to(
                message,
                "❌ Произошла ошибка при сохранении отзыва. Пожалуйста, попробуйте позже.",
                parse_mode='Markdown'
            )
