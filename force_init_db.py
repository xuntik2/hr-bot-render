#!/usr/bin/env python3
"""
СКРИПТ ПРИНУДИТЕЛЬНОГО ЗАПОЛНЕНИЯ БАЗЫ ДАННЫХ ДЛЯ RENDER
Удаляет старые данные и вставляет 75 вопросов заново.
"""
import os
import sys
sys.path.insert(0, '.')

from config import config
from faq_data import get_faq_data
import logging

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

def force_init():
    print("🚀 ЗАПУСК ПРИНУДИТЕЛЬНОЙ ИНИЦИАЛИЗАЦИИ БАЗЫ...")
    
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # 1. Очистка таблицы (для PostgreSQL)
        logger.info("🗑️  Очистка таблицы 'faq'...")
        cursor.execute("TRUNCATE TABLE faq RESTART IDENTITY CASCADE;")
        
        # 2. Получение данных
        faq_list = get_faq_data()
        logger.info(f"📚 Загружено {len(faq_list)} вопросов.")
        
        # 3. Вставка всех 75 вопросов
        placeholder = config.get_placeholder()
        added = 0
        
        for faq in faq_list:
            sql = f"""
            INSERT INTO faq (question, answer, keywords, norm_keywords, norm_question, category)
            VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
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
        conn.close()
        
        logger.info(f"✅ УСПЕХ! В базу добавлено {added} вопросов.")
        return True
        
    except Exception as e:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        return False

if __name__ == "__main__":
    success = force_init()
    sys.exit(0 if success else 1)