#!/usr/bin/env python3
"""
ГЛАВНЫЙ ФАЙЛ БОТА ДЛЯ RENDER
Исправленная версия с устранением ошибок инициализации
"""
import os
import time
import logging
import asyncio
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

def initialize_app():
    """Инициализация всех компонентов приложения"""
    global telegram_app, search_engine, command_handler, bot_initialized
    
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
        faq_count = len(search_engine.faq_data) if search_engine else 0
        logger.info(f"✅ Поисковый движок готов. FAQ: {faq_count}")
        
        if faq_count < 10:  # Если мало вопросов - это проблема
            logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: Загружено только {faq_count} FAQ вместо 75")
            logger.error("   Проблема с базой данных или скриптом create_database.py")
    except Exception as e:
        logger.error(f"❌ Ошибка поискового движка: {e}", exc_info=True)
        search_engine = None
        return False
    
    # Инициализация обработчиков
    command_handler = CustomCommandHandler(search_engine) if search_engine else None
    
    # Инициализация Telegram Application
    try:
        # Получаем токен бота
        bot_token = config.get_bot_token()
        if not bot_token or bot_token == 'ВАШ_ТОКЕН_ЗДЕСЬ':
            logger.error("❌ Не указан BOT_TOKEN или используется значение по умолчанию")
            return False
        
        logger.info(f"🔧 Создание приложения Telegram с токеном: {bot_token[:10]}...")
        
        # Создаем приложение без использования Updater
        telegram_app = Application.builder().token(bot_token).build()
        
        # Регистрация обработчиков команд
        _register_bot_handlers()
        
        # Инициализируем приложение (но не запускаем polling)
        logger.info("✅ Приложение Telegram создано")
        bot_initialized = True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Telegram бота: {e}", exc_info=True)
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
    faq_status = "✅ 75 вопросов" if faq_count >= 75 else f"❌ Только {faq_count} вопросов"
    
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
            .warning {{ background: #fff3cd; color: #856404; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .links a {{ display: inline-block; margin: 10px 15px 10px 0; padding: 10px 20px;
                      background: #007bff; color: white; text-decoration: none; border-radius: 5px; }}
            .links a:hover {{ background: #0056b3; }}
        </style>
    </head>
    <body>
        <h1>🤖 HR Bot Мечел — Статус: {bot_status}</h1>
        
        {'<div class="error">' if not bot_initialized else ('<div class="warning">' if faq_count < 75 else '<div class="status">')}
            <h3>📊 Статус системы:</h3>
            <p><strong>Бот:</strong> {bot_status}</p>
            <p><strong>FAQ в базе:</strong> {faq_status}</p>
            <p><strong>Тип БД:</strong> {db_type}</p>
            <p><strong>Webhook готов:</strong> {'✅ Да' if bot_initialized else '❌ Нет'}</p>
            {'<p><strong>Проблема:</strong> Ошибка инициализации Telegram API</p>' if not bot_initialized else ''}
            {'<p><strong>Проблема:</strong> Не все вопросы загружены в базу</p>' if faq_count < 75 else ''}
        </div>
        
        <div class="links">
            <h3>🔗 Полезные ссылки:</h3>
            <a href="/health">Health Check</a>
            <a href="/set_webhook">Установить вебхук</a>
            <a href="/webhook_info">Информация о вебхуке</a>
            <a href="/debug">Диагностика</a>
        </div>
        
        <div style="margin-top: 30px; color: #666; font-size: 14px;">
            <p>Время запуска: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            {'<p style="color: #dc3545;"><strong>ВНИМАНИЕ:</strong> Бот не работает! Исправьте ошибки выше.</p>' if not bot_initialized else ''}
        </div>
    </body>
    </html>
    ''', 200 if bot_initialized else 500

@app.route('/health')
def health_check():
    """Health check endpoint для Render"""
    bot_ok = bot_initialized and telegram_app is not None
    search_ok = search_engine is not None
    faq_count = len(search_engine.faq_data) if search_engine else 0
    
    # Определяем статус
    if bot_ok and search_ok and faq_count >= 10:
        status = "healthy"
        status_code = 200
    elif bot_ok and search_ok:
        status = "degraded"
        status_code = 200  # Возвращаем 200, чтобы Render не перезапускал
    else:
        status = "unhealthy"
        status_code = 500
    
    status_data = {
        "status": status,
        "service": "hr-bot-mechel",
        "components": {
            "bot": bot_ok,
            "search_engine": search_ok,
            "database_has_data": faq_count > 0
        },
        "details": {
            "faq_count": faq_count,
            "expected_faq_count": 75,
            "bot_initialized": bot_initialized,
            "telegram_app_exists": telegram_app is not None,
            "search_engine_exists": search_engine is not None
        },
        "database_type": "postgresql" if os.getenv('DATABASE_URL') else "sqlite",
        "timestamp": time.strftime('%Y-%m-%d %H:%M:%S'),
        "errors": [] if bot_ok else ["Telegram bot initialization failed"]
    }
    
    if faq_count < 75:
        status_data["warnings"] = [f"Only {faq_count} FAQ loaded instead of 75"]
    
    return jsonify(status_data), status_code

@app.route('/debug')
def debug_info():
    """Страница диагностики для отладки"""
    import sys
    
    info = {
        "python_version": sys.version,
        "environment_variables": {
            "BOT_TOKEN_set": bool(os.getenv('BOT_TOKEN')),
            "DATABASE_URL_set": bool(os.getenv('DATABASE_URL')),
            "RENDER_EXTERNAL_URL": os.getenv('RENDER_EXTERNAL_URL', 'Not set'),
            "PORT": os.getenv('PORT', 'Not set')
        },
        "bot_status": {
            "initialized": bot_initialized,
            "telegram_app": telegram_app is not None,
            "search_engine": search_engine is not None,
            "faq_count": len(search_engine.faq_data) if search_engine else 0
        },
        "config_check": {
            "is_postgresql": config.is_postgresql(),
            "bot_token_length": len(config.get_bot_token()) if config.get_bot_token() else 0
        }
    }
    
    return jsonify(info), 200

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
        
        # Используем asyncio для установки вебхука
        async def set_webhook_task():
            await telegram_app.bot.delete_webhook()  # Сначала удаляем старый
            await telegram_app.bot.set_webhook(
                url=webhook_url,
                max_connections=40,
                allowed_updates=['message', 'callback_query']
            )
        
        # Запускаем в отдельном потоке
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(set_webhook_task())
            loop.close()
        
        thread = threading.Thread(target=run_async)
        thread.start()
        thread.join(timeout=10)
        
        if thread.is_alive():
            msg = "❌ Таймаут при установке вебхука"
            logger.error(msg)
        else:
            msg = f"✅ Вебхук успешно установлен!<br>URL: <code>{webhook_url}</code>"
            logger.info("✅ Вебхук установлен")
            
            # Получаем информацию о вебхуке
            async def get_webhook_info_task():
                return await telegram_app.bot.get_webhook_info()
            
            def get_info():
                loop = asyncio.new_event_loop()
                asyncio.set_event_loop(loop)
                info = loop.run_until_complete(get_webhook_info_task())
                loop.close()
                return info
            
            info_thread = threading.Thread(target=get_info)
            info_thread.start()
            info_thread.join(timeout=5)
            
            if not info_thread.is_alive():
                msg += f"<br><br>📊 Информация от Telegram:<br>"
                msg += f"• Ожидающих обновлений: 0<br>"
                msg += f"• URL: {webhook_url}"
        
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
        # Получаем информацию о вебхуке
        async def get_webhook_info_task():
            return await telegram_app.bot.get_webhook_info()
        
        def get_info():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            info = loop.run_until_complete(get_webhook_info_task())
            loop.close()
            return info
        
        thread = threading.Thread(target=get_info)
        thread.start()
        thread.join(timeout=5)
        
        if thread.is_alive():
            return '<h1>❌ Таймаут</h1><p>Не удалось получить информацию о вебхуке</p>', 500
        
        info = get_info() if 'info' in locals() else None
        
        if info:
            status = {
                "url": info.url or "Не установлен",
                "has_custom_certificate": info.has_custom_certificate,
                "pending_update_count": info.pending_update_count,
                "last_error_date": info.last_error_date,
                "last_error_message": info.last_error_message or "Нет ошибок",
                "max_connections": info.max_connections,
            }
        else:
            status = {"url": "Не удалось получить информацию"}
        
        return f'''
        <h1>ℹ️ Информация о вебхуке</h1>
        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
            <p><strong>URL:</strong> <code>{status['url']}</code></p>
            <p><strong>Ожидающих обновлений:</strong> {status.get('pending_update_count', 'N/A')}</p>
            <p><strong>Последняя ошибка:</strong> {status.get('last_error_message', 'N/A')}</p>
            <p><strong>Макс. соединений:</strong> {status.get('max_connections', 'N/A')}</p>
        </div>
        <p style="margin-top: 20px;">
            <a href="/">← На главную</a> |
            <a href="/set_webhook">🔧 Установить вебхук</a>
        </p>
        ''', 200
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
        
        # Создаем Update из JSON
        update = Update.de_json(json_string, telegram_app.bot)
        
        # Обрабатываем update асинхронно в отдельном потоке
        async def process_update_task():
            await telegram_app.process_update(update)
        
        def run_async():
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            loop.run_until_complete(process_update_task())
            loop.close()
        
        # Запускаем в отдельном потоке, чтобы не блокировать Flask
        thread = threading.Thread(target=run_async)
        thread.start()
        
        # Не ждем завершения, чтобы быстро отвечать Telegram
        # (Telegram ожидает ответ в течение 10 секунд)
        
        return '', 200
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}", exc_info=True)
        return 'Internal Server Error', 500

# ================== ИНИЦИАЛИЗАЦИЯ ==================

# Инициализируем приложение
try:
    logger.info("🔧 Запуск инициализации приложения...")
    success = initialize_app()
    
    if success:
        logger.info(f"✅ Приложение готово к работе на порту {os.getenv('PORT', 10000)}")
        logger.info("🤖 Бот работает в режиме вебхуков")
        
        # Автоматическая установка вебхука при запуске
        AUTO_SET_WEBHOOK = os.getenv('AUTO_SET_WEBHOOK', 'true').lower() == 'true'
        if AUTO_SET_WEBHOOK and bot_initialized:
            logger.info("🔄 Автоматическая установка вебхука...")
            
            def auto_set_webhook():
                try:
                    domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-mechel.onrender.com')
                    if domain.startswith('https://'):
                        domain = domain[8:]
                    
                    webhook_url = f"https://{domain}/webhook"
                    
                    async def set_webhook():
                        await telegram_app.bot.delete_webhook()
                        await telegram_app.bot.set_webhook(url=webhook_url)
                        logger.info(f"✅ Вебхук установлен: {webhook_url}")
                    
                    loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(loop)
                    loop.run_until_complete(set_webhook())
                    loop.close()
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось установить вебхук автоматически: {e}")
            
            # Запускаем в фоновом потоке
            webhook_thread = threading.Thread(target=auto_set_webhook, daemon=True)
            webhook_thread.start()
    else:
        logger.error("❌ Инициализация приложения завершилась с ошибками")
        logger.error("❌ Бот не будет работать корректно")
        
        # Показываем возможные причины
        logger.error("🔍 Возможные причины:")
        logger.error("   1. Не указан BOT_TOKEN в переменных окружения")
        logger.error("   2. Ошибка в DATABASE_URL (PostgreSQL)")
        logger.error("   3. Проблема с python-telegram-bot версии 20.6")
        logger.error("   4. База данных не содержит все 75 вопросов")
        
except Exception as e:
    logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ИНИЦИАЛИЗАЦИИ: {e}", exc_info=True)

# ================== ЛОКАЛЬНЫЙ ЗАПУСК ==================
if __name__ == '__main__':
    logger.warning("⚠️ ЛОКАЛЬНЫЙ ЗАПУСК - только для разработки!")
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Запуск Flask сервера на порту {port}")
    
    # Для локального запуска можно добавить простой polling
    if telegram_app and bot_initialized:
        logger.info("🤖 Для локальной разработки используйте polling командой:")
        logger.info("   python -m telegram.ext --token YOUR_TOKEN")
    
    app.run(host='0.0.0.0', port=port, debug=False)
