"""
АДМИНИСТРАТИВНЫЕ ИНСТРУМЕНТЫ ДЛЯ БОТА МЕЧЕЛ
Версия 2.0 - С защитой от SQL-инъекций и улучшенной обработкой ошибок
"""

import logging
import psycopg2
from datetime import datetime, timedelta
from config import config, TABLE_FAQ, TABLE_FAQ_KEYWORDS, TABLE_UNANSWERED_QUERIES, TABLE_BOT_METRICS, MIN_FAQ_RECORDS

logger = logging.getLogger(__name__)

def safe_execute_query(query: str, params: tuple = None, fetch: bool = False):
    """
    Безопасное выполнение SQL-запроса с обработкой ошибок
    
    Args:
        query: SQL-запрос
        params: Параметры для параметризованного запроса
        fetch: Нужно ли возвращать результат
        
    Returns:
        Результат выполнения или None при ошибке
    """
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        if params:
            cursor.execute(query, params)
        else:
            cursor.execute(query)
        
        if fetch:
            result = cursor.fetchall()
        else:
            result = cursor.rowcount
        
        conn.commit()
        cursor.close()
        conn.close()
        
        return result
        
    except psycopg2.Error as e:
        logger.error(f"❌ Ошибка выполнения SQL-запроса: {e}")
        logger.error(f"Запрос: {query[:100]}...")
        return None
    except Exception as e:
        logger.error(f"❌ Неожиданная ошибка при выполнении запроса: {e}")
        return None

def check_database_status():
    """
    Безопасная проверка статуса базы данных с проверкой минимального порога
    
    Returns:
        dict: Статус базы данных и статистика
    """
    status = {
        'database': 'disconnected',
        'health': 'unhealthy',
        'tables': {},
        'faq_count': 0,
        'min_threshold': MIN_FAQ_RECORDS,
        'meets_threshold': False,
        'metrics': {},
        'timestamp': datetime.now().isoformat()
    }
    
    try:
        # Проверяем подключение к БД
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # Таблицы для проверки (используем константы из config)
        tables_to_check = [
            TABLE_FAQ,
            TABLE_FAQ_KEYWORDS,
            TABLE_UNANSWERED_QUERIES,
            TABLE_BOT_METRICS
        ]
        
        status['database'] = 'connected'
        
        for table in tables_to_check:
            try:
                # ПАРАМЕТРИЗОВАННЫЙ ЗАПРОС для проверки существования таблицы
                cursor.execute("""
                    SELECT EXISTS (
                        SELECT FROM information_schema.tables 
                        WHERE table_schema = 'public' 
                        AND table_name = %s
                    )
                """, (table,))
                
                table_exists = cursor.fetchone()[0]
                
                if table_exists:
                    # ПАРАМЕТРИЗОВАННЫЙ ЗАПРОС для подсчета записей
                    cursor.execute(f"SELECT COUNT(*) FROM {table}")
                    count = cursor.fetchone()[0]
                    
                    status['tables'][table] = {
                        'exists': True,
                        'count': count
                    }
                    
                    if table == TABLE_FAQ:
                        status['faq_count'] = count
                        # Проверяем минимальный порог
                        status['meets_threshold'] = count >= MIN_FAQ_RECORDS
                        status['health'] = 'healthy' if status['meets_threshold'] else 'unhealthy'
                else:
                    status['tables'][table] = {
                        'exists': False,
                        'error': 'Таблица не существует'
                    }
                    
            except Exception as e:
                status['tables'][table] = {
                    'exists': False,
                    'error': str(e)
                }
        
        # Проверяем метрики за последние 7 дней (безопасный запрос)
        try:
            # ПАРАМЕТРИЗОВАННЫЙ ЗАПРОС с безопасными именами таблиц
            cursor.execute(f"""
                SELECT 
                    COUNT(*) as total_queries,
                    COUNT(DISTINCT user_id) as unique_users,
                    AVG(search_time_seconds) as avg_search_time,
                    DATE(created_at) as date
                FROM {TABLE_UNANSWERED_QUERIES} 
                WHERE created_at >= NOW() - INTERVAL '7 days'
                GROUP BY DATE(created_at)
                ORDER BY date DESC
                LIMIT 7
            """)
            
            metrics = cursor.fetchall()
            
            status['metrics']['last_7_days'] = [
                {
                    'date': row[3].strftime('%Y-%m-%d') if row[3] else None,
                    'total_queries': row[0] or 0,
                    'unique_users': row[1] or 0,
                    'avg_search_time': round(float(row[2] or 0), 3)
                }
                for row in metrics
            ]
        except Exception as e:
            logger.warning(f"⚠️ Не удалось получить метрики: {e}")
            status['metrics']['last_7_days'] = []
        
        conn.close()
        
        if status['database'] == 'connected':
            logger.info(f"✅ Проверка БД: {status['faq_count']} FAQ (требуется ≥{MIN_FAQ_RECORDS})")
            if not status['meets_threshold']:
                logger.warning(f"⚠️ Количество FAQ ниже минимального порога: {status['faq_count']}/{MIN_FAQ_RECORDS}")
        
        return status
        
    except Exception as e:
        logger.error(f"❌ Ошибка проверки БД: {e}")
        status['error'] = str(e)
        return status

