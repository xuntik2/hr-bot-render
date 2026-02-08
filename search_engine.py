#!/usr/bin/env python3
"""
ПОИСКОВЫЙ ДВИЖОК С ИНДЕКСАЦИЕЙ И КЭШЕМ
"""
import logging
import time
import hashlib
from typing import List, Optional, Dict, Tuple
from dataclasses import dataclass
from config import config

logger = logging.getLogger(__name__)

@dataclass
class FAQEntry:
    """Структура для хранения FAQ"""
    id: int
    question: str
    answer: str
    keywords: str
    norm_keywords: str
    norm_question: str
    category: str
    usage_count: int

class SearchEngine:
    """Поисковый движок с индексами и кэшированием"""
    
    def __init__(self):
        self.faq_data: List[FAQEntry] = []
        self.keywords_index: Dict[str, List[int]] = {}
        self.question_index: Dict[str, List[int]] = {}
        self.category_index: Dict[str, List[int]] = {}
        self.search_cache: Dict[str, tuple] = {}
        
        self.stats = {
            'total_searches': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'total_time': 0.0
        }
        
        self.load_all_faq()
        self._build_indexes()
        
        logger.info(f"✅ Поисковый движок готов. Загружено {len(self.faq_data)} FAQ")
    
    def load_all_faq(self):
        """Загрузка всех FAQ из базы данных"""
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            
            # Проверяем существование таблицы faq
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
            
            if not table_exists:
                logger.warning("Таблица 'faq' не существует. Загрузка FAQ невозможна.")
                self.faq_data = []
                conn.close()
                return
            
            cursor.execute("SELECT * FROM faq")
            rows = cursor.fetchall()
            
            self.faq_data.clear()
            
            for row in rows:
                faq_entry = FAQEntry(
                    id=row[0],
                    question=row[1],
                    answer=row[2],
                    keywords=row[3] if len(row) > 3 else "",
                    norm_keywords=row[4] if len(row) > 4 else "",
                    norm_question=row[5] if len(row) > 5 else "",
                    category=row[6] if len(row) > 6 else "Общее",
                    usage_count=row[7] if len(row) > 7 else 0
                )
                self.faq_data.append(faq_entry)
            
            conn.close()
            logger.info(f"Загружено {len(self.faq_data)} FAQ из базы данных")
            
        except Exception as e:
            logger.error(f"Ошибка при загрузке FAQ: {e}")
            self.faq_data = []
    
    def _build_indexes(self):
        """Построение инвертированных индексов для быстрого поиска"""
        self.keywords_index.clear()
        self.question_index.clear()
        self.category_index.clear()
        
        for faq in self.faq_data:
            faq_id = faq.id
            
            # Индексация по категории
            if faq.category:
                category = faq.category.strip()
                if category not in self.category_index:
                    self.category_index[category] = []
                self.category_index[category].append(faq_id)
            
            # Индексация ключевых слов
            if faq.keywords:
                keywords_list = [k.strip().lower() for k in faq.keywords.split(',') if k.strip()]
                for keyword in keywords_list:
                    keyword_clean = self._clean_word(keyword)
                    if keyword_clean:
                        if keyword_clean not in self.keywords_index:
                            self.keywords_index[keyword_clean] = []
                        if faq_id not in self.keywords_index[keyword_clean]:
                            self.keywords_index[keyword_clean].append(faq_id)
            
            # Индексация слов из нормализованного вопроса
            if faq.norm_question:
                words = faq.norm_question.split()
                for word in words:
                    word_clean = self._clean_word(word)
                    if word_clean and len(word_clean) > 2:
                        if word_clean not in self.question_index:
                            self.question_index[word_clean] = []
                        if faq_id not in self.question_index[word_clean]:
                            self.question_index[word_clean].append(faq_id)
    
    @staticmethod
    def _clean_word(word: str) -> str:
        """Очистка слова от знаков пунктуации"""
        if not word:
            return ""
        word = word.strip('.,!?;:"\'()[]{}<>«»-–—')
        return word.lower()
    
    def _calculate_relevance(self, query: str, faq: FAQEntry) -> float:
        """Расчет релевантности FAQ к запросу"""
        score = 0.0
        query_lower = query.lower()
        
        # Точное совпадение вопроса
        if query_lower == faq.question.lower():
            return 100.0
        
        # Подготовка данных для сравнения
        query_words = set(self._clean_word(w) for w in query_lower.split() if self._clean_word(w))
        
        # Совпадение ключевых слов
        if faq.keywords:
            faq_keywords = set(k.strip().lower() for k in faq.keywords.split(',') if k.strip())
            common_keywords = query_words.intersection(faq_keywords)
            if common_keywords:
                score += len(common_keywords) * 20.0
        
        # Совпадение слов в вопросе
        for q_word in query_words:
            if q_word in faq.norm_question:
                score += 30.0
        
        # Учитываем популярность (usage_count)
        score += min(faq.usage_count * 0.5, 10.0)
        
        return min(score, 100.0)
    
    def search(self, query: str, user_id: int = 0) -> Optional[tuple]:
        """Основной метод поиска"""
        self.stats['total_searches'] += 1
        start_time = time.time()
        
        # Проверка кэша
        cache_key = hashlib.md5(query.encode()).hexdigest()[:8]
        if cache_key in self.search_cache:
            self.stats['cache_hits'] += 1
            return self.search_cache[cache_key]
        
        self.stats['cache_misses'] += 1
        
        # Получаем кандидатов через инвертированный индекс
        candidate_ids = set()
        query_words = set(self._clean_word(w) for w in query.lower().split() if self._clean_word(w))
        
        for word in query_words:
            if word in self.keywords_index:
                candidate_ids.update(self.keywords_index[word])
            if word in self.question_index:
                candidate_ids.update(self.question_index[word])
        
        # Если кандидатов нет, проверяем все FAQ
        if not candidate_ids:
            candidate_ids = set(range(len(self.faq_data)))
        
        # Поиск лучшего совпадения
        best_match = None
        best_score = 0.0
        threshold = config.get_search_threshold()
        
        for idx in candidate_ids:
            if idx >= len(self.faq_data):
                continue
                
            faq = self.faq_data[idx]
            score = self._calculate_relevance(query, faq)
            
            if score > best_score:
                best_score = score
                best_match = (faq.id, faq.question, faq.answer, faq.category, score)
        
        # Обновляем статистику использования
        if best_match and best_score >= threshold:
            self._update_usage_count(best_match[0])
            self.search_cache[cache_key] = best_match
            
            # Ограничиваем размер кэша
            if len(self.search_cache) > 1000:
                self.search_cache.pop(next(iter(self.search_cache)))
        
        # Логирование времени
        search_time = time.time() - start_time
        self.stats['total_time'] += search_time
        
        return best_match if best_score >= threshold else None
    
    def _update_usage_count(self, faq_id: int):
        """Обновление счетчика использования FAQ в БД"""
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            placeholder = config.get_placeholder()
            
            query = f"UPDATE faq SET usage_count = usage_count + 1 WHERE id = {placeholder}"
            cursor.execute(query, (faq_id,))
            
            conn.commit()
            conn.close()
            
            # Обновляем в памяти
            for faq in self.faq_data:
                if faq.id == faq_id:
                    faq.usage_count += 1
                    break
                    
        except Exception as e:
            logger.error(f"Ошибка при обновлении usage_count: {e}")
    
    def refresh_data(self):
        """Обновление данных из БД и перестроение индексов"""
        old_count = len(self.faq_data)
        self.load_all_faq()
        self._build_indexes()
        self.search_cache.clear()
        logger.info(f"Данные обновлены: было {old_count}, стало {len(self.faq_data)} FAQ")
    
    def get_stats(self) -> dict:
        """Получение статистики работы поискового движка"""
        total_searches = self.stats['total_searches']
        avg_time = (self.stats['total_time'] / total_searches) if total_searches > 0 else 0
        
        return {
            'total_faq': len(self.faq_data),
            'total_searches': total_searches,
            'cache_hits': self.stats['cache_hits'],
            'cache_misses': self.stats['cache_misses'],
            'cache_size': len(self.search_cache),
            'avg_response_time': f"{avg_time:.3f}s",
            'keywords_index_size': len(self.keywords_index),
            'question_index_size': len(self.question_index),
            'categories': sorted(self.category_index.keys())
        }
