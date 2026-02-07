#!/usr/bin/env python3
"""
ПОЛНЫЙ ПОИСКОВЫЙ ДВИЖОК С КОНТЕКСТОМ И КЭШЕМ
Адаптирован для PostgreSQL/SQLite
"""

import json
import time
import logging
import hashlib
from typing import List, Optional, Dict, Tuple, Set
from collections import defaultdict
from dataclasses import dataclass
from datetime import datetime
import re

from config import config

logger = logging.getLogger(__name__)

@dataclass
class FAQEntry:
    """Датакласс для хранения структурированных данных FAQ"""
    id: int
    question: str
    answer: str
    keywords: str
    norm_keywords: str
    norm_question: str
    category: str
    usage_count: int

class QueryExpander:
    """Класс для расширения и исправления запросов"""
    
    def __init__(self):
        self.synonyms = {
            'отпуск': ['отпуск', 'отпуска', 'каникулы', 'отгул', 'отдых', 'отпускной'],
            'зарплата': ['зарплата', 'зарплату', 'заработная плата', 'зарплаты', 'оклад', 'выплата'],
            'когда': ['когда', 'в какие дни', 'какого числа', 'дата', 'день'],
            'аванс': ['аванс', 'аванса', 'предоплата'],
            'больничный': ['больничный', 'больничный лист', 'болезнь'],
            'пропуск': ['пропуск', 'доступ', 'карта доступа', 'проходная'],
            'почта': ['почта', 'email', 'емейл', 'электронная почта'],
        }
        
        self.typo_corrections = {
            'зарпплата': 'зарплата',
            'отпускк': 'отпуск',
            'больничнй': 'больничный',
            'корпаративная': 'корпоративная',
            'емэйл': 'email',
        }
    
    def expand_query(self, query: str) -> List[str]:
        """Расширение запроса синонимами и исправление опечаток"""
        original = query.lower().strip()
        expanded = [original]
        
        # Исправление опечаток
        corrected = self._correct_spelling(original)
        if corrected != original:
            expanded.append(corrected)
        
        # Добавление синонимов
        words = original.split()
        if len(words) <= 3:
            for word in words:
                if word in self.synonyms:
                    for synonym in self.synonyms[word][:2]:
                        new_words = words.copy()
                        idx = new_words.index(word)
                        new_words[idx] = synonym
                        new_query = ' '.join(new_words)
                        if new_query not in expanded:
                            expanded.append(new_query)
        
        return list(dict.fromkeys(expanded))[:5]
    
    def _correct_spelling(self, query: str) -> str:
        """Исправление типичных опечаток"""
        words = query.split()
        corrected_words = []
        
        for word in words:
            if word in self.typo_corrections:
                corrected_words.append(self.typo_corrections[word])
            else:
                corrected_words.append(word)
        
        return ' '.join(corrected_words)

class ContextManager:
    """Управление контекстом диалога"""
    
    def __init__(self, max_contexts: int = 3, max_age: int = 86400):
        self.max_contexts = max_contexts
        self.max_age = max_age
        self.contexts = defaultdict(list)
    
    def add_context(self, user_id: int, query: str, result: Optional[tuple]):
        """Добавляет контекст"""
        if result is None:
            return
        
        context_entry = {
            'query': query,
            'result': result,
            'timestamp': time.time()
        }
        
        self.contexts[user_id].append(context_entry)
        
        if len(self.contexts[user_id]) > self.max_contexts:
            self.contexts[user_id].pop(0)
    
    def get_context(self, user_id: int) -> List[dict]:
        """Возвращает актуальный контекст пользователя"""
        self._cleanup_old_contexts(user_id)
        return self.contexts.get(user_id, [])
    
    def _cleanup_old_contexts(self, user_id: Optional[int] = None):
        """Удаляет устаревшие контексты"""
        current_time = time.time()
        
        if user_id:
            if user_id in self.contexts:
                self.contexts[user_id] = [
                    c for c in self.contexts[user_id]
                    if current_time - c['timestamp'] < self.max_age
                ]
                if not self.contexts[user_id]:
                    del self.contexts[user_id]
        else:
            for uid in list(self.contexts.keys()):
                self.contexts[uid] = [
                    c for c in self.contexts[uid]
                    if current_time - c['timestamp'] < self.max_age
                ]
                if not self.contexts[uid]:
                    del self.contexts[uid]