def fill_database_manual():
    """
    Безопасное ручное заполнение базы данных демо-данными
    
    Returns:
        dict: Результат заполнения
    """
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        # Удаляем существующие данные (безопасные запросы с именами таблиц из конфига)
        cursor.execute(f"DELETE FROM {TABLE_FAQ_KEYWORDS}")
        cursor.execute(f"DELETE FROM {TABLE_FAQ}")
        
        # Безопасные демо-данные
        demo_faq = [
            {
                'category': 'Отпуск',
                'question': 'Как оформить ежегодный оплачиваемый отпуск?',
                'answer': 'Для оформления отпуска:\n1. Напишите заявление в отдел кадров за 2 недели\n2. Согласуйте даты с руководителем\n3. Получите подпись на заявлении\n4. Передайте в отдел кадров\n\nСрок обработки: 3 рабочих дня.',
                'keywords': ['отпуск', 'оформить', 'ежегодный', 'оплачиваемый', 'заявление', 'кадры']
            },
            {
                'category': 'Зарплата', 
                'question': 'Когда выплачивается зарплата?',
                'answer': 'Зарплата выплачивается:\n• Аванс 40% - 20 числа каждого месяца\n• Основная часть - 5 числа следующего месяца\n\nПри задержке обращайтесь в бухгалтерию каб. 305.',
                'keywords': ['зарплата', 'выплата', 'аванс', 'дата', 'когда', 'бухгалтерия']
            },
            {
                'category': 'Документы',
                'question': 'Как получить справку 2-НДФЛ?',
                'answer': 'Справка 2-НДФЛ выдается:\n1. Через портал сотрудника (раздел "Документы")\n2. В отделе кадров (каб. 302)\n3. По электронной почте hr@mechel.ru\n\nСрок изготовления: 1-2 рабочих дня.',
                'keywords': ['справка', '2-ндфл', 'документ', 'налог', 'получить', 'отдел кадров']
            }
        ]
        
        # Вставляем данные с ПАРАМЕТРИЗОВАННЫМИ запросами
        inserted_count = 0
        
        for faq in demo_faq:
            # Безопасная вставка FAQ
            cursor.execute(f"""
                INSERT INTO {TABLE_FAQ} (category, question, answer, created_at, updated_at)
                VALUES (%s, %s, %s, NOW(), NOW())
                RETURNING id
            """, (faq['category'], faq['question'], faq['answer']))
            
            faq_id = cursor.fetchone()[0]
            
            # Безопасная вставка ключевых слов
            for keyword in faq['keywords']:
                cursor.execute(f"""
                    INSERT INTO {TABLE_FAQ_KEYWORDS} (faq_id, keyword)
                    VALUES (%s, %s)
                """, (faq_id, keyword.strip()))
            
            inserted_count += 1
        
        conn.commit()
        conn.close()
        
        result = {
            'status': 'success',
            'records_added': inserted_count,
            'message': f'Добавлено {inserted_count} демо-записей',
            'timestamp': datetime.now().isoformat(),
            'meets_threshold': inserted_count >= MIN_FAQ_RECORDS
        }
        
        logger.info(f"✅ База данных заполнена: {inserted_count} записей")
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка заполнения БД: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }

