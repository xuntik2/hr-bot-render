#!/usr/bin/env python3
"""
Проверка и исправление базы данных
"""
import os
import sys
sys.path.insert(0, '.')

from config import config
from faq_data import get_faq_data

def check_and_fix_database():
    """Проверяет и исправляет базу данных"""
    print("🔍 Проверка базы данных...")
    
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем количество вопросов
        cursor.execute("SELECT COUNT(*) FROM faq")
        count = cursor.fetchone()[0]
        print(f"📊 В базе {count} вопросов")
        
        if count < 75:
            print(f"⚠️ Не хватает {75 - count} вопросов")
            print("📥 Загружаем данные из faq_data.py...")
            
            faq_data = get_faq_data()
            print(f"📚 Получено {len(faq_data)} вопросов из faq_data.py")
            
            placeholder = config.get_placeholder()
            added = 0
            
            for faq in faq_data:
                # Проверяем, есть ли уже такой вопрос
                cursor.execute(
                    f"SELECT id FROM faq WHERE norm_question = {placeholder}",
                    (faq['norm_question'],)
                )
                if not cursor.fetchone():
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
                    added += 1
            
            conn.commit()
            print(f"✅ Добавлено {added} новых вопросов")
            print(f"📊 Теперь в базе {count + added} вопросов")
        else:
            print("✅ База данных содержит все 75 вопросов")
        
        conn.close()
        
    except Exception as e:
        print(f"❌ Ошибка: {e}")
        import traceback
        traceback.print_exc()

if __name__ == "__main__":
    check_and_fix_database()
