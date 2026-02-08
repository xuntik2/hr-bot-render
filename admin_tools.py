#!/usr/bin/env python3
"""
Модуль административных инструментов для HR-бота Мечел
Содержит функции для работы с базой данных
"""
import logging
import os
import traceback
from config import config
from faq_data import get_faq_data

logger = logging.getLogger(__name__)

def check_database_status():
    """Проверка текущего состояния базы данных"""
    try:
        conn = config.get_db_connection()
        cur = conn.cursor()
        
        # Проверяем существование таблицы
        cur.execute('''
            SELECT EXISTS (
                SELECT FROM information_schema.tables 
                WHERE table_schema = 'public' 
                AND table_name = 'faq'
            )
        ''')
        table_exists = cur.fetchone()[0]
        
        if not table_exists:
            conn.close()
            return {
                'table_exists': False,
                'error': "Таблица 'faq' не существует"
            }
        
        # Получаем статистику
        cur.execute('SELECT COUNT(*) FROM faq')
        total_records = cur.fetchone()[0]
        
        cur.execute('SELECT COUNT(DISTINCT category) FROM faq')
        categories_count = cur.fetchone()[0]
        
        cur.execute('SELECT COUNT(DISTINCT norm_question) FROM faq')
        unique_questions = cur.fetchone()[0]
        
        conn.close()
        
        # Рассчитываем процент заполнения
        completion_percentage = (total_records / 75 * 100) if 75 > 0 else 0
        
        return {
            'table_exists': True,
            'total_records': total_records,
            'categories_count': categories_count,
            'unique_questions': unique_questions,
            'completion_percentage': round(completion_percentage, 1),
            'expected_records': 75,
            'status': 'full' if total_records >= 75 else 'partial' if total_records > 0 else 'empty'
        }
        
    except Exception as e:
        logger.error(f"Ошибка при проверке статуса БД: {e}")
        return {
            'error': str(e),
            'table_exists': False
        }

def fill_database_manual():
    """Ручное заполнение базы данных"""
    try:
        # Получаем данные FAQ
        faq_list = get_faq_data()
        total_questions = len(faq_list)
        
        conn = config.get_db_connection()
        cur = conn.cursor()
        
        # Проверяем существование таблицы, создаем если нет
        cur.execute('''
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
        
        # Получаем текущее количество записей
        cur.execute('SELECT COUNT(*) FROM faq')
        count_before = cur.fetchone()[0]
        
        # Очищаем таблицу
        cur.execute('TRUNCATE TABLE faq RESTART IDENTITY CASCADE')
        
        # Вставляем данные
        inserted = 0
        errors = 0
        error_details = []
        
        placeholder = config.get_placeholder()
        
        for faq in faq_list:
            try:
                sql = f'''
                INSERT INTO faq (question, answer, keywords, norm_keywords, norm_question, category)
                VALUES ({placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder}, {placeholder})
                '''
                cur.execute(sql, (
                    faq['question'],
                    faq['answer'],
                    faq['keywords'],
                    faq['norm_keywords'],
                    faq['norm_question'],
                    faq['category']
                ))
                inserted += 1
            except Exception as e:
                errors += 1
                error_msg = f"Ошибка при вставке вопроса '{faq['question'][:50]}...': {str(e)}"
                logger.error(error_msg)
                error_details.append({
                    'question': faq['question'][:100],
                    'error': str(e),
                    'category': faq['category']
                })
        
        conn.commit()
        
        # Получаем итоговую статистику
        cur.execute('SELECT COUNT(*) FROM faq')
        final_count = cur.fetchone()[0]
        
        cur.execute('SELECT COUNT(DISTINCT category) FROM faq')
        categories = cur.fetchone()[0]
        
        cur.execute('SELECT category, COUNT(*) as count FROM faq GROUP BY category ORDER BY count DESC')
        category_stats = cur.fetchall()
        
        conn.close()
        
        completion = (final_count / 75 * 100) if 75 > 0 else 0
        
        # Логируем детальную статистику
        logger.info(f"📊 Статистика заполнения БД:")
        logger.info(f"  - Всего вопросов: {total_questions}")
        logger.info(f"  - Успешно вставлено: {inserted}")
        logger.info(f"  - Ошибок: {errors}")
        logger.info(f"  - Итоговое количество: {final_count}")
        logger.info(f"  - Категорий: {categories}")
        
        for category, count in category_stats:
            logger.info(f"  - {category}: {count} вопросов")
        
        if errors > 0:
            logger.warning(f"⚠️ Было {errors} ошибок при добавлении:")
            for err in error_details[:5]:  # Показываем только первые 5 ошибок
                logger.warning(f"   - {err['question']} ({err['category']}): {err['error']}")
        
        return {
            'success': True,
            'stats': {
                'inserted': inserted,
                'total_questions': total_questions,
                'errors': errors,
                'final_count': final_count,
                'categories': categories,
                'count_before': count_before,
                'category_stats': dict(category_stats)
            },
            'details': {
                'completion': f"{completion:.1f}%",
                'status': 'full' if final_count >= 75 else 'partial',
                'error_details': error_details if errors > 0 else []
            }
        }
        
    except Exception as e:
        logger.error(f"Ошибка при заполнении базы данных: {e}")
        logger.error(traceback.format_exc())
        return {
            'success': False,
            'error': str(e),
            'traceback': traceback.format_exc()
        }
