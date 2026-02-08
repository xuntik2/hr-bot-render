#!/usr/bin/env python3
"""#!/usr/bin/env python3
"""
ГЛАВНЫЙ ФАЙЛ БОТА ДЛЯ RENDER
Исправленная версия для работы с Python-Telegram-Bot 20.3+
"""
import os
import time
import json
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
from admin_tools import check_database_status, fill_database_manual

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
bot_event_loop = None
update_queue = None

def initialize_app():
    """Инициализация всех компонентов приложения"""
    global telegram_app, search_engine, command_handler, bot_initialized, update_queue
    
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
        
        if faq_count < 50:
            logger.warning(f"⚠️  ВНИМАНИЕ: Загружено только {faq_count} FAQ вместо 75")
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
        
        # Создаем приложение
        telegram_app = Application.builder().token(bot_token).build()
        
        # Создаем очередь для обновлений
        update_queue = asyncio.Queue()
        
        # Регистрация обработчиков команд
        _register_bot_handlers()
        
        # Запускаем обработчик обновлений в фоне
        _start_update_processor()
        
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
        logger.error("❌ Не удалось зарегистрировать обработчики")
        return
    
    # Обработчик команды /start и /help
    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /start от {update.effective_user.id}")
        await command_handler.handle_welcome(update, context)
    
    # Обработчик команды /categories
    async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /categories от {update.effective_user.id}")
        await command_handler.handle_categories(update, context)
    
    # Обработчик команды /search
    async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.message.text.replace('/search', '').strip()
        logger.info(f"📝 /search от {update.effective_user.id}: {query[:50]}...")
        await command_handler.handle_search(update, context, query)
    
    # Обработчик команды /feedback
    async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /feedback от {update.effective_user.id}")
        await command_handler.handle_feedback(update, context)
    
    # Обработчик команды /stats
    async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /stats от {update.effective_user.id}")
        await command_handler.handle_stats(update, context)
    
    # Обработчик команды /clear
    async def clear_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /clear от {update.effective_user.id}")
        await command_handler.handle_clear_cache(update, context)
    
    # Обработчик всех текстовых сообщений
    async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text[:100] if update.message.text else ""
        logger.info(f"📝 Сообщение от {update.effective_user.id}: {text}")
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
    telegram_app.add_handler(CommandHandler("categories", categories_command))
    telegram_app.add_handler(CommandHandler("search", search_command))
    telegram_app.add_handler(CommandHandler("feedback", feedback_command))
    telegram_app.add_handler(CommandHandler("stats", stats_command))
    telegram_app.add_handler(CommandHandler("clear", clear_cache_command))
    
    # Обработчик всех текстовых сообщений
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
    
    logger.info("✅ Обработчики команд зарегистрированы")

def _start_update_processor():
    """Запуск обработчика обновлений в фоновом потоке"""
    if not telegram_app:
        logger.error("❌ Не удалось запустить обработчик обновлений: приложение не создано")
        return
    
    def run_processor():
        """Запуск асинхронного обработчика обновлений"""
        try:
            # Создаем новый event loop для этого потока
            loop = asyncio.new_event_loop()
            asyncio.set_event_loop(loop)
            
            async def process_updates():
                """Асинхронная обработка обновлений"""
                try:
                    # Инициализируем приложение
                    await telegram_app.initialize()
                    logger.info("✅ Приложение Telegram инициализировано")
                    
                    # Запускаем приложение
                    await telegram_app.start()
                    logger.info("✅ Обработчик обновлений запущен")
                    
                    # Бесконечно обрабатываем обновления
                    while True:
                        try:
                            # Ждем обновление из очереди
                            update = await update_queue.get()
                            if update:
                                # Обрабатываем обновление
                                await telegram_app.process_update(update)
                                logger.debug(f"✅ Обработано обновление {update.update_id}")
                        except Exception as e:
                            logger.error(f"❌ Ошибка обработки обновления: {e}")
                except Exception as e:
                    logger.error(f"❌ Ошибка в обработчике обновлений: {e}")
            
            # Запускаем обработчик
            loop.run_until_complete(process_updates())
            
        except Exception as e:
            logger.error(f"❌ Ошибка запуска обработчика обновлений: {e}")
    
    # Запускаем обработчик в отдельном потоке
    processor_thread = threading.Thread(target=run_processor, daemon=True)
    processor_thread.start()
    logger.info("✅ Обработчик обновлений запущен в фоне")

# ================== FLASK РОУТЫ ==================

@app.route('/')
def index():
    """Главная страница"""
    current_status = check_database_status()
    faq_count = len(search_engine.faq_data) if search_engine else 0
    db_type = 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite'
    
    bot_status = "✅ Активен" if bot_initialized else "❌ Ошибка инициализации"
    
    # Определяем статус базы данных
    if 'error' in current_status:
        db_status = f"❌ Ошибка: {current_status['error']}"
        db_class = "error"
    elif not current_status.get('table_exists', False):
        db_status = "❌ Таблица не существует"
        db_class = "error"
    elif current_status.get('total_records', 0) >= 75:
        db_status = f"✅ {current_status['total_records']} вопросов"
        db_class = "status"
    elif current_status.get('total_records', 0) > 0:
        db_status = f"⚠️  {current_status['total_records']} вопросов (из 75)"
        db_class = "warning"
    else:
        db_status = "❌ База пуста"
        db_class = "error"
    
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
            .good {{ color: #28a745; }}
            .bad {{ color: #dc3545; }}
            code {{ background: #f8f9fa; padding: 2px 5px; border-radius: 3px; }}
        </style>
    </head>
    <body>
        <h1>🤖 HR Bot Мечел — Статус: {bot_status}</h1>
        
        <div class="{db_class}">
            <h3>📊 Статус системы:</h3>
            <p><strong>Бот:</strong> <span class="{'' if bot_initialized else 'bad'}">{bot_status}</span></p>
            <p><strong>База данных:</strong> {db_status}</p>
            <p><strong>Тип БД:</strong> {db_type}</p>
            <p><strong>Webhook готов:</strong> {'✅ Да' if bot_initialized else '❌ Нет'}</p>
            {f"<p><strong>Заполнение базы:</strong> {current_status.get('completion_percentage', 0)}%</p>" if 'completion_percentage' in current_status else ''}
            {'<p><strong>Проблема:</strong> Ошибка инициализации Telegram API</p>' if not bot_initialized else ''}
            {'<p><strong>Проблема:</strong> Не все вопросы загружены в базу</p>' if current_status.get('total_records', 0) < 75 else ''}
        </div>
        
        <div class="links">
            <h3>🔗 Полезные ссылки:</h3>
            <a href="/health">Health Check</a>
            <a href="/set_webhook">Установить вебхук</a>
            <a href="/webhook_info">Информация о вебхуке</a>
            <a href="/debug">Диагностика</a>
            <a href="/admin/fill-db">Заполнить базу данных</a>
            <a href="/admin/db-status">Статус БД (JSON)</a>
        </div>
        
        <div style="margin-top: 30px; color: #666; font-size: 14px;">
            <p>Время запуска: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Telegram Bot API: python-telegram-bot v20.3</p>
            <p>Обновление: Исправлена работа вебхуков</p>
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
    
    # Получаем статус базы данных
    db_status = check_database_status()
    db_ok = db_status.get('table_exists', False) and db_status.get('total_records', 0) > 0
    
    # Определяем статус
    if bot_ok and search_ok and db_ok and faq_count >= 10:
        status = "healthy"
        status_code = 200
    elif bot_ok and search_ok:
        status = "degraded"
        status_code = 200
    else:
        status = "unhealthy"
        status_code = 503
    
    status_data = {
        "status": status,
        "service": "hr-bot-mechel",
        "components": {
            "bot": bot_ok,
            "search_engine": search_ok,
            "database_has_data": db_ok
        },
        "details": {
            "faq_count": faq_count,
            "expected_faq_count": 75,
            "bot_initialized": bot_initialized,
            "telegram_app_exists": telegram_app is not None,
            "search_engine_exists": search_engine is not None
        },
        "database": db_status,
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
            "BOT_TOKEN_length": len(os.getenv('BOT_TOKEN', '')),
            "DATABASE_URL_set": bool(os.getenv('DATABASE_URL')),
            "RENDER_EXTERNAL_URL": os.getenv('RENDER_EXTERNAL_URL', 'Not set'),
            "PORT": os.getenv('PORT', 'Not set')
        },
        "bot_status": {
            "initialized": bot_initialized,
            "telegram_app": telegram_app is not None,
            "search_engine": search_engine is not None,
            "faq_count": len(search_engine.faq_data) if search_engine else 0,
            "update_queue_exists": update_queue is not None
        },
        "config_check": {
            "is_postgresql": config.is_postgresql(),
            "bot_token_length": len(config.get_bot_token()) if config.get_bot_token() else 0
        },
        "system_info": {
            "cwd": os.getcwd(),
            "files": [f for f in os.listdir('.') if os.path.isfile(f)]
        },
        "database_status": check_database_status()
    }
    
    return jsonify(info), 200

# ================== АДМИНИСТРАТИВНЫЕ РОУТЫ ==================

@app.route('/admin/fill-db', methods=['GET', 'POST'])
def admin_fill_database():
    """Административный эндпоинт для заполнения базы данных"""
    
    if request.method == 'GET':
        current_status = check_database_status()
        
        status_html = "<h3>📊 Текущий статус базы данных:</h3>"
        if 'error' in current_status:
            status_html += f"<p style='color: red;'><strong>Ошибка:</strong> {current_status['error']}</p>"
        else:
            if current_status['table_exists']:
                status_html += f"""
                <div style="background: {'#d4edda' if current_status['total_records'] >= 75 else '#fff3cd'}; padding: 15px; border-radius: 5px; margin: 10px 0;">
                    <p><strong>Записей в базе:</strong> {current_status['total_records']} из 75</p>
                    <p><strong>Заполнение:</strong> {current_status['completion_percentage']}</p>
                    <p><strong>Категорий:</strong> {current_status['categories_count']}</p>
                    <p><strong>Статус:</strong> {'✅ Полностью заполнена' if current_status['total_records'] >= 75 else '⚠️ Частично заполнена' if current_status['total_records'] > 0 else '❌ Пустая'}</p>
                </div>
                """
                
                if current_status['total_records'] < 75:
                    status_html += """
                    <h3>🛠️ Заполнение базы данных</h3>
                    <p>Нажмите кнопку ниже, чтобы заполнить базу 75 вопросами:</p>
                    <form method="POST" onsubmit="return confirm('Вы уверены? Это перезапишет все существующие данные.');">
                        <button type="submit" style="padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 5px; font-size: 16px;">
                            🚀 Заполнить базу данных (75 вопросов)
                        </button>
                    </form>
                    """
                else:
                    status_html += """
                    <h3>🛠️ Перезапись базы данных</h3>
                    <p>База уже заполнена, но вы можете перезаписать её заново:</p>
                    <form method="POST" onsubmit="return confirm('ВНИМАНИЕ: Все существующие данные будут удалены! Вы уверены?');">
                        <button type="submit" style="padding: 10px 20px; background: #dc3545; color: white; border: none; border-radius: 5px; font-size: 16px;">
                            ⚠️  ПЕРЕЗАПИСАТЬ базу данных
                        </button>
                    </form>
                    """
            else:
                status_html += "<p style='color: red;'>❌ Таблица 'faq' не существует в базе данных</p>"
                status_html += """
                <h3>🛠️ Создание и заполнение базы данных</h3>
                <p>Нажмите кнопку ниже, чтобы создать таблицу и заполнить её 75 вопросами:</p>
                <form method="POST">
                    <button type="submit" style="padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 5px; font-size: 16px;">
                        🚀 Создать и заполнить базу данных
                    </button>
                </form>
                """
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>🛠️ Администрирование базы данных</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
                h1 {{ color: #333; }}
                .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .warning {{ background: #fff3cd; color: #856404; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .info {{ background: #d1ecf1; color: #0c5460; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .back-link {{ display: inline-block; margin-top: 20px; padding: 10px 15px; background: #6c757d; color: white; text-decoration: none; border-radius: 5px; }}
                .back-link:hover {{ background: #545b62; }}
            </style>
        </head>
        <body>
            <h1>🛠️ Администрирование базы данных</h1>
            {status_html}
            <a href="/" class="back-link">← На главную</a>
        </body>
        </html>
        '''
    
    # POST запрос - заполнение базы
    try:
        logger.info("🔄 Запуск ручного заполнения базы данных через веб-интерфейс")
        result = fill_database_manual()
        
        if result.get('success'):
            response_html = f"""
            <div class="success">
                <h3>✅ База данных успешно заполнена!</h3>
                <p><strong>Добавлено вопросов:</strong> {result['stats']['inserted']} из {result['stats']['total_questions']}</p>
                <p><strong>Всего в базе:</strong> {result['stats']['final_count']} записей</p>
                <p><strong>Категорий:</strong> {result['stats']['categories']}</p>
                <p><strong>Заполнение:</strong> {result['details']['completion']}</p>
            </div>
            """
            
            if result['stats'].get('errors', 0) > 0:
                response_html += f"""
                <div class="warning">
                    <p><strong>⚠️ Было {result['stats']['errors']} ошибок при добавлении</strong></p>
                </div>
                """
        else:
            response_html = f"""
            <div class="error">
                <h3>❌ Ошибка при заполнении базы данных</h3>
                <p><strong>Ошибка:</strong> {result.get('error', 'Неизвестная ошибка')}</p>
            </div>
            """
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>🛠️ Результат заполнения базы данных</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
                .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .warning {{ background: #fff3cd; color: #856404; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .back-link {{ display: inline-block; margin-top: 20px; padding: 10px 15px; background: #6c757d; color: white; text-decoration: none; border-radius: 5px; }}
                .back-link:hover {{ background: #545b62; }}
            </style>
        </head>
        <body>
            <h1>🛠️ Результат заполнения базы данных</h1>
            {response_html}
            <div style="margin-top: 20px;">
                <a href="/admin/fill-db" class="back-link">← Проверить статус</a>
                <a href="/" class="back-link" style="margin-left: 10px;">На главную</a>
            </div>
        </body>
        </html>
        '''
        
    except Exception as e:
        logger.error(f"❌ Ошибка в админском интерфейсе: {e}")
        return f'''
        <div class="error">
            <h3>❌ Критическая ошибка</h3>
            <p>{str(e)}</p>
        </div>
        ''', 500

@app.route('/admin/db-status')
def admin_db_status():
    """API эндпоинт для проверки статуса базы данных (JSON)"""
    return jsonify(check_database_status())

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook_endpoint():
    """Ручная установка вебхука"""
    if not telegram_app or not bot_initialized:
        msg = "❌ Бот не инициализирован. Сначала исправьте ошибки инициализации."
        return f'''
        <h1>🔧 Результат установки вебхука</h1>
        <div style="padding: 20px; background: #f8d7da; border-radius: 8px;">
            {msg}
        </div>
        <p style="margin-top: 20px;"><a href="/">← На главную</a></p>
        ''', 500
    
    if request.method == 'GET':
        return '''
        <h1>🔧 Установка вебхук</h1>
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
        domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-render.onrender.com')
        if domain.startswith('https://'):
            domain = domain[8:]
        
        webhook_url = f"https://{domain}/webhook"
        logger.info(f"🔄 Установка вебхука на {webhook_url}")
        
        # Устанавливаем вебхук через requests
        import requests
        
        # Удаляем старый вебхук
        delete_url = f"https://api.telegram.org/bot{config.get_bot_token()}/deleteWebhook"
        response = requests.get(delete_url)
        logger.debug(f"Удаление вебхука: {response.status_code}")
        
        # Устанавливаем новый вебхук
        set_url = f"https://api.telegram.org/bot{config.get_bot_token()}/setWebhook"
        payload = {
            'url': webhook_url,
            'max_connections': 40,
            'allowed_updates': ['message', 'callback_query']
        }
        response = requests.post(set_url, json=payload)
        
        if response.status_code == 200 and response.json().get('ok'):
            msg = f"✅ Вебхук успешно установлен!<br>URL: <code>{webhook_url}</code>"
            logger.info("✅ Вебхук установлен (через requests)")
        else:
            msg = f"❌ Не удалось установить вебхук. Ответ API: {response.text}"
            logger.error(f"Ошибка установки вебхука: {response.text}")
        
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
        import requests
        token = config.get_bot_token()
        info_url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
        response = requests.get(info_url)
        
        if response.status_code == 200:
            info = response.json().get('result', {})
            return f'''
            <h1>ℹ️ Информация о вебхуке</h1>
            <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
                <p><strong>URL:</strong> <code>{info.get('url', 'Не установлен')}</code></p>
                <p><strong>Ожидающих обновлений:</strong> {info.get('pending_update_count', 0)}</p>
                <p><strong>Последняя ошибка:</strong> {info.get('last_error_message', 'Нет ошибок')}</p>
                <p><strong>Макс. соединений:</strong> {info.get('max_connections', 'Не указано')}</p>
            </div>
            <p style="margin-top: 20px;">
                <a href="/">← На главную</a> |
                <a href="/set_webhook">🔧 Установить вебхук</a>
            </p>
            ''', 200
        else:
            return f'<h1>❌ Ошибка</h1><p>Не удалось получить информацию: {response.text}</p>', 500
            
    except Exception as e:
        return f'<h1>❌ Ошибка</h1><p>Не удалось получить информацию: {e}</p>', 500

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Основной эндпоинт для получения обновлений от Telegram"""
    if not telegram_app or not bot_initialized or not update_queue:
        logger.error("❌ Вебхук вызван, но бот не инициализирован или очередь не создана")
        return jsonify({'error': 'Bot not initialized'}), 503
    
    try:
        # Получаем JSON данные
        update_data = request.get_json(force=True, silent=True)
        
        if not update_data:
            logger.error("❌ Не удалось получить JSON данные из запроса")
            return 'Bad Request', 400
        
        update_id = update_data.get('update_id', 'unknown')
        logger.info(f"📨 Получен вебхук от Telegram. update_id: {update_id}")
        
        # Логируем информацию о сообщении
        if 'message' in update_data:
            msg = update_data['message']
            user_id = msg.get('from', {}).get('id', 'unknown')
            text = msg.get('text', 'без текста')[:50]
            logger.info(f"👤 Пользователь {user_id} написал: {text}")
        
        # Создаем Update объект из словаря
        update = Update.de_json(update_data, telegram_app.bot)
        
        if update is None:
            logger.error("❌ Не удалось создать Update объект из данных")
            return 'Bad Request', 400
        
        # Добавляем обновление в очередь асинхронно
        try:
            # Используем asyncio.run_coroutine_threadsafe для добавления в очередь
            # из синхронного контекста Flask
            if bot_event_loop:
                asyncio.run_coroutine_threadsafe(
                    update_queue.put(update), 
                    bot_event_loop
                )
            else:
                # Если нет event loop, пробуем добавить напрямую (для простоты)
                asyncio.create_task(update_queue.put(update))
            
            logger.info(f"✅ Обновление {update_id} добавлено в очередь")
            return '', 200
            
        except Exception as e:
            logger.error(f"❌ Ошибка при добавлении update в очередь: {e}", exc_info=True)
            return 'Internal Server Error', 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}", exc_info=True)
        return 'Internal Server Error', 500

