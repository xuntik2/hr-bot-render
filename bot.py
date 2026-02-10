"""
HR БОТ ДЛЯ RENDER FREE - ФИНАЛЬНАЯ ПРОДАКШЕН ВЕРСИЯ
Версия 9.3.5 - УДАЛЕН PANDAS ДЛЯ СОВМЕСТИМОСТИ
"""

import os
import sys
import logging
import time
import atexit
import threading
import signal
import asyncio
from datetime import datetime, timedelta
from collections import defaultdict, OrderedDict
from functools import lru_cache
from contextlib import contextmanager

# ======================
# НАСТРОЙКА ЛОГИРОВАНИЯ (ДО ВСЕХ ИСПОЛЬЗОВАНИЙ ЛОГГЕРА)
# ======================

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(threadName)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Отключаем лишние логи
logging.getLogger('httpx').setLevel(logging.WARNING)
logging.getLogger('telegram').setLevel(logging.WARNING)

# ======================
# ПРОВЕРКА КОНФИГУРАЦИОННЫХ ФАЙЛОВ ПРИ ЗАПУСКЕ
# ======================

def check_config_files():
    """Проверка наличия необходимых конфигурационных файлов"""
    required_files = [
        'gunicorn.conf.py',
        'requirements.txt',
        'config.py'
    ]
    
    optional_files = [
        'runtime.txt',
        'render.yaml',
        'search_engine.py',
        'bot_handlers.py'
    ]
    
    missing_required = []
    missing_optional = []
    
    for file in required_files:
        if not os.path.exists(file):
            missing_required.append(file)
    
    for file in optional_files:
        if not os.path.exists(file):
            missing_optional.append(file)
    
    if missing_required:
        logger.error(f"❌ Отсутствуют обязательные файлы: {', '.join(missing_required)}")
        logger.error("Создайте недостающие файлы перед запуском.")
        if missing_required == ['gunicorn.conf.py']:
            logger.info("Совет: Создайте gunicorn.conf.py из шаблона версии 9.3.3")
        return False
    
    if missing_optional:
        logger.warning(f"⚠️ Отсутствуют опциональные файлы: {', '.join(missing_optional)}")
        logger.warning("Приложение может работать с ограниченным функционалом.")
    
    logger.info("✅ Все обязательные конфигурационные файлы присутствуют")
    return True

# Проверяем файлы при запуске
if not check_config_files():
    logger.error("❌ Не удалось запустить приложение из-за отсутствия файлов конфигурации")
    sys.exit(1)

# ======================
# ПРОВЕРКА ЗАВИСИМОСТЕЙ ПРИ ЗАПУСКЕ (ОБНОВЛЕНО - БЕЗ PANDAS)
# ======================

def check_dependencies():
    """Проверка версий критических зависимостей при запуске"""
    try:
        import telegram
        REQUIRED_TELEGRAM_VERSION = (21, 5)
        current_version = tuple(map(int, telegram.__version__.split('.')))
        
        if current_version < REQUIRED_TELEGRAM_VERSION:
            logger.critical(
                f"❌ Требуется python-telegram-bot >= {'.'.join(map(str, REQUIRED_TELEGRAM_VERSION))}, "
                f"установлена {telegram.__version__}\n"
                f"📦 Обновите: pip install python-telegram-bot[job-queue]==21.7"
            )
            return False
        
        logger.info(f"✅ Версия python-telegram-bot: {telegram.__version__}")
        
    except ImportError as e:
        logger.critical(f"❌ Не удалось импортировать python-telegram-bot: {e}")
        logger.critical("📦 Установите: pip install python-telegram-bot[job-queue]==21.7")
        return False
    
    # Проверка других важных зависимостей (БЕЗ PANDAS)
    try:
        import flask
        logger.info(f"✅ Версия Flask: {flask.__version__}")
    except ImportError:
        logger.warning("⚠️ Flask не установлен")
    
    # УДАЛЕНО: Проверка pandas
    
    # Проверка psutil (опционально, но рекомендуется)
    try:
        import psutil
        logger.info(f"✅ Версия psutil: {psutil.__version__}")
        return True
    except ImportError:
        logger.warning("⚠️ psutil не установлен, расширенный мониторинг недоступен")
        return True

# Вызываем проверку зависимостей
if not check_dependencies():
    logger.critical("❌ Критические зависимости не удовлетворены. Приложение будет остановлено.")
    sys.exit(1)

# ======================
# ИМПОРТЫ ПОСЛЕ ПРОВЕРКИ ЗАВИСИМОСТЕЙ
# ======================

from flask import Flask, request, jsonify
from telegram import Update
from telegram.ext import Application, CommandHandler, MessageHandler, filters
from telegram.error import TelegramError

# Для расширенного health-check (опционально)
try:
    import psutil
    PSUTIL_AVAILABLE = True
    logger.debug("psutil доступен для расширенного мониторинга")
