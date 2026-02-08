#!/usr/bin/env python3
"""
ГЛАВНЫЙ ФАЙЛ БОТА ДЛЯ RENDER
Версия 4.0 - Исправлены критические ошибки
Чистая архитектура: Flask для вебхуков + PTB для обработки
"""
import os
import time
import json
import logging
import asyncio
import threading
import secrets
import traceback
from functools import wraps
from urllib.parse import urlparse
from concurrent.futures import ThreadPoolExecutor

from flask import Flask, request, jsonify
from werkzeug.middleware.proxy_fix import ProxyFix

from telegram import Update
from telegram.ext import (
    ApplicationBuilder,
    CommandHandler,
    MessageHandler,
    filters,
    ContextTypes,
    Application
)
from telegram.request import HTTPXRequest

from config import config
from search_engine import SearchEngine
from bot_handlers import BotCommandHandler
from admin_tools import check_database_status, fill_database_manual

# ================== НАСТРОЙКА ЛОГИРОВАНИЯ ==================
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Уменьшаем шум от внешних библиотек
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

# ================== КОНСТАНТЫ И УТИЛИТЫ ==================
def mask_token(token: str) -> str:
    """Безопасное маскирование токена для логов"""
    if not token or len(token) < 10:
        return '***'
    return token[:6] + '***' + token[-4:]

def validate_url(url: str) -> tuple[bool, str]:
    """Валидация URL и очистка от пробелов"""
    if not url:
        return False, "URL пуст"
    
    # Убираем все пробелы
    url = url.strip().replace(' ', '')
    
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            # Если нет схемы, добавляем https://
            if '://' not in url:
                url = 'https://' + url
                parsed = urlparse(url)
            
            if not parsed.netloc:
                return False, "Неверный формат URL"
        
        return True, url
    except Exception as e:
        return False, f"Ошибка парсинга URL: {e}"

