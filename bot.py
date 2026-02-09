"""
ГЛАВНЫЙ ФАЙЛ ЗАПУСКА TELEGRAM БОТА МЕЧЕЛ
Версия 3.0 - С улучшенной безопасностью, health checks и обработкой ошибок
"""

import asyncio
import logging
import sys
import json
from threading import Thread
from datetime import datetime
from flask import Flask, request, jsonify

from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram import Update

from config import config, TABLE_FAQ, MIN_FAQ_RECORDS
from search_engine import SearchEngine
from bot_handlers import BotCommandHandler
from admin_tools import health_check, get_system_stats

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[
        logging.StreamHandler(sys.stdout),
        logging.FileHandler('bot.log', encoding='utf-8')
    ]
)
logger = logging.getLogger(__name__)

# Создаем Flask приложение для gunicorn
app = Flask(__name__)

# Глобальные переменные
application = None
bot_handler = None
bot_thread = None

def init_bot():
    """Инициализация бота с обработкой ошибок"""
    global bot_handler, application
    
    try:
        logger.info("🚀 Инициализация Telegram бота Мечел...")
        
        # Проверяем конфигурацию перед запуском
        bot_token = config.get_bot_token()
        if not bot_token:
            logger.error("❌ КРИТИЧЕСКАЯ ОШИБКА: ТОКЕН БОТА НЕ НАЙДЕН!")
            logger.error("Установите переменную окружения BOT_TOKEN")
            return False
        
        # Проверяем подключение к базе данных
        try:
            db_status = health_check()
            if db_status['status'] != 'healthy':
                logger.warning(f"⚠️ Предупреждение: статус БД - {db_status['status']}")
                for check_name, check_data in db_status.get('checks', {}).items():
                    if check_data.get('status') != 'healthy':
                        logger.warning(f"  • {check_name}: {check_data.get('message')}")
        except Exception as db_error:
            logger.warning(f"⚠️ Не удалось проверить БД: {db_error}")
        
        # Инициализация поисковой системы
        try:
            search_engine = SearchEngine()
            search_engine.refresh_data()
            logger.info(f"✅ Поисковая система загружена: {search_engine.get_stats()}")
        except Exception as search_error:
            logger.error(f"❌ Ошибка инициализации поисковой системы: {search_error}")
            return False
        
        # Создаем обработчик команд
        bot_handler = BotCommandHandler(search_engine)
        
        # Создаем приложение python-telegram-bot
        application = Application.builder().token(bot_token).build()
        
        # Регистрируем обработчики команд
        handlers = [
            CommandHandler("start", bot_handler.handle_welcome),
            CommandHandler("help", bot_handler.handle_welcome),
            CommandHandler("categories", bot_handler.handle_categories),
            CommandHandler("search", bot_handler.handle_search),
            CommandHandler("feedback", bot_handler.handle_feedback),
            CommandHandler("stats", bot_handler.handle_stats),
            CommandHandler("clear", bot_handler.handle_clear_cache),
        ]
        
        for handler in handlers:
            application.add_handler(handler)
        
        # Обработчик текстовых сообщений
        application.add_handler(
            MessageHandler(filters.TEXT & ~filters.COMMAND, bot_handler.handle_text_message)
        )
        
        logger.info("✅ Бот инициализирован успешно")
        logger.info(f"📋 Зарегистрировано обработчиков: {len(handlers) + 1}")
        return True
        
    except Exception as e:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА инициализации бота: {e}", exc_info=True)
        return False

