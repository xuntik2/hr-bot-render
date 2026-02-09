"""
БЕЗОПАСНЫЙ ПОИСКОВЫЙ ДВИЖОК С УЛУЧШЕННЫМ КЭШИРОВАНИЕМ И СТАТИСТИКОЙ
"""

import logging
import hashlib
from typing import List, Optional, Tuple
from dataclasses import dataclass
from collections import OrderedDict

from config import config

logger = logging.getLogger(__name__)

@dataclass
class FAQEntry:
    id: int
    question: str
    answer: str
    keywords: str
    norm_keywords: str
    norm_question: str
    category: str
    usage_count: int

class SearchEngine:
    """Поисковый движок с улучшенным кэшированием и статистикой"""
    
    def __init__(self):
        self.faq_data: List[FAQEntry] = []
        # Используем OrderedDict для автоматического удаления старых записей
        self.cache = OrderedDict()
        self.max_cache_size = 100
        
        # Статистика
        self.stats = {
            'total_searches': 0,
            'cache_hits': 0,
            'cache_misses': 0
        }
        
        self.load_all_faq()
    
    def load_all_faq(self):
        """Загрузка FAQ с безопасными запросами"""
        try:
            conn = config.get_db_connection()
            if not conn:
                logger.warning("⚠️ БД недоступна")
                return
            
            cursor = conn.cursor()
            
            # Безопасный запрос - используем константу
            cursor.execute(f"SELECT * FROM {config.TABLE_FAQ}")
            rows = cursor.fetchall()
            
            self.faq_data.clear()
            for row in rows:
                faq = FAQEntry(
                    id=row[0],
                    question=row[1],
                    answer=row[2],
                    keywords=row[3] if len(row) > 3 else "",
                    norm_keywords=row[4] if len(row) > 4 else "",
                    norm_question=row[5] if len(row) > 5 else "",
                    category=row[6] if len(row) > 6 else "Общее",
                    usage_count=row[7] if len(row) > 7 else 0
                )
                self.faq_data.append(faq)
            
            conn.close()
            logger.info(f"✅ Загружено {len(self.faq_data)} FAQ")
            
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки FAQ: {e}")
            self.faq_data = []
    
    def search(self, query: str) -> Optional[Tuple]:
        """Простой поиск с улучшенным кэшированием"""
        try:
            # Защита от пустого поиска
            if not query or len(query.strip()) < 2:
                return None
            
            if not self.faq_data:
                logger.warning("⚠️ Поиск без данных FAQ")
                return None
            
            # Увеличиваем счетчик поисков
            self.stats['total_searches'] += 1
            
            # Кэширование
            query_hash = hashlib.md5(query.encode()).hexdigest()[:8]
            
            # Проверяем кэш
            if query_hash in self.cache:
                self.stats['cache_hits'] += 1
                # Перемещаем в конец (самый свежий)
                self.cache.move_to_end(query_hash)
                return self.cache[query_hash]
            
            self.stats['cache_misses'] += 1
            
            best_match = None
            best_score = 0
            
            for faq in self.faq_data:
                score = self._calculate_score(query, faq)
                
                if score > best_score:
                    best_score = score
                    best_match = (
                        faq.id,
                        faq.question,
                        faq.answer,
                        faq.category,
                        min(score, 100)
                    )
            
            # Сохраняем в кэш
            if best_match and best_score >= 30:
                self.cache[query_hash] = best_match
                # Ограничиваем размер кэша
                if len(self.cache) > self.max_cache_size:
                    self.cache.popitem(last=False)  # Удаляем самый старый
            
            return best_match if best_score >= 30 else None
            
        except Exception as e:
            logger.error(f"❌ Ошибка поиска: {e}")
            return None
    
    def _calculate_score(self, query: str, faq: FAQEntry) -> float:
        """Расчет релевантности с оптимизацией"""
        score = 0.0
        query_lower = query.lower().strip()
        faq_question_lower = faq.question.lower()
        
        # 1. Точное совпадение вопроса
        if query_lower == faq_question_lower:
            return 100.0
        
        # 2. Частичное совпадение вопроса (если запрос является подстрокой вопроса)
        if query_lower in faq_question_lower:
            score += 50.0
        
        # 3. Ключевые слова (через множества)
        if faq.keywords:
            keywords_set = set(k.strip().lower() for k in faq.keywords.split(','))
            query_words = set(query_lower.split())
            common_words = query_words.intersection(keywords_set)
            score += len(common_words) * 25.0
        
        # 4. Нормализованный вопрос
        if faq.norm_question and query_lower in faq.norm_question:
            score += 40.0
        
        # 5. Популярность
        if faq.usage_count > 0:
            score += min(faq.usage_count, 20)  # Максимум +20 баллов
        
        return min(score, 100.0)
    
    def refresh_data(self):
        """Обновление данных"""
        self.load_all_faq()
        self.cache.clear()
        logger.info("🔄 Данные обновлены")
    
    def get_stats(self) -> dict:
        """Простая статистика"""
        categories = set()
        for faq in self.faq_data:
            if faq.category:
                categories.add(faq.category)
        
        # Расчет эффективности кэша
        cache_hit_rate = 0
        if self.stats['total_searches'] > 0:
            cache_hit_rate = (self.stats['cache_hits'] / 
                            self.stats['total_searches'] * 100)
        
        return {
            'faq_count': len(self.faq_data),
            'cache_size': len(self.cache),
            'categories': len(categories),
            'total_searches': self.stats['total_searches'],
            'cache_hits': self.stats['cache_hits'],
            'cache_misses': self.stats['cache_misses'],
            'cache_hit_rate': round(cache_hit_rate, 2)
        }