# ================== КЛАСС МЕНЕДЖЕРА БОТА ==================
class BotManager:
    """Потокобезопасный менеджер бота с исправленной архитектурой"""
    
    def __init__(self):
        self.application = None
        self.search_engine = None
        self.command_handler = None
        self.bot_initialized = False
        self.main_loop = None
        self.bot_thread = None
        self.lock = threading.Lock()
        self.update_queue = asyncio.Queue(maxsize=1000)
        self.processing_semaphore = asyncio.Semaphore(10)  # Ограничиваем параллельные обновления
        
    def initialize(self) -> bool:
        """Инициализация всех компонентов бота (синхронная)"""
        with self.lock:
            try:
                logger.info("=" * 60)
                logger.info("🚀 ИНИЦИАЛИЗАЦИЯ КОРПОРАТИВНОГО БОТА МЕЧЕЛ")
                logger.info("Версия 4.0 - Исправлены критические ошибки")
                logger.info("=" * 60)
                
                # Проверка конфигурации
                if not config.validate():
                    logger.error("❌ Конфигурация не прошла валидацию")
                    return False
                
                # Инициализация поискового движка
                self.search_engine = SearchEngine()
                faq_count = len(self.search_engine.faq_data) if self.search_engine else 0
                
                # Строгие критерии для продакшена
                if faq_count < 70:
                    logger.warning(f"⚠️  Загружено {faq_count} FAQ из 75 (минимум 70 для работы)")
                    if faq_count < 20:
                        logger.error(f"❌ Недостаточно FAQ для работы бота: {faq_count}")
                        return False
                else:
                    logger.info(f"✅ Поисковый движок готов. FAQ: {faq_count}/75")
                
                # Инициализация обработчиков
                self.command_handler = BotCommandHandler(self.search_engine)
                
                # Создание и настройка приложения Telegram
                bot_token = config.get_bot_token()
                if not bot_token or bot_token == 'ВАШ_ТОКЕН_ЗДЕСЬ':
                    logger.error("❌ Не указан BOT_TOKEN или используется значение по умолчанию")
                    return False
                
                # Маскируем токен для логов
                masked_token = mask_token(bot_token)
                logger.info(f"📱 Создание приложения Telegram с токеном: {masked_token}")
                
                # Конфигурация HTTP-клиента с исправленными настройками
                request_config = HTTPXRequest(
                    connection_pool_size=50,          # Оптимальный размер для Render
                    read_timeout=30.0,
                    write_timeout=30.0,
                    connect_timeout=30.0,
                    pool_timeout=30.0,
                    http_version='1.1'
                )
                
                # Сборка приложения
                self.application = (
                    ApplicationBuilder()
                    .token(bot_token)
                    .request(request_config)
                    .concurrent_updates(True)        # Разрешаем конкурентную обработку
                    .pool_timeout(30.0)
                    .get_updates_read_timeout(20.0)
                    .get_updates_write_timeout(20.0)
                    .get_updates_connect_timeout(20.0)
                    .build()
                )
                
                # Регистрация обработчиков
                self._register_handlers()
                
                self.bot_initialized = True
                logger.info("✅ Приложение Telegram создано и настроено")
                return True
                
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации бота: {e}\n{traceback.format_exc()}")
                return False
    
    def _register_handlers(self):
        """Регистрация обработчиков команд"""
        if not self.command_handler or not self.application:
            return
        
        # Основные команды
        self.application.add_handler(CommandHandler("start", self.command_handler.handle_welcome))
        self.application.add_handler(CommandHandler("help", self.command_handler.handle_welcome))
        self.application.add_handler(CommandHandler("categories", self.command_handler.handle_categories))
        self.application.add_handler(CommandHandler("search", self.command_handler.handle_search))
        self.application.add_handler(CommandHandler("feedback", self.command_handler.handle_feedback))
        
        # Админские команды
        self.application.add_handler(CommandHandler("stats", self.command_handler.handle_stats))
        self.application.add_handler(CommandHandler("clear", self.command_handler.handle_clear_cache))
        
        # Обработчик текстовых сообщений
        self.application.add_handler(
            MessageHandler(
                filters.TEXT & ~filters.COMMAND,
                self.command_handler.handle_text_message
            )
        )
        
        # Обработчик ошибок
        self.application.add_error_handler(self._error_handler)
        
        logger.info("✅ Обработчики команд зарегистрированы")
    
    async def _error_handler(self, update: object, context):
        """Обработчик ошибок приложения"""
        logger.error(f"Ошибка в обработчике бота: {context.error}", exc_info=True)
    
    async def _setup_webhook(self):
        """Настройка вебхука (асинхронная)"""
        if not self.application or not self.bot_initialized:
            return False
        
        try:
            # Получаем и валидируем URL
            raw_url = os.getenv('RENDER_EXTERNAL_URL', '').strip()
            if not raw_url:
                logger.warning("⚠️  RENDER_EXTERNAL_URL не установлен, вебхук не будет установлен")
                return False
            
            is_valid, clean_url = validate_url(raw_url)
            if not is_valid:
                logger.error(f"❌ Неверный формат RENDER_EXTERNAL_URL: {clean_url}")
                return False
            
            # Формируем URL вебхука
            webhook_url = f"{clean_url}/telegram_webhook"
            logger.info(f"🌐 Настройка вебхука на: {webhook_url}")
            
            # Инициализируем и запускаем приложение
            await self.application.initialize()
            await self.application.start()
            
            # Устанавливаем вебхук с правильным URL (БЕЗ лишних пробелов!)
            bot_token = config.get_bot_token()
            webhook_info = await self.application.bot.set_webhook(
                url=webhook_url,
                max_connections=50,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            
            if webhook_info:
                logger.info(f"✅ Вебхук успешно установлен")
                logger.debug(f"Информация о вебхуке: {webhook_info}")
            else:
                logger.warning("⚠️  Не удалось получить подтверждение установки вебхука")
            
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка настройки вебхука: {e}\n{traceback.format_exc()}")
            return False
    
    def run_bot_in_background(self):
        """Запуск бота в фоновом потоке с восстановлением при падении"""
        def run_loop():
            retry_count = 0
            max_retries = 5
            
            while retry_count < max_retries:
                try:
                    logger.info(f"🔄 Запуск event loop бота (попытка {retry_count + 1}/{max_retries})")
                    
                    # Создаем новый event loop для этого потока
                    self.main_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self.main_loop)
                    
                    # Запускаем настройку вебхука
                    self.main_loop.run_until_complete(self._setup_webhook())
                    
                    logger.info("✅ Бот запущен и готов к работе")
                    
                    # Запускаем обработку обновлений из очереди
                    self.main_loop.create_task(self._process_update_queue())
                    
                    # Запускаем loop навсегда
                    self.main_loop.run_forever()
                    
                except Exception as e:
                    retry_count += 1
                    logger.error(f"❌ Критическая ошибка в event loop (попытка {retry_count}): {e}\n{traceback.format_exc()}")
                    
                    # Очищаем loop при ошибке
                    if self.main_loop and not self.main_loop.is_closed():
                        try:
                            self.main_loop.close()
                        except:
                            pass
                    
                    # Экспоненциальная задержка перед повторной попыткой
                    if retry_count < max_retries:
                        delay = min(30, 2 ** retry_count)
                        logger.info(f"⏳ Повторный запуск через {delay} секунд...")
                        time.sleep(delay)
                    else:
                        logger.critical(f"🚨 Бот не смог запуститься после {max_retries} попыток")
                        break
            
            logger.warning("🛑 Поток бота завершен")
        
        # Запускаем поток
        self.bot_thread = threading.Thread(
            target=run_loop,
            daemon=True,
            name="TelegramBotThread"
        )
        self.bot_thread.start()
        logger.info("✅ Бот запущен в фоновом потоке")
    
    async def _process_update_queue(self):
        """Обработка обновлений из очереди"""
        logger.info("🔄 Запущена обработка очереди обновлений")
        
        while True:
            try:
                # Ждем обновление из очереди
                update_data = await self.update_queue.get()
                
                # Обрабатываем обновление с семафором
                async with self.processing_semaphore:
                    update = Update.de_json(update_data, self.application.bot)
                    if update:
                        await self.application.process_update(update)
                
                # Помечаем задачу как выполненную
                self.update_queue.task_done()
                
            except Exception as e:
                logger.error(f"❌ Ошибка обработки обновления из очереди: {e}")
    
    def add_update_to_queue(self, update_data: dict):
        """Добавление обновления в очередь для обработки (потокобезопасно)"""
        try:
            if self.main_loop and not self.main_loop.is_closed():
                # Используем run_coroutine_threadsafe для добавления в очередь
                asyncio.run_coroutine_threadsafe(
                    self.update_queue.put(update_data),
                    self.main_loop
                )
                return True
            else:
                logger.warning("⚠️  Event loop не запущен, обновление проигнорировано")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка добавления обновления в очередь: {e}")
            return False
    
    def shutdown(self):
        """Корректное завершение работы бота"""
        try:
            if self.main_loop and not self.main_loop.is_closed():
                # Пытаемся остановить приложение
                if self.application:
                    future = asyncio.run_coroutine_threadsafe(
                        self.application.shutdown(),
                        self.main_loop
                    )
                    future.result(timeout=10)
                
                # Останавливаем loop
                self.main_loop.call_soon_threadsafe(self.main_loop.stop)
                logger.info("✅ Бот корректно остановлен")
                
        except Exception as e:
            logger.error(f"❌ Ошибка при остановке бота: {e}")

