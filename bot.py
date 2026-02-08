#!/usr/bin/env python3
"""
ГЛАВНЫЙ ФАЙЛ БОТА ДЛЯ RENDER
Оптимизированная версия для production
"""
import os
import time
import logging
import asyncio
import concurrent.futures
import threading
from flask import Flask, request, jsonify

from telegram import Update
from telegram.ext import (
    Application,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes
)

from config import config
from search_engine import SearchEngine
from handlers import CommandHandler as CustomCommandHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Инициализация приложения Flask
app = Flask(__name__)

# Глобальные объекты
telegram_app = None
search_engine = None
command_handler = None
bot_initialized = False
app_loop = None  # Глобальный event loop для асинхронных операций

def initialize_app():
    """Инициализация всех компонентов приложения"""
    global telegram_app, search_engine, command_handler, bot_initialized, app_loop
    
    logger.info("=" * 60)
    logger.info("🚀 ИНИЦИАЛИЗАЦИЯ КОРПОРАТИВНОГО БОТА МЕЧЕЛ")
    logger.info("=" * 60)
    
    # Проверка конфигурации
    if not config.validate():
        logger.error("❌ Конфигурация не прошла валидацию")
        return False
    
    # Инициализация поискового движка
    try:
        search_engine = SearchEngine()
        logger.info(f"✅ Поисковый движок готов. FAQ: {len(search_engine.faq_data)}")
    except Exception as e:
        logger.error(f"❌ Ошибка поискового движка: {e}", exc_info=True)
        search_engine = None
        return False
    
    # Инициализация обработчиков
    command_handler = CustomCommandHandler(search_engine) if search_engine else None
    
    # Инициализация Telegram Application
    try:
        telegram_app = (
            Application.builder()
            .token(config.get_bot_token())
            .build()
        )
        
        # Регистрация обработчиков команд
        _register_bot_handlers()
        
        logger.info(f"✅ Бот инициализирован. Токен: {config.get_bot_token()[:10]}...")
        bot_initialized = True
        
        # Создаем глобальный event loop для асинхронных операций
        app_loop = asyncio.new_event_loop()
        logger.info("✅ Глобальный event loop создан")
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Telegram бота: {e}")
        telegram_app = None
        return False
    
    logger.info("✅ Приложение полностью инициализировано")
    return True

def _register_bot_handlers():
    """Регистрация обработчиков команд бота"""
    if not command_handler or not telegram_app:
        logger.error("❌ Не удалось зарегистрировать обработчики: telegram_app или command_handler не инициализированы")
        return
    
    # Обработчик команды /start и /help
    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /start от {update.effective_user.id}")
        await command_handler.handle_welcome(update, context)
    
    # Обработчик команды /категории
    async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /категории от {update.effective_user.id}")
        await command_handler.handle_categories(update, context)
    
    # Обработчик команды /поиск
    async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.message.text.replace('/поиск', '').replace('/search', '').strip()
        logger.info(f"📝 /поиск от {update.effective_user.id}: {query[:50]}")
        await command_handler.handle_search(update, context, query)
    
    # Обработчик команды /отзыв
    async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /отзыв от {update.effective_user.id}")
        await command_handler.handle_feedback(update, context)
    
    # Обработчик команды /статистика
    async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /статистика от {update.effective_user.id}")
        await command_handler.handle_stats(update, context)
    
    # Обработчик команды /очистить
    async def clear_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /очистить от {update.effective_user.id}")
        await command_handler.handle_clear_cache(update, context)
    
    # Обработчик всех текстовых сообщений
    async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 Сообщение от {update.effective_user.id}: {update.message.text[:100]}")
        try:
            if command_handler:
                await command_handler.handle_text_message(update, context)
            else:
                await update.message.reply_text("⚠️ Бот временно не готов к работе. Попробуйте позже.")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки: {e}", exc_info=True)
            await update.message.reply_text("❌ Произошла ошибка. Пожалуйста, попробуйте ещё раз.")
    
    # Регистрация обработчиков
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("help", start_command))
    telegram_app.add_handler(CommandHandler("категории", categories_command))
    telegram_app.add_handler(CommandHandler("categories", categories_command))
    telegram_app.add_handler(CommandHandler("поиск", search_command))
    telegram_app.add_handler(CommandHandler("search", search_command))
    telegram_app.add_handler(CommandHandler("отзыв", feedback_command))
    telegram_app.add_handler(CommandHandler("feedback", feedback_command))
    telegram_app.add_handler(CommandHandler("статистика", stats_command))
    telegram_app.add_handler(CommandHandler("stats", stats_command))
    telegram_app.add_handler(CommandHandler("очистить", clear_cache_command))
    
    # Обработчик всех текстовых сообщений (должен быть последним)
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
    
    logger.info("✅ Обработчики команд зарегистрированы")

