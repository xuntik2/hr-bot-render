"""
HR БОТ ДЛЯ RENDER FREE - ФИНАЛЬНАЯ ВЕРСИЯ
Версия 8.1 - Все исправления включены
"""

import os
import sys
import logging
import atexit
from datetime import datetime

from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.error import TelegramError

sys.path.insert(0, '.')
from config import config
from search_engine import SearchEngine
from bot_handlers import BotCommandHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Flask приложение
app = Flask(__name__)

# Глобальные переменные
application = None
initialized = False

# Статистика
stats = {
    'requests_total': 0,
    'errors_total': 0,
    'last_error': None,
    'startup_time': datetime.now().isoformat()
}

# ======================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ======================

def init_bot():
    """Простая синхронная инициализация бота"""
    global application, initialized
    
    try:
        logger.info("🚀 Инициализация бота...")
        
        # 1. Поисковая система
        search_engine = SearchEngine()
        search_engine.refresh_data()
        logger.info(f"✅ Загружено {len(search_engine.faq_data)} FAQ")
        
        # 2. Обработчики команд
        bot_handler = BotCommandHandler(search_engine)
        
        # 3. Telegram Application
        token = config.get_bot_token()
        application = Application.builder().token(token).build()
        
        # 4. Регистрация обработчиков
        handlers = [
            CommandHandler("start", bot_handler.handle_welcome),
            CommandHandler("help", bot_handler.handle_welcome),
            CommandHandler("categories", bot_handler.handle_categories),
            CommandHandler("search", bot_handler.handle_search),
            CommandHandler("feedback", bot_handler.handle_feedback),
            CommandHandler("stats", bot_handler.handle_stats),
            MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.handle_text_message)
        ]
        
        for handler in handlers:
            application.add_handler(handler)
        
        # 5. Инициализация (синхронный вызов асинхронного метода)
        import asyncio
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Инициализируем и запускаем приложение
            loop.run_until_complete(application.initialize())
            loop.run_until_complete(application.start())
            logger.info("✅ Бот запущен")
            
            # 6. Автоматическая установка вебхука
            if os.getenv('AUTO_SET_WEBHOOK', 'true').lower() == 'true':
                webhook_url = get_webhook_url()
                loop.run_until_complete(application.bot.set_webhook(
                    url=webhook_url,
                    drop_pending_updates=True,
                    allowed_updates=["message"]
                ))
                logger.info(f"✅ Вебхук установлен: {webhook_url}")
            
            initialized = True
            return True
            
        finally:
            loop.close()
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации: {e}", exc_info=True)
        return False

def get_webhook_url():
    """Получение URL для вебхука"""
    hostname = os.getenv('RENDER_EXTERNAL_HOSTNAME')
    if not hostname:
        # Попробуем получить из имени сервиса
        service_name = os.getenv('RENDER_SERVICE_NAME', 'hr-bot-mechel')
        hostname = f"{service_name}.onrender.com"
    
    # Убираем http/https префикс если есть
    hostname = hostname.replace('https://', '').replace('http://', '')
    return f"https://{hostname}/webhook"

def cleanup():
    """Очистка ресурсов при завершении"""
    global application
    
    if application:
        try:
            logger.info("🛑 Остановка бота...")
            
            import asyncio
            
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            try:
                loop.run_until_complete(application.stop())
                logger.info("✅ Бот остановлен")
            finally:
                loop.close()
                
        except Exception as e:
            logger.error(f"❌ Ошибка при остановке бота: {e}")

# Регистрируем очистку при завершении
atexit.register(cleanup)

# Инициализация при импорте
if not init_bot():
    logger.critical("❌ Не удалось инициализировать бота")
    # На Render приложение продолжит работать, но будет возвращать ошибки

# ======================
# FLASK ЭНДПОИНТЫ
# ======================

