"""
УЛУЧШЕННАЯ КОНФИГУРАЦИЯ БОТА МЕЧЕЛ
Версия 2.0 - С защитой от SQL-инъекций и гибкой конфигурацией
"""

import os
import logging
from dotenv import load_dotenv

# Загружаем переменные окружения
load_dotenv()

logger = logging.getLogger(__name__)

class Config:
    """Класс для работы с конфигурацией с защитой от SQL-инъекций"""
    
    # Константы для имен таблиц (защита от опечаток и SQL-инъекций)
    TABLE_FAQ = 'faq'
    TABLE_FAQ_KEYWORDS = 'faq_keywords'
    TABLE_UNANSWERED_QUERIES = 'unanswered_queries'
    TABLE_BOT_METRICS = 'bot_metrics'
    
    # Минимальные требования для работы системы
    MIN_FAQ_RECORDS = 70
    MIN_DATABASE_CONNECTIONS = 1
    
    @staticmethod
    def get_bot_token():
        """Получение токена бота с проверкой"""
        token = os.getenv('BOT_TOKEN')
        if not token or token == 'ваш_токен_бота':
            logger.error("❌ ТОКЕН БОТА НЕ НАЙДЕН ИЛИ УСТАНОВЛЕН ПО УМОЛЧАНИЮ")
            logger.error("Установите переменную окружения BOT_TOKEN")
            return None
        return token
    
    @staticmethod
    def get_db_connection():
        """Безопасное получение соединения с базой данных с обработкой ошибок"""
        import psycopg2
        from psycopg2 import OperationalError
        
        database_url = os.getenv('DATABASE_URL')
        if not database_url:
            logger.error("❌ DATABASE_URL не указан в переменных окружения")
            raise ConnectionError("DATABASE_URL не указан")
        
        try:
            # Параметры подключения с таймаутом
            connection_params = {
                'connect_timeout': 10,
                'application_name': 'mechel-hr-bot'
            }
            
            # Парсим URL и создаем подключение
            conn = psycopg2.connect(database_url, **connection_params)
            
            # Проверяем соединение
            conn.autocommit = False
            cursor = conn.cursor()
            cursor.execute("SELECT 1")
            cursor.close()
            
            logger.info("✅ Успешное подключение к базе данных")
            return conn
            
        except OperationalError as e:
            logger.error(f"❌ Ошибка подключения к базе данных: {e}")
            raise ConnectionError(f"Не удалось подключиться к базе данных: {e}")
            
        except Exception as e:
            logger.error(f"❌ Неожиданная ошибка при подключении к БД: {e}")
            raise ConnectionError(f"Неожиданная ошибка БД: {e}")
    
    @staticmethod
    def get_webhook_url():
        """Получение URL вебхука"""
        return os.getenv('WEBHOOK_URL')
    
    @staticmethod
    def get_secret_token():
        """Получение секретного токена для вебхука"""
        return os.getenv('SECRET_TOKEN')
    
    @staticmethod
    def get_port():
        """Получение порта"""
        try:
            return int(os.getenv('PORT', 10000))
        except ValueError:
            logger.warning("⚠️ Неверный формат PORT, используется значение по умолчанию: 10000")
            return 10000
    
    @staticmethod
    def get_admin_ids():
        """Безопасное получение списка ID администраторов"""
        admin_ids_str = os.getenv('ADMIN_IDS', '')
        admin_ids = []
        
        if admin_ids_str:
            try:
                for admin_id in admin_ids_str.split(','):
                    admin_id = admin_id.strip()
                    if admin_id:
                        admin_ids.append(int(admin_id))
            except ValueError as e:
                logger.warning(f"⚠️ Неверный формат ADMIN_IDS: {e}")
        
        return admin_ids
    
    @staticmethod
    def is_feedback_enabled():
        """Проверка включения системы обратной связи"""
        return os.getenv('FEEDBACK_ENABLED', 'true').lower() == 'true'
    
    @staticmethod
    def is_meme_enabled():
        """Проверка включения мемов"""
        return os.getenv('MEME_ENABLED', 'false').lower() == 'true'
    
    @staticmethod
    def get_webhook_path():
        """Получение пути вебхука"""
        return os.getenv('WEBHOOK_PATH', '/')
    
    @staticmethod
    def get_table_name(table_key: str) -> str:
        """
        Безопасное получение имени таблицы по ключу
        Защита от SQL-инъекций через белый список
        """
        table_mapping = {
            'faq': Config.TABLE_FAQ,
            'faq_keywords': Config.TABLE_FAQ_KEYWORDS,
            'unanswered_queries': Config.TABLE_UNANSWERED_QUERIES,
            'bot_metrics': Config.TABLE_BOT_METRICS,
        }
        
        if table_key not in table_mapping:
            raise ValueError(f"Неизвестный ключ таблицы: {table_key}")
        
        return table_mapping[table_key]
    
    @staticmethod
    def get_health_thresholds():
        """Получение пороговых значений для проверки здоровья"""
        return {
            'min_faq_records': Config.MIN_FAQ_RECORDS,
            'max_response_time_ms': 5000,
            'min_database_connections': Config.MIN_DATABASE_CONNECTIONS,
            'max_database_size_mb': 100,
        }
    
    @staticmethod
    def validate_database_url():
        """Валидация URL базы данных"""
        db_url = os.getenv('DATABASE_URL', '')
        
        if not db_url:
            return False, "DATABASE_URL не указан"
        
        # Проверяем формат PostgreSQL URL
        if not (db_url.startswith('postgresql://') or db_url.startswith('postgres://')):
            return False, "Неверный формат DATABASE_URL. Ожидается postgresql://"
        
        return True, "URL базы данных валиден"

# Глобальный экземпляр конфигурации
config = Config()

# Экспортируем константы для использования в других модулях
TABLE_FAQ = Config.TABLE_FAQ
TABLE_FAQ_KEYWORDS = Config.TABLE_FAQ_KEYWORDS
TABLE_UNANSWERED_QUERIES = Config.TABLE_UNANSWERED_QUERIES
TABLE_BOT_METRICS = Config.TABLE_BOT_METRICS
MIN_FAQ_RECORDS = Config.MIN_FAQ_RECORDS
