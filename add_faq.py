#!/usr/bin/env python3
"""
СКРИПТ ДЛЯ ДОБАВЛЕНИЯ ВОПРОСОВ В БАЗУ ДАННЫХ
Использует единый источник faq_data.py
"""
import sys
sys.path.insert(0, '.')

from config import config
from faq_data import get_faq_data
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def add_questions():
    """Добавить вопросы в базу данных"""
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        faq_data = get_faq_data()
        
        logger.info(f"Проверка {len(faq_data)} вопросов...")
        
        added_count = 0
        skipped_count = 0
        placeholder = config.get_placeholder()
        
        for faq in faq_data:
            cursor.execute(
                f"SELECT id FROM faq WHERE norm_question = {placeholder}",
                (faq['norm_question'],)
            )
            
            if cursor.fetchone():
                skipped_count += 1
                continue
            
            query = f'''
                INSERT INTO faq (question, answer, keywords, norm_keywords, norm_question, category, usage_count)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, 0)
            '''
            cursor.execute(query, (
                faq['question'],
                faq['answer'],
                faq['keywords'],
                faq['norm_keywords'],
                faq['norm_question'],
                faq['category']
            ))
            
            added_count += 1
            logger.info(f"✅ Добавлен: {faq['question'][:50]}...")
        
        conn.commit()
        conn.close()
        
        logger.info(f"🎉 Результат:")
        logger.info(f"   • Добавлено: {added_count} вопросов")
        logger.info(f"   • Пропущено (уже есть): {skipped_count}")
        logger.info(f"   • Всего в базе: {added_count + skipped_count}")
        
        return True
        
    except Exception as e:
        logger.error(f"❌ Ошибка: {e}", exc_info=True)
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
        print("📊 ТЕКУЩАЯ СТАТИСТИКА БАЗЫ ДАННЫХ")
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
        
    except Exception as e:
        logger.error(f"Ошибка при получении статистики: {e}")

if __name__ == "__main__":
    print("\n" + "=" * 60)
    print("🔧 ДОБАВЛЕНИЕ ВОПРОСОВ В БАЗУ ДАННЫХ")
    print(f"🗄️  Тип БД: {'PostgreSQL' if config.is_postgresql() else 'SQLite'}")
    print("=" * 60)
    
    show_statistics()
    
    add_questions()
    
    show_statistics()
