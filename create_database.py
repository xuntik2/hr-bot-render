#!/usr/bin/env python3
"""
СОЗДАНИЕ БАЗЫ ДАННЫХ С 75 ВОПРОСАМИ
"""
import logging
from config import config
from faq_data import get_faq_data

logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(levelname)s - %(message)s'
)
logger = logging.getLogger(__name__)

def create_database():
    """Создать базу данных и добавить 75 вопросов"""
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        logger.info("🔧 Создание структуры базы данных...")
        
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
        
        logger.info("✅ Таблицы созданы")
        
        # Получаем данные из единого источника
        faq_data = get_faq_data()
        
        # Проверяем, есть ли уже данные
        cursor.execute("SELECT COUNT(*) FROM faq")
        count = cursor.fetchone()[0]
        
        if count == 0:
            logger.info(f"📚 Добавление {len(faq_data)} вопросов в базу...")
            
            inserted_count = 0
            placeholder = config.get_placeholder()
            
            for faq in faq_data:
                # Проверяем, есть ли уже такой вопрос
                cursor.execute(
                    f"SELECT id FROM faq WHERE norm_question = {placeholder}",
                    (faq['norm_question'],)
                )
                if cursor.fetchone():
                    continue
                
                # Добавляем вопрос
                query = f'''
                    INSERT INTO faq (question, answer, keywords, norm_keywords, norm_question, category)
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
                inserted_count += 1
            
            logger.info(f"✅ Добавлено {inserted_count} вопросов")
        else:
            logger.info(f"ℹ️ База уже содержит {count} вопросов")
        
        # Создаем индексы для ускорения поиска
        logger.info("⚡ Создание индексов...")
        
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
        
        logger.info("✅ База данных готова!")
        
        # Показываем статистику
        show_statistics()
        
    except Exception as e:
        logger.error(f"❌ Ошибка при создании базы: {e}", exc_info=True)
        raise

def show_statistics():
    """Показать статистику базы данных"""
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        cursor.execute("SELECT COUNT(*) FROM faq")
        total_faq = cursor.fetchone()[0]
        
        cursor.execute("SELECT DISTINCT category FROM faq ORDER BY category")
        categories = cursor.fetchall()
        
        print("\n" + "=" * 60)
        print("📊 СТАТИСТИКА БАЗЫ ДАННЫХ")
        print("=" * 60)
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
        logger.error(f"Ошибка при получении статистики: {e}")

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
    print("🔧 СОЗДАНИЕ БАЗЫ ДАННЫХ ДЛЯ HR-БОТА МЕЧЕЛ")
    print(f"🗄️  Тип БД: {'PostgreSQL' if config.is_postgresql() else 'SQLite'}")
    print("=" * 60)
    
    if not check_database_connection():
        print("❌ Не удалось подключиться к базе данных")
        return
    
    create_database()

if __name__ == "__main__":
    main()