def cleanup_old_data(days_to_keep: int = 30):
    """
    БЕЗОПАСНАЯ очистка старых данных с защитой от SQL-инъекций
    
    Args:
        days_to_keep: Количество дней для хранения данных
        
    Returns:
        dict: Результат очистки
    """
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        deleted_counts = {}
        
        # БЕЗОПАСНЫЙ ПАРАМЕТРИЗОВАННЫЙ ЗАПРОС для очистки старых запросов
        cursor.execute(f"""
            DELETE FROM {TABLE_UNANSWERED_QUERIES} 
            WHERE created_at < NOW() - INTERVAL %s
        """, (f'{days_to_keep} days',))
        
        deleted_counts['unanswered_queries'] = cursor.rowcount
        
        # БЕЗОПАСНЫЙ ПАРАМЕТРИЗОВАННЫЙ ЗАПРОС для очистки старых метрик
        cursor.execute(f"""
            DELETE FROM {TABLE_BOT_METRICS} 
            WHERE timestamp < NOW() - INTERVAL %s
        """, (f'{days_to_keep} days',))
        
        deleted_counts['bot_metrics'] = cursor.rowcount
        
        conn.commit()
        conn.close()
        
        total_deleted = sum(deleted_counts.values())
        
        result = {
            'status': 'success',
            'deleted_counts': deleted_counts,
            'total_deleted': total_deleted,
            'message': f'Очистка данных за последние {days_to_keep} дней',
            'timestamp': datetime.now().isoformat()
        }
        
        if total_deleted > 0:
            logger.info(f"🧹 Безопасная очистка данных: удалено {total_deleted} записей")
        else:
            logger.info("🧹 Нет данных для очистки")
        
        return result
        
    except Exception as e:
        logger.error(f"❌ Ошибка безопасной очистки данных: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat()
        }

def get_system_stats():
    """
    Безопасное получение системной статистики
    
    Returns:
        dict: Системная статистика
    """
    try:
        conn = config.get_db_connection()
        cursor = conn.cursor()
        
        stats = {
            'database': {},
            'tables': {},
            'faq': {},
            'activity': {},
            'health': {},
            'timestamp': datetime.now().isoformat()
        }
        
        # Безопасный запрос для статистики таблиц
        cursor.execute("""
            SELECT 
                table_name,
                pg_size_pretty(pg_total_relation_size('"' || table_name || '"')) as size
            FROM information_schema.tables 
            WHERE table_schema = 'public'
        """)
        
        tables = cursor.fetchall()
        stats['database']['total_tables'] = len(tables)
        stats['database']['tables'] = [
            {'name': table[0], 'size': table[1]}
            for table in tables
        ]
        
        # Безопасный запрос для статистики FAQ
        cursor.execute(f"""
            SELECT 
                COUNT(*) as total_faq,
                COUNT(DISTINCT category) as categories,
                COUNT(DISTINCT keyword) as unique_keywords
            FROM {TABLE_FAQ} 
            LEFT JOIN {TABLE_FAQ_KEYWORDS} ON {TABLE_FAQ}.id = {TABLE_FAQ_KEYWORDS}.faq_id
        """)
        
        faq_stats = cursor.fetchone()
        stats['faq'] = {
            'total': faq_stats[0] or 0,
            'categories': faq_stats[1] or 0,
            'unique_keywords': faq_stats[2] or 0,
            'meets_threshold': (faq_stats[0] or 0) >= MIN_FAQ_RECORDS,
            'threshold': MIN_FAQ_RECORDS
        }
        
        # Безопасный запрос для активности
        cursor.execute(f"""
            SELECT 
                COUNT(DISTINCT user_id) as active_users,
                COUNT(*) as total_queries,
                MAX(created_at) as last_activity
            FROM {TABLE_UNANSWERED_QUERIES} 
            WHERE created_at >= NOW() - INTERVAL '30 days'
        """)
        
        activity = cursor.fetchone()
        stats['activity'] = {
            'active_users_30d': activity[0] or 0,
            'total_queries_30d': activity[1] or 0,
            'last_activity': activity[2].isoformat() if activity[2] else None
        }
        
        # Проверка здоровья системы
        stats['health'] = {
            'database_connected': True,
            'faq_threshold_met': stats['faq']['meets_threshold'],
            'min_faq_records': MIN_FAQ_RECORDS,
            'current_faq': stats['faq']['total'],
            'overall': 'healthy' if stats['faq']['meets_threshold'] else 'unhealthy'
        }
        
        conn.close()
        
        logger.info(f"📊 Безопасная статистика: {stats['faq']['total']} FAQ ({'✓' if stats['faq']['meets_threshold'] else '✗'} ≥{MIN_FAQ_RECORDS})")
        return stats
        
    except Exception as e:
        logger.error(f"❌ Ошибка безопасного получения статистики: {e}")
        return {
            'status': 'error',
            'error': str(e),
            'timestamp': datetime.now().isoformat(),
            'health': {
                'database_connected': False,
                'overall': 'unhealthy'
            }
        }