async def run_bot_async():
    """Асинхронный запуск бота"""
    try:
        if application is None:
            logger.error("❌ Приложение не инициализировано")
            return
        
        # Проверяем режим работы (вебхук или polling)
        webhook_url = config.get_webhook_url()
        port = config.get_port()
        
        if webhook_url and port:
            # Режим вебхука (для продакшена)
            logger.info(f"🌐 Запуск в режиме вебхука на порту {port}")
            logger.info(f"🌐 Webhook URL: {webhook_url}")
            
            # Настраиваем вебхук
            await application.initialize()
            await application.start()
            
            # Устанавливаем вебхук
            await application.bot.set_webhook(
                url=webhook_url,
                secret_token=config.get_secret_token(),
                drop_pending_updates=True
            )
            
            logger.info("✅ Вебхук установлен успешно")
            
            # Запускаем веб-сервер для вебхуков
            await application.updater.start_webhook(
                listen="0.0.0.0",
                port=port,
                url_path="/",
                webhook_url=webhook_url,
                secret_token=config.get_secret_token()
            )
            
            # Ждем завершения
            await asyncio.Event().wait()
            
        else:
            # Режим polling (для разработки)
            logger.info("🔄 Запуск в режиме polling")
            await application.initialize()
            await application.start()
            await application.updater.start_polling(
                drop_pending_updates=True,
                allowed_updates=Update.ALL_TYPES
            )
            
            logger.info("✅ Бот запущен в режиме polling")
            await asyncio.Event().wait()
            
    except Exception as e:
        logger.error(f"❌ Ошибка запуска бота: {e}", exc_info=True)
        sys.exit(1)

def run_bot():
    """Запуск бота в синхронной обертке"""
    asyncio.run(run_bot_async())

def start_bot_in_thread():
    """Запуск бота в отдельном потоке"""
    global bot_thread
    
    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()
    logger.info("✅ Бот запущен в отдельном потоке")
    
    # Проверяем, что поток запустился
    if bot_thread.is_alive():
        logger.info("✅ Поток бота активен")
    else:
        logger.error("❌ Поток бота не запустился")
    
    return bot_thread

# ==============================
# FLASK ROUTES (для веб-интерфейса и health checks)
# ==============================