# ================== FLASK ПРИЛОЖЕНИЕ ==================
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Инициализация менеджера бота
bot_manager = BotManager()

# ================== ДЕКОРАТОРЫ БЕЗОПАСНОСТИ ==================
def require_admin_token(f):
    """Декоратор для проверки токена администратора"""
    @wraps(f)
    def decorated(*args, **kwargs):
        admin_token = request.headers.get('X-Admin-Token')
        expected_token = os.getenv('ADMIN_SECRET_TOKEN')
        
        # Разрешаем токен в параметрах только для GET запросов
        if not admin_token and request.method == 'GET':
            admin_token = request.args.get('admin_token')
        
        # Проверяем токен
        if not expected_token:
            return jsonify({'error': 'ADMIN_SECRET_TOKEN не настроен'}), 500
        
        if not admin_token or not secrets.compare_digest(admin_token, expected_token):
            return jsonify({'error': 'Доступ запрещен. Неверный токен администратора.'}), 403
        
        return f(*args, **kwargs)
    return decorated

# ================== РОУТЫ МОНИТОРИНГА ==================
@app.route('/')
def index():
    """Главная страница мониторинга"""
    try:
        current_status = check_database_status()
    except Exception as e:
        current_status = {'error': str(e)}
    
    faq_count = len(bot_manager.search_engine.faq_data) if bot_manager.search_engine else 0
    
    # Статус бота
    if bot_manager.bot_initialized and bot_manager.application:
        if bot_manager.main_loop and not bot_manager.main_loop.is_closed():
            bot_status = {
                'status': '✅ Активен',
                'class': 'success',
                'details': 'Бот запущен и обрабатывает сообщения'
            }
        else:
            bot_status = {
                'status': '⚠️  Инициализирован',
                'class': 'warning',
                'details': 'Бот инициализирован, но event loop не запущен'
            }
    elif bot_manager.bot_initialized:
        bot_status = {
            'status': '⚠️  Частично готов',
            'class': 'warning',
            'details': 'Бот инициализирован, но приложение не создано'
        }
    else:
        bot_status = {
            'status': '❌ Ошибка',
            'class': 'error',
            'details': 'Бот не инициализирован'
        }
    
    # Статус базы данных
    if 'error' in current_status:
        db_status = {
            'text': f"Ошибка: {current_status['error']}",
            'class': 'error'
        }
    elif not current_status.get('table_exists', False):
        db_status = {
            'text': "❌ Таблица не существует",
            'class': 'error'
        }
    elif current_status.get('total_records', 0) >= 75:
        db_status = {
            'text': f"✅ {current_status['total_records']} вопросов (полная база)",
            'class': 'success'
        }
    elif current_status.get('total_records', 0) >= 70:
        db_status = {
            'text': f"⚠️  {current_status['total_records']} вопросов (рабочая база)",
            'class': 'warning'
        }
    elif current_status.get('total_records', 0) > 0:
        db_status = {
            'text': f"❌ {current_status['total_records']} вопросов (недостаточно)",
            'class': 'error'
        }
    else:
        db_status = {
            'text': "❌ База пуста",
            'class': 'error'
        }
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>HR Bot Мечел - Панель мониторинга</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: 'Segoe UI', system-ui, -apple-system, sans-serif; 
                    max-width: 1000px; margin: 0 auto; padding: 20px; 
                    background: linear-gradient(135deg, #f5f7fa 0%, #c3cfe2 100%); 
                    min-height: 100vh; color: #2d3748; }}
            .container {{ background: white; border-radius: 12px; padding: 30px; 
                        box-shadow: 0 10px 25px rgba(0,0,0,0.1); }}
            h1 {{ color: #2d3748; text-align: center; margin-bottom: 25px; 
                 padding-bottom: 15px; border-bottom: 2px solid #4f46e5; }}
            .status-box {{ padding: 20px; border-radius: 10px; margin: 20px 0; 
                         border-left: 5px solid; box-shadow: 0 2px 5px rgba(0,0,0,0.05); }}
            .success {{ background: #f0fdf4; border-left-color: #10b981; }}
            .warning {{ background: #fffbeb; border-left-color: #f59e0b; }}
            .error {{ background: #fef2f2; border-left-color: #ef4444; }}
            .info {{ background: #eff6ff; border-left-color: #3b82f6; }}
            .grid {{ display: grid; grid-template-columns: repeat(auto-fit, minmax(200px, 1fr)); 
                    gap: 15px; margin: 30px 0; }}
            .grid a {{ display: flex; flex-direction: column; align-items: center; 
                     justify-content: center; padding: 20px 15px; background: #4f46e5; 
                     color: white; text-decoration: none; border-radius: 8px; 
                     transition: all 0.3s; font-weight: 600; }}
            .grid a:hover {{ background: #4338ca; transform: translateY(-2px); 
                          box-shadow: 0 4px 12px rgba(79, 70, 229, 0.3); }}
            .stat {{ display: inline-flex; align-items: center; gap: 8px; 
                    padding: 8px 16px; margin: 5px; background: #f8fafc; 
                    border-radius: 20px; font-size: 14px; border: 1px solid #e2e8f0; }}
            .stat-icon {{ font-size: 18px; }}
            .footer {{ margin-top: 30px; padding-top: 20px; border-top: 1px solid #e2e8f0; 
                     color: #64748b; font-size: 14px; text-align: center; }}
            .alert {{ padding: 15px; border-radius: 8px; margin: 20px 0; 
                    background: #fef3c7; border: 2px solid #f59e0b; }}
            code {{ background: #f1f5f9; padding: 2px 6px; border-radius: 4px; 
                   font-family: 'Courier New', monospace; font-size: 14px; }}
        </style>
    </head>
    <body>
        <div class="container">
            <h1>🤖 HR Bot Мечел - Панель управления</h1>
            
            <div class="status-box {bot_status['class']}">
                <h3 style="margin-top: 0;">📱 Статус Telegram-бота</h3>
                <p><strong>{bot_status['status']}</strong> — {bot_status['details']}</p>
                <p>
                    <span class="stat"><span class="stat-icon">🤖</span> Бот: {bot_status['status'].split()[0]}</span>
                    <span class="stat"><span class="stat-icon">🔄</span> Event Loop: {"✅ Активен" if bot_manager.main_loop and not bot_manager.main_loop.is_closed() else "❌ Остановлен"}</span>
                    <span class="stat"><span class="stat-icon">📊</span> FAQ: {faq_count}/75</span>
                </p>
            </div>
            
            <div class="status-box {db_status['class']}">
                <h3>🗄️ База знаний</h3>
                <p><strong>{db_status['text']}</strong></p>
                {f"<p>Категорий: {current_status.get('categories_count', 0)}</p>" if 'categories_count' in current_status else ''}
                {f"<p>Заполнение: {current_status.get('completion_percentage', '0%')}</p>" if 'completion_percentage' in current_status else ''}
            </div>
            
            <div class="grid">
                <a href="/health">🩺 Health Check</a>
                <a href="/debug">🔍 Диагностика</a>
                <a href="/admin/fill-db">🗃️ Заполнить БД</a>
                <a href="/admin/db-status">📊 Статус БД</a>
                <a href="/set_webhook">🔧 Вебхук</a>
                <a href="/test_connection">📡 Тест API</a>
            </div>
            
            <div class="info">
                <h3>📈 Системная информация</h3>
                <p>Время сервера: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
                <p>Python Telegram Bot: v20.3+</p>
                <p>Архитектура: Flask + PTB Webhook</p>
                <p>Режим: {"Render (продакшн)" if os.getenv('RENDER') == 'true' else "Локальная разработка"}</p>
            </div>
            
            {f'<div class="alert"><strong>⚠️ Требуется внимание:</strong> {bot_status["details"]}</div>' 
             if "Ошибка" in bot_status['status'] or "недостаточно" in db_status['text'] else ''}
            
            <div class="footer">
                <p>HR Bot Мечел • Версия 4.0 • {time.strftime('%Y')}</p>
                <p><small>Последняя проверка: {time.strftime('%H:%M:%S')}</small></p>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/health')
def health_check():
    """Health check для Render и мониторинга"""
    try:
        # Проверяем компоненты
        bot_ok = bot_manager.bot_initialized and bot_manager.application is not None
        search_ok = bot_manager.search_engine is not None
        faq_count = len(bot_manager.search_engine.faq_data) if bot_manager.search_engine else 0
        
        # Проверяем event loop
        loop_ok = bot_manager.main_loop is not None and not bot_manager.main_loop.is_closed()
        
        # Проверяем базу данных
        db_status = check_database_status()
        db_ok = db_status.get('table_exists', False) and db_status.get('total_records', 0) >= 70
        
        # Строгие критерии для продакшена
        if bot_ok and search_ok and db_ok and loop_ok and faq_count >= 70:
            status = "healthy"
            status_code = 200
            message = "✅ Все системы работают нормально"
        elif bot_ok and search_ok and db_ok and loop_ok:
            status = "degraded"
            status_code = 200
            message = f"⚠️ Бот работает, но недостаточно FAQ: {faq_count}/70"
        elif bot_ok and search_ok:
            status = "degraded"
            status_code = 200
            message = "⚠️ Бот работает, но есть проблемы с базой данных или event loop"
        else:
            status = "unhealthy"
            status_code = 503
            message = "❌ Критические проблемы с инициализацией бота"
        
        health_data = {
            "status": status,
            "message": message,
            "service": "hr-bot-mechel",
            "version": "4.0",
            "timestamp": time.time(),
            "timestamp_human": time.strftime('%Y-%m-%d %H:%M:%S'),
            "components": {
                "telegram_bot": {
                    "initialized": bot_ok,
                    "application_exists": bot_manager.application is not None,
                    "search_engine": search_ok,
                    "event_loop": loop_ok,
                    "thread_alive": bot_manager.bot_thread and bot_manager.bot_thread.is_alive() if bot_manager.bot_thread else False
                },
                "database": {
                    "connected": db_ok,
                    "total_records": db_status.get('total_records', 0),
                    "table_exists": db_status.get('table_exists', False),
                    "categories_count": db_status.get('categories_count', 0)
                },
                "webhook": {
                    "configured": bot_ok and loop_ok,
                    "url": f"{os.getenv('RENDER_EXTERNAL_URL', '')}/telegram_webhook" if os.getenv('RENDER_EXTERNAL_URL') else None
                }
            },
            "metrics": {
                "faq_count": faq_count,
                "expected_faq": 75,
                "completion_percentage": round((faq_count/75)*100, 1) if faq_count > 0 else 0,
                "uptime_seconds": int(time.time() - app_start_time) if 'app_start_time' in globals() else 0
            }
        }
        
        if not db_ok:
            health_data["errors"] = ["Проблемы с базой данных"]
        if faq_count < 70:
            health_data["warnings"] = [f"Недостаточно FAQ: {faq_count}/70 (минимум для работы)"]
        if not loop_ok:
            health_data["errors"] = health_data.get("errors", []) + ["Event loop не активен"]
        
        return jsonify(health_data), status_code
        
    except Exception as e:
        logger.error(f"❌ Ошибка в health check: {e}\n{traceback.format_exc()}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": time.time()
        }), 503

# ================== TELEGRAM ВЕБХУК РОУТ ==================
@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    """
    Единственный эндпоинт для вебхуков Telegram.
    Принимает обновления и добавляет их в очередь обработки.
    """
    if not bot_manager.bot_initialized or not bot_manager.application:
        logger.error("❌ Получен вебхук, но бот не инициализирован")
        return jsonify({'error': 'Bot not initialized'}), 503
    
    try:
        # Получаем обновление
        update_data = request.get_json(force=True, silent=True)
        if not update_data:
            logger.error("❌ Невалидный JSON в вебхуке")
            return 'Bad Request', 400
        
        update_id = update_data.get('update_id', 'unknown')
        
        # Логируем информацию о сообщении
        if 'message' in update_data:
            msg = update_data['message']
            user_id = msg.get('from', {}).get('id', 'unknown')
            text = msg.get('text', 'без текста')[:100]
            logger.info(f"📩 Обновление #{update_id} от {user_id}: {text}")
        else:
            logger.debug(f"📩 Обновление #{update_id} (без сообщения)")
        
        # Добавляем обновление в очередь обработки (НЕ БЛОКИРУЕМ ОТВЕТ!)
        if bot_manager.add_update_to_queue(update_data):
            logger.debug(f"✅ Обновление #{update_id} добавлено в очередь")
        else:
            logger.warning(f"⚠️ Не удалось добавить обновление #{update_id} в очередь")
        
        # Немедленно отвечаем Telegram, что получили обновление
        return '', 200
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка в вебхуке: {e}\n{traceback.format_exc()}")
        return 'Internal Server Error', 500

# ================== АДМИНИСТРАТИВНЫЕ РОУТЫ ==================
@app.route('/admin/fill-db', methods=['GET', 'POST'])
@require_admin_token
def admin_fill_database():
    """Административный интерфейс для заполнения БД"""
    if request.method == 'GET':
        try:
            current_status = check_database_status()
        except Exception as e:
            current_status = {'error': str(e)}
        
        html = f'''
        <div class="container">
            <h1>🗃️ Управление базой данных</h1>
            <div class="info">
                <h3>Текущий статус:</h3>
        '''
        
        if 'error' in current_status:
            html += f"<p class='error'>❌ Ошибка: {current_status['error']}</p>"
        else:
            records = current_status.get('total_records', 0)
            percentage = current_status.get('completion_percentage', '0%')
            categories = current_status.get('categories_count', 0)
            
            html += f'''
                <p>📊 Записей в базе: <strong>{records}/75</strong></p>
                <p>📈 Заполнение: <strong>{percentage}</strong></p>
                <p>🗂️ Категорий: <strong>{categories}</strong></p>
                <p>{"✅" if current_status.get('table_exists') else "❌"} Таблица FAQ: 
                   <strong>{"Существует" if current_status.get('table_exists') else "Отсутствует"}</strong></p>
            '''
        
        html += '''
            </div>
            
            <div class="warning">
                <h3>⚠️ Внимание:</h3>
                <p>Заполнение базы данных <strong>перезапишет все существующие вопросы</strong>.</p>
                <p>Эта операция может занять несколько секунд.</p>
            </div>
            
            <form method="POST" onsubmit="return confirm('Вы уверены? Все существующие данные будут удалены.');">
                <button type="submit" style="padding: 15px 30px; background: #10b981; color: white; 
                        border: none; border-radius: 8px; font-size: 16px; cursor: pointer; 
                        display: block; width: 100%; margin: 20px 0; font-weight: 600;">
                    🗃️ Заполнить базу данных (75 вопросов)
                </button>
            </form>
            
            <p><a href="/" style="color: #4f46e5; text-decoration: none; font-weight: 600;">← На главную</a></p>
        </div>
        '''
        
        return html
    
    # POST запрос - заполнение базы
    try:
        logger.info("🔄 Запуск ручного заполнения базы данных")
        result = fill_database_manual()
        
        if result.get('success'):
            stats = result['stats']
            response = f'''
            <div class="container">
                <h1>✅ База данных успешно заполнена!</h1>
                <div class="success">
                    <h3>Результаты:</h3>
                    <p>📥 Добавлено вопросов: <strong>{stats['inserted']}/{stats['total_questions']}</strong></p>
                    <p>📊 Всего в базе: <strong>{stats['final_count']} записей</strong></p>
                    <p>🗂️ Категорий: <strong>{stats['categories']}</strong></p>
                    <p>📈 Заполнение: <strong>{result['details']['completion']}</strong></p>
                </div>
            '''
            
            if stats.get('errors', 0) > 0:
                response += f'''
                <div class="warning">
                    <p>⚠️ Было <strong>{stats['errors']} ошибок</strong> при добавлении вопросов</p>
                </div>
                '''
        else:
            response = f'''
            <div class="container">
                <h1>❌ Ошибка при заполнении базы данных</h1>
                <div class="error">
                    <p><strong>Ошибка:</strong> {result.get('error', 'Неизвестная ошибка')}</p>
                </div>
            '''
        
        response += '''
            <div style="margin-top: 30px;">
                <a href="/admin/fill-db" style="padding: 12px 24px; background: #4f46e5; color: white; 
                   text-decoration: none; border-radius: 6px; margin-right: 10px; font-weight: 600;">
                    Проверить статус
                </a>
                <a href="/" style="padding: 12px 24px; background: #6b7280; color: white; 
                   text-decoration: none; border-radius: 6px; font-weight: 600;">
                    На главную
                </a>
            </div>
        </div>
        '''
        
        return response
        
    except Exception as e:
        logger.error(f"❌ Ошибка в админском интерфейсе: {e}\n{traceback.format_exc()}")
        return f'''
        <div class="container">
            <h1>❌ Критическая ошибка</h1>
            <div class="error">
                <p>{str(e)}</p>
            </div>
        </div>
        ''', 500

@app.route('/admin/db-status')
@require_admin_token
def admin_db_status():
    """API эндпоинт для проверки статуса базы данных (JSON)"""
    try:
        return jsonify(check_database_status())
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/set_webhook', methods=['GET', 'POST'])
@require_admin_token
def set_webhook_endpoint():
    """Ручная установка вебхука (только для сброса)"""
    if request.method == 'GET':
        return '''
        <div class="container">
            <h1>🔧 Управление вебхуком</h1>
            <div class="info">
                <p>Вебхук обычно устанавливается автоматически при запуске бота.</p>
                <p>Используйте эту страницу только для ручного сброса вебхука.</p>
            </div>
            <form method="POST">
                <button type="submit" style="padding: 15px 30px; background: #f59e0b; color: white; 
                        border: none; border-radius: 8px; font-size: 16px; cursor: pointer; font-weight: 600;">
                    🔧 Переустановить вебхук
                </button>
            </form>
            <p style="margin-top: 20px;"><a href="/">← На главную</a></p>
        </div>
        '''
    
    # POST запрос - переустановка вебхука
    try:
        raw_url = os.getenv('RENDER_EXTERNAL_URL', '').strip()
        if not raw_url:
            return jsonify({'error': 'RENDER_EXTERNAL_URL не установлен'}), 400
        
        is_valid, clean_url = validate_url(raw_url)
        if not is_valid:
            return jsonify({'error': f'Неверный URL: {clean_url}'}), 400
        
        webhook_url = f"{clean_url}/telegram_webhook"
        
        import requests
        
        token = config.get_bot_token()
        if not token:
            return jsonify({'error': 'Токен бота не установлен'}), 500
        
        # Удаляем старый вебхук (исправленный URL без пробелов!)
        delete_url = f"https://api.telegram.org/bot{token}/deleteWebhook"
        try:
            response = requests.get(delete_url, timeout=10)
            logger.info(f"Удаление вебхука: {response.status_code}")
        except Exception as e:
            logger.warning(f"Не удалось удалить вебхук: {e}")
        
        # Устанавливаем новый вебхук (исправленный URL без пробелов!)
        set_url = f"https://api.telegram.org/bot{token}/setWebhook"
        payload = {
            'url': webhook_url,
            'max_connections': 50,
            'allowed_updates': ['message', 'callback_query'],
            'drop_pending_updates': True
        }
        
        response = requests.post(set_url, json=payload, timeout=10)
        
        if response.status_code == 200 and response.json().get('ok'):
            message = f"✅ Вебхук успешно установлен на: {webhook_url}"
            status_code = 200
        else:
            message = f"❌ Не удалось установить вебхук: {response.text}"
            status_code = 500
        
        return jsonify({'message': message}), status_code
        
    except Exception as e:
        logger.error(f"❌ Ошибка установки вебхука: {e}\n{traceback.format_exc()}")
        return jsonify({'error': str(e)}), 500

@app.route('/test_connection')
def test_connection():
    """Тест соединения с Telegram API (исправленный URL)"""
    try:
        token = config.get_bot_token()
        if not token:
            return jsonify({'error': 'Токен бота не установлен'}), 500
        
        import requests
        
        # ИСПРАВЛЕННЫЙ URL БЕЗ ПРОБЕЛОВ!
        test_url = f"https://api.telegram.org/bot{token}/getMe"
        response = requests.get(test_url, timeout=10)
        
        if response.status_code == 200:
            bot_info = response.json().get('result', {})
            return jsonify({
                'status': 'success',
                'message': '✅ Соединение с Telegram API установлено',
                'bot': {
                    'id': bot_info.get('id'),
                    'username': bot_info.get('username'),
                    'first_name': bot_info.get('first_name'),
                    'can_join_groups': bot_info.get('can_join_groups'),
                    'can_read_all_group_messages': bot_info.get('can_read_all_group_messages'),
                    'supports_inline_queries': bot_info.get('supports_inline_queries')
                }
            }), 200
        else:
            return jsonify({
                'status': 'error',
                'message': f'❌ Ошибка соединения: {response.status_code}',
                'details': response.text[:200] if response.text else 'Нет деталей'
            }), 500
            
    except Exception as e:
        return jsonify({
            'status': 'error',
            'message': f'❌ Ошибка теста соединения: {str(e)}'
        }), 500

@app.route('/debug')
def debug_info():
    """Страница детальной диагностики"""
    import sys
    
    # Маскируем токен
    bot_token = config.get_bot_token()
    masked_token = mask_token(bot_token) if bot_token else 'Не установлен'
    
    # Проверяем вебхук
    webhook_info = None
    if bot_token:
        try:
            import requests
            info_url = f"https://api.telegram.org/bot{bot_token}/getWebhookInfo"
            response = requests.get(info_url, timeout=5)
            if response.status_code == 200:
                webhook_info = response.json().get('result', {})
        except:
            pass
    
    info = {
        'python': {
            'version': sys.version,
            'platform': sys.platform,
            'executable': sys.executable,
            'asyncio_version': asyncio.__version__ if hasattr(asyncio, '__version__') else 'built-in'
        },
        'environment': {
            'BOT_TOKEN_set': bool(bot_token),
            'BOT_TOKEN_masked': masked_token,
            'DATABASE_URL_set': bool(os.getenv('DATABASE_URL')),
            'RENDER_EXTERNAL_URL': os.getenv('RENDER_EXTERNAL_URL', 'Не установлен'),
            'PORT': os.getenv('PORT', '10000'),
            'RENDER': os.getenv('RENDER', 'false'),
            'ADMIN_SECRET_TOKEN_set': bool(os.getenv('ADMIN_SECRET_TOKEN'))
        },
        'bot': {
            'initialized': bot_manager.bot_initialized,
            'application_exists': bot_manager.application is not None,
            'search_engine_exists': bot_manager.search_engine is not None,
            'faq_count': len(bot_manager.search_engine.faq_data) if bot_manager.search_engine else 0,
            'thread_alive': bot_manager.bot_thread and bot_manager.bot_thread.is_alive() if bot_manager.bot_thread else False,
            'loop_running': bot_manager.main_loop and not bot_manager.main_loop.is_closed() if bot_manager.main_loop else False,
            'queue_size': bot_manager.update_queue.qsize() if hasattr(bot_manager.update_queue, 'qsize') else 0
        },
        'webhook': webhook_info,
        'system': {
            'working_directory': os.getcwd(),
            'files_count': len([f for f in os.listdir('.') if os.path.isfile(f)]),
            'timestamp': time.time(),
            'uptime_seconds': int(time.time() - app_start_time) if 'app_start_time' in globals() else 0
        },
        'database': check_database_status()
    }
    
    return jsonify(info), 200

# ================== ИНИЦИАЛИЗАЦИЯ И ЗАПУСК ==================
# Глобальная переменная для времени старта
app_start_time = time.time()

@app.before_first_request
def initialize_app():
    """Инициализация приложения при первом запросе"""
    logger.info("🔧 Инициализация приложения...")
    
    # Запускаем инициализацию бота
    if bot_manager.initialize():
        logger.info("✅ Бот инициализирован успешно")
        
        # Запускаем бота в фоновом потоке
        bot_manager.run_bot_in_background()
        
        # Ждем немного и проверяем статус
        def check_bot_status():
            time.sleep(3)
            if (bot_manager.main_loop and not bot_manager.main_loop.is_closed() and 
                bot_manager.bot_thread and bot_manager.bot_thread.is_alive()):
                logger.info("🎉 Бот успешно запущен и готов к работе!")
            else:
                logger.warning("⚠️  Бот запущен, но есть проблемы с event loop или потоком")
        
        # Запускаем проверку в фоне
        threading.Thread(target=check_bot_status, daemon=True).start()
    else:
        logger.error("❌ Не удалось инициализировать бота")

@app.teardown_appcontext
def shutdown_app(exception=None):
    """Корректное завершение работы при остановке Flask"""
    if exception:
        logger.error(f"Ошибка в контексте приложения: {exception}")
    
    # Останавливаем бота
    if bot_manager:
        bot_manager.shutdown()
        logger.info("🛑 Бот остановлен")

# ================== ТОЧКА ВХОДА ==================
if __name__ == '__main__':
    # Локальный запуск для разработки
    logger.warning("⚡ ЛОКАЛЬНЫЙ ЗАПУСК - только для разработки!")
    
    # Инициализируем бота сразу для локального запуска
    if bot_manager.initialize():
        bot_manager.run_bot_in_background()
    
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Запуск Flask сервера на http://0.0.0.0:{port}")
    
    # Запускаем Flask с отключенным reloader (он мешает event loop)
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False
    )