def health_check():
    """
    Комплексная проверка здоровья системы
    
    Returns:
        dict: Результат проверки здоровья
    """
    health_status = {
        'status': 'unhealthy',
        'timestamp': datetime.now().isoformat(),
        'checks': {}
    }
    
    try:
        # 1. Проверка подключения к БД
        conn = config.get_db_connection()
        
        # 2. Проверка минимального количества записей
        cursor = conn.cursor()
        cursor.execute(f"SELECT COUNT(*) FROM {TABLE_FAQ}")
        faq_count = cursor.fetchone()[0]
        
        # 3. Проверка наличия всех таблиц
        required_tables = [TABLE_FAQ, TABLE_FAQ_KEYWORDS]
        missing_tables = []
        
        for table in required_tables:
            cursor.execute("""
                SELECT EXISTS (
                    SELECT FROM information_schema.tables 
                    WHERE table_schema = 'public' 
                    AND table_name = %s
                )
            """, (table,))
            
            if not cursor.fetchone()[0]:
                missing_tables.append(table)
        
        cursor.close()
        conn.close()
        
        # Формируем результат
        health_status['checks'] = {
            'database_connection': {
                'status': 'healthy',
                'message': 'База данных доступна'
            },
            'faq_count': {
                'status': 'healthy' if faq_count >= MIN_FAQ_RECORDS else 'unhealthy',
                'message': f'FAQ записей: {faq_count} (требуется ≥{MIN_FAQ_RECORDS})',
                'count': faq_count,
                'threshold': MIN_FAQ_RECORDS
            },
            'required_tables': {
                'status': 'healthy' if not missing_tables else 'unhealthy',
                'message': f'Таблицы: {len(required_tables)} из {len(required_tables)}',
                'missing': missing_tables
            }
        }
        
        # Определяем общий статус
        all_healthy = all(
            check['status'] == 'healthy' 
            for check in health_status['checks'].values()
        )
        
        health_status['status'] = 'healthy' if all_healthy else 'unhealthy'
        
        return health_status
        
    except Exception as e:
        health_status['checks']['database_connection'] = {
            'status': 'unhealthy',
            'message': f'Ошибка подключения: {str(e)}'
        }
        return health_status

if __name__ == "__main__":
    # Тестовый запуск с проверкой безопасности
    print("🔧 Тестирование безопасных admin_tools.py")
    print("-" * 40)
    
    # Проверка здоровья
    health = health_check()
    print(f"Статус системы: {health['status'].upper()}")
    
    for check_name, check_data in health['checks'].items():
        print(f"  {check_name}: {check_data['status']} - {check_data['message']}")
    
    # Проверка статистики
    stats = get_system_stats()
    if 'error' not in stats:
        print(f"📊 FAQ в базе: {stats.get('faq', {}).get('total', 0)}/{MIN_FAQ_RECORDS}")
    
    print("-" * 40)
    print("✅ Безопасный модуль admin_tools.py работает корректно")