# ================== FLASK РОУТЫ ==================

@app.route('/')
def index():
    """Главная страница"""
    faq_count = len(search_engine.faq_data) if search_engine else 0
    db_type = 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite'
    
    bot_status = "✅ Активен" if bot_initialized else "❌ Ошибка инициализации"
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 HR Bot Мечел</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
            h1 {{ color: #333; }}
            .status {{ background: #f0f9ff; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .links a {{ display: inline-block; margin: 10px 15px 10px 0; padding: 10px 20px;
                      background: #007bff; color: white; text-decoration: none; border-radius: 5px; }}
            .links a:hover {{ background: #0056b3; }}
        </style>
    </head>
    <body>
        <h1>🤖 HR Bot Мечел — Статус: {bot_status}</h1>
        <div class="{'error' if not bot_initialized else 'status'}">
            <h3>📊 Статус системы:</h3>
            <p><strong>Бот:</strong> {bot_status}</p>
            <p><strong>FAQ в базе:</strong> {faq_count}</p>
            <p><strong>Тип БД:</strong> {db_type}</p>
            <p><strong>Webhook готов:</strong> {'✅ Да' if bot_initialized else '❌ Нет'}</p>
        </div>
        <div class="links">
            <h3>🔗 Полезные ссылки:</h3>
            <a href="/health">Health Check</a>
            <a href="/set_webhook">Установить вебхук</a>
            <a href="/webhook_info">Информация о вебхуке</a>
        </div>
        <div style="margin-top: 30px; color: #666; font-size: 14px;">
            <p>Время запуска: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </body>
    </html>
    ''', 200

@app.route('/health')
def health_check():
    """Health check endpoint для Render"""
    bot_ok = bot_initialized and telegram_app is not None
    search_ok = search_engine is not None
    faq_count = len(search_engine.faq_data) if search_engine else 0
    
    # Разделяем статусы: здоровый, деградировавший, нерабочий
    if bot_ok and search_ok and faq_count > 0:
        status = "healthy"
        status_code = 200
    elif bot_ok and search_ok:
        # Работает, но нет вопросов в базе
        status = "degraded"
        status_code = 200  # Возвращаем 200, чтобы Render не перезапускал сервис
    else:
        status = "unhealthy"
        status_code = 500
    
    status_data = {
        "status": status,
        "service": "hr-bot-mechel",
        "components": {
            "bot": bot_ok,
            "search_engine": search_ok,
            "database": faq_count > 0
        },
        "details": {
            "faq_count": faq_count,
            "bot_initialized": bot_initialized,
            "telegram_app_exists": telegram_app is not None,
            "search_engine_exists": search_engine is not None
        },
        "database_type": "postgresql" if os.getenv('DATABASE_URL') else "sqlite",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S')
    }
    
    return jsonify(status_data), status_code

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook_endpoint():
    """Ручная установка вебхука"""
    if request.method == 'GET':
        return '''
        <h1>🔧 Установка вебхука</h1>
        <p>Нажмите кнопку ниже, чтобы установить вебхук вручную:</p>
        <form method="POST" style="margin: 20px 0;">
            <button type="submit" style="padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 5px;">
                🚀 Установить вебхук
            </button>
        </form>
        <p><a href="/">← Назад</a></p>
        '''
    
    # POST запрос - установка вебхука
    if not telegram_app or not bot_initialized:
        msg = "❌ Бот не инициализирован. Сначала исправьте ошибки инициализации."
        return f'''
        <h1>🔧 Результат установки вебхука</h1>
        <div style="padding: 20px; background: #f8d7da; border-radius: 8px;">
            {msg}
        </div>
        <p style="margin-top: 20px;"><a href="/">← На главную</a></p>
        ''', 500
    
    try:
        domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-mechel.onrender.com')
        if domain.startswith('https://'):
            domain = domain[8:]
        
        webhook_url = f"https://{domain}/webhook"
        logger.info(f"🔄 Установка вебхука на {webhook_url}")
        
        # Используем ThreadPoolExecutor для асинхронной установки вебхука
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                lambda: asyncio.run_coroutine_threadsafe(
                    telegram_app.bot.set_webhook(
                        url=webhook_url,
                        max_connections=40,
                        allowed_updates=['message', 'callback_query']
                    ),
                    app_loop
                ).result(timeout=10)
            )
            future.result()
        
        msg = f"✅ Вебхук успешно установлен!<br>URL: <code>{webhook_url}</code>"
        logger.info("✅ Вебхук установлен")
        
        # Получаем информацию о вебхуке
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                lambda: asyncio.run_coroutine_threadsafe(
                    telegram_app.bot.get_webhook_info(),
                    app_loop
                ).result(timeout=5)
            )
            webhook_info = future.result()
        
        msg += f"<br><br>📊 Информация от Telegram:<br>"
        msg += f"• Ожидающих обновлений: {webhook_info.pending_update_count}<br>"
        msg += f"• Последняя ошибка: {webhook_info.last_error_message or 'нет'}<br>"
        msg += f"• URL: {webhook_info.url}"
        
    except concurrent.futures.TimeoutError:
        msg = "❌ Таймаут при установке вебхука"
        logger.error(msg)
    except Exception as e:
        msg = f"❌ Ошибка: {str(e)}"
        logger.error(f"❌ Ошибка установки вебхука: {e}", exc_info=True)
    
    return f'''
    <h1>🔧 Результат установки вебхука</h1>
    <div style="padding: 20px; background: {'#d4edda' if '✅' in msg else '#f8d7da'}; border-radius: 8px;">
        {msg}
    </div>
    <p style="margin-top: 20px;"><a href="/">← На главную</a> | <a href="/webhook_info">ℹ️ Информация о вебхуке</a></p>
    ''', 200 if '✅' in msg else 500

@app.route('/webhook_info')
def webhook_info():
    """Страница с информацией о текущем вебхуке"""
    if not telegram_app or not bot_initialized:
        return '''
        <h1>❌ Бот не инициализирован</h1>
        <p>Сначала исправьте ошибки инициализации бота.</p>
        <p><a href="/">← На главную</a></p>
        ''', 500
    
    try:
        # Получаем информацию о вебхуке через ThreadPoolExecutor
        with concurrent.futures.ThreadPoolExecutor(max_workers=1) as executor:
            future = executor.submit(
                lambda: asyncio.run_coroutine_threadsafe(
                    telegram_app.bot.get_webhook_info(),
                    app_loop
                ).result(timeout=5)
            )
            info = future.result()
        
        status = {
            "url": info.url or "Не установлен",
            "has_custom_certificate": info.has_custom_certificate,
            "pending_update_count": info.pending_update_count,
            "last_error_date": info.last_error_date,
            "last_error_message": info.last_error_message or "Нет ошибок",
            "max_connections": info.max_connections,
            "allowed_updates": info.allowed_updates
        }
        return f'''
        <h1>ℹ️ Информация о вебхуке</h1>
        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
            <p><strong>URL:</strong> <code>{status['url']}</code></p>
            <p><strong>Ожидающих обновлений:</strong> {status['pending_update_count']}</p>
            <p><strong>Последняя ошибка:</strong> {status['last_error_message']}</p>
            <p><strong>Макс. соединений:</strong> {status['max_connections']}</p>
        </div>
        <p style="margin-top: 20px;">
            <a href="/">← На главную</a> |
            <a href="/set_webhook">🔧 Установить вебхук</a>
        </p>
        ''', 200
    except concurrent.futures.TimeoutError:
        return '<h1>❌ Таймаут</h1><p>Не удалось получить информацию о вебхуке</p>', 500
    except Exception as e:
        return f'<h1>❌ Ошибка</h1><p>Не удалось получить информацию: {e}</p>', 500

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Основной эндпоинт для получения обновлений от Telegram"""
    if not telegram_app or not bot_initialized:
        logger.error("❌ Вебхук вызван, но бот не инициализирован")
        return 'Bot not initialized', 500
    
    if request.headers.get('content-type') != 'application/json':
        return 'Bad Request', 400
    
    try:
        json_string = request.get_data().decode('utf-8')
        
        # Создаем Update из JSON (синхронная операция)
        update = Update.de_json(json_string, telegram_app.bot)
        
        # Обрабатываем update асинхронно через ThreadPoolExecutor
        # Это позволяет обрабатывать несколько запросов одновременно
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as executor:
            future = executor.submit(
                lambda: asyncio.run_coroutine_threadsafe(
                    telegram_app.process_update(update),
                    app_loop
                ).result(timeout=10)  # Таймаут 10 секунд на обработку
            )
            try:
                future.result()
            except concurrent.futures.TimeoutError:
                logger.warning("⚠️ Обработка вебхука заняла слишком долго, продолжаем в фоне")
                # Не прерываем выполнение, просто логируем
            except Exception as e:
                logger.error(f"❌ Ошибка в обработке вебхука: {e}")
        
        return '', 200
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}", exc_info=True)
        return 'Internal Server Error', 500

def run_loop_in_thread(loop):
    """Запускает event loop в отдельном потоке"""
    asyncio.set_event_loop(loop)
    loop.run_forever()

async def setup_webhook_async():
    """Асинхронная функция для настройки вебхука"""
    if not telegram_app or not bot_initialized:
        logger.error("❌ Не удалось настроить вебхук: бот не инициализирован")
        return
    
    AUTO_SET_WEBHOOK = os.getenv('AUTO_SET_WEBHOOK', 'true').lower() == 'true'
    if AUTO_SET_WEBHOOK:
        try:
            domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-mechel.onrender.com')
            if domain.startswith('https://'):
                domain = domain[8:]
            
            webhook_url = f"https://{domain}/webhook"
            
            # Удаляем старый вебхук
            await telegram_app.bot.delete_webhook()
            
            # Устанавливаем новый
            await telegram_app.bot.set_webhook(
                url=webhook_url,
                max_connections=40,
                allowed_updates=['message', 'callback_query']
            )
            
            logger.info(f"✅ Вебхук автоматически установлен на {webhook_url}")
            
        except Exception as e:
            logger.warning(f"⚠️ Ошибка автоматической установки вебхука: {e}")

def setup_webhook():
    """Запуск настройки вебхука"""
    try:
        if telegram_app and bot_initialized and app_loop:
            # Используем существующий loop
            asyncio.run_coroutine_threadsafe(
                setup_webhook_async(),
                app_loop
            ).result(timeout=15)
        else:
            logger.warning("⚠️ Не удалось настроить вебхук: бот не инициализирован")
    except Exception as e:
        logger.error(f"❌ Ошибка при настройке вебхука: {e}")

# ================== ИНИЦИАЛИЗАЦИЯ ==================

# Инициализируем приложение
try:
    success = initialize_app()
    
    if success and app_loop:
        # Запускаем event loop в отдельном потоке
        loop_thread = threading.Thread(
            target=run_loop_in_thread,
            args=(app_loop,),
            daemon=True,
            name="EventLoopThread"
        )
        loop_thread.start()
        logger.info("✅ Event loop запущен в отдельном потоке")
        
        # Даем время на запуск loop
        time.sleep(1)
        
        # Настраиваем вебхук
        webhook_thread = threading.Thread(target=setup_webhook, daemon=True)
        webhook_thread.start()
        
        logger.info(f"✅ Приложение готово к работе на порту {os.getenv('PORT', 10000)}")
        logger.info("✅ Бот настроен на работу через вебхуки")
    else:
        logger.error("❌ Инициализация приложения завершилась с ошибками")
        logger.error("❌ Бот не будет работать корректно")
        
except Exception as e:
    logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ИНИЦИАЛИЗАЦИИ: {e}", exc_info=True)

# ================== ЛОКАЛЬНЫЙ ЗАПУСК ==================
if __name__ == '__main__':
    logger.warning("⚠️ ЛОКАЛЬНЫЙ ЗАПУСК - только для разработки!")
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Запуск Flask сервера на порту {port}")
    
    # Для локального запуска можно использовать polling
    async def local_polling():
        if telegram_app and bot_initialized:
            await telegram_app.initialize()
            await telegram_app.start()
            logger.info("🤖 Бот запущен в режиме polling")
            
            # Ожидаем завершения
            await telegram_app.updater.start_polling()
            await telegram_app.updater.idle()
    
    # Запускаем polling в отдельном потоке для локальной разработки
    if telegram_app and bot_initialized:
        polling_thread = threading.Thread(
            target=lambda: asyncio.run(local_polling()),
            daemon=True
        )
        polling_thread.start()
    
    app.run(host='0.0.0.0', port=port, debug=False)
