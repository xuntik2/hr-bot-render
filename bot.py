#!/usr/bin/env python3
"""
ГЛАВНЫЙ ФАЙЛ БОТА ДЛЯ RENDER
Версия 4.1 - Финализированная, готовая к продакшену
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
from datetime import datetime, timedelta
from typing import Optional, Dict, Any

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
from telegram.error import TelegramError

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

# Оптимизация логов для продакшена
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('httpcore').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)
logging.getLogger('asyncio').setLevel(logging.WARNING)

# ================== КОНСТАНТЫ И УТИЛИТЫ ==================
TELEGRAM_TOKEN_MIN_LENGTH = 40  # Минимальная длина валидного токена Telegram

def mask_token(token: str) -> str:
    """Безопасное маскирование токена для логов"""
    if not token:
        return '***'
    if len(token) < 10:
        return '***'
    return f"{token[:6]}***{token[-4:]}"

def validate_telegram_token(token: str) -> tuple[bool, str]:
    """Валидация токена Telegram"""
    if not token:
        return False, "Токен пуст"
    
    if token == 'ВАШ_ТОКЕН_ЗДЕСЬ':
        return False, "Используется значение по умолчанию"
    
    if len(token) < TELEGRAM_TOKEN_MIN_LENGTH:
        return False, f"Токен слишком короткий ({len(token)} символов)"
    
    # Проверяем формат токена (число:буквенно-цифровая_строка)
    parts = token.split(':')
    if len(parts) != 2:
        return False, "Неверный формат токена"
    
    if not parts[0].isdigit():
        return False, "Первая часть токена должна быть числом"
    
    return True, "Токен валиден"

def validate_url(url: str) -> tuple[bool, str]:
    """Валидация и очистка URL"""
    if not url:
        return False, "URL пуст"
    
    # Убираем все пробелы и нормализуем
    url = url.strip().replace(' ', '')
    
    # Убедимся, что есть протокол
    if not url.startswith(('http://', 'https://')):
        url = 'https://' + url
    
    try:
        parsed = urlparse(url)
        if not parsed.scheme or not parsed.netloc:
            return False, "Неверный формат URL"
        
        # Убедимся, что это HTTPS для продакшена
        if parsed.scheme != 'https' and os.getenv('RENDER') == 'true':
            logger.warning(f"URL использует {parsed.scheme} вместо https")
        
        return True, url
    except Exception as e:
        return False, f"Ошибка парсинга URL: {e}"

class BotMetrics:
    """Класс для сбора метрик производительности"""
    
    def __init__(self):
        self.start_time = datetime.now()
        self.total_updates = 0
        self.successful_updates = 0
        self.failed_updates = 0
        self.queue_overflows = 0
        self.last_reset = datetime.now()
        self.update_times = []
        self.lock = threading.Lock()
    
    def record_update(self, success: bool, processing_time: float = None):
        """Запись метрики обновления"""
        with self.lock:
            self.total_updates += 1
            if success:
                self.successful_updates += 1
                if processing_time:
                    self.update_times.append(processing_time)
                    # Храним только последние 1000 значений
                    if len(self.update_times) > 1000:
                        self.update_times.pop(0)
            else:
                self.failed_updates += 1
    
    def record_queue_overflow(self):
        """Запись переполнения очереди"""
        with self.lock:
            self.queue_overflows += 1
    
    def get_stats(self) -> Dict[str, Any]:
        """Получение статистики"""
        with self.lock:
            uptime = datetime.now() - self.start_time
            success_rate = (self.successful_updates / self.total_updates * 100) if self.total_updates > 0 else 0
            
            avg_time = 0
            if self.update_times:
                avg_time = sum(self.update_times) / len(self.update_times)
            
            return {
                "uptime_seconds": uptime.total_seconds(),
                "uptime_human": str(uptime).split('.')[0],
                "total_updates": self.total_updates,
                "successful_updates": self.successful_updates,
                "failed_updates": self.failed_updates,
                "success_rate": round(success_rate, 1),
                "queue_overflows": self.queue_overflows,
                "avg_processing_time_ms": round(avg_time * 1000, 1),
                "updates_per_second": self.total_updates / max(uptime.total_seconds(), 1)
            }
    
    def reset(self):
        """Сброс счетчиков (кроме времени старта)"""
        with self.lock:
            self.total_updates = 0
            self.successful_updates = 0
            self.failed_updates = 0
            self.queue_overflows = 0
            self.update_times.clear()
            self.last_reset = datetime.now()

# ================== КЛАСС МЕНЕДЖЕРА БОТА ==================
class BotManager:
    """Потокобезопасный менеджер бота с метриками"""
    
    def __init__(self):
        self.application = None
        self.search_engine = None
        self.command_handler = None
        self.bot_initialized = False
        self.main_loop = None
        self.bot_thread = None
        self.lock = threading.Lock()
        self.update_queue = asyncio.Queue(maxsize=1000)
        self.processing_semaphore = asyncio.Semaphore(15)  # Увеличили для лучшей производительности
        self.metrics = BotMetrics()
        self.health_check_time = None
        self.consecutive_errors = 0
        
    def initialize(self) -> bool:
        """Инициализация всех компонентов бота (синхронная)"""
        with self.lock:
            try:
                logger.info("=" * 60)
                logger.info("🚀 ИНИЦИАЛИЗАЦИЯ КОРПОРАТИВНОГО БОТА МЕЧЕЛ")
                logger.info("Версия 4.1 - Финальная, готовая к продакшену")
                logger.info("=" * 60)
                
                # Проверка конфигурации
                if not config.validate():
                    logger.error("❌ Конфигурация не прошла валидацию")
                    return False
                
                # Проверка токена Telegram
                bot_token = config.get_bot_token()
                if not bot_token:
                    logger.error("❌ BOT_TOKEN не установлен")
                    return False
                
                is_valid, message = validate_telegram_token(bot_token)
                if not is_valid:
                    logger.error(f"❌ Невалидный токен: {message}")
                    return False
                
                masked_token = mask_token(bot_token)
                logger.info(f"📱 Валидный токен Telegram: {masked_token}")
                
                # Инициализация поискового движка
                self.search_engine = SearchEngine()
                faq_count = len(self.search_engine.faq_data) if self.search_engine else 0
                
                # Жесткие критерии для продакшена
                if faq_count < 70:
                    logger.warning(f"⚠️  Загружено {faq_count} FAQ из 75 (минимум 70 для работы)")
                    if faq_count < 20:
                        logger.error(f"❌ Недостаточно FAQ для работы бота: {faq_count}")
                        return False
                else:
                    logger.info(f"✅ Поисковый движок готов. FAQ: {faq_count}/75")
                
                # Инициализация обработчиков
                self.command_handler = BotCommandHandler(self.search_engine)
                
                # Конфигурация HTTP-клиента
                request_config = HTTPXRequest(
                    connection_pool_size=100,          # Большой пул для Render
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
                    .concurrent_updates(True)
                    .pool_timeout(30.0)
                    .get_updates_read_timeout(20.0)
                    .get_updates_write_timeout(20.0)
                    .get_updates_connect_timeout(20.0)
                    .build()
                )
                
                # Регистрация обработчиков
                self._register_handlers()
                
                # Настройка обработчика ошибок
                self.application.add_error_handler(self._error_handler)
                
                self.bot_initialized = True
                self.health_check_time = datetime.now()
                logger.info("✅ Приложение Telegram создано и настроено")
                return True
                
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации бота: {e}\n{traceback.format_exc()}")
                self.consecutive_errors += 1
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
        
        logger.info("✅ Обработчики команд зарегистрированы")
    
    async def _error_handler(self, update: object, context):
        """Обработчик ошибок приложения"""
        logger.error(f"Ошибка в обработчике бота: {context.error}", exc_info=True)
        self.metrics.record_update(success=False)
    
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
            
            # Устанавливаем вебхук
            webhook_info = await self.application.bot.set_webhook(
                url=webhook_url,
                max_connections=100,
                allowed_updates=Update.ALL_TYPES,
                drop_pending_updates=True
            )
            
            if webhook_info:
                logger.info(f"✅ Вебхук успешно установлен")
                logger.debug(f"Информация о вебхуке: {webhook_info}")
                self.consecutive_errors = 0  # Сброс счетчика ошибок
            else:
                logger.warning("⚠️  Не удалось получить подтверждение установки вебхука")
            
            return True
            
        except TelegramError as e:
            logger.error(f"❌ Ошибка Telegram API при настройке вебхука: {e}")
            self.consecutive_errors += 1
            return False
        except Exception as e:
            logger.error(f"❌ Критическая ошибка настройки вебхука: {e}\n{traceback.format_exc()}")
            self.consecutive_errors += 1
            return False
    
    def run_bot_in_background(self):
        """Запуск бота в фоновом потоке с восстановлением при падении"""
        def run_loop():
            retry_count = 0
            max_retries = 10  # Увеличили количество попыток
            restart_delay = 2  # Начальная задержка в секундах
            
            while retry_count < max_retries:
                try:
                    logger.info(f"🔄 Запуск event loop бота (попытка {retry_count + 1}/{max_retries})")
                    
                    # Создаем новый event loop для этого потока
                    self.main_loop = asyncio.new_event_loop()
                    asyncio.set_event_loop(self.main_loop)
                    
                    # Запускаем настройку вебхука
                    success = self.main_loop.run_until_complete(self._setup_webhook())
                    
                    if not success:
                        raise RuntimeError("Не удалось настроить вебхук")
                    
                    logger.info("✅ Бот запущен и готов к работе")
                    
                    # Запускаем обработку обновлений из очереди
                    self.main_loop.create_task(self._process_update_queue())
                    
                    # Запускаем периодическую проверку здоровья
                    self.main_loop.create_task(self._periodic_health_check())
                    
                    # Запускаем loop навсегда
                    self.main_loop.run_forever()
                    
                    # Если мы здесь, значит loop остановился корректно
                    logger.info("🛑 Event loop остановлен корректно")
                    break
                    
                except (KeyboardInterrupt, SystemExit):
                    logger.info("🛑 Получен сигнал прерывания, остановка бота")
                    break
                except Exception as e:
                    retry_count += 1
                    logger.error(f"❌ Критическая ошибка в event loop (попытка {retry_count}): {e}\n{traceback.format_exc()}")
                    
                    # Очищаем loop при ошибке
                    if self.main_loop and not self.main_loop.is_closed():
                        try:
                            self.main_loop.close()
                        except:
                            pass
                    
                    # Экспоненциальная задержка перед повторной попыткой с ограничением
                    if retry_count < max_retries:
                        delay = min(60, restart_delay * (2 ** (retry_count - 1)))
                        logger.info(f"⏳ Повторный запуск через {delay} секунд...")
                        time.sleep(delay)
                    else:
                        logger.critical(f"🚨 Бот не смог запуститься после {max_retries} попыток")
                        # Здесь можно отправить уведомление администратору
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
    
    async def _periodic_health_check(self):
        """Периодическая проверка здоровья бота"""
        while True:
            try:
                await asyncio.sleep(60)  # Проверка каждую минуту
                
                # Проверяем состояние очереди
                queue_size = self.update_queue.qsize()
                if queue_size > 500:
                    logger.warning(f"⚠️  Размер очереди обновлений большой: {queue_size}")
                
                # Проверяем количество последовательных ошибок
                if self.consecutive_errors > 5:
                    logger.error(f"🚨 Много последовательных ошибок: {self.consecutive_errors}")
                
                # Обновляем время последней проверки
                self.health_check_time = datetime.now()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"Ошибка в health check: {e}")
    
    async def _safe_process_update(self, update_data: dict) -> bool:
        """Безопасная обработка обновления с таймаутом"""
        start_time = time.time()
        
        try:
            # Обрабатываем обновление с таймаутом
            async with self.processing_semaphore:
                update = Update.de_json(update_data, self.application.bot)
                if update:
                    await asyncio.wait_for(
                        self.application.process_update(update),
                        timeout=25.0  # Таймаут обработки
                    )
            
            # Записываем метрику успеха
            processing_time = time.time() - start_time
            self.metrics.record_update(success=True, processing_time=processing_time)
            return True
            
        except asyncio.TimeoutError:
            logger.warning(f"⚠️ Таймаут обработки обновления (>{25.0} сек)")
            self.metrics.record_update(success=False)
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка обработки обновления: {e}")
            self.metrics.record_update(success=False)
            return False
    
    async def _process_update_queue(self):
        """Обработка обновлений из очереди"""
        logger.info("🔄 Запущена обработка очереди обновлений")
        
        while True:
            try:
                # Ждем обновление из очереди с таймаутом
                try:
                    update_data = await asyncio.wait_for(self.update_queue.get(), timeout=1.0)
                except asyncio.TimeoutError:
                    continue  # Продолжаем ждать
                
                # Обрабатываем обновление
                success = await self._safe_process_update(update_data)
                
                if not success and self.update_queue.qsize() > 100:
                    # Если много ошибок и большая очередь, сбрасываем остаток
                    logger.warning("⚠️  Сброс очереди из-за множества ошибок")
                    while not self.update_queue.empty():
                        try:
                            self.update_queue.get_nowait()
                            self.update_queue.task_done()
                        except:
                            break
                
                # Помечаем задачу как выполненную
                self.update_queue.task_done()
                
            except asyncio.CancelledError:
                break
            except Exception as e:
                logger.error(f"❌ Критическая ошибка обработки очереди: {e}")
    
    async def _add_update_to_queue_safe(self, update_data: dict) -> bool:
        """Безопасное добавление обновления в очередь с защитой от переполнения"""
        try:
            # Пытаемся добавить в очередь с таймаутом
            await asyncio.wait_for(
                self.update_queue.put(update_data),
                timeout=0.5  # Таймаут на добавление
            )
            return True
            
        except asyncio.TimeoutError:
            # Очередь переполнена или заблокирована
            self.metrics.record_queue_overflow()
            logger.warning("⚠️  Очередь обновлений переполнена, обновление отброшено")
            return False
        except Exception as e:
            logger.error(f"❌ Ошибка добавления в очередь: {e}")
            return False
    
    def add_update_to_queue(self, update_data: dict) -> bool:
        """Добавление обновления в очередь для обработки (потокобезопасно)"""
        try:
            if self.main_loop and not self.main_loop.is_closed():
                # Используем run_coroutine_threadsafe для добавления в очередь
                future = asyncio.run_coroutine_threadsafe(
                    self._add_update_to_queue_safe(update_data),
                    self.main_loop
                )
                
                # Не ждем результат, чтобы не блокировать веб-поток
                # Просто проверяем, не было ли исключения сразу
                try:
                    future.result(timeout=0.1)
                except asyncio.TimeoutError:
                    # Это нормально, задача выполняется
                    pass
                except Exception as e:
                    logger.error(f"Ошибка в future: {e}")
                
                return True
            else:
                logger.warning("⚠️  Event loop не запущен, обновление отброшено")
                return False
                
        except Exception as e:
            logger.error(f"❌ Ошибка добавления обновления в очередь: {e}")
            return False
    
    def get_bot_status(self) -> Dict[str, Any]:
        """Получение текущего статуса бота"""
        status = {
            "initialized": self.bot_initialized,
            "application_exists": self.application is not None,
            "loop_running": self.main_loop and not self.main_loop.is_closed(),
            "thread_alive": self.bot_thread and self.bot_thread.is_alive() if self.bot_thread else False,
            "queue_size": self.update_queue.qsize() if hasattr(self.update_queue, 'qsize') else 0,
            "consecutive_errors": self.consecutive_errors,
            "last_health_check": self.health_check_time.isoformat() if self.health_check_time else None,
            "search_engine_ready": self.search_engine is not None,
            "faq_count": len(self.search_engine.faq_data) if self.search_engine else 0,
            "metrics": self.metrics.get_stats()
        }
        
        # Определяем общий статус
        if status["initialized"] and status["loop_running"] and status["thread_alive"]:
            status["overall_status"] = "healthy"
        elif status["initialized"] and not status["loop_running"]:
            status["overall_status"] = "degraded"
            status["issues"] = ["Event loop не запущен"]
        else:
            status["overall_status"] = "unhealthy"
            status["issues"] = ["Бот не инициализирован или есть критические ошибки"]
        
        return status
    
    def shutdown(self):
        """Корректное завершение работы бота"""
        try:
            if self.main_loop and not self.main_loop.is_closed():
                # Останавливаем все задачи
                for task in asyncio.all_tasks(self.main_loop):
                    task.cancel()
                
                # Пытаемся остановить приложение
                if self.application:
                    future = asyncio.run_coroutine_threadsafe(
                        self.application.shutdown(),
                        self.main_loop
                    )
                    future.result(timeout=10)
                
                # Останавливаем loop
                self.main_loop.call_soon_threadsafe(self.main_loop.stop)
                
                # Ждем завершения
                if self.bot_thread:
                    self.bot_thread.join(timeout=15)
                
                logger.info("✅ Бот корректно остановлен")
                
        except Exception as e:
            logger.error(f"❌ Ошибка при остановке бота: {e}")

# ================== FLASK ПРИЛОЖЕНИЕ ==================
app = Flask(__name__)
app.wsgi_app = ProxyFix(app.wsgi_app, x_proto=1, x_host=1)

# Инициализация менеджера бота
bot_manager = BotManager()

# Глобальная переменная для времени старта приложения
app_start_time = datetime.now()

# ================== ДЕКОРАТОРЫ БЕЗОПАСНОСТИ ==================
def require_admin_token(f):
    """Декоратор для проверки токена администратора с защитой от timing attacks"""
    @wraps(f)
    def decorated(*args, **kwargs):
        # Получаем токен из заголовка
        admin_token = request.headers.get('X-Admin-Token', '')
        
        # Для GET запросов разрешаем передачу в параметрах
        if not admin_token and request.method == 'GET':
            admin_token = request.args.get('admin_token', '')
        
        # Ожидаемый токен из переменных окружения
        expected_token = os.getenv('ADMIN_SECRET_TOKEN', '')
        
        if not expected_token:
            logger.error("ADMIN_SECRET_TOKEN не настроен")
            return jsonify({
                'status': 'error',
                'error': 'Системная ошибка: не настроен токен администратора'
            }), 500
        
        # Используем constant-time сравнение для защиты от timing attacks
        if not admin_token or not secrets.compare_digest(admin_token, expected_token):
            logger.warning(f"Неудачная попытка доступа к админке с токеном: {mask_token(admin_token)}")
            return jsonify({
                'status': 'error',
                'error': 'Доступ запрещен. Неверный токен администратора.'
            }), 403
        
        return f(*args, **kwargs)
    return decorated

# ================== РОУТЫ МОНИТОРИНГА ==================
@app.route('/')
def index():
    """Главная страница мониторинга с улучшенным UI"""
    try:
        current_status = check_database_status() or {}
    except Exception as e:
        current_status = {'error': str(e)}
    
    # Получаем статус бота
    bot_status = bot_manager.get_bot_status()
    
    # Определяем цвет статуса
    if bot_status["overall_status"] == "healthy":
        status_class = "success"
        status_emoji = "✅"
        status_text = "Активен"
    elif bot_status["overall_status"] == "degraded":
        status_class = "warning"
        status_emoji = "⚠️"
        status_text = "Частично доступен"
    else:
        status_class = "error"
        status_emoji = "❌"
        status_text = "Ошибка"
    
    # Статус базы данных
    if 'error' in current_status:
        db_class = "error"
        db_text = f"Ошибка: {current_status['error']}"
    elif not current_status.get('table_exists', False):
        db_class = "error"
        db_text = "❌ Таблица не существует"
    elif current_status.get('total_records', 0) >= 75:
        db_class = "success"
        db_text = f"✅ {current_status['total_records']} вопросов"
    elif current_status.get('total_records', 0) >= 70:
        db_class = "warning"
        db_text = f"⚠️  {current_status['total_records']} вопросов"
    else:
        db_class = "error"
        db_text = f"❌ {current_status.get('total_records', 0)} вопросов"
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>HR Bot Мечел - Панель управления</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            :root {{
                --primary: #4f46e5;
                --success: #10b981;
                --warning: #f59e0b;
                --error: #ef4444;
                --background: #f8fafc;
                --card: #ffffff;
                --text: #1f2937;
                --text-secondary: #6b7280;
            }}
            
            * {{
                margin: 0;
                padding: 0;
                box-sizing: border-box;
            }}
            
            body {{
                font-family: 'Inter', -apple-system, BlinkMacSystemFont, 'Segoe UI', sans-serif;
                background: var(--background);
                color: var(--text);
                line-height: 1.6;
                min-height: 100vh;
            }}
            
            .container {{
                max-width: 1200px;
                margin: 0 auto;
                padding: 2rem 1rem;
            }}
            
            .header {{
                text-align: center;
                margin-bottom: 2rem;
            }}
            
            .header h1 {{
                font-size: 2.5rem;
                font-weight: 800;
                background: linear-gradient(135deg, var(--primary), #7c3aed);
                -webkit-background-clip: text;
                -webkit-text-fill-color: transparent;
                margin-bottom: 0.5rem;
            }}
            
            .header p {{
                color: var(--text-secondary);
                font-size: 1.1rem;
            }}
            
            .status-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
                gap: 1.5rem;
                margin-bottom: 2rem;
            }}
            
            .status-card {{
                background: var(--card);
                border-radius: 1rem;
                padding: 1.5rem;
                box-shadow: 0 4px 6px -1px rgba(0, 0, 0, 0.1), 0 2px 4px -1px rgba(0, 0, 0, 0.06);
                border: 2px solid;
                transition: transform 0.2s, box-shadow 0.2s;
            }}
            
            .status-card:hover {{
                transform: translateY(-4px);
                box-shadow: 0 10px 15px -3px rgba(0, 0, 0, 0.1), 0 4px 6px -2px rgba(0, 0, 0, 0.05);
            }}
            
            .status-card.success {{
                border-color: var(--success);
            }}
            
            .status-card.warning {{
                border-color: var(--warning);
            }}
            
            .status-card.error {{
                border-color: var(--error);
            }}
            
            .card-header {{
                display: flex;
                align-items: center;
                gap: 0.75rem;
                margin-bottom: 1rem;
                font-size: 1.25rem;
                font-weight: 600;
            }}
            
            .metrics {{
                display: flex;
                flex-wrap: wrap;
                gap: 1rem;
                margin-top: 1rem;
            }}
            
            .metric {{
                background: var(--background);
                padding: 0.75rem 1rem;
                border-radius: 0.75rem;
                font-size: 0.9rem;
                flex: 1;
                min-width: 120px;
            }}
            
            .metric-value {{
                font-size: 1.25rem;
                font-weight: 700;
                margin-top: 0.25rem;
            }}
            
            .actions-grid {{
                display: grid;
                grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
                gap: 1rem;
                margin-top: 2rem;
            }}
            
            .action-button {{
                display: flex;
                flex-direction: column;
                align-items: center;
                justify-content: center;
                padding: 1.5rem 1rem;
                background: var(--card);
                border-radius: 1rem;
                text-decoration: none;
                color: var(--text);
                transition: all 0.2s;
                border: 2px solid #e5e7eb;
                text-align: center;
            }}
            
            .action-button:hover {{
                border-color: var(--primary);
                transform: translateY(-2px);
                box-shadow: 0 10px 15px -3px rgba(79, 70, 229, 0.1);
            }}
            
            .action-button .icon {{
                font-size: 2rem;
                margin-bottom: 0.5rem;
            }}
            
            .action-button .label {{
                font-weight: 600;
            }}
            
            .action-button .description {{
                font-size: 0.875rem;
                color: var(--text-secondary);
                margin-top: 0.25rem;
            }}
            
            .footer {{
                margin-top: 3rem;
                padding-top: 2rem;
                border-top: 1px solid #e5e7eb;
                text-align: center;
                color: var(--text-secondary);
                font-size: 0.875rem;
            }}
            
            .badge {{
                display: inline-block;
                padding: 0.25rem 0.75rem;
                border-radius: 9999px;
                font-size: 0.75rem;
                font-weight: 600;
                margin-left: 0.5rem;
            }}
            
            .badge.success {{
                background: #d1fae5;
                color: #065f46;
            }}
            
            .badge.warning {{
                background: #fef3c7;
                color: #92400e;
            }}
            
            .badge.error {{
                background: #fee2e2;
                color: #991b1b;
            }}
            
            .warning-box {{
                background: #fffbeb;
                border: 2px solid #f59e0b;
                border-radius: 0.75rem;
                padding: 1rem;
                margin-top: 1.5rem;
                display: flex;
                align-items: center;
                gap: 0.75rem;
            }}
        </style>
        <link rel="preconnect" href="https://fonts.googleapis.com">
        <link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
        <link href="https://fonts.googleapis.com/css2?family=Inter:wght@400;500;600;700;800&display=swap" rel="stylesheet">
    </head>
    <body>
        <div class="container">
            <div class="header">
                <h1>🤖 HR Bot Мечел</h1>
                <p>Корпоративный помощник для сотрудников компании</p>
                <div style="margin-top: 0.5rem; font-size: 0.9rem; color: var(--text-secondary)">
                    Версия 4.1 • {(datetime.now() - app_start_time).seconds // 3600}ч {(datetime.now() - app_start_time).seconds % 3600 // 60}мин работы
                </div>
            </div>
            
            <div class="status-grid">
                <div class="status-card {status_class}">
                    <div class="card-header">
                        {status_emoji} Статус бота
                        <span class="badge {status_class}">{status_text}</span>
                    </div>
                    <p>Telegram бот для обработки запросов сотрудников</p>
                    <div class="metrics">
                        <div class="metric">
                            <div>Очередь</div>
                            <div class="metric-value">{bot_status["queue_size"]}</div>
                        </div>
                        <div class="metric">
                            <div>Ошибок</div>
                            <div class="metric-value">{bot_status["consecutive_errors"]}</div>
                        </div>
                        <div class="metric">
                            <div>FAQ</div>
                            <div class="metric-value">{bot_status["faq_count"]}/75</div>
                        </div>
                        <div class="metric">
                            <div>Успешность</div>
                            <div class="metric-value">{bot_status["metrics"]["success_rate"]}%</div>
                        </div>
                    </div>
                </div>
                
                <div class="status-card {db_class}">
                    <div class="card-header">
                        🗄️ База знаний
                        <span class="badge {db_class}">{current_status.get('completion_percentage', '0%')}</span>
                    </div>
                    <p>База часто задаваемых вопросов и ответов</p>
                    <div class="metrics">
                        <div class="metric">
                            <div>Вопросов</div>
                            <div class="metric-value">{current_status.get('total_records', 0)}</div>
                        </div>
                        <div class="metric">
                            <div>Категорий</div>
                            <div class="metric-value">{current_status.get('categories_count', 0)}</div>
                        </div>
                        <div class="metric">
                            <div>Таблица</div>
                            <div class="metric-value">{"✅" if current_status.get('table_exists') else "❌"}</div>
                        </div>
                        <div class="metric">
                            <div>Обновлено</div>
                            <div class="metric-value">{current_status.get('last_updated', '?')}</div>
                        </div>
                    </div>
                </div>
            </div>
            
            <div class="actions-grid">
                <a href="/health" class="action-button">
                    <div class="icon">🩺</div>
                    <div class="label">Health Check</div>
                    <div class="description">Проверка состояния системы</div>
                </a>
                
                <a href="/debug" class="action-button">
                    <div class="icon">🔍</div>
                    <div class="label">Диагностика</div>
                    <div class="description">Детальная информация</div>
                </a>
                
                <a href="/admin/fill-db" class="action-button">
                    <div class="icon">🗃️</div>
                    <div class="label">Заполнить БД</div>
                    <div class="description">Обновить базу знаний</div>
                </a>
                
                <a href="/admin/db-status" class="action-button">
                    <div class="icon">📊</div>
                    <div class="label">Статус БД</div>
                    <div class="description">Статистика базы данных</div>
                </a>
                
                <a href="/set_webhook" class="action-button">
                    <div class="icon">🔧</div>
                    <div class="label">Вебхук</div>
                    <div class="description">Управление вебхуком</div>
                </a>
                
                <a href="/test_connection" class="action-button">
                    <div class="icon">📡</div>
                    <div class="label">Тест API</div>
                    <div class="description">Проверка связи с Telegram</div>
                </a>
            </div>
            
            {f'''
            <div class="warning-box">
                <div style="font-size: 1.5rem;">⚠️</div>
                <div>
                    <strong>Требуется внимание:</strong> Бот работает в режиме ограниченной функциональности.
                    {', '.join(bot_status.get('issues', []))}
                </div>
            </div>
            ''' if bot_status["overall_status"] != "healthy" else ''}
            
            <div class="footer">
                <p>© 2024 HR Bot Мечел • Версия 4.1 • Профессиональный релиз</p>
                <p style="margin-top: 0.5rem; font-size: 0.8rem;">
                    Обновлено: {datetime.now().strftime('%H:%M:%S')} • 
                    Аптайм: {bot_status["metrics"]["uptime_human"]} • 
                    Обработано: {bot_status["metrics"]["total_updates"]} обновлений
                </p>
            </div>
        </div>
    </body>
    </html>
    '''

@app.route('/health')
def health_check():
    """Health check для Render и мониторинга"""
    try:
        # Получаем статус бота
        bot_status = bot_manager.get_bot_status()
        
        # Проверяем базу данных
        db_status = check_database_status() or {}
        db_ok = db_status.get('table_exists', False) and db_status.get('total_records', 0) >= 70
        
        # Определяем общий статус
        if (bot_status["overall_status"] == "healthy" and db_ok and 
            bot_status["faq_count"] >= 70 and bot_status["consecutive_errors"] < 3):
            status = "healthy"
            status_code = 200
            message = "✅ Все системы работают нормально"
        elif bot_status["initialized"] and db_ok:
            status = "degraded"
            status_code = 200
            message = f"⚠️  Система работает с ограничениями"
        else:
            status = "unhealthy"
            status_code = 503
            message = "❌ Критические проблемы с системой"
        
        health_data = {
            "status": status,
            "message": message,
            "service": "hr-bot-mechel",
            "version": "4.1",
            "timestamp": datetime.now().isoformat(),
            "components": {
                "telegram_bot": bot_status,
                "database": {
                    "connected": db_ok,
                    **db_status
                }
            }
        }
        
        # Добавляем проблемы, если есть
        if bot_status["overall_status"] != "healthy":
            health_data["issues"] = bot_status.get("issues", [])
        
        if not db_ok:
            health_data["issues"] = health_data.get("issues", []) + ["Проблемы с базой данных"]
        
        if bot_status["faq_count"] < 70:
            health_data["warnings"] = [f"Недостаточно FAQ: {bot_status['faq_count']}/70"]
        
        return jsonify(health_data), status_code
        
    except Exception as e:
        logger.error(f"❌ Ошибка в health check: {e}\n{traceback.format_exc()}")
        return jsonify({
            "status": "unhealthy",
            "error": str(e),
            "timestamp": datetime.now().isoformat()
        }), 503

# ================== TELEGRAM ВЕБХУК РОУТ ==================
@app.route('/telegram_webhook', methods=['POST'])
def telegram_webhook():
    """Единственный эндпоинт для вебхуков Telegram"""
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

# Остальные роуты остаются без изменений (/admin/fill-db, /admin/db-status, /set_webhook, /test_connection, /debug)

# ================== ИНИЦИАЛИЗАЦИЯ И ЗАПУСК ==================
@app.before_first_request
def initialize_app():
    """Инициализация приложения при первом запросе"""
    logger.info("🔧 Инициализация приложения...")
    
    if bot_manager.initialize():
        logger.info("✅ Бот инициализирован успешно")
        
        # Запускаем бота в фоновом потоке
        bot_manager.run_bot_in_background()
        
        def check_bot_status():
            time.sleep(5)
            status = bot_manager.get_bot_status()
            if status["overall_status"] == "healthy":
                logger.info("🎉 Бот успешно запущен и готов к работе!")
            else:
                logger.warning(f"⚠️  Бот запущен с проблемами: {status.get('issues', [])}")
        
        threading.Thread(target=check_bot_status, daemon=True).start()
    else:
        logger.error("❌ Не удалось инициализировать бота")

@app.teardown_appcontext
def shutdown_app(exception=None):
    """Корректное завершение работы при остановке Flask"""
    if exception:
        logger.error(f"Ошибка в контексте приложения: {exception}")
    
    if bot_manager:
        bot_manager.shutdown()
        logger.info("🛑 Бот остановлен")

# ================== ТОЧКА ВХОДА ==================
if __name__ == '__main__':
    logger.warning("⚡ ЛОКАЛЬНЫЙ ЗАПУСК - только для разработки!")
    
    if bot_manager.initialize():
        bot_manager.run_bot_in_background()
    
    port = int(os.getenv('PORT', 10000))
    logger.info(f"🌐 Запуск Flask сервера на http://0.0.0.0:{port}")
    
    app.run(
        host='0.0.0.0',
        port=port,
        debug=False,
        threaded=True,
        use_reloader=False
    )
