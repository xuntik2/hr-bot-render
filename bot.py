#!/usr/bin/env python3
"""
HR-BOT ДЛЯ RENDER (ИСПРАВЛЕННАЯ И ГОТОВАЯ ВЕРСИЯ)
Web Service с health-эндпоинтом, без зависимостей от отсутствующих модулей.
"""

import logging
import time
import threading
import os
from datetime import datetime, timedelta
from http.server import HTTPServer, BaseHTTPRequestHandler

import telebot
from apscheduler.schedulers.background import BackgroundScheduler

from config import config
from search_engine import SearchEngine
from handlers import CommandHandler
# Импорт MemeHandler убран, так как его нет. Если он нужен, раскомментируйте.
# from meme_handler import MemeHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# ================== ПРОСТОЙ HEALTH СЕРВЕР ДЛЯ RENDER ==================
class HealthHandler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/health':
            self.send_response(200)
            self.send_header('Content-type', 'text/plain')
            self.end_headers()
            self.wfile.write(b'OK')
        else:
            self.send_response(404)
            self.end_headers()
    def log_message(self, format, *args):
        pass

def run_health_server():
    """Запуск HTTP-сервера для проверки здоровья (обязательно для Render Web Service)"""
    # Render сам предоставляет порт в переменной PORT
    port = int(os.environ.get('PORT', 10000))
    server = HTTPServer(('0.0.0.0', port), HealthHandler)
    logger.info(f'✅ Health-сервер запущен на порту {port} (для Render Web Service)')
    server.serve_forever()

# ================== ОСНОВНОЙ КЛАСС БОТА ==================
class HRBot:
    def __init__(self):
        # Валидация конфигурации
        if not config.validate():
            raise ValueError("Ошибка в конфигурации")
        
        self.bot = telebot.TeleBot(config.get_bot_token(), threaded=True)
        
        # Инициализация поискового движка (база создается при первом поиске)
        try:
            self.search_engine = SearchEngine()
            logger.info(f"✅ Поисковый движок готов. FAQ: {len(self.search_engine.faq_data)}")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации поискового движка: {e}", exc_info=True)
            # Не падаем, пытаемся работать дальше
            self.search_engine = None
        
        # Инициализация обработчиков
        self.command_handler = CommandHandler(self.search_engine) if self.search_engine else None
        
        # Инициализация планировщика (без мемов)
        self.scheduler = BackgroundScheduler()
        # Здесь можно добавить задачи, например, ежедневную статистику
        # self.scheduler.start()
        
        # Регистрация обработчиков
        self._register_handlers()
        
        # Определяем тип БД для логов (исправление по замечанию)
        db_type = 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite'
        logger.info(f"HR Bot инициализирован. БД: {db_type}")
    
    def _register_handlers(self):
        """Регистрация обработчиков сообщений"""
        if not self.command_handler:
            return
        
        @self.bot.message_handler(commands=['start', 'help'])
        def send_welcome(message):
            self.command_handler.handle_welcome(message, self.bot)
        
        @self.bot.message_handler(commands=['категории', 'categories'])
        def show_categories(message):
            self.command_handler.handle_categories(message, self.bot)
        
        @self.bot.message_handler(commands=['поиск', 'search'])
        def search_command(message):
            self.command_handler.handle_search(message, self.bot)
        
        @self.bot.message_handler(commands=['отзыв', 'feedback'])
        def feedback(message):
            self.command_handler.handle_feedback(message, self.bot)
        
        # АДМИНСКИЕ КОМАНДЫ (только для пользователей из ADMIN_IDS)
        @self.bot.message_handler(commands=['статистика', 'stats'])
        def show_stats(message):
            # Простая проверка на админа
            admin_ids = config.get_admin_ids()
            if admin_ids and message.from_user.id in admin_ids:
                try:
                    stats = self.search_engine.get_stats() if self.search_engine else {}
                    response = f"📊 Статистика:\nЗапросов: {stats.get('total_searches', 0)}\nFAQ в базе: {stats.get('total_faq', 0)}"
                    self.bot.reply_to(message, response)
                except:
                    self.bot.reply_to(message, "Не удалось собрать статистику.")
            else:
                self.bot.reply_to(message, "Команда доступна только администраторам.")
        
        @self.bot.message_handler(commands=['очистить', 'clear'])
        def clear_cache(message):
            admin_ids = config.get_admin_ids()
            if admin_ids and message.from_user.id in admin_ids:
                if self.search_engine:
                    self.search_engine.refresh_data()
                    self.bot.reply_to(message, "Кэш и индексы обновлены.")
                else:
                    self.bot.reply_to(message, "Поисковый движок не доступен.")
            else:
                self.bot.reply_to(message, "Команда доступна только администраторам.")
        
        # Обработка всех текстовых сообщений
        @self.bot.message_handler(func=lambda message: True)
        def handle_all_messages(message):
            if self.command_handler:
                self.command_handler.handle_text_message(message, self.bot)
            else:
                self.bot.reply_to(message, "Бот временно не готов к работе. Попробуйте позже.")
    
    def run_bot(self):
        """Основной цикл работы бота"""
        logger.info("🚀 Запуск поллинга Telegram бота...")
        restart_delay = 10
        
        while True:
            try:
                # Бесконечный опрос серверов Telegram
                self.bot.infinity_polling(timeout=30, long_polling_timeout=5)
            except telebot.apihelper.ApiTelegramException as e:
                # Обработка ошибки 409 (конфликт) - ждем подольше
                if "409" in str(e):
                    logger.error(f"Конфликт 409. Убедитесь, что бот запущен только в одном месте. Ждем {restart_delay*2} сек.")
                    time.sleep(restart_delay * 2)
                else:
                    logger.error(f"Ошибка Telegram API: {e}. Перезапуск через {restart_delay} сек.")
                    time.sleep(restart_delay)
            except Exception as e:
                logger.error(f"Неизвестная ошибка: {e}. Перезапуск через {restart_delay} сек.")
                time.sleep(restart_delay)

# ================== ГЛАВНАЯ ФУНКЦИЯ ==================
def main():
    """Точка входа: запускает health-сервер и бота в разных потоках"""
    logger.info("=" * 60)
    logger.info("🤖 ЗАПУСК HR BOT НА RENDER")
    logger.info("=" * 60)
    
    # Поток для health-сервера (обязательно для Render Web Service)
    health_thread = threading.Thread(target=run_health_server, daemon=True)
    health_thread.start()
    logger.info("Health-сервер запущен в отдельном потоке.")
    
    # Создаем и запускаем бота в основном потоке
    try:
        bot_instance = HRBot()
        bot_instance.run_bot()  # Этот метод работает бесконечно
    except Exception as e:
        logger.critical(f"Критическая ошибка при создании бота: {e}")
        raise

if __name__ == '__main__':
    main()
