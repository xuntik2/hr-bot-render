"""
УПРОЩЕННЫЙ ПОИСКОВЫЙ ДВИЖОК ТОЛЬКО С CSV (БЕЗ БАЗЫ ДАННЫХ)
Версия 3.2 - Все ошибки исправлены, проверка self.config добавлена
"""

import logging
import csv
import os
import hashlib
from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass
from collections import OrderedDict

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
    usage_count: int = 0

class SearchEngine:
    """Поисковый движок работающий только с CSV файлами и резервными данными"""
    
    def __init__(self, config=None):
        self.faq_data: List[FAQEntry] = []
        self.config = config
        
        # LRU кэш для быстрого поиска
        self.cache = OrderedDict()
        self.max_cache_size = 50
        
        # Своя статистика (не глобальная stats из bot.py!)
        self.stats = {
            'total_searches': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'loaded_from': 'unknown'
        }
        
        self.load_all_faq()
    
    def load_all_faq(self):
        """Загрузка FAQ из всех доступных источников"""
        try:
            # Пробуем загрузить из CSV (если есть конфиг и файл)
            if self.config:
                csv_loaded = self._load_from_csv()
                if csv_loaded:
                    logger.info(f"✅ Загружено {len(self.faq_data)} FAQ из CSV")
                    self.stats['loaded_from'] = f'CSV ({len(self.faq_data)} записей)'
                    return
            
            # Если нет конфига или CSV не загружен, используем резервные данные
            self._load_fallback_data()
            
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки FAQ: {e}")
            # Создаем минимальный набор на случай полного сбоя
            self._create_minimal_data()
    
    def _load_from_csv(self) -> bool:
        """Загрузка FAQ из CSV файла"""
        try:
            # КРИТИЧЕСКОЕ ИСПРАВЛЕНИЕ: проверяем наличие конфига
            if not self.config:
                logger.warning("⚠️ Конфигурация не передана, пропускаем загрузку CSV")
                return False
            
            csv_file = self.config.get_faq_file()
            
            if not os.path.exists(csv_file):
                logger.warning(f"⚠️ Файл FAQ не найден: {csv_file}")
                return False
            
            # Читаем CSV файл
            self.faq_data.clear()
            
            with open(csv_file, 'r', encoding='utf-8') as file:
                reader = csv.DictReader(file)
                
                for row_num, row in enumerate(reader, 1):
                    try:
                        category = row.get('Категория', 'Общее').strip()
                        question = row.get('Вопрос', '').strip()
                        answer = row.get('Ответ', '').strip()
                        keywords = row.get('Ключевые слова', '').strip()
                        norm_keywords = row.get('Норм ключевые', '').strip()
                        norm_question = row.get('Норм вопрос', '').strip()
                        
                        if question and answer:
                            faq = FAQEntry(
                                id=row_num,
                                question=question,
                                answer=answer,
                                keywords=keywords,
                                norm_keywords=norm_keywords,
                                norm_question=norm_question,
                                category=category
                            )
                            self.faq_data.append(faq)
                            
                    except Exception as e:
                        logger.warning(f"⚠️ Ошибка парсинга строки {row_num}: {e}")
                        continue
            
            return len(self.faq_data) > 0
            
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки из CSV: {e}")
            return False
    
    def _load_fallback_data(self):
        """Загрузка резервных данных из faq_data.py"""
        try:
            from faq_data import get_faq_data
            faq_list = get_faq_data()
            
            self.faq_data.clear()
            for i, faq_dict in enumerate(faq_list, 1):
                faq = FAQEntry(
                    id=i,
                    question=faq_dict['question'],
                    answer=faq_dict['answer'],
                    keywords=faq_dict.get('keywords', ''),
                    norm_keywords=faq_dict.get('norm_keywords', ''),
                    norm_question=faq_dict.get('norm_question', ''),
                    category=faq_dict['category']
                )
                self.faq_data.append(faq)
            
            self.stats['loaded_from'] = f'резервные данные ({len(self.faq_data)} записей)'
            logger.info("✅ Используются резервные данные из faq_data.py")
            
        except ImportError as e:
            logger.error(f"❌ Не удалось импортировать faq_data: {e}")
            self._create_minimal_data()
    
    def _create_minimal_data(self):
        """Создание минимального набора данных"""
        self.faq_data = [
            FAQEntry(
                id=1,
                question='Как оформить отпуск?',
                answer='Обратитесь в отдел кадров с заявлением за 2 недели до начала отпуска.',
                keywords='отпуск, оформить, кадры, заявление',
                norm_keywords='отпуск оформить кадры заявление',
                norm_question='как оформить отпуск',
                category='Отпуск'
            ),
            FAQEntry(
                id=2,
                question='Когда выплачивается зарплата?',
                answer='Зарплата выплачивается 5 и 20 числа каждого месяца.',
                keywords='зарплата, выплата, дата, аванс',
                norm_keywords='зарплата выплата дата аванс',
                norm_question='когда выплачивается зарплата',
                category='Зарплата'
            )
        ]
        self.stats['loaded_from'] = 'минимальный набор (2 записи)'
        logger.warning("⚠️ Используется минимальный набор данных")
    
    def search(self, query: str) -> Optional[Tuple]:
        """Интеллектуальный поиск по FAQ с кэшированием"""
        try:
            if not query or len(query.strip()) < 2:
                return None
            
            if not self.faq_data:
                logger.warning("⚠️ Поиск без данных FAQ")
                return None
            
            # Увеличиваем счетчик поисков
            self.stats['total_searches'] += 1
            
            # Создаем ключ для кэша (хеш запроса)
            query_hash = hashlib.md5(query.lower().strip().encode()).hexdigest()[:12]
            
            # Проверяем кэш
            if query_hash in self.cache:
                self.stats['cache_hits'] += 1
                self.cache.move_to_end(query_hash)
                return self.cache[query_hash]
            
            self.stats['cache_misses'] += 1
            
            best_match = None
            best_score = 0
            
            query_lower = query.lower().strip()
            query_words = set(query_lower.split())
            
            for faq in self.faq_data:
                score = self._calculate_relevance_score(query_lower, query_words, faq)
                
                if score > best_score:
                    best_score = score
                    best_match = (
                        faq.id,
                        faq.question,
                        faq.answer,
                        faq.category,
                        min(score, 100)
                    )
            
            # Сохраняем в кэш если релевантность достаточно высока
            if best_match and best_score >= 20:
                self.cache[query_hash] = best_match
                
                # Ограничиваем размер кэша (LRU)
                if len(self.cache) > self.max_cache_size:
                    self.cache.popitem(last=False)
            
            return best_match if best_score >= 20 else None
            
        except Exception as e:
            logger.error(f"❌ Ошибка поиска: {e}")
            return None
    
    def _calculate_relevance_score(self, query_lower: str, query_words: set, faq: FAQEntry) -> float:
        """Расчет релевантности запроса и FAQ"""
        score = 0.0
        
        # 1. Точное совпадение вопроса
        if query_lower == faq.question.lower():
            return 100.0
        
        # 2. Запрос является подстрокой вопроса
        if query_lower in faq.question.lower():
            score += 60.0
        
        # 3. Совпадение с нормализованным вопросом
        if faq.norm_question and query_lower in faq.norm_question.lower():
            score += 50.0
        
        # 4. Ключевые слова
        if faq.keywords:
            faq_keywords = set(faq.keywords.lower().replace(',', ' ').split())
            common_words = query_words.intersection(faq_keywords)
            score += len(common_words) * 15.0
        
        # 5. Частичное совпадение слов
        for word in query_words:
            if len(word) > 3:
                if word in faq.question.lower():
                    score += 5.0
                if faq.keywords and word in faq.keywords.lower():
                    score += 8.0
        
        return score
    
    def refresh_data(self):
        """Обновление данных"""
        self.load_all_faq()
        self.cache.clear()
        logger.info("🔄 Данные обновлены")
    
    def get_stats(self) -> dict:
        """Получение статистики"""
        categories = set()
        for faq in self.faq_data:
            if faq.category:
                categories.add(faq.category)
        
        cache_hit_rate = 0
        if self.stats['total_searches'] > 0:
            cache_hit_rate = (self.stats['cache_hits'] / self.stats['total_searches'] * 100)
        
        return {
            'faq_count': len(self.faq_data),
            'cache_size': len(self.cache),
            'categories': len(categories),
            'category_list': sorted(list(categories)),
            'total_searches': self.stats['total_searches'],
            'cache_hits': self.stats['cache_hits'],
            'cache_misses': self.stats['cache_misses'],
            'cache_hit_rate': round(cache_hit_rate, 2),
            'loaded_from': self.stats['loaded_from']
        }