# ================== ИНИЦИАЛИЗАЦИЯ ==================

# Инициализируем приложение при старте
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
                    domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-render.onrender.com')
                    if domain.startswith('https://'):
                        domain = domain[8:]
                    
                    webhook_url = f"https://{domain}/webhook"
                    
                    import requests
                    token = config.get_bot_token()
                    
                    # Удаляем старый вебхук
                    delete_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
                    requests.get(delete_url)
                    
                    # Устанавливаем новый
                    set_url = f"https://api.telegram.org/bot{token}/setWebhook"
                    payload = {
                        'url': webhook_url,
                        'max_connections': 40,
                        'allowed_updates': ['message', 'callback_query']
                    }
                    response = requests.post(set_url, json=payload)
                    
                    if response.status_code == 200 and response.json().get('ok'):
                        logger.info(f"✅ Вебхук установлен: {webhook_url}")
                    else:
                        logger.warning(f"⚠️ Не удалось установить вебхук автоматически: {response.text}")
                        
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось установить вебхук автоматически: {e}")
            
            # Запускаем в фоновом потоке
            webhook_thread = threading.Thread(target=auto_set_webhook, daemon=True)
            webhook_thread.start()
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
    app.run(host='0.0.0.0', port=port, debug=False)ГЛАВНЫЙ ФАЙЛ БОТА ДЛЯ RENDER
