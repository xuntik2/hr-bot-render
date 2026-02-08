"""
КОНФИГУРАЦИЯ ДЛЯ RENDER + ВЕБХУКИ
Поддержка PostgreSQL и SQLite
"""

import os
from typing import List
from dotenv import load_dotenv
import sqlite3
import logging

load_dotenv()
logger = logging.getLogger(__name__)

class Config:
    """Конфигурация бота для Render с поддержкой вебхуков"""
    
    # =========== ПУТИ И ФАЙЛЫ ===========
    DB_PATH = 'faq_database.db'  # Для локальной разработки
    
    # =========== КОНСТАНТЫ ПО УМОЛЧАНИЮ ===========
    MAX_MESSAGE_LENGTH = 500
    FEEDBACK_MIN_LENGTH = 3
    FEEDBACK_MAX_LENGTH = 500
    REQUEST_TIMEOUT = 3
    
    # Кэширование
    CACHE_MAX_SIZE = 1000
    CACHE_TTL_SECONDS = 1800
    
    # Расписание
    SLEEP_INTERVAL_HOURS = 6
    CLEANUP_OLDER_THAN_DAYS = 30
    
    # Поисковые настройки
    SEARCH_THRESHOLD = 0.3
    MAX_SEARCH_RESULTS = 5
    
    @classmethod
    def validate(cls) -> bool:
        """Валидация конфигурации"""
        errors = []
        
        # Проверка токена
        token = cls.get_bot_token()
        if not token:
            errors.append("BOT_TOKEN не найден. Проверьте переменные окружения")
        elif token == 'ВАШ_ТОКЕН_ЗДЕСЬ':
            errors.append("Замените 'ВАШ_ТОКЕН_ЗДЕСЬ' на реальный токен")
        elif len(token) < 30:
            errors.append(f"Токен слишком короткий ({len(token)} символов)")
        
        # Проверка для продакшена (Render)
        if cls.is_postgresql():
            database_url = os.getenv('DATABASE_URL')
            if not database_url:
                errors.append("DATABASE_URL не установлен для PostgreSQL")
            elif 'postgresql://' not in database_url and 'postgres://' not in database_url:
                errors.append("Некорректный формат DATABASE_URL для PostgreSQL")
        
        # Вывод ошибок
        if errors:
            for error in errors:
                logger.error(f"❌ {error}")
            return False
        
        # Успешная валидация
        logger.info("✅ Конфигурация успешно загружена!")
        logger.info(f"   🤖 Токен: {token[:10]}...{token[-10:]}")
        logger.info(f"   👑 Админы: {cls.get_admin_ids()}")
        logger.info(f"   🗄️  БД: {'PostgreSQL' if cls.is_postgresql() else 'SQLite'}")
        logger.info(f"   🎭 Мемы: {'ВКЛ' if cls.is_meme_enabled() else 'ВЫКЛ'}")
        logger.info(f"   💬 Отзывы: {'ВКЛ' if cls.is_feedback_enabled() else 'ВЫКЛ'}")
        return True
    
    # =========== МЕТОДЫ ДЛЯ ПОЛУЧЕНИЯ НАСТРОЕК ===========
    
    @classmethod
    def get_admin_ids(cls) -> List[int]:
        """Получить список ID администраторов"""
        admin_ids_str = os.getenv('ADMIN_IDS', '')
        if not admin_ids_str:
            return []
        
        try:
            ids = []
            for id_str in admin_ids_str.split(','):
                id_str = id_str.strip()
                if id_str:
                    ids.append(int(id_str))
            return ids
        except ValueError as e:
            logger.error(f"❌ Ошибка в формате ADMIN_IDS: {e}")
            return []
    
    @classmethod
    def get_bot_token(cls) -> str:
        """Получить токен бота"""
        token = os.getenv('BOT_TOKEN', '')
        return token.strip(" '\"")
    
    @classmethod
    def get_max_message_length(cls) -> int:
        """Получить максимальную длину сообщения"""
        try:
            return int(os.getenv('MAX_MESSAGE_LENGTH', cls.MAX_MESSAGE_LENGTH))
        except ValueError:
            return cls.MAX_MESSAGE_LENGTH
    
    @classmethod
    def get_rate_limit_seconds(cls) -> int:
        """Получить лимит запросов в секундах"""
        try:
            return int(os.getenv('RATE_LIMIT_SECONDS', 2))
        except ValueError:
            return 2
    
    @classmethod
    def get_search_threshold(cls) -> float:
        """Получить порог релевантности"""
        try:
            return float(os.getenv('SEARCH_THRESHOLD', cls.SEARCH_THRESHOLD))
        except ValueError:
            return cls.SEARCH_THRESHOLD
    
    @classmethod
    def get_max_search_results(cls) -> int:
        """Получить максимальное количество результатов поиска"""
        try:
            return int(os.getenv('MAX_SEARCH_RESULTS', cls.MAX_SEARCH_RESULTS))
        except ValueError:
            return cls.MAX_SEARCH_RESULTS
    
    # =========== ФЛАГИ (ВКЛ/ВЫКЛ) ===========
    
    @classmethod
    def is_meme_enabled(cls) -> bool:
        """Проверить, включены ли мемы"""
        return os.getenv('MEME_ENABLED', 'False').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def is_feedback_enabled(cls) -> bool:
        """Проверить, включены ли отзывы"""
        return os.getenv('FEEDBACK_ENABLED', 'True').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def is_spam_protection_enabled(cls) -> bool:
        """Проверить, включена ли защита от спама"""
        return os.getenv('SPAM_PROTECTION_ENABLED', 'True').lower() in ['true', '1', 'yes', 'y']
    
    # =========== МЕТОДЫ ДЛЯ РАБОТЫ С БАЗОЙ ДАННЫХ ===========
    
    @classmethod
    def is_postgresql(cls) -> bool:
        """Определить, используется ли PostgreSQL"""
        return bool(os.getenv('DATABASE_URL'))
    
    @classmethod
    def get_db_connection(cls):
        """Получить соединение с БД (PostgreSQL или SQLite)"""
        if cls.is_postgresql():
            # PostgreSQL для Render (Psycopg 3)
            try:
                from psycopg import connect
                database_url = os.getenv('DATABASE_URL')
                
                # Fix для Render: замена postgres:// на postgresql://
                if database_url.startswith('postgres://'):
                    database_url = database_url.replace('postgres://', 'postgresql://', 1)
                
                logger.info(f"🔗 Подключение к PostgreSQL: {database_url[:30]}...")
                conn = connect(database_url)
                return conn
            except ImportError:
                logger.error("❌ Psycopg 3 не установлен. Установите: pip install psycopg[binary]")
                raise
            except Exception as e:
                logger.error(f"❌ Ошибка подключения к PostgreSQL: {e}")
                raise
        else:
            # SQLite для локальной разработки
            logger.info(f"🔗 Подключение к SQLite: {cls.DB_PATH}")
            return sqlite3.connect(cls.DB_PATH)
    
    @classmethod
    def get_placeholder(cls) -> str:
        """Получить placeholder для SQL запросов"""
        return '%s' if cls.is_postgresql() else '?'
    
    @classmethod
    def get_database_type(cls) -> str:
        """Получить тип базы данных"""
        return 'postgresql' if cls.is_postgresql() else 'sqlite'
    
    # =========== ДОПОЛНИТЕЛЬНЫЕ МЕТОДЫ ===========
    
    @classmethod
    def get_feedback_limits(cls) -> tuple:
        """Получить минимальную и максимальную длину отзыва"""
        return (cls.FEEDBACK_MIN_LENGTH, cls.FEEDBACK_MAX_LENGTH)
    
    @classmethod
    def get_cache_settings(cls) -> tuple:
        """Получить настройки кэширования"""
        return (cls.CACHE_MAX_SIZE, cls.CACHE_TTL_SECONDS)
    
    @classmethod
    def get_schedule_settings(cls) -> tuple:
        """Получить настройки расписания"""
        return (cls.SLEEP_INTERVAL_HOURS, cls.CLEANUP_OLDER_THAN_DAYS)

# Экспортируем экземпляр для удобства
config = Config()