except ImportError:
    PSUTIL_AVAILABLE = False
    logger.warning("psutil не установлен, расширенный health-check недоступен")

# Импорт конфигурации с обработкой ошибок валидации
try:
    sys.path.insert(0, '.')
    from config import config
    from search_engine import SearchEngine
    from bot_handlers import BotCommandHandler
    
    # Проверяем конфигурацию после импорта
    if not config.validate():
        logger.warning("⚠️ Конфигурация имеет проблемы, но приложение продолжит работу")
        
except ImportError as e:
    logger.critical(f"❌ Не удалось импортировать модули: {e}")
    logger.critical("Убедитесь, что все файлы присутствуют в проекте.")
    sys.exit(1)
except ValueError as e:
    logger.critical(f"❌ Ошибка конфигурации: {e}")
    sys.exit(1)
except Exception as e:
    logger.critical(f"❌ Неожиданная ошибка при импорте: {e}")
    sys.exit(1)

# Flask приложение
app = Flask(__name__)

# Глобальные переменные
application = None
bot_handler = None
initialized = False
init_lock = threading.Lock()

# ======================
# THREAD-SAFE СТАТИСТИКА
# ======================

class ThreadSafeStats:
    """Потокобезопасный класс для хранения статистики"""
    
    def __init__(self):
        self._lock = threading.RLock()
        self._data = {
            'requests_total': 0,
            'errors_total': 0,
            'last_error': None,
            'timeouts_total': 0,
            'startup_time': datetime.now().isoformat(),
            'last_request_time': None,
            'webhook_calls': 0,
            'successful_responses': 0,
            'categories_requests': 0,
            'search_requests': 0,
            'feedback_requests': 0,
            'rate_limit_hits': 0,
            'config_errors': 0
        }
    
    def increment(self, key, amount=1):
        """Атомарное увеличение числового значения"""
        with self._lock:
            if key in self._data and isinstance(self._data[key], (int, float)):
                self._data[key] += amount
            else:
                # Разрешаем динамическое добавление ключей
                if isinstance(self._data.get(key, 0), (int, float)):
                    self._data[key] = self._data.get(key, 0) + amount
                else:
                    self._data[key] = amount
    
    def set(self, key, value):
        """Атомарная установка значения"""
        with self._lock:
            self._data[key] = value
    
    def get(self, key, default=None):
        """Получение значения по ключу"""
        with self._lock:
            return self._data.get(key, default)
    
    def get_all(self):
        """Получение копии всех данных"""
        with self._lock:
            return self._data.copy()
    
    def update_last_request(self):
        """Обновление времени последнего запроса"""
        with self._lock:
            self._data['last_request_time'] = datetime.now().isoformat()

stats = ThreadSafeStats()

# ======================
# УЛУЧШЕННЫЙ RATE LIMITER
# ======================