@app.route('/')
def index():
    """Главная страница"""
    status = "🟢 Активен" if initialized else "🔴 Ошибка инициализации"
    status_class = "status-ok" if initialized else "status-error"
    
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>HR Bot Мечел</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ 
            font-family: Arial, sans-serif; 
            padding: 20px; 
            max-width: 800px; 
            margin: 0 auto;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: white;
        }}
        .container {{ 
            background: rgba(255, 255, 255, 0.95); 
            padding: 30px; 
            border-radius: 15px; 
            box-shadow: 0 10px 30px rgba(0,0,0,0.2);
            color: #333;
        }}
        .status {{ 
            display: inline-block; 
            padding: 10px 20px; 
            border-radius: 50px; 
            font-weight: bold;
            margin: 15px 0;
        }}
        .status-ok {{ background: #27ae60; color: white; }}
        .status-error {{ background: #e74c3c; color: white; }}
        .btn {{ 
            display: inline-block; 
            padding: 10px 20px; 
            background: #667eea; 
            color: white; 
            text-decoration: none; 
            border-radius: 50px; 
            margin: 8px 5px;
            font-size: 14px;
        }}
        .btn:hover {{ 
            background: #764ba2;
        }}
        h1 {{ 
            color: #2c3e50;
            margin-top: 0;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 HR Bot Мечел</h1>
        <p><strong>Статус:</strong></p>
        <div class="status {status_class}">
            {status}
        </div>
        <p><strong>Режим:</strong> Webhook</p>
        <p><strong>Время:</strong> {datetime.now().strftime('%H:%M:%S')}</p>
        <p><strong>Версия:</strong> 8.1</p>
        
        <div style="margin-top: 20px;">
            <a href="/health" class="btn">Проверка здоровья</a>
            <a href="/setwebhook" class="btn">Переустановить вебхук</a>
            <a href="/stats" class="btn">Статистика</a>
        </div>
    </div>
</body>
</html>
"""

@app.route('/health')
def health():
    """Health-check для Render"""
    return jsonify({
        'status': 'healthy' if initialized else 'unhealthy',
        'service': 'hr-bot-mechel',
        'timestamp': datetime.now().isoformat(),
        'bot_initialized': initialized,
        'version': '8.1',
        'uptime_seconds': (datetime.now() - datetime.fromisoformat(stats['startup_time'])).total_seconds()
    }), 200 if initialized else 503

@app.route('/ping')
def ping():
    """Для UptimeRobot"""
    return "pong", 200

@app.route('/stats')
def get_stats():
    """Статистика бота"""
    return jsonify({
        **stats,
        'initialized': initialized,
        'current_time': datetime.now().isoformat(),
        'webhook_url': get_webhook_url() if initialized else None
    }), 200

@app.route('/setwebhook', methods=['GET', 'POST'])
def set_webhook():
    """Ручная установка вебхука"""
    if not initialized:
        return jsonify({'error': 'Бот не инициализирован'}), 503
    
    try:
        webhook_url = get_webhook_url()
        
        import asyncio
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            loop.run_until_complete(application.bot.set_webhook(
                url=webhook_url,
                drop_pending_updates=True
            ))
        finally:
            loop.close()
        
        return jsonify({
            'status': 'ok',
            'webhook_url': webhook_url,
            'message': 'Webhook установлен'
        }), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}", exc_info=True)
        return jsonify({'error': str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    """Обработчик вебхуков от Telegram"""
    stats['requests_total'] += 1
    
    if not initialized:
        logger.warning("Вебхук получен, но бот не инициализирован")
        return jsonify({'status': 'bot_not_initialized'}), 200
    
    try:
        # Проверка Content-Type
        if not request.is_json:
            return jsonify({'error': 'JSON required'}), 400
        
        # Получение данных
        update_data = request.get_json()
        if not update_data:
            return jsonify({'status': 'empty'}), 200
        
        # Создание объекта Update
        update = Update.de_json(update_data, application.bot)
        if update is None:
            return jsonify({'status': 'invalid update'}), 200
        
        # Обработка обновления
        import asyncio
        
        loop = asyncio.new_event_loop()
        asyncio.set_event_loop(loop)
        
        try:
            # Запускаем обработку обновления
            loop.run_until_complete(application.process_update(update))
            
            logger.debug(f"✅ Обработано обновление {update.update_id}")
            return jsonify({'status': 'ok'}), 200
            
        finally:
            loop.close()
        
    except TelegramError as e:
        # ✅ КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: отдельная обработка TelegramError
        logger.warning(f"⚠️ Telegram ошибка: {e}")
        # Возвращаем 200 чтобы Telegram не повторял запрос
        return jsonify({'status': 'telegram_error_ignored'}), 200
        
    except Exception as e:
        stats['errors_total'] += 1
        stats['last_error'] = str(e)
        
        logger.error(f"❌ Ошибка обработки вебхука: {e}", exc_info=True)
        # Всегда возвращаем 200, чтобы Telegram не повторял запрос
        return jsonify({'status': 'error', 'message': str(e)}), 200

# ======================
# ЗАПУСК СЕРВЕРА
# ======================

if __name__ == "__main__":
    port = config.get_port()
    logger.info(f"🚀 Запуск сервера на порту {port}")
    app.run(host='0.0.0.0', port=port, debug=False)
