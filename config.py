"""
ПРОСТОЙ КОНФИГ ДЛЯ RENDER FREE
"""

import os
import sys
import logging
from typing import List

# Загружаем .env если есть
if os.path.exists('.env'):
    from dotenv import load_dotenv
    load_dotenv()

logger = logging.getLogger(__name__)

class Config:
    """Простая конфигурация"""
    
    # Константы таблиц
    TABLE_FAQ = 'faq'
    TABLE_KEYWORDS = 'faq_keywords'
    TABLE_METRICS = 'bot_metrics'
    
    # Порог FAQ
    MIN_FAQ_RECORDS = 50
    
    # Белый список таблиц для безопасности
    ALLOWED_TABLES = ['faq', 'faq_keywords', 'bot_metrics', 'unanswered_queries']
    
    @staticmethod
    def get_bot_token() -> str:
        """Получение токена бота"""
        token = os.getenv('BOT_TOKEN')
        if not token:
            logger.error("❌ BOT_TOKEN не найден")
            raise ValueError("Требуется BOT_TOKEN")
        return token
    
    @staticmethod
    def get_db_connection():
        """Безопасное подключение к БД"""
        import psycopg2
        
        db_url = os.getenv('DATABASE_URL')
        if not db_url:
            logger.error("❌ DATABASE_URL не найден")
            raise ValueError("Требуется DATABASE_URL")
        
        try:
            conn = psycopg2.connect(db_url, connect_timeout=10)
            return conn
        except Exception as e:
            logger.error(f"❌ Ошибка подключения к БД: {e}")
            return None
    
    @staticmethod
    def get_port() -> int:
        """Получение порта"""
        try:
            return int(os.getenv('PORT', 10000))
        except ValueError:
            return 10000
    
    @staticmethod
    def get_admin_ids() -> List[int]:
        """Получение ID администраторов"""
        ids_str = os.getenv('ADMIN_IDS', '')
        ids = []
        
        for id_str in ids_str.split(','):
            id_str = id_str.strip()
            if id_str and id_str.isdigit():
                ids.append(int(id_str))
        
        return ids
    
    @staticmethod
    def validate_table_name(table: str) -> bool:
        """Валидация имени таблицы (защита от SQL injection)"""
        return table in Config.ALLOWED_TABLES
    
    @staticmethod
    def get_safe_table_name(table_key: str) -> str:
        """Безопасное получение имени таблицы по ключу"""
        mapping = {
            'faq': Config.TABLE_FAQ,
            'keywords': Config.TABLE_KEYWORDS,
            'metrics': Config.TABLE_METRICS,
        }
        
        if table_key not in mapping:
            raise ValueError(f"Неизвестный ключ таблицы: {table_key}")
        
        return mapping[table_key]

# Глобальный экземпляр
config = Config()

# Экспорт констант
TABLE_FAQ = Config.TABLE_FAQ
TABLE_KEYWORDS = Config.TABLE_KEYWORDS
TABLE_METRICS = Config.TABLE_METRICS
MIN_FAQ_RECORDS = Config.MIN_FAQ_RECORDS