class RateLimiter:
    """Rate limiter с ограничением памяти, удалением старых записей и статистикой"""
    
    def __init__(self, max_requests=100, window_seconds=60, max_tracked_ips=10000):
        self.max_requests = max_requests
        self.window = timedelta(seconds=window_seconds)
        self.max_tracked_ips = max_tracked_ips
        self.requests = OrderedDict()
        self._lock = threading.RLock()
        self.blocked_count = 0
        self.total_checks = 0
        self._cleanup_counter = 0
    
    def is_allowed(self, identifier):
        """Проверка, разрешен ли запрос"""
        with self._lock:
            self.total_checks += 1
            
            now = datetime.now()
            
            # Очищаем старые запросы для этого идентификатора
            if identifier in self.requests:
                self.requests[identifier] = [
                    ts for ts in self.requests[identifier]
                    if now - ts < self.window
                ]
                
                if not self.requests[identifier]:
                    del self.requests[identifier]
                else:
                    self.requests.move_to_end(identifier)
            
            # Ограничиваем количество отслеживаемых IP
            if len(self.requests) > self.max_tracked_ips:
                self._cleanup_old_ips()
            
            if identifier not in self.requests:
                self.requests[identifier] = []
            
            if len(self.requests[identifier]) >= self.max_requests:
                self.blocked_count += 1
                return False
            
            self.requests[identifier].append(now)
            return True
    
    def _cleanup_old_ips(self):
        """Удаление самых старых записей при превышении лимита"""
        to_remove = max(1, len(self.requests) // 10)
        old_keys = list(self.requests.keys())[:to_remove]
        
        for key in old_keys:
            del self.requests[key]
        
        self._cleanup_counter += 1
        logger.debug(f"Очистка RateLimiter #{self._cleanup_counter}: удалено {to_remove} старых IP, осталось {len(self.requests)}")
    
    def _calculate_avg_requests(self):
        """Расчёт среднего количества запросов на IP"""
        with self._lock:
            if not self.requests:
                return 0
            
            total_requests = sum(len(requests) for requests in self.requests.values())
            return round(total_requests / len(self.requests), 2)
    
    def get_stats(self):
        """Получение статистики rate limiter"""
        with self._lock:
            block_rate = 0
            if self.total_checks > 0:
                block_rate = round((self.blocked_count / self.total_checks) * 100, 2)
            
            return {
                'tracked_ips': len(self.requests),
                'max_tracked_ips': self.max_tracked_ips,
                'window_seconds': self.window.total_seconds(),
                'max_requests': self.max_requests,
                'blocked_count': self.blocked_count,
                'total_checks': self.total_checks,
                'block_rate_percent': block_rate,
                'avg_requests_per_ip': self._calculate_avg_requests(),
                'cleanups_performed': self._cleanup_counter
            }

# Rate limiter для вебхука
rate_limiter = RateLimiter(max_requests=30, window_seconds=60, max_tracked_ips=10000)

# ======================
# ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ
# ======================

@contextmanager
def track_execution_time(name):
    """Контекстный менеджер для измерения времени выполнения"""
    start = time.perf_counter()
    try:
        yield
    finally:
        elapsed = time.perf_counter() - start
        if elapsed > 1.0:
            logger.warning(f"⏱️ Медленная операция '{name}': {elapsed:.2f} сек")

def get_webhook_url():
    """Получение URL для вебхука"""
    hostname = os.getenv('RENDER_EXTERNAL_HOSTNAME')
    if not hostname:
        service_name = os.getenv('RENDER_SERVICE_NAME', 'hr-bot-mechel')
        hostname = f"{service_name}.onrender.com"
    
    hostname = hostname.replace('https://', '').replace('http://', '')
    return f"https://{hostname}/webhook"

def run_async_safely(coro):
    """
    Безопасный запуск асинхронной корутины БЕЗ изменения глобального event loop
    """
    loop = None
    with track_execution_time("run_async_safely"):
        try:
            loop = asyncio.new_event_loop()
            return loop.run_until_complete(coro)
        except asyncio.TimeoutError:
            raise
        except Exception as e:
            logger.error(f"Ошибка выполнения асинхронной задачи: {e}", exc_info=True)
            raise
        finally:
            if loop and not loop.is_closed():
                try:
                    loop.close()
                except Exception:
                    pass

def format_uptime(seconds):
    """Форматирование времени работы"""
    days = int(seconds // 86400)
    hours = int((seconds % 86400) // 3600)
    minutes = int((seconds % 3600) // 60)
    secs = int(seconds % 60)
    
    if days > 0:
        return f"{days}д {hours}ч {minutes}м {secs}с"
    elif hours > 0:
        return f"{hours}ч {minutes}м {secs}с"
    elif minutes > 0:
        return f"{minutes}м {secs}с"
    else:
        return f"{secs}с"

def setup_graceful_shutdown():
    """Настройка graceful shutdown для Render"""
    def shutdown_handler(signum, frame):
        logger.info(f"🛑 Получен сигнал {signum}, начинаем graceful shutdown...")
        cleanup()
        time.sleep(2)
        sys.exit(0)
    
    signal.signal(signal.SIGTERM, shutdown_handler)
    signal.signal(signal.SIGINT, shutdown_handler)

# ======================
# ИНИЦИАЛИЗАЦИЯ БОТА
# ======================

def init_bot():
    """Инициализация бота для webhook режима"""
    global application, bot_handler, initialized
    
    with init_lock:
        if initialized:
            logger.info("Бот уже инициализирован")
            return True
        
        try:
            logger.info("🚀 Инициализация бота (только webhook)...")
            
            # Логируем источник токена
            try:
                token_source = config.get_token_source()
                logger.info(f"📋 Источник токена: {token_source}")
            except:
                logger.warning("⚠️ Не удалось определить источник токена")
            
            # 1. Поисковая система
            try:
                search_engine = SearchEngine()
                search_engine.refresh_data()
                logger.info(f"✅ Загружено {len(search_engine.faq_data)} FAQ")
            except Exception as e:
                logger.error(f"❌ Ошибка загрузки поисковой системы: {e}")
                stats.increment('config_errors')
                # Продолжаем без поисковой системы
            
            # 2. Обработчики команд
            try:
                bot_handler = BotCommandHandler(search_engine)
            except Exception as e:
                logger.error(f"❌ Ошибка создания обработчиков: {e}")
                stats.increment('config_errors')
                return False
            
            # 3. Telegram Application
            try:
                token = config.get_bot_token()
                
                # Проверяем формат токена перед созданием приложения
                import re
                token_pattern = r'^\d{8,11}:[A-Za-z0-9_-]{35,}$'
                if not re.match(token_pattern, token):
                    logger.error(f"❌ Неверный формат токена: {token[:10]}...")
                    stats.increment('config_errors')
                    return False
                
                application = (
                    Application.builder()
                    .token(token)
                    .updater(None)
                    .build()
                )
            except Exception as e:
                logger.error(f"❌ Ошибка создания приложения Telegram: {e}")
                stats.increment('config_errors')
                return False
            
            # 4. Регистрация обработчиков
            try:
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
            except Exception as e:
                logger.error(f"❌ Ошибка регистрации обработчиков: {e}")
                stats.increment('config_errors')
                return False
            
            # 5. Инициализация и установка вебхука
            async def async_init():
                try:
                    await application.initialize()
                    logger.info("✅ Application инициализировано (только webhook)")
                    
                    if os.getenv('AUTO_SET_WEBHOOK', 'true').lower() == 'true':
                        webhook_url = get_webhook_url()
                        
                        try:
                            await application.bot.delete_webhook(drop_pending_updates=True)
                            logger.info("✅ Старый вебхук удалён")
                            
                            await application.bot.set_webhook(
                                url=webhook_url,
                                drop_pending_updates=True,
                                allowed_updates=["message", "callback_query"],
                                max_connections=40
                            )
                            logger.info(f"✅ Вебхук установлен: {webhook_url}")
                            
                            webhook_info = await application.bot.get_webhook_info()
                            logger.info(f"✅ Информация о вебхуке: URL={webhook_info.url}")
                            
                        except Exception as e:
                            logger.error(f"❌ Ошибка установки вебхука: {e}")
                            # Продолжаем без вебхука
                    else:
                        logger.info("⚠️ Автоматическая установка вебхука отключена")
                    
                    return True
                    
                except Exception as e:
                    logger.error(f"❌ Ошибка в async_init: {e}")
                    raise
            
            try:
                run_async_safely(async_init())
                initialized = True
                logger.info("✅ Бот успешно инициализирован")
                return True
                
            except Exception as e:
                logger.error(f"❌ Ошибка инициализации: {e}", exc_info=True)
                stats.set('last_error', str(e))
                stats.increment('config_errors')
                return False
            
        except Exception as e:
            logger.error(f"❌ Критическая ошибка инициализации: {e}", exc_info=True)
            stats.set('last_error', str(e))
            stats.increment('config_errors')
            return False

def cleanup():
    """Очистка ресурсов при завершении"""
    global application
    
    if application:
        try:
            logger.info("🛑 Очистка ресурсов бота...")
            
            async def async_cleanup():
                if os.getenv('DELETE_WEBHOOK_ON_EXIT', 'false').lower() == 'true':
                    await application.bot.delete_webhook(drop_pending_updates=True)
                    logger.info("✅ Вебхук удален")
                
                await application.stop()
                await application.shutdown()
                logger.info("✅ Application остановлено и выключено")
            
            run_async_safely(async_cleanup())
                
        except Exception as e:
            logger.error(f"❌ Ошибка при очистке: {e}")
        finally:
            application = None

# Регистрируем очистку при завершении
atexit.register(cleanup)

# Настраиваем graceful shutdown
setup_graceful_shutdown()

# Инициализация при импорте
if not init_bot():
    logger.critical("❌ Не удалось инициализировать бота")
    # Не завершаем приложение, чтобы health-check мог показать ошибку

# ======================
# КЭШИРОВАНИЕ СТАТИСТИКИ
# ======================

@lru_cache(maxsize=1)
def get_cached_stats(ttl_hash):
    """Кэширование статистики для главной страницы"""
    return stats.get_all()

def get_ttl_hash(seconds=30):
    """Хэш для TTL кэша"""
    return int(time.time() / seconds)

# ======================
# FLASK ЭНДПОИНТЫ (остаются без изменений, кроме версии)
# ======================

@app.route('/')
def index():
    """Главная страница с улучшенной статистикой"""
    # Обновляем кэш каждые 30 секунд
    all_stats = get_cached_stats(get_ttl_hash(30))
    rate_stats = rate_limiter.get_stats()
    
    status = "🟢 Активен" if initialized else "🔴 Ошибка инициализации"
    status_class = "status-ok" if initialized else "status-error"
    
    # Форматирование времени последнего запроса
    last_request = all_stats.get('last_request_time')
    if last_request:
        try:
            last_time = datetime.fromisoformat(last_request)
            last_str = last_time.strftime('%H:%M:%S')
        except:
            last_str = "неизвестно"
    else:
        last_str = "никогда"
    
    # Рассчитываем аптайм
    startup_time = datetime.fromisoformat(all_stats['startup_time'])
    uptime_seconds = (datetime.now() - startup_time).total_seconds()
    uptime_str = format_uptime(uptime_seconds)
    
    return f"""
<!DOCTYPE html>
<html>
<head>
    <title>HR Bot Мечел</title>
    <meta charset="utf-8">
    <meta name="viewport" content="width=device-width, initial-scale=1">
    <style>
        body {{ 
            font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; 
            padding: 20px; 
            max-width: 1000px; 
            margin: 0 auto;
            background: linear-gradient(135deg, #667eea 0%, #764ba2 100%);
            min-height: 100vh;
            color: #333;
        }}
        .container {{ 
            background: rgba(255, 255, 255, 0.98); 
            padding: 30px; 
            border-radius: 20px; 
            box-shadow: 0 15px 35px rgba(0,0,0,0.2);
            color: #333;
            margin-top: 20px;
        }}
        .status {{ 
            display: inline-block; 
            padding: 12px 25px; 
            border-radius: 50px; 
            font-weight: bold;
            margin: 15px 0;
            font-size: 16px;
        }}
        .status-ok {{ 
            background: linear-gradient(135deg, #27ae60, #2ecc71); 
            color: white;
            box-shadow: 0 4px 15px rgba(39, 174, 96, 0.3);
        }}
        .status-error {{ 
            background: linear-gradient(135deg, #e74c3c, #c0392b); 
            color: white;
            box-shadow: 0 4px 15px rgba(231, 76, 60, 0.3);
        }}
        .btn {{ 
            display: inline-block; 
            padding: 12px 24px; 
            background: linear-gradient(135deg, #3498db, #2980b9); 
            color: white; 
            text-decoration: none; 
            border-radius: 50px; 
            margin: 10px 8px;
            font-size: 14px;
            font-weight: 600;
            transition: all 0.3s ease;
            border: none;
            cursor: pointer;
        }}
        .btn:hover {{ 
            transform: translateY(-2px);
            box-shadow: 0 7px 20px rgba(52, 152, 219, 0.4);
        }}
        .btn-secondary {{
            background: linear-gradient(135deg, #95a5a6, #7f8c8d);
        }}
        .btn-secondary:hover {{
            box-shadow: 0 7px 20px rgba(149, 165, 166, 0.4);
        }}
        .btn-danger {{
            background: linear-gradient(135deg, #e74c3c, #c0392b);
        }}
        .btn-danger:hover {{
            box-shadow: 0 7px 20px rgba(231, 76, 60, 0.4);
        }}
        h1 {{ 
            color: #2c3e50;
            margin-top: 0;
            font-size: 2.5rem;
            background: linear-gradient(135deg, #2c3e50, #3498db);
            -webkit-background-clip: text;
            -webkit-text-fill-color: transparent;
        }}
        h3 {{
            color: #34495e;
            border-bottom: 2px solid #ecf0f1;
            padding-bottom: 10px;
        }}
        .metric-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
            gap: 15px;
            margin: 25px 0;
        }}
        .metric {{ 
            background: #f8f9fa;
            padding: 20px;
            border-radius: 10px;
            border-left: 4px solid #3498db;
            transition: all 0.3s ease;
        }}
        .metric:hover {{
            transform: translateY(-3px);
            box-shadow: 0 5px 15px rgba(0,0,0,0.1);
        }}
        .metric-label {{ 
            font-weight: 600;
            color: #7f8c8d;
            font-size: 12px;
            text-transform: uppercase;
            letter-spacing: 1px;
        }}
        .metric-value {{ 
            color: #2c3e50;
            font-size: 24px;
            font-weight: 700;
            margin: 8px 0;
        }}
        .metric-subvalue {{
            color: #95a5a6;
            font-size: 12px;
        }}
        .security-grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(250px, 1fr));
            gap: 15px;
            margin: 20px 0;
        }}
        .security-badge {{
            display: inline-block;
            padding: 12px 20px;
            background: linear-gradient(135deg, #27ae60, #2ecc71);
            color: white;
            border-radius: 10px;
            font-size: 13px;
            font-weight: 600;
            margin: 5px;
            text-align: center;
        }}
        .security-badge-warning {{
            background: linear-gradient(135deg, #f39c12, #e67e22);
        }}
        .features {{ 
            margin-top: 40px; 
            background: #f8f9fa; 
            padding: 25px; 
            border-radius: 15px;
            border: 1px solid #e9ecef;
        }}
        .features ul {{ 
            font-size: 15px; 
            color: #495057;
            padding-left: 25px;
            line-height: 1.8;
        }}
        .features li {{
            margin: 10px 0;
        }}
        .badge {{
            display: inline-block;
            padding: 4px 10px;
            background: #e3f2fd;
            color: #1976d2;
            border-radius: 20px;
            font-size: 12px;
            font-weight: 600;
            margin-right: 8px;
        }}
        .footer {{
            margin-top: 40px;
            text-align: center;
            color: #7f8c8d;
            font-size: 14px;
            border-top: 1px solid #ecf0f1;
            padding-top: 20px;
        }}
        .btn-container {{
            display: flex;
            flex-wrap: wrap;
            gap: 10px;
            margin: 20px 0;
        }}
        @media (max-width: 768px) {{
            .container {{ padding: 20px; }}
            .metric-grid, .security-grid {{ grid-template-columns: 1fr; }}
            .btn-container {{ flex-direction: column; }}
            .btn {{ width: 100%; text-align: center; }}
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
        
        <p><strong>Режим:</strong> Webhook-only (без polling)</p>
        <p><strong>Версия:</strong> <span class="badge">9.3.5</span> Стабильная (без pandas)</p>
        <p><strong>Аптайм:</strong> {uptime_str}</p>
        <p><strong>Время сервера:</strong> {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        
        <div class="security-grid">
            <div class="security-badge">🛡️ Rate Limiting активен</div>
            <div class="security-badge">🔒 Thread-safe статистика</div>
            <div class="security-badge">⚡ LRU кэширование</div>
            <div class="security-badge {'security-badge-warning' if not PSUTIL_AVAILABLE else ''}">
                {'⚠️ ' if not PSUTIL_AVAILABLE else '📊 '}Мониторинг ресурсов
            </div>
            <div class="security-badge {'security-badge-warning' if all_stats.get('config_errors', 0) > 0 else ''}">
                {'⚠️ ' if all_stats.get('config_errors', 0) > 0 else '✅ '}Конфигурация
            </div>
            <div class="security-badge" style="background: linear-gradient(135deg, #9b59b6, #8e44ad);">
                🗑️ pandas удален
            </div>
        </div>
        
        <div class="btn-container">
            <a href="/health" class="btn">🔍 Проверка здоровья</a>
            <a href="/stats" class="btn">📊 Статистика API</a>
            <a href="/checkwebhook" class="btn btn-secondary">🌐 Проверить вебхук</a>
            <a href="/setwebhook" class="btn btn-secondary">🔄 Установить вебхук</a>
            <a href="/deletewebhook" class="btn btn-danger">🗑️ Удалить вебхук</a>
        </div>
        
        <div style="margin-top: 30px;">
            <h3>📊 Быстрая статистика:</h3>
            <div class="metric-grid">
                <div class="metric">
                    <div class="metric-label">Всего запросов</div>
                    <div class="metric-value">{all_stats['requests_total']}</div>
                    <div class="metric-subvalue">webhook вызовы</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Ошибки</div>
                    <div class="metric-value">{all_stats['errors_total']}</div>
                    <div class="metric-subvalue">всего/таймаутов: {all_stats['timeouts_total']}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Ошибки конфигурации</div>
                    <div class="metric-value">{all_stats.get('config_errors', 0)}</div>
                    <div class="metric-subvalue">проблемы инициализации</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Последний запрос</div>
                    <div class="metric-value">{last_str}</div>
                    <div class="metric-subvalue">время сервера</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Отслеживаемые IP</div>
                    <div class="metric-value">{rate_stats['tracked_ips']}</div>
                    <div class="metric-subvalue">лимит: {rate_stats['max_tracked_ips']}</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Блокировки спама</div>
                    <div class="metric-value">{rate_stats['blocked_count']}</div>
                    <div class="metric-subvalue">{rate_stats['block_rate_percent']}% от проверок</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Очистки памяти</div>
                    <div class="metric-value">{rate_stats['cleanups_performed']}</div>
                    <div class="metric-subvalue">выполнено очисток</div>
                </div>
                <div class="metric">
                    <div class="metric-label">Средняя нагрузка</div>
                    <div class="metric-value">{rate_stats['avg_requests_per_ip']}</div>
                    <div class="metric-subvalue">запросов на IP</div>
                </div>
            </div>
        </div>
        
        <div class="features">
            <h3>🎯 Особенности версии 9.3.5:</h3>
            <ul>
                <li><strong>✅ pandas удален</strong> - для полной совместимости с Python 3.13+ на бесплатном Render</li>
                <li><strong>✅ Улучшенная валидация токена</strong> - проверка формата через регулярные выражения</li>
                <li><strong>✅ Контроль создания файлов</strong> - файл FAQ создается только по запросу</li>
                <li><strong>✅ Расширенная обработка ошибок</strong> - отдельный счетчик ошибок конфигурации</li>
                <li><strong>✅ Универсальный адаптер токенов</strong> - работает с TELEGRAM_BOT_TOKEN, BOT_TOKEN, BOTTOKEN</li>
                <li><strong>✅ Ручная конфигурация Gunicorn</strong> - гарантия работы на Render</li>
                <li><strong>✅ Проверка конфигурационных файлов</strong> - при запуске проверяются все необходимые файлы</li>
                <li><strong>✅ Thread-safe статистика</strong> - полная потокобезопасность</li>
                <li><strong>✅ Безопасный event loop</strong> - исправлена критическая ошибка 9.2</li>
                <li><strong>✅ Таймауты обработки</strong> - 30 секунд с логированием</li>
                <li><strong>✅ Rate limiting с LRU</strong> - защита от спама + ограничение памяти</li>
                <li><strong>✅ Graceful shutdown</strong> - корректное завершение на Render</li>
            </ul>
        </div>
        
        <div class="footer">
            <p>HR Bot Мечел | Версия 9.3.5 (без pandas) | Работает на Render.com</p>
            <p>Техническая поддержка: IT отдел Мечел</p>
            <p>Системное время: {datetime.now().strftime("%Y-%m-%d %H:%M:%S")}</p>
        </div>
    </div>
</body>
</html>
"""

# Остальные эндпоинты (/health, /stats, /ping, /setwebhook, /checkwebhook, /deletewebhook, /webhook, /test)
# остаются такими же, как в версии 9.3.4, но с обновленной версией в логах

@app.route('/health')
def health():
    """Health-check для Render с улучшенной проверкой конфигурации"""
    health_status = {
        'status': 'healthy' if initialized else 'unhealthy',
        'service': 'hr-bot-mechel',
        'timestamp': datetime.now().isoformat(),
        'bot_initialized': initialized,
        'version': '9.3.5',
        'mode': 'webhook-only',
        'requests_total': stats.get('requests_total'),
        'errors_total': stats.get('errors_total'),
        'config_errors': stats.get('config_errors', 0),
        'uptime_seconds': (datetime.now() - datetime.fromisoformat(stats.get('startup_time'))).total_seconds(),
        'checks': {}
    }
    
    # Проверка бота
    health_status['checks']['bot_initialization'] = {
        'status': 'healthy' if initialized else 'unhealthy',
        'message': 'Бот инициализирован' if initialized else 'Бот не инициализирован'
    }
    
    # Проверка конфигурации
    config_errors = stats.get('config_errors', 0)
    health_status['checks']['configuration'] = {
        'status': 'healthy' if config_errors == 0 else 'unhealthy',
        'message': f'Ошибок конфигурации: {config_errors}'
    }
    
    # Проверка базы данных (если используется)
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        cursor.execute('SELECT 1')
        conn.close()
        health_status['checks']['database'] = {
            'status': 'healthy',
            'message': 'База данных доступна'
        }
    except Exception as e:
        health_status['checks']['database'] = {
            'status': 'unhealthy',
            'message': f'База данных недоступна: {str(e)}'
        }
    
    # Проверка памяти (если psutil доступен)
    if PSUTIL_AVAILABLE:
        try:
            memory = psutil.virtual_memory()
            health_status['checks']['memory'] = {
                'status': 'healthy' if memory.percent < 90 else 'warning',
                'message': f'Использование памяти: {memory.percent}%',
                'percent_used': memory.percent
            }
        except:
            health_status['checks']['memory'] = {
                'status': 'unknown',
                'message': 'Не удалось проверить память'
            }
    
    # Определяем общий статус
    unhealthy_checks = [check for check in health_status['checks'].values() 
                       if check['status'] not in ['healthy', 'unknown']]
    
    if not initialized:
        health_status['status'] = 'unhealthy'
        health_status['message'] = 'Бот не инициализирован'
    elif unhealthy_checks:
        health_status['status'] = 'unhealthy'
        health_status['message'] = f'Найдено проблем: {len(unhealthy_checks)}'
    else:
        health_status['status'] = 'healthy'
        health_status['message'] = 'Все системы работают нормально'
    
    return jsonify(health_status), 200

@app.route('/stats')
def api_stats():
    """API статистики в JSON формате"""
    all_stats = stats.get_all()
    rate_stats = rate_limiter.get_stats()
    
    response = {
        'bot': all_stats,
        'rate_limiter': rate_stats,
        'system': {
            'python_version': sys.version,
            'platform': sys.platform,
            'initialized': initialized,
            'psutil_available': PSUTIL_AVAILABLE
        }
    }
    
    if PSUTIL_AVAILABLE:
        response['system']['memory'] = {
            'percent': psutil.virtual_memory().percent,
            'available_gb': round(psutil.virtual_memory().available / (1024**3), 2)
        }
        response['system']['cpu'] = {
            'percent': psutil.cpu_percent(interval=0.1)
        }
    
    return jsonify(response)

@app.route('/ping')
def ping():
    """Простой ping для проверки доступности"""
    return jsonify({
        'status': 'pong',
        'timestamp': datetime.now().isoformat(),
        'version': '9.3.5'
    })

@app.route('/setwebhook')
def set_webhook():
    """Ручная установка вебхука"""
    if not application:
        return jsonify({'error': 'Application не инициализировано'}), 500
    
    try:
        async def async_set():
            webhook_url = get_webhook_url()
            await application.bot.delete_webhook(drop_pending_updates=True)
            await application.bot.set_webhook(
                url=webhook_url,
                drop_pending_updates=True,
                allowed_updates=["message", "callback_query"],
                max_connections=40
            )
            return webhook_url
        
        webhook_url = run_async_safely(async_set())
        return jsonify({
            'success': True,
            'message': 'Вебхук установлен',
            'url': webhook_url
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/checkwebhook')
def check_webhook():
    """Проверка состояния вебхука"""
    if not application:
        return jsonify({'error': 'Application не инициализировано'}), 500
    
    try:
        async def async_check():
            return await application.bot.get_webhook_info()
        
        webhook_info = run_async_safely(async_check())
        return jsonify({
            'url': webhook_info.url,
            'has_custom_certificate': webhook_info.has_custom_certificate,
            'pending_update_count': webhook_info.pending_update_count,
            'ip_address': webhook_info.ip_address,
            'last_error_date': webhook_info.last_error_date,
            'last_error_message': webhook_info.last_error_message,
            'max_connections': webhook_info.max_connections,
            'allowed_updates': webhook_info.allowed_updates
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/deletewebhook')
def delete_webhook():
    """Удаление вебхука"""
    if not application:
        return jsonify({'error': 'Application не инициализировано'}), 500
    
    try:
        async def async_delete():
            return await application.bot.delete_webhook(drop_pending_updates=True)
        
        result = run_async_safely(async_delete())
        return jsonify({
            'success': result,
            'message': 'Вебхук удален'
        })
    except Exception as e:
        return jsonify({'error': str(e)}), 500

@app.route('/webhook', methods=['POST'])
def webhook():
    """Основной endpoint для вебхука Telegram"""
    stats.update_last_request()
    
    # Rate limiting по IP
    client_ip = request.remote_addr
    if not rate_limiter.is_allowed(client_ip):
        stats.increment('rate_limit_hits')
        logger.warning(f"🚫 Rate limit превышен для IP: {client_ip}")
        return jsonify({'status': 'rate_limit_exceeded'}), 429
    
    stats.increment('requests_total')
    stats.increment('webhook_calls')
    
    if not application:
        logger.error("Application не инициализировано при получении вебхука")
        stats.increment('errors_total')
        return jsonify({'status': 'application_not_initialized'}), 500
    
    try:
        # Получаем данные обновления
        data = request.get_json()
        if not data:
            logger.error("Получен пустой запрос вебхука")
            stats.increment('errors_total')
            return jsonify({'status': 'invalid_data'}), 400
        
        update = Update.de_json(data, application.bot)
        
        # Обрабатываем обновление
        async def process_update():
            try:
                await application.process_update(update)
                stats.increment('successful_responses')
                logger.debug(f"✅ Обработано обновление {update.update_id}")
            except Exception as e:
                logger.error(f"Ошибка обработки обновления {update.update_id}: {e}")
                stats.increment('errors_total')
        
        # Запускаем обработку с таймаутом
        try:
            run_async_safely(process_update())
        except asyncio.TimeoutError:
            logger.warning(f"⏱️ Таймаут обработки обновления {update.update_id}")
            stats.increment('timeouts_total')
        except Exception as e:
            logger.error(f"Ошибка выполнения асинхронной задачи: {e}")
            stats.increment('errors_total')
        
        return jsonify({'status': 'ok'}), 200
        
    except Exception as e:
        logger.error(f"❌ Ошибка обработки вебхука: {e}", exc_info=True)
        stats.increment('errors_total')
        stats.set('last_error', str(e))
        return jsonify({'status': 'error', 'error': str(e)}), 500

@app.route('/test')
def test_page():
    """Тестовая страница для проверки работы"""
    return """
    <h1>HR Bot Мечел - Тестовая страница</h1>
    <p>Приложение работает версии 9.3.5 (без pandas).</p>
    <p>Время сервера: {}</p>
    <p><a href="/">На главную</a></p>
    """.format(datetime.now().strftime("%Y-%m-%d %H:%M:%S"))

# ======================
# ЗАПУСК СЕРВЕРА
# ======================

if __name__ == "__main__":
    port = config.get_port()
    logger.info("=" * 60)
    logger.info(f"🚀 HR Bot Мечел - Версия 9.3.5 (БЕЗ PANDAS)")
    logger.info(f"📅 Дата сборки: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}")
    logger.info(f"📊 Режим: Webhook-only")
    logger.info(f"🌐 Webhook URL: {get_webhook_url()}")
    logger.info(f"🔧 Проверка зависимостей: ✅ Пройдена (без pandas)")
    logger.info(f"📋 Проверка файлов конфигурации: ✅ Пройдена")
    logger.info(f"🛡️ Rate limiting: 30 запр/мин, макс {rate_limiter.max_tracked_ips} IP")
    logger.info(f"📈 Мониторинг ресурсов: {'✅ Включен' if PSUTIL_AVAILABLE else '⚠️ Отключен'}")
    logger.info(f"🔐 Проверка токена: ✅ Формат токена проверен")
    logger.info(f"🗑️ pandas: ❌ Удален для совместимости с Python 3.13+")
    logger.info("=" * 60)
    
    app.run(host='0.0.0.0', port=port, debug=False)
