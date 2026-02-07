"""
КОНФИГУРАЦИЯ ДЛЯ RENDER + POSTGRESQL (ИСПРАВЛЕННАЯ ВЕРСИЯ)
Поддержка Psycopg 3 и SQLite для разработки
"""

import os
from typing import List
from dotenv import load_dotenv
import sqlite3

load_dotenv()

class Config:
    """Конфигурация бота для Render с поддержкой обоих БД"""
    
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
    
    # Мемы
    MEME_MAX_ATTEMPTS = 15
    MEME_RETRY_DELAY = 3
    
    # Поисковые настройки
    SEARCH_THRESHOLD = 0.3
    MAX_SEARCH_RESULTS = 5
    
    @classmethod
    def validate(cls) -> bool:
        """Валидация конфигурации"""
        errors = []
        warnings = []
        
        # Проверка токена
        token = cls.get_bot_token()
        if not token:
            errors.append("BOT_TOKEN не найден. Проверьте файл .env")
        elif token == 'ВАШ_ТОКЕН_ЗДЕСЬ':
            errors.append("Замените 'ВАШ_ТОКЕН_ЗДЕСЬ' на реальный токен")
        elif len(token) < 30:
            errors.append(f"Токен слишком короткий ({len(token)} символов)")
        
        # Вывод ошибок
        if errors:
            for error in errors:
                print(f"❌ {error}")
            print("\nФайл .env должен содержать:")
            print("BOT_TOKEN=ваш_токен_от_BotFather")
            print("ADMIN_IDS=ваш_telegram_id")
            print("\nОпционально:")
            print("MEME_ENABLED=False")
            print("FEEDBACK_ENABLED=True")
            return False
        
        # Успешная валидация
        print("✅ Конфигурация успешно загружена!")
        print(f"   🤖 Токен: {token[:10]}...{token[-10:]}")
        print(f"   👑 Админы: {cls.get_admin_ids()}")
        print(f"   💬 Отзывы: {'включены' if cls.is_feedback_enabled() else 'выключены'}")
        print(f"   🎭 Мемы: {'включены' if cls.is_meme_enabled() else 'выключены'}")
        print(f"   🛡️ Защита от спама: {'включена' if cls.is_spam_protection_enabled() else 'выключена'}")
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
            print(f"❌ Ошибка в формате ADMIN_IDS: {e}")
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
    
    # =========== ФЛАГИ (ВКЛ/ВЫКЛ) ===========
    
    @classmethod
    def is_feedback_enabled(cls) -> bool:
        """Проверить, включены ли отзывы"""
        return os.getenv('FEEDBACK_ENABLED', 'True').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def is_meme_enabled(cls) -> bool:
        """Проверить, включены ли мемы"""
        return os.getenv('MEME_ENABLED', 'False').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def is_spam_protection_enabled(cls) -> bool:
        """Проверить, включена ли защита от спама"""
        return os.getenv('SPAM_PROTECTION_ENABLED', 'True').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def is_filter_enabled(cls) -> bool:
        """Проверить, включен ли фильтр мата (для мемов)"""
        return os.getenv('MEME_FILTER_ENABLED', 'True').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def force_russian_memes(cls) -> bool:
        """Проверить, принудительно ли русские мемы"""
        return os.getenv('FORCE_RUSSIAN_MEMES', 'True').lower() in ['true', '1', 'yes', 'y']
    
    @classmethod
    def get_meme_max_attempts(cls) -> int:
        """Получить максимальное количество попыток для мемов"""
        try:
            return int(os.getenv('MEME_MAX_ATTEMPTS', cls.MEME_MAX_ATTEMPTS))
        except ValueError:
            return cls.MEME_MAX_ATTEMPTS
    
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
                # Импортируем psycopg только при необходимости
                from psycopg import connect
                database_url = os.getenv('DATABASE_URL')
                
                # Простое подключение по URL (Psycopg 3)
                conn = connect(database_url)
                return conn
            except ImportError:
                print("❌ Psycopg 3 не установлен. Установите: pip install psycopg[binary]")
                raise
        else:
            # SQLite для локальной разработки
            return sqlite3.connect(cls.DB_PATH)
    
    @classmethod
    def get_placeholder(cls) -> str:
        """Получить placeholder для SQL запросов"""
        return '%s' if cls.is_postgresql() else '?'

# Экспортируем экземпляр для удобства
config = Config()