@app.route('/')
def index():
    """Корневой маршрут для проверки работы"""
    try:
        bot_status = "активен" if bot_thread and bot_thread.is_alive() else "неактивен"
        return f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <meta name="viewport" content="width=device-width, initial-scale=1.0">
            <title>HR Bot Мечел - Статус</title>
            <style>
                body {{ font-family: Arial, sans-serif; margin: 40px; background: #f5f5f5; }}
                .container {{ max-width: 800px; margin: 0 auto; background: white; padding: 30px; border-radius: 10px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }}
                h1 {{ color: #2c3e50; border-bottom: 2px solid #3498db; padding-bottom: 10px; }}
                .status {{ padding: 15px; border-radius: 5px; margin: 20px 0; }}
                .healthy {{ background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }}
                .unhealthy {{ background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }}
                .info {{ background: #d1ecf1; color: #0c5460; border: 1px solid #bee5eb; }}
                .endpoints {{ margin-top: 30px; }}
                .endpoint {{ background: #e9ecef; padding: 10px; margin: 5px 0; border-radius: 5px; font-family: monospace; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🤖 HR Bot Мечел - Статус системы</h1>
                
                <div class="status info">
                    <strong>Статус бота:</strong> {bot_status}<br>
                    <strong>Время:</strong> {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}<br>
                    <strong>Версия:</strong> 3.0
                </div>
                
                <h2>📊 Быстрые ссылки:</h2>
                <div class="endpoints">
                    <div class="endpoint"><a href="/health">/health</a> - Полная проверка здоровья системы</div>
                    <div class="endpoint"><a href="/health/simple">/health/simple</a> - Простая проверка для балансировщиков</div>
                    <div class="endpoint"><a href="/stats">/stats</a> - Статистика системы</div>
                    <div class="endpoint"><a href="/webhook" target="_blank">/webhook</a> - Эндпоинт для вебхуков Telegram</div>
                </div>
                
                <h2>🔧 Информация:</h2>
                <p>Telegram бот для HR-вопросов компании Мечел</p>
                <p>Минимальное требование: {MIN_FAQ_RECORDS} записей в базе знаний</p>
                <p>Порт: {config.get_port()}</p>
                
                <h2>📞 Контакты:</h2>
                <p>При проблемах с работой бота обращайтесь в IT-отдел</p>
                <p>Email: it-support@mechel.ru | Телефон: (495) 123-45-67 (доб. 301)</p>
            </div>
        </body>
        </html>
        """
    except Exception as e:
        return f"🤖 HR Bot Мечел работает! (ошибка рендеринга: {str(e)})"

@app.route('/health', methods=['GET'])
def health():
    """Комплексная проверка здоровья системы"""
    try:
        # Получаем полную проверку здоровья
        health_status = health_check()
        
        # Добавляем дополнительную информацию
        health_status['service'] = 'mechel-hr-bot'
        health_status['version'] = '3.0'
        health_status['timestamp_human'] = datetime.now().strftime('%Y-%m-%d %H:%M:%S')
        
        # Добавляем информацию о боте
        health_status['bot_status'] = {
            'thread_alive': bot_thread.is_alive() if bot_thread else False,
            'initialized': application is not None,
            'webhook_mode': bool(config.get_webhook_url())
        }
        
        # Определяем HTTP статус
        http_status = 200 if health_status['status'] == 'healthy' else 503
        
        logger.info(f"🔍 Health check: {health_status['status'].upper()}")
        return jsonify(health_status), http_status
        
    except Exception as e:
        logger.error(f"❌ Ошибка health check: {e}")
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'service': 'mechel-hr-bot',
            'timestamp': datetime.now().isoformat()
        }), 503

@app.route('/health/simple', methods=['GET'])
def health_simple():
    """Простая проверка здоровья (для балансировщиков нагрузки)"""
    try:
        # Быстрая проверка БД
        import psycopg2
        from psycopg2 import OperationalError
        
        conn = None
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            
            # Проверяем количество FAQ записей
            cursor.execute(f"SELECT COUNT(*) FROM {TABLE_FAQ}")
            faq_count = cursor.fetchone()[0]
            
            # Проверяем наличие минимального количества записей
            meets_threshold = faq_count >= MIN_FAQ_RECORDS
            
            # Проверяем, что бот инициализирован
            bot_ok = application is not None
            
            status = 'healthy' if (meets_threshold and bot_ok) else 'unhealthy'
            
            cursor.close()
            conn.close()
            
            response = {
                'status': status,
                'checks': {
                    'database': 'connected',
                    'faq_count': {
                        'count': faq_count,
                        'min_required': MIN_FAQ_RECORDS,
                        'meets_threshold': meets_threshold
                    },
                    'bot_initialized': bot_ok
                },
                'timestamp': datetime.now().isoformat(),
                'service': 'mechel-hr-bot'
            }
            
            http_status = 200 if status == 'healthy' else 503
            return jsonify(response), http_status
            
        except OperationalError as e:
            if conn:
                conn.close()
            return jsonify({
                'status': 'unhealthy',
                'error': f'Database connection failed: {str(e)}',
                'timestamp': datetime.now().isoformat()
            }), 503
            
    except Exception as e:
        return jsonify({
            'status': 'unhealthy',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 503

@app.route('/stats', methods=['GET'])
def stats():
    """Получение статистики системы"""
    try:
        system_stats = get_system_stats()
        
        # Форматируем ответ для удобства чтения
        formatted_stats = {
            'status': 'success',
            'service': 'mechel-hr-bot',
            'timestamp': datetime.now().isoformat(),
            'data': {
                'database': system_stats.get('database', {}),
                'faq': system_stats.get('faq', {}),
                'activity': system_stats.get('activity', {}),
                'health': system_stats.get('health', {}),
                'bot': {
                    'initialized': application is not None,
                    'thread_alive': bot_thread.is_alive() if bot_thread else False,
                    'webhook_enabled': bool(config.get_webhook_url()),
                    'feedback_enabled': config.is_feedback_enabled()
                }
            }
        }
        
        # Логируем основные метрики
        faq_data = system_stats.get('faq', {})
        logger.info(f"📊 Статистика запрошена: {faq_data.get('total', 0)} FAQ, порог: {faq_data.get('threshold', MIN_FAQ_RECORDS)}")
        
        return jsonify(formatted_stats), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка получения статистики: {e}")
        return jsonify({
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    """Эндпоинт для вебхука Telegram"""
    try:
        if request.is_json:
            data = request.get_json()
            
            # Логируем получение вебхука (без чувствительных данных)
            update_id = data.get('update_id', 'unknown')
            logger.info(f"🌐 Webhook received: update_id={update_id}")
            
            # Здесь можно добавить обработку вебхука если нужно
            # В текущей реализации бот использует polling или webhook через python-telegram-bot
            
            return jsonify({"status": "ok", "update_id": update_id}), 200
        else:
            return jsonify({"error": "Invalid content type, expected JSON"}), 400
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/restart', methods=['POST'])
def restart():
    """Перезапуск бота (только для администраторов)"""
    try:
        # Проверяем секретный ключ для безопасности
        secret = request.headers.get('X-Secret-Key')
        expected_secret = config.get_secret_token()
        
        if not expected_secret or secret != expected_secret:
            return jsonify({"error": "Unauthorized"}), 401
        
        logger.warning("🔄 Запрошен перезапуск бота через API")
        
        # Останавливаем текущий поток бота
        global bot_thread, application
        
        if bot_thread and bot_thread.is_alive():
            # В python-telegram-bot v20+ нужно корректно остановить приложение
            # Это упрощенная реализация
            logger.info("⏸️ Остановка текущего экземпляра бота...")
        
        # Переинициализируем бота
        success = init_bot()
        
        if success:
            start_bot_in_thread()
            logger.info("✅ Бот успешно перезапущен")
            return jsonify({"status": "success", "message": "Bot restarted successfully"}), 200
        else:
            logger.error("❌ Не удалось перезапустить бота")
            return jsonify({"status": "error", "message": "Failed to restart bot"}), 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка перезапуска бота: {e}")
        return jsonify({"error": str(e)}), 500

@app.route('/config', methods=['GET'])
def config_info():
    """Информация о конфигурации (безопасная, без секретов)"""
    try:
        config_data = {
            'service': 'mechel-hr-bot',
            'version': '3.0',
            'timestamp': datetime.now().isoformat(),
            'configuration': {
                'webhook_enabled': bool(config.get_webhook_url()),
                'feedback_enabled': config.is_feedback_enabled(),
                'meme_enabled': config.is_meme_enabled(),
                'port': config.get_port(),
                'min_faq_records': MIN_FAQ_RECORDS,
                'admin_ids_count': len(config.get_admin_ids()),
                'database_configured': bool(config.get_db_connection()) if hasattr(config, 'get_db_connection') else False
            },
            'environment': {
                'python_version': sys.version,
                'platform': sys.platform
            }
        }
        
        return jsonify(config_data), 200
        
    except Exception as e:
        return jsonify({"error": str(e)}), 500

# ==============================
# ЗАПУСК ПРИЛОЖЕНИЯ
# ==============================

# Инициализируем бота при старте приложения
if init_bot():
    bot_thread = start_bot_in_thread()
    logger.info("✅ Приложение Flask и Telegram бот инициализированы")
    
    # Запускаем периодическую проверку здоровья в фоне
    def periodic_health_check():
        """Периодическая проверка здоровья системы"""
        import time
        while True:
            try:
                health_status = health_check()
                if health_status['status'] != 'healthy':
                    logger.warning(f"⚠️ Периодическая проверка: система нездорова - {health_status}")
                
                # Проверяем активность потока бота
                if bot_thread and not bot_thread.is_alive():
                    logger.error("❌ Поток бота умер, пытаемся перезапустить...")
                    init_bot()
                    start_bot_in_thread()
                
                time.sleep(300)  # Проверка каждые 5 минут
                
            except Exception as e:
                logger.error(f"❌ Ошибка периодической проверки здоровья: {e}")
                time.sleep(60)
    
    # Запускаем периодическую проверку в отдельном потоке
    health_thread = Thread(target=periodic_health_check, daemon=True)
    health_thread.start()
    logger.info("✅ Запущена фоновая проверка здоровья системы")
    
else:
    logger.error("❌ Не удалось инициализировать бота. Приложение Flask будет запущено без бота.")

if __name__ == "__main__":
    # Локальный запуск
    logger.info("🚀 Локальный запуск приложения HR Bot Мечел...")
    
    # Получаем порт из конфигурации
    port = config.get_port()
    host = "0.0.0.0"  # Доступно со всех интерфейсов
    
    logger.info(f"🌐 Запуск Flask на {host}:{port}")
    logger.info(f"📞 Доступные эндпоинты:")
    logger.info(f"   • http://{host}:{port}/ - Главная страница")
    logger.info(f"   • http://{host}:{port}/health - Проверка здоровья")
    logger.info(f"   • http://{host}:{port}/stats - Статистика")
    
    # Запускаем Flask приложение
    app.run(
        host=host,
        port=port,
        debug=False,  # В продакшене debug должен быть False
        threaded=True  # Поддержка многопоточности
    )
