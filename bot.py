"""
МИНИМАЛЬНЫЙ РАБОЧИЙ БОТ ДЛЯ RENDER FREE
Версия 2.0 - Исправленная и оптимизированная для бесплатного тарифа
"""

import os
import sys
import asyncio
import logging
import time
import threading
from datetime import datetime
from typing import Optional

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
application: Optional[Application] = None
bot_handler: Optional[BotCommandHandler] = None
shutdown_event = threading.Event()

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
        logger.error(f"❌ Ошибка инициализации: {e}", exc_info=True)
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
        
        # Бесконечный цикл с возможностью остановки
        while not shutdown_event.is_set():
            await asyncio.sleep(1)
            
    except Exception as e:
        logger.error(f"❌ Ошибка polling: {e}", exc_info=True)
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
    bot_status = "🟢 Активен" if application is not None else "🔴 Неактивен"
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>HR Bot Мечел</title>
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{
            font-family: Arial, sans-serif;
            margin: 40px;
            line-height: 1.6;
        }}
        .status {{
            padding: 15px;
            margin: 20px 0;
            border-radius: 5px;
            background: #f0f0f0;
        }}
        .healthy {{
            background: #d4edda;
            border-left: 4px solid #28a745;
        }}
        .unhealthy {{
            background: #f8d7da;
            border-left: 4px solid #dc3545;
        }}
        h1 {{
            color: #333;
        }}
        ul {{
            list-style: none;
            padding: 0;
        }}
        li {{
            margin: 10px 0;
        }}
    </style>
</head>
<body>
    <h1>🤖 HR Bot Мечел</h1>
    
    <div class="status {'healthy' if application is not None else 'unhealthy'}">
        <strong>Статус:</strong> {bot_status}<br>
        <strong>Время:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
        <strong>Режим:</strong> Polling (бесплатный тариф)
    </div>
    
    <div class="status">
        <strong>Эндпоинты:</strong>
        <ul>
            <li>• <a href="/ping">/ping</a> - Для UptimeRobot (keep-alive)</li>
            <li>• <a href="/health">/health</a> - Проверка здоровья системы</li>
            <li>• <a href="/webhook">/webhook</a> - Обработчик вебхуков Telegram</li>
        </ul>
    </div>
    
    <p><em>Telegram бот для HR-вопросов компании Мечел</em></p>
    
    <hr>
    <p><strong>Информация:</strong></p>
    <ul>
        <li>Версия: 2.0</li>
        <li>Режим работы: Polling</li>
        <li>Бесплатный тариф Render</li>
        <li>UptimeRobot: каждые 5 минут</li>
    </ul>
</body>
</html>
"""

@app.route('/ping')
def ping():
    """Простой ping для UptimeRobot (keep-alive)"""
    return "pong", 200

@app.route('/health')
def health():
    """Проверка здоровья системы"""
    try:
        # Базовая проверка
        checks = {
            'bot_initialized': application is not None,
            'bot_handler': bot_handler is not None,
            'database': check_database(),
            'timestamp': datetime.now().isoformat()
        }
        
        # Определяем статус
        if checks['bot_initialized'] and checks['database'].get('status') == 'connected':
            status = 'healthy'
            code = 200
        else:
            status = 'unhealthy'
            code = 503
        
        return jsonify({
            'status': status,
            'version': '2.0',
            'environment': 'render-free',
            'checks': checks
        }), code
        
    except Exception as e:
        logger.error(f"Ошибка проверки здоровья: {e}", exc_info=True)
        return jsonify({
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    """
    Обработчик вебхуков от Telegram.
    На бесплатном тарифе используется режим polling,
    но этот эндпоинт на случай, если Telegram пытается отправить вебхук.
    """
    try:
        if request.is_json:
            data = request.get_json()
            update_id = data.get('update_id', 'unknown')
            logger.info(f"🌐 Webhook received (update_id={update_id})")
            
            # В режиме polling вебхуки не обрабатываются
            # Возвращаем успешный ответ, чтобы избежать ошибок 404
            return jsonify({
                'status': 'webhook_received',
                'mode': 'polling',
                'message': 'Bot is running in polling mode. Webhooks are not processed.'
            }), 200
        else:
            return jsonify({
                'error': 'Invalid content type, expected JSON'
            }), 400
            
    except Exception as e:
        logger.error(f"Ошибка обработки вебхука: {e}", exc_info=True)
        return jsonify({
            'error': str(e)
        }), 500

def check_database():
    """Проверка подключения к БД"""
    try:
        conn = config.get_db_connection()
        if not conn:
            return {
                'status': 'disconnected',
                'error': 'Database connection failed'
            }
        
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM faq")
        count = cursor.fetchone()[0]
        conn.close()
        
        return {
            'status': 'connected',
            'faq_count': count,
            'meets_threshold': count >= config.MIN_FAQ_RECORDS,
            'threshold': config.MIN_FAQ_RECORDS
        }
        
    except Exception as e:
        logger.error(f"Ошибка проверки БД: {e}", exc_info=True)
        return {
            'status': 'error',
            'error': str(e)
        }

# ======================
# ИНИЦИАЛИЗАЦИЯ ПРИ ЗАПУСКЕ ЧЕРЕЗ GUNICORN
# ======================

# При запуске через gunicorn __name__ != "__main__"
# Нужно инициализировать бота в фоновом режиме
if __name__ != "__main__":
    logger.info("🔧 Инициализация бота для gunicorn...")
    
    def init_bot_background():
        """Инициализация бота в фоне при запуске через gunicorn"""
        if init_bot():
            # Запускаем polling в отдельном потоке
            bot_thread = threading.Thread(
                target=lambda: asyncio.run(run_bot_polling()),
                daemon=True,
                name="BotThread"
            )
            bot_thread.start()
            logger.info("✅ Бот инициализирован и запущен в фоновом режиме")
        else:
            logger.error("❌ Не удалось инициализировать бота")
    
    # Запускаем инициализацию в фоне
    init_thread = threading.Thread(
        target=init_bot_background,
        daemon=True,
        name="InitThread"
    )
    init_thread.start()

# ======================
# ЗАПУСК ПРИЛОЖЕНИЯ
# ======================

if __name__ == "__main__":
    logger.info("=" * 50)
    logger.info("🚀 ЗАПУСК HR BOT МЕЧЕЛ")
    logger.info("=" * 50)
    
    # Используем asyncio для запуска
    try:
        asyncio.run(main())
    except KeyboardInterrupt:
        logger.info("🛑 Бот остановлен пользователем")
        shutdown_event.set()
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}", exc_info=True)
        sys.exit(1)
