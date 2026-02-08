#!/usr/bin/env python3
"""
СОЗДАНИЕ БАЗЫ ДАННЫХ ДЛЯ RENDER (PostgreSQL/SQLite)
С тестовыми данными для быстрого старта
"""

import logging
import os
from config import config

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def normalize_keywords(keywords: str) -> str:
    """Нормализация ключевых слов для поиска"""
    if not keywords:
        return ""
    normalized = keywords.lower().strip()
    normalized = ' '.join(normalized.split())
    words = normalized.split()
    unique_words = list(dict.fromkeys(words))
    return ' '.join(unique_words)

def normalize_question(question: str) -> str:
    """Нормализация вопроса для поиска"""
    if not question:
        return ""
    question = question.lower().strip()
    for char in '?!.,;:()[]{}"\'«»':
        question = question.replace(char, '')
    question = ' '.join(question.split())
    return question

def create_database():
    """Создать и заполнить базу данных"""
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        logger.info("🔧 СОЗДАНИЕ СТРУКТУРЫ БАЗЫ ДАННЫХ...")
        
        # Создаем таблицу FAQ
        if config.is_postgresql():
            cursor.execute('''
            CREATE TABLE IF NOT EXISTS faq (
                id SERIAL PRIMARY KEY,
                question TEXT NOT NULL,
                answer TEXT NOT NULL,
                keywords TEXT,
                norm_keywords TEXT,
                norm_question TEXT,
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
                norm_question TEXT,
                category TEXT,
                usage_count INTEGER DEFAULT 0,
                created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
            ''')
        
        # Создаем таблицу для обратной связи
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS feedback (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            comment TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Создаем таблицу для неотвеченных запросов
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS unanswered_queries (
            id SERIAL PRIMARY KEY,
            user_id INTEGER,
            query_text TEXT,
            timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        # Создаем таблицу для подписок на мемы
        cursor.execute('''
        CREATE TABLE IF NOT EXISTS meme_subscriptions (
            id SERIAL PRIMARY KEY,
            user_id INTEGER UNIQUE,
            subscribed BOOLEAN DEFAULT true,
            subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
        )
        ''')
        
        logger.info("✅ ТАБЛИЦЫ СОЗДАНЫ")
        
        # Тестовые данные
        test_data = [
            (
                "Как оформить отпуск?",
                "Для оформления отпуска:\n1. Подайте заявление через корпоративный портал за 14 дней до начала отпуска.\n2. Согласуйте с руководителем.\n3. Дождитесь приказа и выплаты отпускных.",
                "отпуск, оформить отпуск, заявление на отпуск",
                "отпуск оформить отпуск заявление на отпуск",
                "как оформить отпуск",
                "Отпуск",
                0
            ),
            (
                "Когда выплачивается зарплата?",
                "Зарплата выплачивается 10-го числа каждого месяца, аванс - 25-го числа.",
                "зарплата, аванс, выплата, когда зарплата",
                "зарплата аванс выплата когда зарплата",
                "когда выплачивается зарплата",
                "Зарплата",
                0
            ),
            (
                "Как подключить корпоративную почту?",
                "Обратитесь в IT-отдел для получения данных для подключения почты. Вам понадобится ваш логин и временный пароль.",
                "почта, корпоративная почта, email, подключить почту",
                "почта корпоративная почта email подключить почту",
                "как подключить корпоративную почту",
                "IT",
                0
            )
        ]
        
        logger.info("📚 ДОБАВЛЕНИЕ ТЕСТОВЫХ ДАННЫХ...")
        
        # Проверяем, есть ли уже данные
        cursor.execute("SELECT COUNT(*) FROM faq")
        count = cursor.fetchone()[0]
        
        if count == 0:
            inserted_count = 0
            placeholder = config.get_placeholder()
            
            for question, answer, keywords, norm_keywords, norm_question, category, usage_count in test_data:
                query = f'''
                INSERT INTO faq (question, answer, keywords, norm_keywords, norm_question, category, usage_count)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                '''
                cursor.execute(query, (question, answer, keywords, norm_keywords, norm_question, category, usage_count))
                inserted_count += 1
            
            logger.info(f"✅ ДОБАВЛЕНО {inserted_count} ТЕСТОВЫХ ВОПРОСОВ")
        
        # Создаем индексы для ускорения поиска
        logger.info("⚡ СОЗДАНИЕ ИНДЕКСОВ...")
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_faq_category ON faq(category)",
            "CREATE INDEX IF NOT EXISTS idx_faq_norm_question ON faq(norm_question)",
            "CREATE INDEX IF NOT EXISTS idx_faq_norm_keywords ON faq(norm_keywords)"
        ]
        
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
            except Exception as e:
                logger.warning(f"Не удалось создать индекс: {e}")
        
        conn.commit()
        conn.close()
        
        logger.info("✅ БАЗА ДАННЫХ ГОТОВА!")
        
        # Показываем статистику
        print("\n" + "=" * 60)
        print("📊 СТАТИСТИКА БАЗЫ ДАННЫХ")
        print("=" * 60)
        
        conn = config.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT COUNT(*) FROM faq")
        total_faq = cursor.fetchone()[0]
        
        cursor.execute("SELECT DISTINCT category FROM faq")
        categories = cursor.fetchall()
        
        print(f"\n📂 Всего вопросов: {total_faq}")
        print(f"📁 Категорий: {len(categories)}")
        
        print("\n📝 Распределение по категориям:")
        cursor.execute('''
        SELECT category, COUNT(*) as count
        FROM faq
        GROUP BY category
        ORDER BY count DESC
        ''')
        
        for category, count in cursor.fetchall():
            print(f"  • {category}: {count} вопросов")
        
        conn.close()
        print("\n🎉 База данных готова к использованию!")
        
    except Exception as e:
        logger.error(f"❌ ОШИБКА ПРИ СОЗДАНИИ БАЗЫ: {e}", exc_info=True)
        raise

def check_database_connection():
    """Проверка подключения к базе данных"""
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        cursor.execute("SELECT 1")
        result = cursor.fetchone()
        conn.close()
        
        if result and result[0] == 1:
            logger.info("✅ Подключение к базе данных успешно")
            return True
        else:
            logger.error("❌ Ошибка подключения к базе данных")
            return False
    except Exception as e:
        logger.error(f"❌ Ошибка подключения: {e}")
        return False

def main():
    """Основная функция"""
    print("\n" + "=" * 60)
    print("🔧 СОЗДАНИЕ БАЗЫ ДАННЫХ ДЛЯ HR-БОТА")
    print(f"📅 Тип БД: {'PostgreSQL' if config.is_postgresql() else 'SQLite'}")
    print("=" * 60)
    
    # Проверяем подключение
    if not check_database_connection():
        print("❌ Не удалось подключиться к базе данных")
        return
    
    # Создаем базу
    create_database()

if __name__ == "__main__":
    main()
