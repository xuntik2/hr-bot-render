#!/usr/bin/env python3
"""
СКРИПТ ПРИНУДИТЕЛЬНОГО ЗАПОЛНЕНИЯ БАЗЫ ДАННЫХ ДЛЯ RENDER
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
    print("=" * 60)
    print("🚀 ЗАПУСК ПРИНУДИТЕЛЬНОЙ ИНИЦИАЛИЗАЦИИ БАЗЫ...")
    print("=" * 60)
    
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # 1. Проверяем существование таблицы
        logger.info("🔍 Проверка существования таблицы 'faq'...")
        if config.is_postgresql():
            cursor.execute("SELECT to_regclass('public.faq')")
            table_exists = cursor.fetchone()[0]
        else:
            cursor.execute("SELECT name FROM sqlite_master WHERE type='table' AND name='faq'")
            table_exists = cursor.fetchone()
        
        if not table_exists:
            logger.error("❌ Таблица 'faq' не существует. Создайте её сначала.")
            sys.exit(1)
        
        # 2. Очистка таблицы (для PostgreSQL)
        logger.info("🗑️  Очистка таблицы 'faq'...")
        cursor.execute("TRUNCATE TABLE faq RESTART IDENTITY CASCADE;")
        
        # 3. Получение данных
        faq_list = get_faq_data()
        logger.info(f"📚 Загружено {len(faq_list)} вопросов из faq_data.py.")
        
        # 4. Вставка всех 75 вопросов
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
        
        # 5. Проверка вставленных данных
        cursor.execute("SELECT COUNT(*) FROM faq")
        count = cursor.fetchone()[0]
        conn.close()
        
        if count == added:
            logger.info(f"✅ УСПЕХ! В базу добавлено {added} вопросов.")
            print("=" * 60)
            print(f"🎉 БАЗА ДАННЫХ ЗАПОЛНЕНА: {count} ЗАПИСЕЙ")
            print("=" * 60)
            return True
        else:
            logger.error(f"❌ ОШИБКА: Добавлено {added} записей, но в таблице {count} записей.")
            return False
        
    except Exception as e:
        logger.error(f"❌ КРИТИЧЕСКАЯ ОШИБКА: {e}")
        import traceback
        traceback.print_exc()
        # Принудительно завершаем с ошибкой
        sys.exit(1)

if __name__ == "__main__":
    success = force_init()
    sys.exit(0 if success else 1)
