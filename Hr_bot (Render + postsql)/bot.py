#!/usr/bin/env python3
"""
ПОЛНЫЙ HR-BOT ДЛЯ RENDER + POSTGRESQL
С периодическими задачами и улучшенной обработкой
"""

import logging
import sqlite3
import time
import threading
from datetime import datetime, timedelta

import telebot
from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger

from config import config
from search_engine import SearchEngine
from handlers import CommandHandler
from meme_handler import MemeHandler
from create_database import create_database

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.FileHandler('hr_bot.log', encoding='utf-8'),
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

class HRBot:
    """Полный класс бота с обновленным поисковым движком"""
    
    def __init__(self):
        # Валидация конфигурации
        if not config.validate():
            raise ValueError("Ошибка в конфигурации")
        
        self.bot = telebot.TeleBot(config.get_bot_token(), threaded=True)
        
        # Создаем/проверяем базу данных
        try:
            logger.info("🔧 Проверяем базу данных...")
            create_database()
        except Exception as e:
            logger.warning(f"⚠️ Не удалось проверить базу: {e}")
        
        # Инициализация поискового движка
        try:
            self.search_engine = SearchEngine()
            logger.info(f"✅ Поисковый движок инициализирован: {len(self.search_engine.faq_data)} FAQ загружено")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации поискового движка: {e}", exc_info=True)
            raise
        
        # Инициализация обработчиков
        self.command_handler = CommandHandler(self.search_engine)
        
        # Если мемы включены, инициализируем мем  хендлер
        self.meme_handler = None
        if config.is_meme_enabled():
            self.meme_handler = MemeHandler()
        
        # Статистика бота
        self.stats = {
            'total_queries': 0,
            'successful_queries': 0,
            'failed_queries': 0,
            'start_time': datetime.now(),
            'users': set(),
            'active_sessions': 0
        }
        
        # Планировщик для рассылки мемов
        self.scheduler = BackgroundScheduler()
        if config.is_meme_enabled() and self.meme_handler:
            self._setup_scheduler()
        
        # Регистрация обработчиков
        self._register_handlers()
        
        logger.info("HR Bot инициализирован с PostgreSQL")
        logger.info(f"Конфигурация: БД={'PostgreSQL' if config.is_postgresql() else 'SQLite'}, Мемы={'включены' if config.is_meme_enabled() else 'выключены'}")
    
    def _setup_scheduler(self):
        """Настройка планировщика для мемов"""
        if not config.is_meme_enabled() or not self.meme_handler:
            logger.info("Мемы отключены в конфигурации, планировщик не запущен")
            return
        
        def send_memes():
            try:
                logger.info("Запуск ежедневной рассылки мемов...")
                self.meme_handler.send_daily_memes(self.bot)
            except Exception as e:
                logger.error(f"Ошибка в рассылке мемов: {e}", exc_info=True)
        
        # Ежедневная рассылка в 10:00
        self.scheduler.add_job(
            send_memes,
            CronTrigger(hour=10, minute=0),
            id='daily_meme_delivery',
            replace_existing=True
        )
        
        self.scheduler.start()
        logger.info("Планировщик запущен (ежедневно в 10:00)")
    
    def _register_handlers(self):
        """Регистрация обработчиков сообщений"""
        
        @self.bot.message_handler(commands=['start', 'help'])
        def send_welcome(message):
            self.command_handler.handle_welcome(message, self.bot)
            self._update_stats(message, success=True)
        
        @self.bot.message_handler(commands=['категории', 'categories'])
        def show_categories(message):
            self.command_handler.handle_categories(message, self.bot)
            self._update_stats(message, success=True)
        
        @self.bot.message_handler(commands=['поиск', 'search'])
        def search_command(message):
            self.command_handler.handle_search(message, self.bot)
        
        @self.bot.message_handler(commands=['отзыв', 'feedback'])
        def feedback(message):
            self.command_handler.handle_feedback(message, self.bot)
            self._update_stats(message, success=True)
        
        # Команды для мемов (только если включены в конфиге)
        if config.is_meme_enabled() and self.meme_handler:
            @self.bot.message_handler(commands=['мем', 'мем_дня'])
            def send_meme(message):
                self.meme_handler.handle_meme(message, self.bot)
                self._update_stats(message, success=True)
            
            @self.bot.message_handler(commands=['мемподписка'])
            def subscribe_meme(message):
                self.meme_handler.handle_subscribe(message, self.bot)
                self._update_stats(message, success=True)
            
            @self.bot.message_handler(commands=['мемотписка'])
            def unsubscribe_meme(message):
                self.meme_handler.handle_unsubscribe(message, self.bot)
                self._update_stats(message, success=True)
        
        # Команды для администраторов
        @self.bot.message_handler(commands=['статистика', 'stats'])
        def show_stats(message):
            self.command_handler.handle_stats(message, self.bot)
            self._update_stats(message, success=True)
        
        @self.bot.message_handler(commands=['очистить', 'clear'])
        def clear_cache(message):
            self.command_handler.handle_clear_cache(message, self.bot)
            self._update_stats(message, success=True)
        
        @self.bot.message_handler(func=lambda message: True)
        def handle_all_messages(message):
            """Обработка всех текстовых сообщений"""
            try:
                self.stats['total_queries'] += 1
                self.stats['users'].add(message.from_user.id)
                
                logger.info(f"Запрос от {message.from_user.id}: {message.text[:50]}...")
                
                self.command_handler.handle_text_message(message, self.bot)
                
            except Exception as e:
                logger.error(f"Ошибка обработки сообщения: {e}", exc_info=True)
                self.bot.reply_to(
                    message,
                    "❌ Произошла ошибка при обработке вашего сообщения. Пожалуйста, попробуйте позже.",
                    parse_mode='Markdown'
                )
    
    def _update_stats(self, message, success: bool):
        """Обновление статистики"""
        if success:
            self.stats['successful_queries'] += 1
        else:
            self.stats['failed_queries'] += 1
    
    def run(self):
        """Запуск бота с автоматическим перезапуском"""
        logger.info("🚀 Запуск HR Bot на Render...")
        
        # Запуск периодических задач в отдельном потоке
        periodic_thread = threading.Thread(target=self._run_periodic_tasks, daemon=True)
        periodic_thread.start()
        
        logger.info("✅ HR Bot запущен и готов к работе!")
        logger.info("📊 Статистика системы:")
        logger.info(f"  • FAQ в базе: {len(self.search_engine.faq_data)}")
        logger.info(f"  • Категории: {len(self.search_engine.category_index)}")
        
        # Основной цикл с автоматическим перезапуском
        restart_delay = 10
        
        while True:
            try:
                self.bot.infinity_polling(timeout=30, long_polling_timeout=5)
                
            except KeyboardInterrupt:
                logger.info("Бот остановлен пользователем")
                if hasattr(self, 'scheduler') and self.scheduler.running:
                    self.scheduler.shutdown()
                break
                
            except Exception as e:
                logger.error(f"Ошибка polling: {e}", exc_info=True)
                logger.info(f"Повторный запуск через {restart_delay} секунд...")
                time.sleep(restart_delay)
    
    def _run_periodic_tasks(self):
        """Периодические задачи"""
        logger.info("Периодические задачи запущены")
        
        while True:
            try:
                sleep_seconds = config.SLEEP_INTERVAL_HOURS * 3600
                time.sleep(sleep_seconds)
                
                logger.info("Выполнение периодических задач...")
                
                # 1. Очистка старых записей в БД
                self._cleanup_old_records()
                
                # 2. Обновление данных поискового движка
                self.search_engine.refresh_data()
                
                # 3. Проверка состояния системы
                self._check_system_health()
                
                logger.info(f"Периодические задачи завершены. Следующий запуск через {sleep_seconds/3600} часов")
                
            except Exception as e:
                logger.error(f"Ошибка в периодических задачах: {e}", exc_info=True)
                time.sleep(60)
    
    def _cleanup_old_records(self):
        """Очистка старых записей в БД"""
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            
            # Очистка старых отзывов
            cutoff_date = (datetime.now() - timedelta(days=config.CLEANUP_OLDER_THAN_DAYS)).isoformat()
            
            placeholder = config.get_placeholder()
            cursor.execute(f"DELETE FROM feedback WHERE timestamp < {placeholder}", (cutoff_date,))
            deleted_feedback = cursor.rowcount
            
            # Очистка старых неотвеченных запросов
            cursor.execute(f"DELETE FROM unanswered_queries WHERE timestamp < {placeholder}", (cutoff_date,))
            deleted_queries = cursor.rowcount
            
            conn.commit()
            conn.close()
            
            if deleted_feedback > 0 or deleted_queries > 0:
                logger.info(f"Очищено {deleted_feedback} отзывов и {deleted_queries} запросов")
                    
        except Exception as e:
            logger.error(f"Ошибка при очистке записей: {e}", exc_info=True)
    
    def _check_system_health(self):
        """Проверка состояния системы"""
        try:
            search_stats = self.search_engine.get_stats()
            
            total_faq = search_stats.get('total_faq', 0)
            
            health_status = "🟢 Здоров"
            if total_faq == 0:
                health_status = "🔴 Критический: нет FAQ в базе"
            
            logger.info(f"Проверка здоровья: {health_status}")
            logger.info(f"  • FAQ: {total_faq}")
            logger.info(f"  • Запросов: {search_stats.get('total_searches', 0)}")
            logger.info(f"  • Пользователей: {len(self.stats['users'])}")
            
        except Exception as e:
            logger.error(f"Ошибка проверки состояния системы: {e}", exc_info=True)

def main():
    """Основная функция"""
    try:
        logger.info("=" * 60)
        logger.info("🚀 Запуск HR Bot версии для Render + PostgreSQL")
        logger.info("📅 " + datetime.now().strftime("%Y-%m-%d %H:%M:%S"))
        logger.info("=" * 60)
        
        bot = HRBot()
        bot.run()
        
    except Exception as e:
        logger.error(f"Ошибка при запуске: {e}", exc_info=True)
        raise

if __name__ == '__main__':
    main()