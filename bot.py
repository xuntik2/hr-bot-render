"""
МИНИМАЛЬНЫЙ РАБОЧИЙ БОТ ДЛЯ RENDER FREE
"""

import os
import sys
import asyncio
import logging
import time
from datetime import datetime
import threading

from flask import Flask, request, jsonify
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import Update

# Локальные импорты
sys.path.insert(0, '.')
from config import config
from search_engine import SearchEngine
from bot_handlers import BotCommandHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler(sys.stdout)]
)
logger = logging.getLogger(__name__)

# Flask приложение
app = Flask(__name__)

# Глобальные переменные
application = None
bot_handler = None

def init_bot():
    """Простая инициализация бота"""
    global application, bot_handler
    
    try:
        logger.info("🤖 Инициализация бота...")
        
        # Проверяем токен
        token = config.get_bot_token()
        if not token:
            logger.error("❌ Токен не найден")
            return False
        
        # Инициализируем поисковую систему
        try:
            search_engine = SearchEngine()
            search_engine.refresh_data()
            logger.info(f"✅ Поисковая система: {len(search_engine.faq_data)} FAQ")
        except Exception as e:
            logger.error(f"❌ Ошибка поисковой системы: {e}")
            return False
        
        # Создаем обработчик
        bot_handler = BotCommandHandler(search_engine)
        
        # Создаем приложение Telegram
        application = Application.builder().token(token).build()
        
        # Регистрируем обработчики
        handlers = [
            CommandHandler("start", bot_handler.handle_welcome),
            CommandHandler("help", bot_handler.handle_welcome),
            CommandHandler("categories", bot_handler.handle_categories),
            CommandHandler("search", bot_handler.handle_search),
            CommandHandler("feedback", bot_handler.handle_feedback),
            CommandHandler("stats", bot_handler.handle_stats),
        ]
        
        for handler in handlers:
            application.add_handler(handler)
        
        # Текстовые сообщения
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.handle_text_message)
        )
        
        logger.info("✅ Бот инициализирован")
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}")
        return False

async def run_bot_polling():
    """Только polling режим для Render Free"""
    try:
        await application.initialize()
        await application.start()
        
        # Используем polling
        await application.updater.start_polling(
            drop_pending_updates=True,
            poll_interval=1.0,
            timeout=20
        )
        
        logger.info("✅ Бот запущен в режиме polling")
        
        # Бесконечный цикл
        while True:
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}")
        raise

def run_flask(port):
    """Запуск Flask в отдельном потоке"""
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False
    )

async def main():
    """Основная асинхронная функция запуска"""
    if not init_bot():
        logger.error("❌ Инициализация провалена")
        return
    
    port = config.get_port()
    
    # Запускаем Flask в отдельном потоке
    flask_thread = threading.Thread(
        target=run_flask,
        args=(port,),
        daemon=True
    )
    flask_thread.start()
    
    logger.info(f"🌐 Flask запущен на порту {port}")
    logger.info(f"📞 UptimeRobot URL: http://localhost:{port}/ping")
    
    # Ждем немного чтобы Flask успел запуститься
    await asyncio.sleep(2)
    
    # Запускаем бота
    await run_bot_polling()

# ======================
# FLASK ROUTES
# ======================

@app.route('/')
def index():
    """Простая главная страница"""
    return """
    <!DOCTYPE html>
    <html>
    <head><title>HR Bot Мечел</title></head>
    <body>
        <h1>🤖 HR Bot Мечел</h1>
        <p>Telegram бот для HR-вопросов компании Мечел</p>
        <p><strong>Статус:</strong> Работает</p>
        <p><strong>Эндпоинты:</strong></p>
        <ul>
            <li><a href="/ping">/ping</a> - Для UptimeRobot</li>
            <li><a href="/health">/health</a> - Проверка здоровья</li>
        </ul>
    </body>
    </html>
    """

@app.route('/ping')
def ping():
    """Простой ping для UptimeRobot"""
    return "pong", 200

@app.route('/health')
def health():
    """Проверка здоровья"""
    try:
        # Базовая проверка
        checks = {
            'bot_initialized': application is not None,
            'database': check_database(),
            'timestamp': datetime.now().isoformat()
        }
        
        # Определяем статус
        if checks['bot_initialized']:
            status = 'healthy'
            code = 200
        else:
            status = 'unhealthy'
            code = 503
        
        return jsonify({'status': status, 'checks': checks}), code
        
    except Exception as e:
        return jsonify({'status': 'error', 'error': str(e)}), 500

def check_database():
    """Проверка подключения к БД"""
    try:
        conn = config.get_db_connection()
        if not conn:
            return {'status': 'disconnected'}
        
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM faq")
        count = cursor.fetchone()[0]
        conn.close()
        
        return {
            'status': 'connected',
            'faq_count': count,
            'meets_threshold': count >= config.MIN_FAQ_RECORDS
        }
        
    except Exception as e:
        return {'status': 'error', 'error': str(e)}

# ======================
# ЗАПУСК
# ======================

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК HR BOT МЕЧЕЛ")
    logger.info("=" * 50)
    
    # Используем asyncio для запуска
    asyncio.run(main())
