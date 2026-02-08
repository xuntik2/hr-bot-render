#!/usr/bin/env python3
"""
ПРОСТОЙ СКРИПТ ДЛЯ ЗАПОЛНЕНИЯ БАЗЫ ДАННЫХ
Гарантированно работает с PostgreSQL на Render
"""
import os
import sys
sys.path.insert(0, '.')

from config import config
from faq_data import get_faq_data
import logging

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def simple_fill():
    print("=" * 60)
    print("🚀 ПРОСТОЕ ЗАПОЛНЕНИЕ БАЗЫ ДАННЫХ")
    print("=" * 60)
    
    try:
        # 1. Подключаемся к базе
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # 2. Проверяем, есть ли таблица
        cursor.execute("SELECT EXISTS (SELECT FROM information_schema.tables WHERE table_name = 'faq')")
        table_exists = cursor.fetchone()[0]
        
        if not table_exists:
            print("❌ Таблицы 'faq' не существует!")
            print("   Создайте таблицу через init_database.py или вручную")
            return False
        
        # 3. Считаем текущие записи
        cursor.execute("SELECT COUNT(*) FROM faq")
        current_count = cursor.fetchone()[0]
        print(f"📊 Текущее количество записей: {current_count}")
        
        # 4. Получаем данные
        faq_list = get_faq_data()
        print(f"📚 Загружено {len(faq_list)} вопросов из faq_data.py")
        
        # 5. Очищаем таблицу
        print("🧹 Очистка таблицы...")
        cursor.execute("DELETE FROM faq")
        
        # 6. Вставляем все вопросы
        print("📝 Вставка вопросов...")
        added = 0
        for faq in faq_list:
            sql = """
            INSERT INTO faq (question, answer, keywords, norm_keywords, norm_question, category)
            VALUES (%s, %s, %s, %s, %s, %s)
            """
            cursor.execute(sql, (
                faq['question'],
                faq['answer'],
                faq['keywords'],
                faq['norm_keywords'],
                faq['norm_question'],
                faq['category']
            ))
            added += 1
        
        conn.commit()
        
        # 7. Проверяем результат
        cursor.execute("SELECT COUNT(*) FROM faq")
        new_count = cursor.fetchone()[0]
        conn.close()
        
        print("=" * 60)
        print(f"✅ УСПЕХ! Добавлено {added} записей")
        print(f"📊 Всего в базе: {new_count} записей")
        print("=" * 60)
        
        return new_count >= 75
        
    except Exception as e:
        print(f"❌ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = simple_fill()
    sys.exit(0 if success else 1)