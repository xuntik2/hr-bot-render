#!/usr/bin/env python3
"""
СКРИПТ ДЛЯ ПРОВЕРКИ ВСЕЙ СИСТЕМЫ ПЕРЕД ЗАПУСКОМ
"""
import os
import sys
import logging
from config import config

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

def check_system():
    """Проверка всей системы"""
    print("\n" + "=" * 60)
    print("🔍 ПРОВЕРКА СИСТЕМЫ HR-БОТА")
    print("=" * 60)
    
    checks = []
    
    # Проверка переменных окружения
    checks.append(check_env_vars())
    
    # Проверка подключения к БД
    checks.append(check_database())
    
    # Проверка наличия файлов
    checks.append(check_files())
    
    # Итог
    success = all(checks)
    
    print("\n" + "=" * 60)
    if success:
        print("✅ ВСЕ ПРОВЕРКИ ПРОЙДЕНЫ УСПЕШНО")
    else:
        print("❌ НАЙДЕНЫ ПРОБЛЕМЫ")
    print("=" * 60)
    
    return success

def check_env_vars():
    """Проверка переменных окружения"""
    print("\n🔑 Проверка переменных окружения...")
    
    required = ['BOT_TOKEN']
    optional = ['DATABASE_URL', 'ADMIN_IDS']
    
    all_ok = True
    
    for var in required:
        if os.getenv(var):
            print(f"  ✅ {var}: присутствует")
        else:
            print(f"  ❌ {var}: ОТСУТСТВУЕТ!")
            all_ok = False
    
    for var in optional:
        if os.getenv(var):
            print(f"  ✅ {var}: присутствует")
        else:
            print(f"  ⚠️  {var}: отсутствует (не критично)")
    
    return all_ok

def check_database():
    """Проверка подключения к БД"""
    print("\n🗄️  Проверка базы данных...")
    
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # Проверяем наличие таблицы faq
        if config.is_postgresql():
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_name = 'faq'
                );
            """)
        else:
            cursor.execute("""
                SELECT name FROM sqlite_master 
                WHERE type='table' AND name='faq';
            """)
        
        table_exists = cursor.fetchone()[0]
        
        if table_exists:
            cursor.execute("SELECT COUNT(*) FROM faq")
            count = cursor.fetchone()[0]
            print(f"  ✅ Таблица 'faq' существует")
            print(f"  📊 Записей в таблице: {count}")
            
            if count >= 75:
                print(f"  ✅ Все 75 вопросов загружены")
            elif count > 0:
                print(f"  ⚠️  Загружено только {count} из 75 вопросов")
            else:
                print(f"  ❌ Таблица пустая!")
                
            conn.close()
            return count > 0
        else:
            print("  ❌ Таблица 'faq' не существует")
            conn.close()
            return False
            
    except Exception as e:
        print(f"  ❌ Ошибка подключения к БД: {e}")
        return False

def check_files():
    """Проверка наличия необходимых файлов"""
    print("\n📁 Проверка файлов...")
    
    required_files = [
        'bot.py',
        'config.py',
        'faq_data.py',
        'handlers.py',
        'search_engine.py',
        'requirements.txt',
        'runtime.txt'
    ]
    
    all_exist = True
    
    for file in required_files:
        if os.path.exists(file):
            print(f"  ✅ {file}: присутствует")
        else:
            print(f"  ❌ {file}: ОТСУТСТВУЕТ!")
            all_exist = False
    
    return all_exist

if __name__ == "__main__":
    success = check_system()
    sys.exit(0 if success else 1)
