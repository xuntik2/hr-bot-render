#!/usr/bin/env python3
"""
HR-BOT ДЛЯ RENDER С ВЕБХУКАМИ
Версия 3.0 - Полный переход на Flask + Webhooks
"""

import os
import logging
from flask import Flask, request, jsonify
import telebot
from telebot.types import Update

from config import config
from search_engine import SearchEngine
from handlers import CommandHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler()
    ]
)
logger = logging.getLogger(__name__)

# Инициализация Flask приложения
app = Flask(__name__)

# Глобальные объекты (инициализируются позже)
bot = None
search_engine = None
command_handler = None

# ================== КЛАСС БОТА ==================
class HRBot:
    def __init__(self):
        # Валидация конфигурации
        if not config.validate():
            raise ValueError("Ошибка в конфигурации")
        
        # Инициализация бота
        self.bot = telebot.TeleBot(config.get_bot_token(), threaded=True)
        
        # Инициализация поискового движка
        try:
            self.search_engine = SearchEngine()
            logger.info(f"✅ Поисковый движок готов. FAQ: {len(self.search_engine.faq_data)}")
        except Exception as e:
            logger.error(f"❌ Ошибка инициализации поискового движка: {e}", exc_info=True)
            # Не падаем, пытаемся работать дальше
            self.search_engine = None
        
        # Инициализация обработчиков
        self.command_handler = CommandHandler(self.search_engine) if self.search_engine else None
        
        # Регистрация обработчиков
        self._register_handlers()
        
        # Определяем тип БД для логов
        db_type = 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite'
        logger.info(f"HR Bot инициализирован. БД: {db_type}")
    
    def _register_handlers(self):
        """Регистрация обработчиков сообщений"""
        if not self.command_handler:
            return
        
        @self.bot.message_handler(commands=['start', 'help'])
        def send_welcome(message):
            logger.info(f"📝 /start от {message.from_user.id}")
            self.command_handler.handle_welcome(message, self.bot)
        
        @self.bot.message_handler(commands=['категории', 'categories'])
        def show_categories(message):
            logger.info(f"📝 /категории от {message.from_user.id}")
            self.command_handler.handle_categories(message, self.bot)
        
        @self.bot.message_handler(commands=['поиск', 'search'])
        def search_command(message):
            logger.info(f"📝 /поиск от {message.from_user.id}: {message.text}")
            self.command_handler.handle_search(message, self.bot)
        
        @self.bot.message_handler(commands=['отзыв', 'feedback'])
        def feedback(message):
            logger.info(f"📝 /отзыв от {message.from_user.id}")
            self.command_handler.handle_feedback(message, self.bot)
        
        # АДМИНСКИЕ КОМАНДЫ
        @self.bot.message_handler(commands=['статистика', 'stats'])
        def show_stats(message):
            admin_ids = config.get_admin_ids()
            if admin_ids and message.from_user.id in admin_ids:
                try:
                    stats = self.search_engine.get_stats() if self.search_engine else {}
                    response = f"📊 Статистика:\nЗапросов: {stats.get('total_searches', 0)}\nFAQ в базе: {stats.get('total_faq', 0)}"
                    self.bot.reply_to(message, response)
                except Exception as e:
                    logger.error(f"❌ Ошибка статистики: {e}")
                    self.bot.reply_to(message, "Не удалось собрать статистику.")
            else:
                self.bot.reply_to(message, "Команда доступна только администраторам.")
        
        @self.bot.message_handler(commands=['очистить', 'clear'])
        def clear_cache(message):
            admin_ids = config.get_admin_ids()
            if admin_ids and message.from_user.id in admin_ids:
                if self.search_engine:
                    self.search_engine.refresh_data()
                    self.bot.reply_to(message, "✅ Кэш и индексы обновлены.")
                else:
                    self.bot.reply_to(message, "Поисковый движок не доступен.")
            else:
                self.bot.reply_to(message, "Команда доступна только администраторам.")
        
        # Обработка всех текстовых сообщений
        @self.bot.message_handler(func=lambda message: True)
        def handle_all_messages(message):
            logger.info(f"📝 Сообщение от {message.from_user.id}: {message.text[:100]}")
            try:
                if self.command_handler:
                    self.command_handler.handle_text_message(message, self.bot)
                else:
                    self.bot.reply_to(message, "Бот временно не готов к работе. Попробуйте позже.")
            except Exception as e:
                logger.error(f"❌ Ошибка обработки сообщения: {e}", exc_info=True)
                self.bot.reply_to(message, "Произошла ошибка при обработке вашего сообщения. Попробуйте позже.")

# ================== FLASK РОУТЫ ==================

