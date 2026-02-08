#!/usr/bin/env python3
"""
СОЗДАНИЕ БАЗЫ ДАННЫХ С 75 ВОПРОСАМИ
С улучшенной диагностикой и проверками
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

def check_database_connection():
    """Проверка подключения к базе данных"""
    try:
        logger.info("🔍 Проверка подключения к базе данных...")
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
            logger.info("✅ Таблица faq создана (PostgreSQL)")
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
            logger.info("✅ Таблица faq создана (SQLite)")
        
        # Создаем таблицу для обратной связи
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS feedback (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                comment TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("✅ Таблица feedback создана")
        
        # Создаем таблицу для неотвеченных запросов
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS unanswered_queries (
                id SERIAL PRIMARY KEY,
                user_id INTEGER,
                query_text TEXT,
                timestamp TIMESTAMP DEFAULT CURRENT_TIMESTAMP
            )
        ''')
        logger.info("✅ Таблица unanswered_queries создана")
        
        # Создаем таблицу для подписок на мемы
        cursor.execute('''
            CREATE TABLE IF NOT EXISTS meme_subscriptions (
                id SERIAL PRIMARY KEY,
                user_id BIGINT UNIQUE NOT NULL,
                subscribed BOOLEAN DEFAULT TRUE,
                subscribed_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
                last_meme_sent TIMESTAMP
            )
        ''')
        logger.info("✅ Таблица meme_subscriptions создана")
        
        logger.info("✅ Все таблицы созданы")
        
        # Получаем данные из единого источника
        faq_data = get_faq_data()
        logger.info(f"📚 Получено {len(faq_data)} вопросов из faq_data.py")
        
        # Проверяем, есть ли уже данные
        cursor.execute("SELECT COUNT(*) FROM faq")
        count = cursor.fetchone()[0]
        logger.info(f"ℹ️ В базе уже содержится {count} вопросов")
        
        if count == 0:
            logger.info(f"📝 Добавление {len(faq_data)} вопросов в базу...")
            
            inserted_count = 0
            placeholder = config.get_placeholder()
            
            for faq in faq_data:
                # Проверяем, есть ли уже такой вопрос (по нормализованному вопросу)
                cursor.execute(
                    f"SELECT id FROM faq WHERE norm_question = {placeholder}",
                    (faq['norm_question'],)
                )
                if cursor.fetchone():
                    logger.debug(f"Пропуск: вопрос уже существует - {faq['question'][:50]}...")
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
                
                if inserted_count % 10 == 0:
                    logger.info(f"Добавлено {inserted_count} вопросов...")
            
            logger.info(f"✅ Добавлено {inserted_count} вопросов")
        else:
            logger.info(f"ℹ️ База уже содержит {count} вопросов. Пропускаем добавление.")
            
            # Проверяем, сколько вопросов должно быть
            if count < len(faq_data):
                logger.warning(f"⚠️ В базе только {count} вопросов из {len(faq_data)} ожидаемых")
                logger.info("🔍 Проверяем, какие вопросы отсутствуют...")
                
                missing_count = 0
                for faq in faq_data:
                    cursor.execute(
                        f"SELECT id FROM faq WHERE norm_question = {placeholder}",
                        (faq['norm_question'],)
                    )
                    if not cursor.fetchone():
                        missing_count += 1
                        logger.info(f"Отсутствует: {faq['question'][:60]}...")
                
                if missing_count > 0:
                    logger.warning(f"⚠️ Найдено {missing_count} отсутствующих вопросов")
                    logger.info("Запустите add_faq.py для добавления отсутствующих вопросов")
        
        # Создаем индексы для ускорения поиска
        logger.info("⚡ Создание индексов...")
        
        indexes = [
            "CREATE INDEX IF NOT EXISTS idx_faq_category ON faq(category)",
            "CREATE INDEX IF NOT EXISTS idx_faq_norm_question ON faq(norm_question)",
            "CREATE INDEX IF NOT EXISTS idx_faq_norm_keywords ON faq(norm_keywords)",
            "CREATE INDEX IF NOT EXISTS idx_faq_usage_count ON faq(usage_count)",
            "CREATE INDEX IF NOT EXISTS idx_feedback_user_id ON feedback(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_unanswered_queries_user_id ON unanswered_queries(user_id)",
            "CREATE INDEX IF NOT EXISTS idx_meme_subscriptions_user_id ON meme_subscriptions(user_id)"
        ]
        
        created_indexes = 0
        for index_sql in indexes:
            try:
                cursor.execute(index_sql)
                created_indexes += 1
                logger.debug(f"Создан индекс: {index_sql[:50]}...")
            except Exception as e:
                logger.warning(f"Не удалось создать индекс {index_sql[:30]}...: {e}")
        
        logger.info(f"✅ Создано {created_indexes} индексов")
        
        conn.commit()
        conn.close()
        
        logger.info("✅ База данных успешно создана и настроена!")
        
        # ДИАГНОСТИКА: Проверяем итоговый результат
        final_check()
        
    except Exception as e:
        logger.error(f"❌ Ошибка при создании базы: {e}", exc_info=True)
        raise

def final_check():
    """Финальная проверка состояния базы данных"""
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем количество вопросов
        cursor.execute("SELECT COUNT(*) FROM faq")
        final_count = cursor.fetchone()[0]
        
        # Проверяем категории
        cursor.execute("SELECT COUNT(DISTINCT category) FROM faq")
        category_count = cursor.fetchone()[0]
        
        # Проверяем таблицы
        cursor.execute("""
            SELECT table_name 
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """ if config.is_postgresql() else """
            SELECT name 
            FROM sqlite_master 
            WHERE type='table'
        """)
        tables = cursor.fetchall()
        
        conn.close()
        
        # Выводим итоговую диагностику
        print("\n" + "=" * 60)
        print("📊 ДИАГНОСТИКА БАЗЫ ДАННЫХ")
        print("=" * 60)
        
        print(f"\n✅ Подключение к БД: Успешно")
        print(f"   Тип БД: {'PostgreSQL' if config.is_postgresql() else 'SQLite'}")
        
        if config.is_postgresql():
            db_url = os.getenv('DATABASE_URL', '')
            if db_url:
                # Маскируем пароль в URL для безопасности
                masked_url = db_url
                if '@' in db_url:
                    parts = db_url.split('@')
                    if ':' in parts[0]:
                        user_pass = parts[0].split(':')
                        if len(user_pass) > 2:
                            user_pass[2] = '***'
                        parts[0] = ':'.join(user_pass)
                    masked_url = '@'.join(parts)
                print(f"   URL: {masked_url[:80]}...")
        
        print(f"\n📊 Содержимое базы:")
        print(f"   • Вопросов в FAQ: {final_count}")
        print(f"   • Уникальных категорий: {category_count}")
        print(f"   • Созданных таблиц: {len(tables)}")
        
        for table in tables:
            table_name = table[0]
            cursor = conn.cursor()
            try:
                cursor.execute(f"SELECT COUNT(*) FROM {table_name}")
                row_count = cursor.fetchone()[0]
                print(f"     - {table_name}: {row_count} записей")
            except:
                print(f"     - {table_name}: (ошибка подсчета)")
        
        if final_count == 0:
            print(f"\n❌ КРИТИЧЕСКАЯ ОШИБКА: База данных пустая!")
            print(f"   Возможные причины:")
            print(f"   1. Нет подключения к PostgreSQL")
            print(f"   2. Нет прав на создание таблиц")
            print(f"   3. Ошибка в DATABASE_URL")
            print(f"   4. Проблемы с сетью к удаленной БД")
            print(f"\n   Действия:")
            print(f"   1. Проверьте переменную DATABASE_URL в Render")
            print(f"   2. Убедитесь, что PostgreSQL сервер доступен")
            print(f"   3. Запустите add_faq.py вручную")
            return False
        elif final_count < 75:
            print(f"\n⚠️ ПРЕДУПРЕЖДЕНИЕ: Не все вопросы загружены")
            print(f"   Загружено: {final_count} из 75")
            print(f"   Запустите: python add_faq.py")
        else:
            print(f"\n✅ УСПЕХ: Все 75 вопросов загружены в базу!")
            print(f"   База данных готова к работе.")
        
        print("\n" + "=" * 60)
        return final_count > 0
        
    except Exception as e:
        print(f"\n❌ Ошибка при диагностике: {e}")
        return False

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
        
        if categories:
            print("\n📝 Распределение по категориям:")
            cursor.execute('''
                SELECT category, COUNT(*) as count
                FROM faq
                GROUP BY category
                ORDER BY count DESC
            ''')
            
            for category, count in cursor.fetchall():
                percentage = (count / total_faq * 100) if total_faq > 0 else 0
                print(f"  • {category}: {count} вопросов ({percentage:.1f}%)")
        
        # Показываем самые популярные вопросы
        print("\n🔥 Самые популярные вопросы:")
        cursor.execute('''
            SELECT question, usage_count 
            FROM faq 
            WHERE usage_count > 0 
            ORDER BY usage_count DESC 
            LIMIT 5
        ''')
        
        popular = cursor.fetchall()
        if popular:
            for question, usage in popular:
                print(f"  • {question[:50]}... - {usage} использований")
        else:
            print("  • Пока нет статистики использования")
        
        conn.close()
        
    except Exception as e:
        print(f"Ошибка при получении статистики: {e}")

def main():
    """Основная функция"""
    print("\n" + "=" * 60)
    print("🔧 СОЗДАНИЕ БАЗЫ ДАННЫХ ДЛЯ HR-БОТА МЕЧЕЛ")
    print(f"🗄️  Тип БД: {'PostgreSQL' if config.is_postgresql() else 'SQLite'}")
    
    if config.is_postgresql():
        db_url = os.getenv('DATABASE_URL', '')
        if db_url:
            # Безопасный вывод URL (маскируем пароль)
            if '@' in db_url:
                parts = db_url.split('@')
                if ':' in parts[0]:
                    user_pass = parts[0].split(':')
                    if len(user_pass) > 2:
                        user_pass[2] = '***'
                    parts[0] = ':'.join(user_pass)
                safe_url = '@'.join(parts)
                print(f"📡 Подключение: {safe_url[:80]}...")
        else:
            print("⚠️ ВНИМАНИЕ: DATABASE_URL не установлен!")
            print("   Используется SQLite для локальной разработки")
    
    print("=" * 60)
    
    # Проверка подключения
    if not check_database_connection():
        print("❌ Не удалось подключиться к базе данных")
        print("   Проверьте настройки подключения и перезапустите скрипт")
        return False
    
    # Создание базы данных
    try:
        create_database()
        
        # Показываем статистику
        show_statistics()
        
        # Финальная диагностика
        success = final_check()
        
        if success:
            print("\n🎉 База данных готова к использованию!")
            print("🤖 Теперь можно запустить бота командой: python bot.py")
            return True
        else:
            print("\n❌ Есть проблемы с базой данных")
            print("   Проверьте логи выше и исправьте ошибки")
            return False
        
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")
        print("   Проверьте настройки и повторите попытку")
        return False

if __name__ == "__main__":
    # Добавляем корневую директорию в путь Python
    sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
    
    # Запускаем главную функцию
    success = main()
    
    # Возвращаем соответствующий код выхода
    sys.exit(0 if success else 1)
