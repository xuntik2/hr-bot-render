#!/usr/bin/env python3
"""
НАДЕЖНЫЙ СКРИПТ ИНИЦИАЛИЗАЦИИ БАЗЫ ДАННЫХ
Заполняет БД 75 вопросами даже если она уже существует
"""
import os
import sys
import logging
from config import config
from faq_data import get_faq_data

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def init_database():
    """Инициализация и заполнение базы данных 75 вопросами"""
    print("\n" + "=" * 60)
    print("🚀 ИНИЦИАЛИЗАЦИЯ БАЗЫ ДАННЫХ")
    print(f"🗄️  Тип БД: {'PostgreSQL' if config.is_postgresql() else 'SQLite'}")
    print("=" * 60)
    
    try:
        # Подключение к БД
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # Создание таблиц
        create_tables(cursor)
        
        # Получение данных
        faq_data = get_faq_data()
        logger.info(f"📚 Загружено {len(faq_data)} вопросов из faq_data.py")
        
        # Заполнение данных
        populate_data(cursor, conn, faq_data)
        
        # Создание индексов
        create_indexes(cursor)
        
        # Финальная проверка
        check_result(cursor)
        
        conn.close()
        
        print("\n" + "=" * 60)
        print("✅ БАЗА ДАННЫХ УСПЕШНО ИНИЦИАЛИЗИРОВАНА")
        print("=" * 60)
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Критическая ошибка: {e}")
        import traceback
        traceback.print_exc()
        return False

def create_tables(cursor):
    """Создание таблиц"""
    logger.info("🔧 Создание таблиц...")
    
    # Таблица FAQ
    if config.is_postgresql():
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS faq (
                id SERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                keywords TEXT,
                norm_keywords TEXT,
                norm_question TEXT UNIQUE,  -- Уникальный индекс для предотвращения дублей
                category TEXT,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    else:
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS faq (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                keywords TEXT,
                norm_keywords TEXT,
                norm_question TEXT UNIQUE,
                category TEXT,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
    
    # Другие таблицы
    tables = ['feedback', 'unanswered_queries', 'meme_subscriptions']
    for table in tables:
        if config.is_postgresql():
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {table} (
                    id SERIAL PRIMARY KEY,
                    user_id BIGINT,
                    comment TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
        else:
            cursor.execute(f'''
                CREATE TABLE IF NOT EXISTS {table} (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id INTEGER,
                    comment TEXT,
                    timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
            ''')
    
    logger.info("✅ Таблицы созданы/проверены")

def populate_data(cursor, conn, faq_data):
    """Заполнение таблицы faq данными"""
    logger.info("📝 Заполнение таблицы FAQ...")
    
    placeholder = config.get_placeholder()
    added = 0
    updated = 0
    errors = 0
    
    for faq in faq_data:
        try:
            if config.is_postgresql():
                # Для PostgreSQL используем UPSERT (INSERT ... ON CONFLICT)
                query = f'''
                    INSERT INTO faq (question, answer, keywords, norm_keywords, norm_question, category)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                    ON CONFLICT (norm_question) DO UPDATE SET
                        question = EXCLUDED.question,
                        answer = EXCLUDED.answer,
                        keywords = EXCLUDED.keywords,
                        norm_keywords = EXCLUDED.norm_keywords,
                        category = EXCLUDED.category
                '''
            else:
                # Для SQLite используем INSERT OR REPLACE
                query = f'''
                    INSERT OR REPLACE INTO faq 
                    (question, answer, keywords, norm_keywords, norm_question, category)
                    VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                '''
            
            cursor.execute(query, (
                faq['question'],
                faq['answer'],
                faq['keywords'],
                faq['norm_keywords'],
                faq['norm_question'],
                faq['category']
            ))
            
            if cursor.rowcount > 0:
                added += 1
            
        except Exception as e:
            errors += 1
            logger.error(f"Ошибка при добавлении вопроса '{faq['question'][:50]}...': {e}")
    
    conn.commit()
    
    logger.info(f"✅ Данные добавлены: {added} записей")
    if errors > 0:
        logger.warning(f"⚠️  Ошибок при добавлении: {errors}")

def create_indexes(cursor):
    """Создание индексов для ускорения поиска"""
    logger.info("⚡ Создание индексов...")
    
    indexes = [
        "CREATE INDEX IF NOT EXISTS idx_faq_category ON faq(category)",
        "CREATE INDEX IF NOT EXISTS idx_faq_norm_keywords ON faq(norm_keywords)",
        "CREATE INDEX IF NOT EXISTS idx_faq_usage_count ON faq(usage_count)",
    ]
    
    for index_sql in indexes:
        try:
            cursor.execute(index_sql)
        except Exception as e:
            logger.warning(f"Не удалось создать индекс: {e}")

def check_result(cursor):
    """Проверка результата"""
    cursor.execute("SELECT COUNT(*) FROM faq")
    count = cursor.fetchone()[0]
    
    cursor.execute("SELECT COUNT(DISTINCT category) FROM faq")
    categories = cursor.fetchone()[0]
    
    print(f"\n📊 РЕЗУЛЬТАТ:")
    print(f"   • Вопросов в базе: {count}")
    print(f"   • Категорий: {categories}")
    
    if count >= 75:
        print(f"✅ Все 75 вопросов успешно загружены!")
    elif count > 0:
        print(f"⚠️  Загружено {count} вопросов из 75")
    else:
        print(f"❌ База данных пустая!")

if __name__ == "__main__":
    success = init_database()
    sys.exit(0 if success else 1)