Версия с административным интерфейсом для управления базой данных
"""
import os
import time
import json
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
from admin_tools import check_database_status, fill_database_manual

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
bot_started = False

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
        
        if faq_count < 50:  # Если мало вопросов - это проблема
            logger.warning(f"⚠️  ВНИМАНИЕ: Загружено только {faq_count} FAQ вместо 75")
            logger.warning("   Бот будет работать, но с ограниченной базой знаний")
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
        
        # Создаем приложение с настройками таймаутов (версия 20.3)
        telegram_app = Application.builder().token(bot_token).build()
        
        # Регистрация обработчиков команд
        _register_bot_handlers()
        
        # Инициализируем приложение
        logger.info("✅ Приложение Telegram создано")
        bot_initialized = True
        
    except Exception as e:
        logger.error(f"❌ Ошибка инициализации Telegram бота: {e}", exc_info=True)
        telegram_app = None
        return False
    
    logger.info("✅ Приложение полностью инициализировано")
    return True

def _register_bot_handlers():
    """Регистрация обработчиков команд бота (ТОЛЬКО ЛАТИНСКИЕ КОМАНДЫ)"""
    if not command_handler or not telegram_app:
        logger.error("❌ Не удалось зарегистрировать обработчики")
        return
    
    # Обработчик команды /start и /help
    async def start_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /start от {update.effective_user.id}")
        await command_handler.handle_welcome(update, context)
    
    # Обработчик команды /categories (было /категории)
    async def categories_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /categories от {update.effective_user.id}")
        await command_handler.handle_categories(update, context)
    
    # Обработчик команды /search (было /поиск)
    async def search_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        query = update.message.text.replace('/search', '').replace('/поиск', '').strip()
        logger.info(f"📝 /search от {update.effective_user.id}: {query[:50]}...")
        await command_handler.handle_search(update, context, query)
    
    # Обработчик команды /feedback (было /отзыв)
    async def feedback_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /feedback от {update.effective_user.id}")
        await command_handler.handle_feedback(update, context)
    
    # Обработчик команды /stats (было /статистика)
    async def stats_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /stats от {update.effective_user.id}")
        await command_handler.handle_stats(update, context)
    
    # Обработчик команды /clear (было /очистить)
    async def clear_cache_command(update: Update, context: ContextTypes.DEFAULT_TYPE):
        logger.info(f"📝 /clear от {update.effective_user.id}")
        await command_handler.handle_clear_cache(update, context)
    
    # Обработчик всех текстовых сообщений
    async def handle_all_messages(update: Update, context: ContextTypes.DEFAULT_TYPE):
        text = update.message.text[:100] if update.message.text else ""
        logger.info(f"📝 Сообщение от {update.effective_user.id}: {text}")
        try:
            if command_handler:
                await command_handler.handle_text_message(update, context)
            else:
                await update.message.reply_text("⚠️ Бот временно не готов к работе. Попробуйте позже.")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки: {e}", exc_info=True)
            await update.message.reply_text("❌ Произошла ошибка. Пожалуйста, попробуйте ещё раз.")
    
    # Регистрация обработчиков - ТОЛЬКО ЛАТИНСКИЕ КОМАНДЫ (версия 20.3)
    telegram_app.add_handler(CommandHandler("start", start_command))
    telegram_app.add_handler(CommandHandler("help", start_command))
    telegram_app.add_handler(CommandHandler("categories", categories_command))
    telegram_app.add_handler(CommandHandler("search", search_command))
    telegram_app.add_handler(CommandHandler("feedback", feedback_command))
    telegram_app.add_handler(CommandHandler("stats", stats_command))
    telegram_app.add_handler(CommandHandler("clear", clear_cache_command))
    
    # Обработчик всех текстовых сообщений (должен быть последним)
    telegram_app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, handle_all_messages))
    
    logger.info("✅ Обработчики команд зарегистрированы (латинские команды)")

# ================== FLASK РОУТЫ ==================

@app.route('/')
def index():
    """Главная страница"""
    current_status = check_database_status()
    faq_count = len(search_engine.faq_data) if search_engine else 0
    db_type = 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite'
    
    bot_status = "✅ Активен" if bot_initialized else "❌ Ошибка инициализации"
    
    # Определяем статус базы данных
    if 'error' in current_status:
        db_status = f"❌ Ошибка: {current_status['error']}"
        db_class = "error"
    elif not current_status.get('table_exists', False):
        db_status = "❌ Таблица не существует"
        db_class = "error"
    elif current_status.get('total_records', 0) >= 75:
        db_status = f"✅ {current_status['total_records']} вопросов"
        db_class = "status"
    elif current_status.get('total_records', 0) > 0:
        db_status = f"⚠️  {current_status['total_records']} вопросов (из 75)"
        db_class = "warning"
    else:
        db_status = "❌ База пуста"
        db_class = "error"
    
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
            .good {{ color: #28a745; }}
            .bad {{ color: #dc3545; }}
            code {{ background: #f8f9fa; padding: 2px 5px; border-radius: 3px; }}
        </style>
    </head>
    <body>
        <h1>🤖 HR Bot Мечел — Статус: {bot_status}</h1>
        
        <div class="{db_class}">
            <h3>📊 Статус системы:</h3>
            <p><strong>Бот:</strong> <span class="{'' if bot_initialized else 'bad'}">{bot_status}</span></p>
            <p><strong>База данных:</strong> {db_status}</p>
            <p><strong>Тип БД:</strong> {db_type}</p>
            <p><strong>Webhook готов:</strong> {'✅ Да' if bot_initialized else '❌ Нет'}</p>
            {f"<p><strong>Заполнение базы:</strong> {current_status.get('completion_percentage', 0)}%</p>" if 'completion_percentage' in current_status else ''}
            {'<p><strong>Проблема:</strong> Ошибка инициализации Telegram API</p>' if not bot_initialized else ''}
            {'<p><strong>Проблема:</strong> Не все вопросы загружены в базу</p>' if current_status.get('total_records', 0) < 75 else ''}
        </div>
        
        <div class="links">
            <h3>🔗 Полезные ссылки:</h3>
            <a href="/health">Health Check</a>
            <a href="/set_webhook">Установить вебхук</a>
            <a href="/webhook_info">Информация о вебхуке</a>
            <a href="/debug">Диагностика</a>
            <a href="/admin/fill-db">Заполнить базу данных</a>
            <a href="/admin/db-status">Статус БД (JSON)</a>
        </div>
        
        <div style="margin-top: 30px; color: #666; font-size: 14px;">
            <p>Время запуска: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
            <p>Telegram Bot API: python-telegram-bot v20.3</p>
            <p>Обновление: Добавлен административный интерфейс</p>
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
    
    # Получаем статус базы данных
    db_status = check_database_status()
    db_ok = db_status.get('table_exists', False) and db_status.get('total_records', 0) > 0
    
    # Определяем статус
    if bot_ok and search_ok and db_ok and faq_count >= 10:
        status = "healthy"
        status_code = 200
    elif bot_ok and search_ok:
        status = "degraded"
        status_code = 200  # Возвращаем 200, чтобы Render не перезапускал
    else:
        status = "unhealthy"
        status_code = 503  # 503 - Service Unavailable
    
    status_data = {
        "status": status,
        "service": "hr-bot-mechel",
        "components": {
            "bot": bot_ok,
            "search_engine": search_ok,
            "database_has_data": db_ok
        },
        "details": {
            "faq_count": faq_count,
            "expected_faq_count": 75,
            "bot_initialized": bot_initialized,
            "telegram_app_exists": telegram_app is not None,
            "search_engine_exists": search_engine is not None
        },
        "database": db_status,
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
            "BOT_TOKEN_length": len(os.getenv('BOT_TOKEN', '')),
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
        },
        "system_info": {
            "cwd": os.getcwd(),
            "files": [f for f in os.listdir('.') if os.path.isfile(f)]
        },
        "database_status": check_database_status()
    }
    
    return jsonify(info), 200

# ================== АДМИНИСТРАТИВНЫЕ РОУТЫ ==================

@app.route('/admin/fill-db', methods=['GET', 'POST'])
def admin_fill_database():
    """Административный эндпоинт для заполнения базы данных"""
    
    # Проверяем права администратора (по IP или токену)
    # Для простоты разрешаем всем в демонстрационных целях
    # В продакшене добавьте проверку авторизации
    
    if request.method == 'GET':
        # Показываем форму
        current_status = check_database_status()
        
        status_html = "<h3>📊 Текущий статус базы данных:</h3>"
        if 'error' in current_status:
            status_html += f"<p style='color: red;'><strong>Ошибка:</strong> {current_status['error']}</p>"
        else:
            if current_status['table_exists']:
                status_html += f"""
                <div style="background: {'#d4edda' if current_status['total_records'] >= 75 else '#fff3cd'}; padding: 15px; border-radius: 5px; margin: 10px 0;">
                    <p><strong>Записей в базе:</strong> {current_status['total_records']} из 75</p>
                    <p><strong>Заполнение:</strong> {current_status['completion_percentage']}</p>
                    <p><strong>Категорий:</strong> {current_status['categories_count']}</p>
                    <p><strong>Статус:</strong> {'✅ Полностью заполнена' if current_status['total_records'] >= 75 else '⚠️ Частично заполнена' if current_status['total_records'] > 0 else '❌ Пустая'}</p>
                </div>
                """
                
                if current_status['total_records'] < 75:
                    status_html += """
                    <h3>🛠️ Заполнение базы данных</h3>
                    <p>Нажмите кнопку ниже, чтобы заполнить базу 75 вопросами:</p>
                    <form method="POST" onsubmit="return confirm('Вы уверены? Это перезапишет все существующие данные.');">
                        <button type="submit" style="padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 5px; font-size: 16px;">
                            🚀 Заполнить базу данных (75 вопросов)
                        </button>
                    </form>
                    """
                else:
                    status_html += """
                    <h3>🛠️ Перезапись базы данных</h3>
                    <p>База уже заполнена, но вы можете перезаписать её заново:</p>
                    <form method="POST" onsubmit="return confirm('ВНИМАНИЕ: Все существующие данные будут удалены! Вы уверены?');">
                        <button type="submit" style="padding: 10px 20px; background: #dc3545; color: white; border: none; border-radius: 5px; font-size: 16px;">
                            ⚠️  ПЕРЕЗАПИСАТЬ базу данных
                        </button>
                    </form>
                    """
            else:
                status_html += "<p style='color: red;'>❌ Таблица 'faq' не существует в базе данных</p>"
                status_html += """
                <h3>🛠️ Создание и заполнение базы данных</h3>
                <p>Нажмите кнопку ниже, чтобы создать таблицу и заполнить её 75 вопросами:</p>
                <form method="POST">
                    <button type="submit" style="padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 5px; font-size: 16px;">
                        🚀 Создать и заполнить базу данных
                    </button>
                </form>
                """
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>🛠️ Администрирование базы данных</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
                h1 {{ color: #333; }}
                .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .warning {{ background: #fff3cd; color: #856404; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .info {{ background: #d1ecf1; color: #0c5460; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .back-link {{ display: inline-block; margin-top: 20px; padding: 10px 15px; background: #6c757d; color: white; text-decoration: none; border-radius: 5px; }}
                .back-link:hover {{ background: #545b62; }}
            </style>
        </head>
        <body>
            <h1>🛠️ Администрирование базы данных</h1>
            {status_html}
            <a href="/" class="back-link">← На главную</a>
        </body>
        </html>
        '''
    
    # POST запрос - заполнение базы
    try:
        logger.info("🔄 Запуск ручного заполнения базы данных через веб-интерфейс")
        result = fill_database_manual()
        
        if result.get('success'):
            response_html = f"""
            <div class="success">
                <h3>✅ База данных успешно заполнена!</h3>
                <p><strong>Добавлено вопросов:</strong> {result['stats']['inserted']} из {result['stats']['total_questions']}</p>
                <p><strong>Всего в базе:</strong> {result['stats']['final_count']} записей</p>
                <p><strong>Категорий:</strong> {result['stats']['categories']}</p>
                <p><strong>Заполнение:</strong> {result['details']['completion']}</p>
            </div>
            """
            
            if result['stats'].get('errors', 0) > 0:
                response_html += f"""
                <div class="warning">
                    <p><strong>⚠️ Было {result['stats']['errors']} ошибок при добавлении</strong></p>
                </div>
                """
        else:
            response_html = f"""
            <div class="error">
                <h3>❌ Ошибка при заполнении базы данных</h3>
                <p><strong>Ошибка:</strong> {result.get('error', 'Неизвестная ошибка')}</p>
            </div>
            """
        
        return f'''
        <!DOCTYPE html>
        <html>
        <head>
            <title>🛠️ Результат заполнения базы данных</title>
            <meta charset="utf-8">
            <meta name="viewport" content="width=device-width, initial-scale=1">
            <style>
                body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
                .success {{ background: #d4edda; color: #155724; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .warning {{ background: #fff3cd; color: #856404; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .error {{ background: #f8d7da; color: #721c24; padding: 15px; border-radius: 8px; margin: 20px 0; }}
                .back-link {{ display: inline-block; margin-top: 20px; padding: 10px 15px; background: #6c757d; color: white; text-decoration: none; border-radius: 5px; }}
                .back-link:hover {{ background: #545b62; }}
            </style>
        </head>
        <body>
            <h1>🛠️ Результат заполнения базы данных</h1>
            {response_html}
            <div style="margin-top: 20px;">
                <a href="/admin/fill-db" class="back-link">← Проверить статус</a>
                <a href="/" class="back-link" style="margin-left: 10px;">На главную</a>
            </div>
        </body>
        </html>
        '''
        
    except Exception as e:
        logger.error(f"❌ Ошибка в админском интерфейсе: {e}")
        return f'''
        <div class="error">
            <h3>❌ Критическая ошибка</h3>
            <p>{str(e)}</p>
        </div>
        ''', 500

@app.route('/admin/db-status')
def admin_db_status():
    """API эндпоинт для проверки статуса базы данных (JSON)"""
    return jsonify(check_database_status())

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook_endpoint():
    """Ручная установка вебхука"""
    if not telegram_app or not bot_initialized:
        msg = "❌ Бот не инициализирован. Сначала исправьте ошибки инициализации."
        return f'''
        <h1>🔧 Результат установки вебхука</h1>
        <div style="padding: 20px; background: #f8d7da; border-radius: 8px;">
            {msg}
        </div>
        <p style="margin-top: 20px;"><a href="/">← На главную</a></p>
        ''', 500
    
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
        domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-render.onrender.com')
        if domain.startswith('https://'):
            domain = domain[8:]
        
        webhook_url = f"https://{domain}/webhook"
        logger.info(f"🔄 Установка вебхука на {webhook_url}")
        
        # Устанавливаем вебхук (синхронная версия для избежания проблем с блокировками)
        import requests
        
        # Удаляем старый вебхук
        delete_url = f"https://api.telegram.org/bot{config.get_bot_token()}/deleteWebhook"
        response = requests.get(delete_url)
        logger.debug(f"Удаление вебхука: {response.status_code}")
        
        # Устанавливаем новый вебхук
        set_url = f"https://api.telegram.org/bot{config.get_bot_token()}/setWebhook"
        payload = {
            'url': webhook_url,
            'max_connections': 40,
            'allowed_updates': ['message', 'callback_query']
        }
        response = requests.post(set_url, json=payload)
        
        if response.status_code == 200 and response.json().get('ok'):
            msg = f"✅ Вебхук успешно установлен!<br>URL: <code>{webhook_url}</code>"
            logger.info("✅ Вебхук установлен (через requests)")
        else:
            msg = f"❌ Не удалось установить вебхук. Ответ API: {response.text}"
            logger.error(f"Ошибка установки вебхука: {response.text}")
        
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
        # Получаем информацию о вебхуке через requests (синхронно)
        import requests
        token = config.get_bot_token()
        info_url = f"https://api.telegram.org/bot{token}/getWebhookInfo"
        response = requests.get(info_url)
        
        if response.status_code == 200:
            info = response.json().get('result', {})
            return f'''
            <h1>ℹ️ Информация о вебхуке</h1>
            <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
                <p><strong>URL:</strong> <code>{info.get('url', 'Не установлен')}</code></p>
                <p><strong>Ожидающих обновлений:</strong> {info.get('pending_update_count', 0)}</p>
                <p><strong>Последняя ошибка:</strong> {info.get('last_error_message', 'Нет ошибок')}</p>
                <p><strong>Макс. соединений:</strong> {info.get('max_connections', 'Не указано')}</p>
            </div>
            <p style="margin-top: 20px;">
                <a href="/">← На главную</a> |
                <a href="/set_webhook">🔧 Установить вебхук</a>
            </p>
            ''', 200
        else:
            return f'<h1>❌ Ошибка</h1><p>Не удалось получить информацию: {response.text}</p>', 500
            
    except Exception as e:
        return f'<h1>❌ Ошибка</h1><p>Не удалось получить информацию: {e}</p>', 500

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Основной эндпоинт для получения обновлений от Telegram"""
    if not telegram_app or not bot_initialized:
        logger.error("❌ Вебхук вызван, но бот не инициализирован")
        return jsonify({'error': 'Bot not initialized'}), 503
    
    # Проверяем content-type
    content_type = request.headers.get('content-type', '')
    if 'application/json' not in content_type:
        logger.warning(f"❌ Неверный content-type: {content_type}")
        return 'Bad Request', 400
    
    try:
        # Получаем JSON данные
        update_data = request.get_json(force=True, silent=True)
        
        if not update_data:
            logger.error("❌ Не удалось получить JSON данные из запроса")
            return 'Bad Request', 400
        
        logger.debug(f"📨 Получен вебхук от Telegram. update_id: {update_data.get('update_id', 'unknown')}")
        
        # Создаем Update объект из словаря
        update = Update.de_json(update_data, telegram_app.bot)
        
        if update is None:
            logger.error("❌ Не удалось создать Update объект из данных")
            return 'Bad Request', 400
        
        # Обрабатываем update с использованием update_queue
        try:
            # Используем update_queue вместо ручного создания event loop
            # Это более правильный способ для python-telegram-bot 20.3+
            telegram_app.update_queue.put_nowait(update)
            
            # Telegram ожидает ответ в течение 10 секунд
            # Мы возвращаем ответ сразу, а обработка идет в фоне
            return '', 200
            
        except Exception as e:
            logger.error(f"❌ Ошибка при добавлении update в очередь: {e}", exc_info=True)
            return 'Internal Server Error', 500
            
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}", exc_info=True)
        return 'Internal Server Error', 500

