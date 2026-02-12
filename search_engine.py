"""
ПОИСКОВЫЙ ДВИЖОК ДЛЯ HR-БОТА МЕЧЕЛ
Версия 4.5 — оптимизированный поиск (быстрая предфильтрация + Левенштейн),
предложения по исправлению запроса.
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

# ------------------------------------------------------------
#  ФУНКЦИЯ ЛЕВЕНШТЕЙНА
# ------------------------------------------------------------
def levenshtein_distance(s1: str, s2: str) -> int:
    """Вычисляет расстояние Левенштейна между двумя строками."""
    if len(s1) < len(s2):
        return levenshtein_distance(s2, s1)
    if len(s2) == 0:
        return len(s1)
    previous_row = range(len(s2) + 1)
    for i, c1 in enumerate(s1):
        current_row = [i + 1]
        for j, c2 in enumerate(s2):
            insertions = previous_row[j + 1] + 1
            deletions = current_row[j] + 1
            substitutions = previous_row[j] + (c1 != c2)
            current_row.append(min(insertions, deletions, substitutions))
        previous_row = current_row
    return previous_row[-1]

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
    """
    Поисковый движок с оптимизированным нечётким поиском:
    1. Быстрая предварительная фильтрация (без Левенштейна).
    2. Только для топ-10 кандидатов — расчёт полной релевантности с Левенштейном.
    3. Функция предложения исправлений для запросов без результатов.
    """
    
    STOP_WORDS = {
        'как', 'что', 'где', 'когда', 'почему', 'зачем', 'сколько', 'чей',
        'а', 'и', 'но', 'или', 'если', 'то', 'же', 'бы', 'в', 'на', 'с', 'по',
        'о', 'об', 'от', 'до', 'для', 'из', 'у', 'не', 'нет', 'да', 'это',
        'тот', 'этот', 'такой', 'какой', 'все', 'всё', 'его', 'ее', 'их',
        'можно', 'нужно', 'надо', 'будет', 'есть', 'быть', 'весь', 'эта', 'эти'
    }
    
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
        
        self.stats = {
            'total_searches': 0,
            'cache_hits': 0,
            'cache_misses': 0,
            'loaded_from': 'не загружено'
        }
        
        self._load_faq()
        self._build_category_index()
        logger.info(f"✅ SearchEngine v4.5: загружено {len(self.faq_data)} записей, "
                   f"источник: {self.stats['loaded_from']}, оптимизированный нечёткий поиск")

    # ------------------------------------------------------------
    #  ЗАГРУЗКА ДАННЫХ
    # ------------------------------------------------------------
    def _load_faq(self):
        if self._load_from_json():
            return
        logger.warning("⚠️ Не удалось загрузить faq.json, используются встроенные резервные вопросы")
        self._load_fallback()

    def _load_from_json(self) -> bool:
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

    def _build_category_index(self):
        self._category_index.clear()
        for faq in self.faq_data:
            cat_lower = faq.category.lower()
            self._category_index[cat_lower].append(faq)

    # ------------------------------------------------------------
    #  НОРМАЛИЗАЦИЯ ТЕКСТА
    # ------------------------------------------------------------
    def _normalize_text(self, text: str) -> str:
        if not text:
            return ""
        
        text = text.lower().strip()
        for orig, repl in self.SYNONYMS.items():
            text = re.sub(r'\b' + re.escape(orig) + r'\b', repl, text)
        
        text = re.sub(r'[^\w\s]', ' ', text)
        words = text.split()
        words = [w for w in words if w not in self.STOP_WORDS and len(w) > 2]
        
        normalized = []
        for w in words:
            if w.endswith('ться'): w = w[:-4] + 'ть'
            elif w.endswith('тся'): w = w[:-3] + 'ться'
            elif w.endswith('ать') and len(w) > 4: w = w[:-3]
            elif w.endswith('ять') and len(w) > 4: w = w[:-3]
            elif w.endswith('ить') and len(w) > 4: w = w[:-3]
            elif w.endswith('еть') and len(w) > 4: w = w[:-3]
            elif w.endswith('ый') or w.endswith('ий') or w.endswith('ой'): w = w[:-2]
            elif w.endswith('ая') or w.endswith('яя'): w = w[:-2]
            elif w.endswith('ое') or w.endswith('ее'): w = w[:-2]
            elif w.endswith('ам') or w.endswith('ям'): w = w[:-2]
            elif w.endswith('ами') or w.endswith('ями'): w = w[:-3]
            elif w.endswith('ах') or w.endswith('ях'): w = w[:-2]
            elif w.endswith('ов') or w.endswith('ев'): w = w[:-2]
            elif w.endswith('ей'): w = w[:-2]
            normalized.append(w)
        
        return ' '.join(normalized)

    # ------------------------------------------------------------
    #  БЫСТРАЯ ПРЕДВАРИТЕЛЬНАЯ ФИЛЬТРАЦИЯ (БЕЗ ЛЕВЕНШТЕЙНА)
    # ------------------------------------------------------------
    def _quick_match(self, norm_query: str, faq: FAQEntry) -> bool:
        """
        Быстрая проверка: есть ли хотя бы одно совпадение слов запроса
        с нормализованным вопросом или ключевыми словами.
        """
        if not norm_query:
            return False
        q_words = set(norm_query.split())
        # Проверка пересечения с вопросом
        if faq.norm_question:
            q_words_question = set(faq.norm_question.split())
            if q_words.intersection(q_words_question):
                return True
        # Проверка пересечения с ключевыми словами
        if faq.norm_keywords:
            q_words_keywords = set(faq.norm_keywords.split())
            if q_words.intersection(q_words_keywords):
                return True
        return False

    # ------------------------------------------------------------
    #  ПОЛНЫЙ РАСЧЁТ РЕЛЕВАНТНОСТИ (С ЛЕВЕНШТЕЙНОМ)
    # ------------------------------------------------------------
    def _calculate_full_score(self, norm_query: str, query_words: set, faq: FAQEntry) -> float:
        """Полный расчёт релевантности с использованием Левенштейна."""
        score = 0.0

        # 1. Точное совпадение нормализованного вопроса
        if norm_query == faq.norm_question:
            return 100.0

        # 2. Запрос является подстрокой нормализованного вопроса
        if norm_query in faq.norm_question:
            score += 50.0

        # 3. Нечёткое сравнение (Левенштейн)
        if len(norm_query) >= 4 and faq.norm_question:
            lev_dist = levenshtein_distance(norm_query, faq.norm_question)
            if lev_dist == 0:
                return 100.0
            elif lev_dist <= 2:
                score += 40.0
            elif lev_dist <= 4:
                score += 20.0
            if faq.norm_keywords:
                kw_lev = levenshtein_distance(norm_query, faq.norm_keywords[:len(norm_query)+5])
                if kw_lev <= 2:
                    score += 30.0

        # 4. Совпадение по словам в вопросе
        q_words = set(faq.norm_question.split()) if faq.norm_question else set()
        common_q = query_words.intersection(q_words)
        score += len(common_q) * 12.0

        # 5. Совпадение по ключевым словам
        if faq.norm_keywords:
            kw_words = set(faq.norm_keywords.split())
            common_kw = query_words.intersection(kw_words)
            score += len(common_kw) * 20.0

        # 6. Частичное совпадение отдельных слов
        for word in query_words:
            if len(word) > 3:
                if word in faq.norm_question:
                    score += 3.0
                if faq.norm_keywords and word in faq.norm_keywords:
                    score += 5.0

        return score

    # ------------------------------------------------------------
    #  ОСНОВНОЙ ПОИСК (ОПТИМИЗИРОВАННЫЙ)
    # ------------------------------------------------------------
    def search(self, query: str, category: Optional[str] = None, top_k: int = 5) -> List[Tuple[str, str, float]]:
        if not query or len(query.strip()) < 2:
            return []
        if not self.faq_data:
            logger.warning("⚠️ Поиск при пустой базе знаний")
            return []
        
        norm_query = self._normalize_text(query)
        if not norm_query:
            return []
        
        cache_key = f"{norm_query}_{category}_{top_k}"
        cache_key_hash = hashlib.md5(cache_key.encode()).hexdigest()[:16]
        
        if cache_key_hash in self.cache:
            expiry = self.cache_ttl.get(cache_key_hash)
            if expiry and datetime.now() < expiry:
                self.stats['cache_hits'] += 1
                self.stats['total_searches'] += 1
                self.cache.move_to_end(cache_key_hash)
                return self.cache[cache_key_hash]
        
        self.stats['total_searches'] += 1
        self.stats['cache_misses'] += 1
        
        # Фильтрация по категории (неточное совпадение)
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
        
        query_words = set(norm_query.split())
        
        # --- ЭТАП 1: БЫСТРАЯ ПРЕДВАРИТЕЛЬНАЯ ФИЛЬТРАЦИЯ ---
        preliminary = []
        for faq in faq_list:
            if self._quick_match(norm_query, faq):
                preliminary.append(faq)
        
        # Если предварительных кандидатов нет, используем первые 20 из faq_list
        if not preliminary:
            preliminary = faq_list[:20]
        
        # --- ЭТАП 2: ПОЛНЫЙ РАСЧЁТ ДЛЯ ТОП-10 КАНДИДАТОВ ---
        # Сначала наберём базовые очки без Левенштейна (только совпадение слов)
        candidates_with_score = []
        for faq in preliminary[:20]:  # лимит 20 для безопасности
            # быстрая оценка только по словам (без Левенштейна)
            base_score = 0.0
            q_words = set(faq.norm_question.split()) if faq.norm_question else set()
            common_q = query_words.intersection(q_words)
            base_score += len(common_q) * 12.0
            if faq.norm_keywords:
                kw_words = set(faq.norm_keywords.split())
                common_kw = query_words.intersection(kw_words)
                base_score += len(common_kw) * 20.0
            candidates_with_score.append((faq, base_score))
        
        # Сортируем по базовой оценке и берём топ-10
        candidates_with_score.sort(key=lambda x: x[1], reverse=True)
        top_candidates = [faq for faq, _ in candidates_with_score[:10]]
        
        # Теперь вычисляем полную оценку (с Левенштейном) для топ-10
        results = []
        for faq in top_candidates:
            score = self._calculate_full_score(norm_query, query_words, faq)
            if score > 0:
                results.append((faq.question, faq.answer, min(score, 100.0)))
        
        # Сортируем по итоговой релевантности
        results.sort(key=lambda x: x[2], reverse=True)
        top_results = results[:top_k]
        
        # Сохраняем в кэш
        if top_results:
            if len(self.cache) >= self.max_cache_size:
                oldest = next(iter(self.cache))
                del self.cache[oldest]
                del self.cache_ttl[oldest]
            self.cache[cache_key_hash] = top_results
            self.cache_ttl[cache_key_hash] = datetime.now() + timedelta(hours=1)
        
        return top_results

    # ------------------------------------------------------------
    #  ПРЕДЛОЖЕНИЯ ПО ИСПРАВЛЕНИЮ ЗАПРОСА
    # ------------------------------------------------------------
    def suggest_correction(self, query: str, top_k: int = 3) -> List[str]:
        """
        Возвращает список вопросов, наиболее близких к запросу по расстоянию Левенштейна.
        Используется, когда поиск не дал результатов.
        """
        if not query or not self.faq_data:
            return []
        
        norm_query = self._normalize_text(query)
        if not norm_query or len(norm_query) < 3:
            return []
        
        candidates = []
        for faq in self.faq_data[:50]:  # ограничим первыми 50 для производительности
            if faq.norm_question:
                dist = levenshtein_distance(norm_query, faq.norm_question)
                if dist <= 5:  # только достаточно близкие
                    candidates.append((faq.question, dist))
        
        candidates.sort(key=lambda x: x[1])
        return [q for q, _ in candidates[:top_k]]

    # ------------------------------------------------------------
    #  УПРАВЛЕНИЕ И СТАТИСТИКА
    # ------------------------------------------------------------
    def refresh_data(self):
        self._load_faq()
        self._build_category_index()
        self.cache.clear()
        self.cache_ttl.clear()
        logger.info("🔄 Данные перезагружены, кэш сброшен")

    def get_stats(self) -> Dict[str, Any]:
        categories = {}
        for faq in self.faq_data:
            categories[faq.category] = categories.get(faq.category, 0) + 1
        
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

# Для обратной совместимости
EnhancedSearchEngine = SearchEngine