@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной вебхук для Telegram"""
    try:
        if request.headers.get('content-type') == 'application/json':
            json_string = request.get_data().decode('utf-8')
            update = Update.de_json(json_string)
            bot.process_new_updates([update])
            return '', 200
        else:
            logger.warning("❌ Неверный content-type в вебхуке")
            return 'Bad request', 400
    except Exception as e:
        logger.error(f"❌ Ошибка в обработке вебхука: {e}", exc_info=True)
        return 'Internal server error', 500

@app.route('/health')
def health_check():
    """Health check для Render"""
    return jsonify({
        "status": "ok",
        "service": "hr-bot",
        "bot_initialized": bot is not None,
        "search_engine_ready": search_engine is not None,
        "webhook_set": check_webhook_status()
    }), 200

@app.route('/set_webhook', methods=['POST', 'GET'])
def set_webhook():
    """Ручная установка вебхука (для отладки)"""
    try:
        webhook_url = f"https://{get_webhook_domain()}/webhook"
        
        # Удаляем старый вебхук
        bot.remove_webhook()
        logger.info("✅ Старый вебхук удален")
        
        # Устанавливаем новый
        success = bot.set_webhook(
            url=webhook_url,
            max_connections=100,
            allowed_updates=['message', 'callback_query']
        )
        
        if success:
            logger.info(f"✅ Вебхук успешно установлен: {webhook_url}")
            return jsonify({
                "status": "success",
                "message": "Webhook установлен",
                "webhook_url": webhook_url
            }), 200
        else:
            logger.error("❌ Не удалось установить вебхук")
            return jsonify({
                "status": "failed",
                "message": "Не удалось установить вебхук"
            }), 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка при установке вебхука: {e}")
        return jsonify({
            "status": "error",
            "message": str(e)
        }), 500

@app.route('/delete_webhook', methods=['POST'])
def delete_webhook():
    """Удаление вебхука"""
    try:
        success = bot.remove_webhook()
        if success:
            logger.info("✅ Вебхук удален")
            return jsonify({"status": "success", "message": "Webhook удален"}), 200
        else:
            return jsonify({"status": "failed", "message": "Не удалось удалить вебхук"}), 500
    except Exception as e:
        logger.error(f"❌ Ошибка удаления вебхука: {e}")
        return jsonify({"status": "error", "message": str(e)}), 500

@app.route('/')
def index():
    """Главная страница"""
    return '''
    <!DOCTYPE html>
    <html>
    <head>
        <title>HR Bot</title>
        <meta name="viewport" content="width=device-width, initial-scale=1">
    </head>
    <body style="font-family: Arial, sans-serif; padding: 20px;">
        <h1>🤖 HR Bot</h1>
        <p>Бот для поиска информации по HR вопросам</p>
        <p><a href="/health">Health Check</a> • <a href="/set_webhook">Установить Webhook</a></p>
    </body>
    </html>
    '''

# ================== ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ==================

def get_webhook_domain():
    """Получение домена для вебхука"""
    # Сначала проверяем специальную переменную
    domain = os.getenv('WEBHOOK_DOMAIN')
    if domain:
        return domain
    
    # Потом проверяем RENDER_EXTERNAL_URL
    render_url = os.getenv('RENDER_EXTERNAL_URL')
    if render_url:
        # Убираем протокол
        if render_url.startswith('https://'):
            return render_url[8:]
        elif render_url.startswith('http://'):
            return render_url[7:]
        return render_url
    
    # Если ничего нет, используем стандартный домен для Render
    return 'hr-bot-render.onrender.com'

def check_webhook_status():
    """Проверка статуса вебхука"""
    try:
        if bot:
            info = bot.get_webhook_info()
            return bool(info.url)
        return False
    except Exception as e:
        logger.error(f"❌ Ошибка проверки вебхука: {e}")
        return False

def initialize_bot():
    """Инициализация бота и компонентов"""
    global bot, search_engine, command_handler
    
    try:
        logger.info("🔄 Инициализация HR Bot...")
        
        # Создаем экземпляр бота
        hr_bot = HRBot()
        
        # Сохраняем глобальные ссылки
        bot = hr_bot.bot
        search_engine = hr_bot.search_engine
        command_handler = hr_bot.command_handler
        
        logger.info("✅ HR Bot успешно инициализирован")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации бота: {e}", exc_info=True)
        return False

# ================== ЗАПУСК ПРИЛОЖЕНИЯ ==================

if __name__ == '__main__':
    logger.info("=" * 60)
    logger.info("🚀 ЗАПУСК HR BOT С ВЕБХУКАМИ НА RENDER")
    logger.info("=" * 60)
    
    # Инициализация бота
    if not initialize_bot():
        logger.error("❌ Не удалось инициализировать бота. Завершение.")
        exit(1)
    
    # Установка вебхука при запуске
    try:
        webhook_url = f"https://{get_webhook_domain()}/webhook"
        logger.info(f"🔄 Установка вебхука на {webhook_url}")
        
        # Удаляем старый вебхук
        bot.remove_webhook()
        
        # Устанавливаем новый
        success = bot.set_webhook(
            url=webhook_url,
            max_connections=100,
            allowed_updates=['message', 'callback_query']
        )
        
        if success:
            logger.info("✅ Вебхук успешно установлен")
        else:
            logger.warning("⚠️ Не удалось установить вебхук автоматически. Используйте /set_webhook для ручной установки.")
            
    except Exception as e:
        logger.error(f"❌ Ошибка при установке вебхука: {e}")
        # Продолжаем работу, вебхук можно установить позже через /set_webhook
    
    # Запуск Flask приложения
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Flask сервер запускается на порту {port}")
    
    # ВНИМАНИЕ: Не используем debug=True на продакшене!
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False  # Обязательно False для продакшена!
    )
