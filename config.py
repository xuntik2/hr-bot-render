"""
КОНФИГУРАЦИЯ БОТА ДЛЯ RENDER
"""
import os
import logging
from typing import List
from dotenv import load_dotenv

load_dotenv()
logger = logging.getLogger(__name__)

class Config:
    """Конфигурация бота"""
    
    DB_PATH = 'faq_database.db'
    MAX_MESSAGE_LENGTH = 500
    FEEDBACK_MIN_LENGTH = 3
    FEEDBACK_MAX_LENGTH = 500
    SEARCH_THRESHOLD = 0.3
    MAX_SEARCH_RESULTS = 5
    CACHE_MAX_SIZE = 1000
    CACHE_TTL_SECONDS = 1800
    
    @classmethod
    def validate(cls) -> bool:
        """Валидация конфигурации"""
        errors = []
        
        token = cls.get_bot_token()
        if not token:
            errors.append("BOT_TOKEN не найден")
        elif token == 'ВАШ_ТОКЕН_ЗДЕСЬ':
            errors.append("Замените BOT_TOKEN на реальный токен")
        
        if cls.is_postgresql():
            db_url = os.getenv('DATABASE_URL')
            if not db_url:
                errors.append("DATABASE_URL не установлен для PostgreSQL")
        
        if errors:
            for error in errors:
                logger.error(f"❌ {error}")
            return False
        
        logger.info("✅ Конфигурация успешно загружена")
        logger.info(f"   🗄️  БД: {'PostgreSQL' if cls.is_postgresql() else 'SQLite'}")
        return True
    
    @classmethod
    def get_admin_ids(cls) -> List[int]:
        admin_ids_str = os.getenv('ADMIN_IDS', '')
        if not admin_ids_str:
            return []
        try:
            return [int(id_str.strip()) for id_str in admin_ids_str.split(',') if id_str.strip()]
        except ValueError:
            return []
    
    @classmethod
    def get_bot_token(cls) -> str:
        return os.getenv('BOT_TOKEN', '').strip(" '\"")
    
    @classmethod
    def is_postgresql(cls) -> bool:
        return bool(os.getenv('DATABASE_URL'))
    
    @classmethod
    def get_db_connection(cls):
        if cls.is_postgresql():
            import psycopg2
            db_url = os.getenv('DATABASE_URL')
            if db_url.startswith('postgres://'):
                db_url = db_url.replace('postgres://', 'postgresql://', 1)
            return psycopg2.connect(db_url)
        else:
            import sqlite3
            return sqlite3.connect(cls.DB_PATH)
    
    @classmethod
    def get_placeholder(cls) -> str:
        return '%s' if cls.is_postgresql() else '?'
    
    @classmethod
    def is_meme_enabled(cls) -> bool:
        return os.getenv('MEME_ENABLED', 'False').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def is_feedback_enabled(cls) -> bool:
        return os.getenv('FEEDBACK_ENABLED', 'True').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def get_search_threshold(cls) -> float:
        try:
            return float(os.getenv('SEARCH_THRESHOLD', cls.SEARCH_THRESHOLD))
        except ValueError:
            return cls.SEARCH_THRESHOLD
    
    @classmethod
    def get_rate_limit_seconds(cls) -> int:
        try:
            return int(os.getenv('RATE_LIMIT_SECONDS', 2))
        except ValueError:
            return 2

config = Config()
