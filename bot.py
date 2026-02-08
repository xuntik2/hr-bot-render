#!/usr/bin/env python3
"""
ГЛАВНЫЙ ФАЙЛ БОТА ДЛЯ RENDER
С использованием python-telegram-bot v20.6
"""
import os
import time
import logging
import asyncio
from threading import Thread
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

def initialize_app():
    """Инициализация всех компонентов приложения"""
    global telegram_app, search_engine, command_handler
    
    logger.info("=" * 60)
    logger.info("🚀 ИНИЦИАЛИЗАЦИЯ КОРПОРАТИВНОГО БОТА МЕЧЕЛ")
    logger.info("=" * 60)
    
    # Проверка конфигурации
    if not config.validate():
        raise RuntimeError("Конфигурация не прошла валидацию")
    
    # Инициализация поискового движка
    try:
        search_engine = SearchEngine()
        logger.info(f"✅ Поисковый движок готов. FAQ: {len(search_engine.faq_data)}")
    except Exception as e:
        logger.error(f"❌ Ошибка поискового движка: {e}", exc_info=True)
        search_engine = None
    
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
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Telegram бота: {e}")
        telegram_app = None
    
    logger.info("✅ Приложение полностью инициализировано")
    return True

def _register_bot_handlers():
    """Регистрация обработчиков команд бота"""
    if not command_handler or not telegram_app:
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

# ================== FLASK РОУТЫ ==================

@app.route('/')
def index():
    """Главная страница"""
    faq_count = len(search_engine.faq_data) if search_engine else 0
    db_type = 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite'
    
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
            .links a {{ display: inline-block; margin: 10px 15px 10px 0; padding: 10px 20px;
                      background: #007bff; color: white; text-decoration: none; border-radius: 5px; }}
            .links a:hover {{ background: #0056b3; }}
        </style>
    </head>
    <body>
        <h1>🤖 HR Bot Мечел — Успешно работает!</h1>
        <div class="status">
            <h3>✅ Статус: Бот активен</h3>
            <p>Сервис запущен на Render с использованием вебхуков.</p>
            <p><strong>FAQ в базе:</strong> {faq_count}</p>
            <p><strong>Тип БД:</strong> {db_type}</p>
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
    status = {
        "status": "healthy",
        "service": "hr-bot-mechel",
        "bot_initialized": telegram_app is not None,
        "search_engine": search_engine is not None,
        "faq_count": len(search_engine.faq_data) if search_engine else 0,
        "database": "postgresql" if os.getenv('DATABASE_URL') else "sqlite"
    }
    return jsonify(status), 200

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
    try:
        domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-mechel.onrender.com')
        if domain.startswith('https://'):
            domain = domain[8:]
        
        webhook_url = f"https://{domain}/webhook"
        logger.info(f"🔄 Установка вебхука на {webhook_url}")
        
        # Устанавливаем вебхук асинхронно
        async def set_webhook_async():
            await telegram_app.bot.set_webhook(
                url=webhook_url,
                max_connections=40,
                allowed_updates=['message', 'callback_query']
            )
        
        # Запускаем асинхронную функцию
        asyncio.run(set_webhook_async())
        
        msg = f"✅ Вебхук успешно установлен!<br>URL: <code>{webhook_url}</code>"
        logger.info("✅ Вебхук установлен")
        
        # Получаем информацию о вебхуке
        async def get_webhook_info_async():
            webhook_info = await telegram_app.bot.get_webhook_info()
            return webhook_info
        
        webhook_info = asyncio.run(get_webhook_info_async())
        msg += f"<br><br>📊 Информация от Telegram:<br>"
        msg += f"• Ожидающих обновлений: {webhook_info.pending_update_count}<br>"
        msg += f"• Последняя ошибка: {webhook_info.last_error_message or 'нет'}<br>"
        msg += f"• URL: {webhook_info.url}"
        
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
    try:
        # Получаем информацию о вебхуке асинхронно
        async def get_webhook_info_async():
            return await telegram_app.bot.get_webhook_info()
        
        info = asyncio.run(get_webhook_info_async())
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
    except Exception as e:
        return f'<h1>❌ Ошибка</h1><p>Не удалось получить информацию: {e}</p>', 500

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Основной эндпоинт для получения обновлений от Telegram"""
    if request.headers.get('content-type') == 'application/json':
        try:
            json_string = request.get_data().decode('utf-8')
            
            # Создаем Update из JSON
            update = Update.de_json(json_string, telegram_app.bot)
            
            # Обрабатываем update асинхронно
            async def process_update_async():
                await telegram_app.process_update(update)
            
            # Запускаем в отдельном потоке, чтобы не блокировать Flask
            Thread(target=lambda: asyncio.run(process_update_async())).start()
            
            return '', 200
            
        except Exception as e:
            logger.error(f"❌ Ошибка обработки вебхука: {e}", exc_info=True)
            return 'Internal Server Error', 500
    
    return 'Bad Request', 400

async def main_async():
    """Асинхронная основная функция для запуска бота"""
    # Автоматическая установка вебхука
    AUTO_SET_WEBHOOK = os.getenv('AUTO_SET_WEBHOOK', 'true').lower() == 'true'
    if AUTO_SET_WEBHOOK and telegram_app:
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

def run_bot():
    """Запуск бота в отдельном потоке"""
    try:
        # Запускаем асинхронную основную функцию
        asyncio.run(main_async())
        
        # Бот теперь работает через вебхуки, поэтому просто логируем
        logger.info(f"✅ Приложение готово к работе на порту {os.getenv('PORT', 10000)}")
        logger.info("✅ Бот настроен на работу через вебхуки")
        
    except Exception as e:
        logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ИНИЦИАЛИЗАЦИИ: {e}", exc_info=True)
        raise

# ================== ИНИЦИАЛИЗАЦИЯ ==================

# Инициализируем приложение сразу при импорте
try:
    initialize_app()
    
    # Запускаем бота в фоновом потоке
    bot_thread = Thread(target=run_bot, daemon=True)
    bot_thread.start()
    
    logger.info("✅ Инициализация завершена, бот запущен в фоновом режиме")
    
except Exception as e:
    logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ИНИЦИАЛИЗАЦИИ: {e}", exc_info=True)
    raise

# ================== ЛОКАЛЬНЫЙ ЗАПУСК ==================
if __name__ == '__main__':
    logger.warning("⚠️ ЛОКАЛЬНЫЙ ЗАПУСК - только для разработки!")
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Запуск Flask сервера на порту {port}")
    
    # Для локального запуска также запускаем polling
    async def local_polling():
        await telegram_app.initialize()
        await telegram_app.start()
        logger.info("🤖 Бот запущен в режиме polling")
        await telegram_app.updater.start_polling()
        
        # Ждем завершения
        await telegram_app.updater.idle()
    
    # Запускаем polling в отдельном потоке
    if telegram_app:
        polling_thread = Thread(
            target=lambda: asyncio.run(local_polling()),
            daemon=True
        )
        polling_thread.start()
    
    app.run(host='0.0.0.0', port=port, debug=False)
