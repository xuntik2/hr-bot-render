"""
ПОИСКОВЫЙ ДВИЖОК ДЛЯ HR-БОТА МЕЧЕЛ
Версия 4.3 - Совместимость с адаптером (question, answer, score), индекс категорий, неточное совпадение
Полностью исправлена ошибка преобразования float, оптимизирован для Render Free.
"""

import logging
import json
import os
import re
import hashlib
from typing import List, Optional, Tuple, Dict, Any
from dataclasses import dataclass
from collections import OrderedDict, defaultdict
from datetime import datetime, timedelta

logger = logging.getLogger(__name__)

@dataclass
class FAQEntry:
    """Запись в базе знаний"""
    id: int
    question: str
    answer: str
    keywords: str          # строка с ключевыми словами через запятую
    norm_keywords: str     # нормализованные ключевые слова
    norm_question: str     # нормализованный вопрос
    category: str
    usage_count: int = 0

class SearchEngine:
    """
    Поисковый движок с поддержкой:
    - загрузки из faq.json (приоритет) или встроенных резервных данных
    - поиска с учётом категории (неточное совпадение) и top_k
    - нормализации запроса (стоп-слова, обрезание окончаний, синонимы)
    - индекса категорий для быстрой фильтрации
    - LRU-кэша результатов
    - подробной статистики
    - возврата кортежей (question, answer, score) для совместимости с адаптером
    """
    
    # Стоп-слова (не влияют на поиск)
    STOP_WORDS = {
        'как', 'что', 'где', 'когда', 'почему', 'зачем', 'сколько', 'чей',
        'а', 'и', 'но', 'или', 'если', 'то', 'же', 'бы', 'в', 'на', 'с', 'по',
        'о', 'об', 'от', 'до', 'для', 'из', 'у', 'не', 'нет', 'да', 'это',
        'тот', 'этот', 'такой', 'какой', 'все', 'всё', 'его', 'ее', 'их',
        'можно', 'нужно', 'надо', 'будет', 'есть', 'быть', 'весь', 'эта', 'эти'
    }
    
    # Синонимы (расширенный набор)
    SYNONYMS = {
        'зп': 'зарплата',
        'отдых': 'отпуск',
        'больничный': 'листок нетрудоспособности',
        'декрет': 'отпуск по уходу за ребенком',
        'увольнение': 'расчет',
        'премия': 'бонус',
        'справка': 'документ',
        'трудовая': 'трудовая книжка',
        'оклад': 'зарплата',
        'отгул': 'дополнительный выходной',
        'кадры': 'отдел кадров',
        'льгота': 'социальная поддержка'
    }

    def __init__(self, max_cache_size: int = 200):
        self.max_cache_size = max_cache_size
        self.cache = OrderedDict()
        self.cache_ttl = {}
        self.faq_data: List[FAQEntry] = []
        self._category_index: Dict[str, List[FAQEntry]] = defaultdict(list)
        
        # Статистика
        self.stats = {
            'total_searches': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'loaded_from': 'не загружено'
        }
        
        self._load_faq()
        self._build_category_index()
        logger.info(f"✅ SearchEngine v4.3: загружено {len(self.faq_data)} записей, "
                   f"источник: {self.stats['loaded_from']}")

    # ------------------------------------------------------------
    #  ЗАГРУЗКА ДАННЫХ И ИНДЕКСАЦИЯ
    # ------------------------------------------------------------
    def _load_faq(self):
        """Загрузка FAQ: JSON -> резервные данные"""
        if self._load_from_json():
            return
        logger.warning("⚠️ Не удалось загрузить faq.json, используются встроенные резервные вопросы")
        self._load_fallback()

    def _load_from_json(self) -> bool:
        """Загрузка из faq.json (ожидается структура, сгенерированная из faq_data.py)"""
        json_path = "faq.json"
        if not os.path.exists(json_path):
            logger.debug(f"Файл {json_path} не найден")
            return False
        
        try:
            with open(json_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
            
            self.faq_data.clear()
            loaded_count = 0
            for idx, item in enumerate(data, start=1):
                question = item.get('question', '').strip()
                answer = item.get('answer', '').strip()
                if not question or not answer:
                    logger.warning(f"⚠️ Пропущена запись {idx}: пустой вопрос или ответ")
                    continue
                
                keywords_raw = item.get('keywords', '')
                if isinstance(keywords_raw, list):
                    keywords_str = ', '.join(keywords_raw)
                else:
                    keywords_str = keywords_raw
                
                norm_keywords = item.get('norm_keywords', '')
                if not norm_keywords and keywords_str:
                    norm_keywords = self._normalize_text(keywords_str)
                
                norm_question = item.get('norm_question', '')
                if not norm_question and question:
                    norm_question = self._normalize_text(question)
                
                faq = FAQEntry(
                    id=idx,
                    question=question,
                    answer=answer,
                    keywords=keywords_str,
                    norm_keywords=norm_keywords,
                    norm_question=norm_question,
                    category=item.get('category', 'Без категории').strip()
                )
                self.faq_data.append(faq)
                loaded_count += 1
            
            self.stats['loaded_from'] = f'JSON ({loaded_count} записей)'
            logger.info(f"✅ Загружено {loaded_count} записей из {json_path}")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки JSON: {e}")
            return False

    def _load_fallback(self):
        """Резервные вопросы (встроенные)"""
        self.faq_data = [
            FAQEntry(
                id=1,
                question="Как оформить отпуск?",
                answer="Обратитесь в отдел кадров с заявлением за 2 недели до начала отпуска.",
                keywords="отпуск, оформить, кадры, заявление",
                norm_keywords="отпуск оформить кадры заявление",
                norm_question="как оформить отпуск",
                category="Отпуск"
            ),
            FAQEntry(
                id=2,
                question="Когда выплачивается зарплата?",
                answer="Зарплата выплачивается 5 и 20 числа каждого месяца.",
                keywords="зарплата, выплата, дата, аванс",
                norm_keywords="зарплата выплата дата аванс",
                norm_question="когда выплачивается зарплата",
                category="Зарплата"
            )
        ]
        self.stats['loaded_from'] = 'резервные данные (2 записи)'
        logger.info("✅ Используются резервные данные (2 записи)")

    def _build_category_index(self):
        """Построение индекса категорий для быстрого поиска"""
        self._category_index.clear()
        for faq in self.faq_data:
            cat_lower = faq.category.lower()
            self._category_index[cat_lower].append(faq)
        logger.debug(f"📂 Построен индекс категорий: {len(self._category_index)} категорий")

    # ------------------------------------------------------------
    #  НОРМАЛИЗАЦИЯ ТЕКСТА (СТОП-СЛОВА, СТЕММИНГ, СИНОНИМЫ)
    # ------------------------------------------------------------
    def _normalize_text(self, text: str) -> str:
        """Приведение текста к нормальной форме для сравнения"""
        if not text:
            return ""
        
        # Нижний регистр
        text = text.lower().strip()
        
        # Замена синонимов (целые слова)
        for orig, repl in self.SYNONYMS.items():
            text = re.sub(r'\b' + re.escape(orig) + r'\b', repl, text)
        
        # Удаление знаков препинания (оставляем буквы, цифры, пробелы)
        text = re.sub(r'[^\w\s]', ' ', text)
        
        # Разбиваем на слова
        words = text.split()
        
        # Удаляем стоп-слова и слишком короткие слова
        words = [w for w in words if w not in self.STOP_WORDS and len(w) > 2]
        
        # Обрезаем окончания (очень простой стемминг для русского)
        normalized = []
        for w in words:
            # Глагольные окончания
            if w.endswith('ться'): w = w[:-4] + 'ть'
            elif w.endswith('тся'): w = w[:-3] + 'ться'
            elif w.endswith('ать') and len(w) > 4: w = w[:-3]
            elif w.endswith('ять') and len(w) > 4: w = w[:-3]
            elif w.endswith('ить') and len(w) > 4: w = w[:-3]
            elif w.endswith('еть') and len(w) > 4: w = w[:-3]
            # Прилагательные
            elif w.endswith('ый') or w.endswith('ий') or w.endswith('ой'): w = w[:-2]
            elif w.endswith('ая') or w.endswith('яя'): w = w[:-2]
            elif w.endswith('ое') or w.endswith('ее'): w = w[:-2]
            # Существительные (очень грубо)
            elif w.endswith('ам') or w.endswith('ям'): w = w[:-2]
            elif w.endswith('ами') or w.endswith('ями'): w = w[:-3]
            elif w.endswith('ах') or w.endswith('ях'): w = w[:-2]
            elif w.endswith('ов') or w.endswith('ев'): w = w[:-2]
            elif w.endswith('ей'): w = w[:-2]
            normalized.append(w)
        
        return ' '.join(normalized)

    # ------------------------------------------------------------
    #  ПОИСК (ОСНОВНОЙ МЕТОД) - версия 4.3
    # ------------------------------------------------------------
    def search(self, query: str, category: Optional[str] = None, top_k: int = 5) -> List[Tuple[str, str, float]]:
        """
        Поиск по базе знаний.
        
        Параметры:
            query (str): поисковый запрос
            category (Optional[str]): фильтр по категории (неточное, регистронезависимое совпадение)
            top_k (int): количество возвращаемых результатов (по умолчанию 5)
        
        Возвращает:
            List[Tuple[str, str, float]]: список кортежей (вопрос, ответ, релевантность)
                         отсортирован по убыванию релевантности
        """
        # Валидация
        if not query or len(query.strip()) < 2:
            return []
        if not self.faq_data:
            logger.warning("⚠️ Поиск при пустой базе знаний")
            return []
        
        # Нормализуем запрос
        norm_query = self._normalize_text(query)
        if not norm_query:
            return []
        
        # Ключ кэша
        cache_key = f"{norm_query}_{category}_{top_k}"
        cache_key_hash = hashlib.md5(cache_key.encode()).hexdigest()[:16]
        
        # Проверяем кэш
        if cache_key_hash in self.cache:
            expiry = self.cache_ttl.get(cache_key_hash)
            if expiry and datetime.now() < expiry:
                self.stats['cache_hits'] += 1
                self.stats['total_searches'] += 1
                self.cache.move_to_end(cache_key_hash)
                return self.cache[cache_key_hash]
        
        self.stats['total_searches'] += 1
        self.stats['cache_misses'] += 1
        
        # Фильтрация по категории с неточным совпадением
        if category:
            cat_lower = category.lower()
            faq_list = self._category_index.get(cat_lower, [])
            if not faq_list:
                for cat_key, entries in self._category_index.items():
                    if cat_lower in cat_key:
                        faq_list = entries
                        logger.debug(f"Частичное совпадение категории: '{category}' -> '{cat_key}'")
                        break
        else:
            faq_list = self.faq_data
        
        if not faq_list:
            return []
        
        # Расчёт релевантности
        results = []
        query_words = set(norm_query.split())
        
        for faq in faq_list:
            score = self._calculate_score(norm_query, query_words, faq)
            if score > 0:
                results.append((faq.question, faq.answer, min(score, 100.0)))
        
        # Сортируем по убыванию релевантности
        results.sort(key=lambda x: x[2], reverse=True)
        
        # Берём top_k
        top_results = results[:top_k]
        
        # Сохраняем в кэш (если есть результаты)
        if top_results:
            if len(self.cache) >= self.max_cache_size:
                oldest = next(iter(self.cache))
                del self.cache[oldest]
                del self.cache_ttl[oldest]
            
            self.cache[cache_key_hash] = top_results
            self.cache_ttl[cache_key_hash] = datetime.now() + timedelta(hours=1)
        
        return top_results

    def _calculate_score(self, norm_query: str, query_words: set, faq: FAQEntry) -> float:
        """
        Вычисление релевантности записи запросу.
        Возвращает число от 0 до 100.
        """
        score = 0.0
        
        # 1. Точное совпадение нормализованного вопроса
        if norm_query == faq.norm_question:
            return 100.0
        
        # 2. Запрос является подстрокой нормализованного вопроса
        if norm_query in faq.norm_question:
            score += 50.0
        
        # 3. Совпадение по словам в вопросе
        q_words = set(faq.norm_question.split()) if faq.norm_question else set()
        common_q = query_words.intersection(q_words)
        score += len(common_q) * 12.0
        
        # 4. Совпадение по ключевым словам
        if faq.norm_keywords:
            kw_words = set(faq.norm_keywords.split())
            common_kw = query_words.intersection(kw_words)
            score += len(common_kw) * 20.0
        
        # 5. Частичное совпадение отдельных слов
        for word in query_words:
            if len(word) > 3:
                if word in faq.norm_question:
                    score += 3.0
                if faq.norm_keywords and word in faq.norm_keywords:
                    score += 5.0
        
        return score

    # ------------------------------------------------------------
    #  УПРАВЛЕНИЕ ДАННЫМИ И СТАТИСТИКА
    # ------------------------------------------------------------
    def refresh_data(self):
        """Принудительная перезагрузка данных и сброс кэша"""
        self._load_faq()
        self._build_category_index()
        self.cache.clear()
        self.cache_ttl.clear()
        logger.info("🔄 Данные перезагружены, кэш сброшен")

    def get_stats(self) -> Dict[str, Any]:
        """Детальная статистика движка"""
        categories = {}
        for faq in self.faq_data:
            cat = faq.category
            categories[cat] = categories.get(cat, 0) + 1
        
        cache_hit_rate = 0.0
        if self.stats['total_searches'] > 0:
            cache_hit_rate = (self.stats['cache_hits'] / self.stats['total_searches']) * 100
        
        return {
            'faq_count': len(self.faq_data),
            'categories': len(categories),
            'category_list': sorted(categories.keys()),
            'category_counts': categories,
            'cache_size': len(self.cache),
            'max_cache_size': self.max_cache_size,
            'total_searches': self.stats['total_searches'],
            'cache_hits': self.stats['cache_hits'],
            'cache_misses': self.stats['cache_misses'],
            'cache_hit_rate': round(cache_hit_rate, 2),
            'loaded_from': self.stats['loaded_from']
        }

    def get_faq_by_id(self, faq_id: int) -> Optional[Dict]:
        """Получить запись по ID (для детального просмотра)"""
        for faq in self.faq_data:
            if faq.id == faq_id:
                return {
                    'id': faq.id,
                    'question': faq.question,
                    'answer': faq.answer,
                    'category': faq.category,
                    'keywords': faq.keywords
                }
        return None

# Для обратной совместимости с bot.py
EnhancedSearchEngine = SearchEngine