class SearchEngine:
    """Полноценный поисковый движок с индексами и кэшированием"""
    
    def __init__(self):
        self.expander = QueryExpander()
        self.context_manager = ContextManager()
        
        # Данные FAQ
        self.faq_data: List[FAQEntry] = []
        
        # Индексы для быстрого поиска
        self.keywords_index: Dict[str, List[int]] = {}
        self.question_index: Dict[str, List[int]] = {}
        self.category_index: Dict[str, List[int]] = {}
        
        # Кэш результатов поиска
        self.search_cache: Dict[str, tuple] = {}
        
        # Статистика
        self.stats = {
            'total_searches': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'context_used': 0,
            'total_time': 0.0,
            'last_update': time.time()
        }
        
        # Загрузка данных
        self.load_all_faq()
        self._build_indexes()
        
        logger.info(f"✅ Поисковый движок загружен. FAQ: {len(self.faq_data)}")
    
    def load_all_faq(self):
        """Загрузка всех FAQ из базы данных"""
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            
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
                    norm_question=row[5] if len(row) > 5 else row[1].lower(),
                    category=row[6] if len(row) > 6 else "Общее",
                    usage_count=row[7] if len(row) > 7 else 0
                )
                self.faq_data.append(faq_entry)
            
            conn.close()
            logger.info(f"✅ Загружено {len(self.faq_data)} FAQ из базы данных")
            
        except Exception as e:
            logger.error(f"❌ Ошибка при загрузке FAQ: {e}")
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
        
        logger.info(f"📊 Построены индексы: {len(self.question_index)} слов, {len(self.category_index)} категорий")
    
    @staticmethod
    def _clean_word(word: str) -> str:
        """Очистка слова от знаков пунктуации"""
        if not word:
            return ""
        word = word.strip('.,!?;:"\'()[]{}<>«»-–—')
        return word.lower()
    
    def _calculate_relevance(self, query: str, faq: FAQEntry, expanded_queries: List[str]) -> float:
        """Расчет релевантности FAQ к запросу"""
        score = 0.0
        query_lower = query.lower()
        
        # Точное совпадение вопроса
        if query_lower == faq.question.lower():
            score += 10.0
            return score
        
        # Подготовка данных для сравнения
        query_words = set(self._clean_word(w) for w in query_lower.split() if self._clean_word(w))
        
        # Совпадение ключевых слов
        if faq.keywords:
            faq_keywords = set(k.strip().lower() for k in faq.keywords.split(',') if k.strip())
            common_keywords = query_words.intersection(faq_keywords)
            if common_keywords:
                score += len(common_keywords) * 2.0
        
        # Совпадение слов в вопросе
        for q_word in query_words:
            if q_word in faq.norm_question:
                score += 3.0
        
        # Проверка расширенных запросов
        for expanded in expanded_queries:
            if expanded in faq.norm_question:
                score += 2.0
        
        return max(score, 0)
    
    def _search_single(self, query: str, use_cache: bool = True) -> Optional[tuple]:
        """Поиск одного ответа по запросу"""
        start_time = time.time()
        
        # Проверка кэша
        cache_key = hashlib.md5(query.encode()).hexdigest()[:8]
        if use_cache and cache_key in self.search_cache:
            self.stats['cache_hits'] += 1
            return self.search_cache[cache_key]
        
        self.stats['cache_misses'] += 1
        
        # Расширение запроса
        expanded_queries = self.expander.expand_query(query)
        
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
            score = self._calculate_relevance(query, faq, expanded_queries)
            
            if score > best_score:
                best_score = score
                best_match = (faq.id, faq.question, faq.answer, faq.category, score)
        
        # Обновляем статистику использования
        if best_match and best_score >= threshold:
            self._update_usage_count(best_match[0])
            self.search_cache[cache_key] = best_match
            
            if len(self.search_cache) > 1000:
                self.search_cache.pop(next(iter(self.search_cache)))
        
        # Логирование
        search_time = time.time() - start_time
        self.stats['total_time'] += search_time
        
        return best_match if best_score >= threshold else None
    
    def search(self, query: str, user_id: int = 0) -> Optional[tuple]:
        """Основной метод поиска с учетом контекста"""
        self.stats['total_searches'] += 1
        start_time = time.time()
        
        # 1. Прямой поиск
        result = self._search_single(query)
        
        # 2. Поиск с учетом контекста
        if result is None and user_id > 0:
            context = self.context_manager.get_context(user_id)
            if context:
                last_context = context[-1]
                enhanced_query = f"{last_context['query']} {query}"
                result = self._search_single(enhanced_query)
                
                if result:
                    self.stats['context_used'] += 1
                    logger.info(f"Использован контекст для user_id={user_id}")
        
        # 3. Обновляем контекст
        if result and user_id > 0:
            self.context_manager.add_context(user_id, query, result)
        
        return result
    
    def _update_usage_count(self, faq_id: int):
        """Обновление счетчика использования FAQ в БД"""
        try:
            conn = config.get_db_connection()
            cursor = conn.cursor()
            
            placeholder = config.get_placeholder()
            query = f"UPDATE faq SET usage_count = usage_count + 1 WHERE id = {placeholder}"
            
            # ИСПРАВЛЕНИЕ: Заменяем config.execute_query на cursor.execute
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
        
        logger.info(f"✅ Данные обновлены: было {old_count}, стало {len(self.faq_data)} FAQ")
    
    def get_stats(self) -> dict:
        """Получение статистики работы поискового движка"""
        total_searches = self.stats['total_searches']
        context_used = self.stats['context_used']
        
        avg_time = (self.stats['total_time'] / total_searches) if total_searches > 0 else 0
        
        return {
            'total_faq': len(self.faq_data),
            'total_searches': total_searches,
            'context_searches': context_used,
            'cache_hits': self.stats['cache_hits'],
            'cache_misses': self.stats['cache_misses'],
            'cache_size': len(self.search_cache),
            'avg_response_time': f"{avg_time:.3f}s",
            'context_usage_rate': f"{(context_used/total_searches*100):.1f}%" if total_searches else "0%",
            'keywords_index_size': len(self.keywords_index),
            'question_index_size': len(self.question_index),
            'categories': sorted(self.category_index.keys())
        }