# ================== ИНИЦИАЛИЗАЦИЯ ==================

# Инициализируем приложение при старте
try:
    logger.info("🔧 Запуск инициализации приложения...")
    success = initialize_app()
    
    if success:
        logger.info(f"✅ Приложение готово к работе на порту {os.getenv('PORT', 10000)}")
        logger.info("🤖 Бот работает в режиме вебхуков")
        
        # Автоматическая установка вебхука при запуске (синхронная версия)
        AUTO_SET_WEBHOOK = os.getenv('AUTO_SET_WEBHOOK', 'true').lower() == 'true'
        if AUTO_SET_WEBHOOK and bot_initialized:
            logger.info("🔄 Автоматическая установка вебхука...")
            
            def auto_set_webhook():
                try:
                    domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-render.onrender.com')
                    if domain.startswith('https://'):
                        domain = domain[8:]
                    
                    webhook_url = f"https://{domain}/webhook"
                    
                    # Используем синхронный подход через requests
                    import requests
                    token = config.get_bot_token()
                    
                    # Удаляем старый вебхук
                    delete_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
                    requests.get(delete_url)
                    
                    # Устанавливаем новый
                    set_url = f"https://api.telegram.org/bot{token}/setWebhook"
                    payload = {
                        'url': webhook_url,
                        'max_connections': 40,
                        'allowed_updates': ['message', 'callback_query']
                    }
                    response = requests.post(set_url, json=payload)
                    
                    if response.status_code == 200 and response.json().get('ok'):
                        logger.info(f"✅ Вебхук установлен: {webhook_url}")
                    else:
                        logger.warning(f"⚠️ Не удалось установить вебхук автоматически: {response.text}")
                        
                except Exception as e:
                    logger.warning(f"⚠️ Не удалось установить вебхук автоматически: {e}")
            
            # Запускаем в фоновом потоке
            webhook_thread = threading.Thread(target=auto_set_webhook, daemon=True)
            webhook_thread.start()
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
    app.run(host='0.0.0.0', port=port, debug=False)

