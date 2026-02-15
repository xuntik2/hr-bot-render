# stats.py
import io
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
from openpyxl import Workbook
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

class BotStatistics:
    def __init__(self):
        self.start_time = datetime.now()
        self.user_stats = defaultdict(lambda: {
            'messages': 0,
            'commands': 0,
            'searches': 0,
            'last_active': None,
            'first_seen': None,
            'feedback_count': 0,
            'ratings_given': 0,
            'ratings_helpful': 0,
            'ratings_unhelpful': 0,
            'subscribed': False
        })
        self.daily_stats = defaultdict(lambda: {
            'messages': 0,
            'commands': 0,
            'searches': 0,
            'users': set(),
            'feedback': 0,
            'response_times': [],      # список float
            'ratings': {'helpful': 0, 'unhelpful': 0}
        })
        self.feedback_list = []
        self.max_feedback = 10000
        self.error_log = deque(maxlen=1000)
        self.response_times = deque(maxlen=100)   # список словарей с timestamp и response_time
        self.faq_ratings = defaultdict(lambda: {'helpful': 0, 'unhelpful': 0})
        self._weekly_html_cache = ""
        self._weekly_cache_ts = datetime.min

    # --- Новый метод очистки старых данных ---
    def cleanup_old_data(self, max_days: int = 180):
        """
        Удаляет записи из daily_stats, которые старше max_days дней.
        Рекомендуется вызывать периодически (например, раз в сутки).
        """
        cutoff = (datetime.now() - timedelta(days=max_days)).strftime("%Y-%m-%d")
        old_keys = [k for k in self.daily_stats.keys() if k < cutoff]
        for key in old_keys:
            del self.daily_stats[key]
        if old_keys:
            logger.info(f"Очищено {len(old_keys)} устаревших дней из статистики")

    def track_response_time(self, response_time: float):
        self.response_times.append({
            'timestamp': datetime.now(),
            'response_time': response_time
        })
        date_key = datetime.now().strftime("%Y-%m-%d")
        self.daily_stats[date_key]['response_times'].append(response_time)

    def get_avg_response_time(self) -> float:
        if not self.response_times:
            return 0.0
        return sum(rt['response_time'] for rt in self.response_times) / len(self.response_times)

    def get_response_time_status(self) -> Tuple[str, str]:
        avg = self.get_avg_response_time()
        if avg < 1.0:
            return "Хорошо", "green"
        elif avg < 3.0:
            return "Нормально", "yellow"
        else:
            return "Медленно", "red"

    def log_message(self, user_id: int, username: str, msg_type: str, text: str = ""):
        now = datetime.now()
        date_key = now.strftime("%Y-%m-%d")
        if self.user_stats[user_id]['first_seen'] is None:
            self.user_stats[user_id]['first_seen'] = now
        self.user_stats[user_id]['last_active'] = now

        if msg_type == 'command':
            self.user_stats[user_id]['commands'] += 1
            self.daily_stats[date_key]['commands'] += 1
        elif msg_type == 'message':
            self.user_stats[user_id]['messages'] += 1
            self.daily_stats[date_key]['messages'] += 1
        elif msg_type == 'search':
            self.user_stats[user_id]['searches'] += 1
            self.daily_stats[date_key]['searches'] += 1
        elif msg_type == 'feedback':
            self.user_stats[user_id]['feedback_count'] += 1
            self.daily_stats[date_key]['feedback'] += 1
            self.feedback_list.append({
                'user_id': user_id,
                'username': username,
                'text': text,
                'timestamp': now
            })
            if len(self.feedback_list) > self.max_feedback:
                self.feedback_list = self.feedback_list[-self.max_feedback:]
        elif msg_type == 'rating_helpful':
            self.user_stats[user_id]['ratings_given'] += 1
            self.user_stats[user_id]['ratings_helpful'] += 1
            self.daily_stats[date_key]['ratings']['helpful'] += 1
        elif msg_type == 'rating_unhelpful':
            self.user_stats[user_id]['ratings_given'] += 1
            self.user_stats[user_id]['ratings_unhelpful'] += 1
            self.daily_stats[date_key]['ratings']['unhelpful'] += 1
        elif msg_type == 'subscribe':
            self.user_stats[user_id]['subscribed'] = True
        elif msg_type == 'unsubscribe':
            self.user_stats[user_id]['subscribed'] = False

        self.daily_stats[date_key]['users'].add(user_id)

    def log_error(self, error_type: str, error_msg: str, user_id: int = None):
        self.error_log.append({
            'timestamp': datetime.now(),
            'type': error_type,
            'message': error_msg,
            'user_id': user_id
        })

    def record_rating(self, faq_id: int, is_helpful: bool):
        date_key = datetime.now().strftime("%Y-%m-%d")
        if is_helpful:
            self.faq_ratings[faq_id]['helpful'] += 1
            self.daily_stats[date_key]['ratings']['helpful'] += 1
        else:
            self.faq_ratings[faq_id]['unhelpful'] += 1
            self.daily_stats[date_key]['ratings']['unhelpful'] += 1

    def get_rating_stats(self) -> Dict[str, Any]:
        total_helpful = sum(v['helpful'] for v in self.faq_ratings.values())
        total_unhelpful = sum(v['unhelpful'] for v in self.faq_ratings.values())
        total_ratings = total_helpful + total_unhelpful
        satisfaction_rate = (total_helpful / total_ratings * 100) if total_ratings > 0 else 0
        return {
            'total_ratings': total_ratings,
            'helpful': total_helpful,
            'unhelpful': total_unhelpful,
            'satisfaction_rate': round(satisfaction_rate, 2),
            'by_faq': dict(self.faq_ratings)
        }

    def get_summary_stats(self, period: str = 'all', cache_size: int = 0) -> Dict[str, Any]:
        now = datetime.now()
        if period == 'all':
            daily_items = self.daily_stats.items()
        else:
            delta_map = {
                'day': timedelta(days=1),
                'week': timedelta(days=7),
                'month': timedelta(days=30),
                'quarter': timedelta(days=90),
                'halfyear': timedelta(days=180),
                'year': timedelta(days=365)
            }
            delta = delta_map.get(period, timedelta(days=30))
            cutoff = (now - delta).strftime("%Y-%m-%d")
            daily_items = [(d, ds) for d, ds in self.daily_stats.items() if d >= cutoff]

        total_users = set()
        total_messages = 0
        total_commands = 0
        total_searches = 0
        total_feedback = 0
        total_ratings_helpful = 0
        total_ratings_unhelpful = 0
        all_response_times = []

        for date, ds in daily_items:
            total_users.update(ds['users'])
            total_messages += ds['messages']
            total_commands += ds['commands']
            total_searches += ds['searches']
            total_feedback += ds['feedback']
            total_ratings_helpful += ds['ratings']['helpful']
            total_ratings_unhelpful += ds['ratings']['unhelpful']
            all_response_times.extend(ds['response_times'])

        avg_response_time = sum(all_response_times) / len(all_response_times) if all_response_times else 0
        active_24h = 0
        if period == 'all':
            active_24h = sum(
                1 for u in self.user_stats.values()
                if u['last_active'] and (now - u['last_active']) < timedelta(hours=24)
            )

        status, color = self.get_response_time_status()
        return {
            'period': period,
            'uptime': str(now - self.start_time),
            'start_time': self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            'total_users': len(total_users),
            'active_users_24h': active_24h if period == 'all' else 'N/A',
            'total_messages': total_messages,
            'total_commands': total_commands,
            'total_searches': total_searches,
            'total_feedback': total_feedback,
            'total_ratings_helpful': total_ratings_helpful,
            'total_ratings_unhelpful': total_ratings_unhelpful,
            'total_ratings': total_ratings_helpful + total_ratings_unhelpful,
            'avg_response_time': avg_response_time,
            'response_time_status': status,
            'response_time_color': color,
            'cache_size': cache_size,
            'error_count': len(self.error_log)
        }

    def get_total_users(self) -> int:
        all_users = set()
        for day in self.daily_stats.values():
            all_users.update(day['users'])
        return len(all_users)

    def get_feedback_list(self, limit: int = 1000) -> List[Dict]:
        return sorted(self.feedback_list, key=lambda x: x['timestamp'], reverse=True)[:limit]

    def get_weekly_stats_html(self, ttl_seconds: int = 60) -> str:
        now = datetime.now()
        if (now - self._weekly_cache_ts).total_seconds() < ttl_seconds:
            return self._weekly_html_cache

        rows = []
        for date, ds in sorted(self.daily_stats.items(), reverse=True)[:7]:
            avg_resp = sum(ds['response_times']) / len(ds['response_times']) if ds['response_times'] else 0
            helpful = ds['ratings']['helpful']
            unhelpful = ds['ratings']['unhelpful']
            rows.append(f"""
                <tr>
                    <td>{date}</td>
                    <td>{len(ds['users'])}</td>
                    <td>{ds['messages']}</td>
                    <td>{ds['commands']}</td>
                    <td>{ds['searches']}</td>
                    <td>{avg_resp:.2f}с</td>
                    <td>{helpful}</td>
                    <td>{unhelpful}</td>
                </tr>
            """)

        self._weekly_html_cache = ''.join(rows)
        self._weekly_cache_ts = now
        return self._weekly_html_cache


# ---------- Генераторы отчётов (синхронные) ----------

def generate_feedback_report(bot_stats: BotStatistics) -> io.BytesIO:
    output = io.BytesIO()
    wb = Workbook()
    ws = wb.active
    ws.title = "Отзывы и предложения"
    headers = ["Дата", "User ID", "Имя пользователя", "Текст"]
    for col, h in enumerate(headers, 1):
        cell = ws.cell(row=1, column=col)
        cell.value = h
        cell.font = Font(bold=True)
    if bot_stats:
        for i, fb in enumerate(bot_stats.get_feedback_list(), start=2):
            ws.cell(row=i, column=1, value=fb['timestamp'].strftime("%Y-%m-%d %H:%M:%S"))
            ws.cell(row=i, column=2, value=fb['user_id'])
            ws.cell(row=i, column=3, value=fb['username'])
            ws.cell(row=i, column=4, value=fb['text'])
    for col in ws.columns:
        max_len = 0
        col_letter = get_column_letter(col[0].column)
        for cell in col:
            try:
                if cell.value and len(str(cell.value)) > max_len:
                    max_len = len(str(cell.value))
            except:
                pass
        ws.column_dimensions[col_letter].width = min(max_len + 2, 70)
    wb.save(output)
    output.seek(0)
    return output


def generate_excel_report(bot_stats: BotStatistics, subscribers: List[int], search_engine=None) -> io.BytesIO:
    """
    Полный экспорт в Excel: общая статистика, время ответа, база FAQ, пользователи, оценки.
    """
    output = io.BytesIO()
    wb = Workbook()
    stats = bot_stats.get_summary_stats() if bot_stats else {}
    rating_stats = bot_stats.get_rating_stats() if bot_stats else {}

    # Лист 1: Общая статистика
    ws1 = wb.active
    ws1.title = "Общая статистика"
    ws1['A1'] = "Статистика HR-бота Мечел"
    ws1['A1'].font = Font(bold=True, size=14)
    ws1.merge_cells('A1:D1')
    ws1['A3'] = "Показатель"; ws1['B3'] = "Значение"
    for cell in ['A3','B3']: ws1[cell].font = Font(bold=True)
    rows = [
        ("Дата экспорта", datetime.now().strftime("%Y-%m-%d %H:%M:%S")),
        ("Время работы", stats.get('uptime', 'N/A')),
        ("Запущен", stats.get('start_time', 'N/A')),
        ("Всего пользователей", stats.get('total_users', 0)),
        ("Активные (24ч)", stats.get('active_users_24h', 0)),
        ("Всего сообщений", stats.get('total_messages', 0)),
        ("Всего команд", stats.get('total_commands', 0)),
        ("Всего поисков", stats.get('total_searches', 0)),
        ("Всего отзывов/предложений", stats.get('total_feedback', 0)),
        ("Всего оценок", rating_stats.get('total_ratings', 0)),
        ("Полезных ответов", rating_stats.get('helpful', 0)),
        ("Бесполезных ответов", rating_stats.get('unhelpful', 0)),
        ("Удовлетворённость", f"{rating_stats.get('satisfaction_rate', 0)}%"),
        ("Ср. время ответа", f"{stats.get('avg_response_time', 0):.2f} сек"),
        ("Статус времени", stats.get('response_time_status', 'N/A')),
        ("Размер кэша", stats.get('cache_size', 0)),
        ("Количество ошибок", stats.get('error_count', 0)),
        ("Подписчиков", len(subscribers))
    ]
    for i, (k, v) in enumerate(rows, 4):
        ws1[f'A{i}'] = k; ws1[f'B{i}'] = v

    # Лист 2: Время ответа (история)
    ws2 = wb.create_sheet("Время ответа")
    ws2['A1'] = "История времени ответа (последние 100)"
    ws2['A1'].font = Font(bold=True, size=14)
    ws2.merge_cells('A1:C1')
    ws2['A3'] = "Время"; ws2['B3'] = "Ответ (сек)"; ws2['C3'] = "Статус"
    for c in ['A3','B3','C3']: ws2[c].font = Font(bold=True)
    if bot_stats:
        for i, rt in enumerate(bot_stats.response_times, 4):
            ws2[f'A{i}'] = rt['timestamp'].strftime("%Y-%m-%d %H:%M:%S")
            ws2[f'B{i}'] = rt['response_time']
            t = rt['response_time']
            ws2[f'C{i}'] = "Хорошо" if t < 1 else "Нормально" if t < 3 else "Медленно"

    # Лист 3: База знаний FAQ
    ws3 = wb.create_sheet("FAQ База")
    ws3['A1'] = "База знаний FAQ"
    ws3['A1'].font = Font(bold=True, size=14)
    ws3.merge_cells('A1:E1')
    headers = ["ID", "Категория", "Вопрос", "Ответ", "Ключевые слова"]
    for col, h in enumerate(headers, 1):
        cell = ws3.cell(row=3, column=col); cell.value = h; cell.font = Font(bold=True)

    if search_engine and hasattr(search_engine, 'faq_data') and search_engine.faq_data:
        row = 4
        for item in search_engine.faq_data:
            item_id = item.get('id', '')
            cat = item.get('category', 'Без категории')
            q = item.get('question', '')
            a = item.get('answer', '')
            kw = ', '.join(item.get('keywords', [])) if isinstance(item.get('keywords'), list) else str(item.get('keywords', ''))
            ws3.cell(row=row, column=1, value=item_id)
            ws3.cell(row=row, column=2, value=cat)
            ws3.cell(row=row, column=3, value=q)
            ws3.cell(row=row, column=4, value=a)
            ws3.cell(row=row, column=5, value=kw)
            row += 1
    else:
        ws3.cell(row=4, column=1, value="Поисковый движок недоступен или база знаний пуста")

    # Лист 4: Пользователи
    ws4 = wb.create_sheet("Пользователи")
    ws4['A1'] = "Статистика пользователей"
    ws4['A1'].font = Font(bold=True, size=14)
    ws4.merge_cells('A1:I1')
    headers2 = ["ID", "Сообщ", "Команд", "Поиск", "Отзывы", "Оценок", "Полезно", "Бесполезно", "Посл. активность", "Подписка"]
    for col, h in enumerate(headers2, 1):
        cell = ws4.cell(row=3, column=col); cell.value = h; cell.font = Font(bold=True)
    if bot_stats:
        subs_set = set(subscribers)
        for i, (uid, udata) in enumerate(bot_stats.user_stats.items(), 4):
            ws4.cell(row=i, column=1, value=uid)
            ws4.cell(row=i, column=2, value=udata['messages'])
            ws4.cell(row=i, column=3, value=udata['commands'])
            ws4.cell(row=i, column=4, value=udata['searches'])
            ws4.cell(row=i, column=5, value=udata['feedback_count'])
            ws4.cell(row=i, column=6, value=udata['ratings_given'])
            ws4.cell(row=i, column=7, value=udata['ratings_helpful'])
            ws4.cell(row=i, column=8, value=udata['ratings_unhelpful'])
            last = udata['last_active']
            ws4.cell(row=i, column=9, value=last.strftime("%Y-%m-%d %H:%M:%S") if last else '')
            ws4.cell(row=i, column=10, value="Да" if uid in subs_set else "Нет")

    # Лист 5: Оценки FAQ
    ws5 = wb.create_sheet("Оценки FAQ")
    ws5['A1'] = "Статистика оценок по вопросам"
    ws5['A1'].font = Font(bold=True, size=14)
    ws5.merge_cells('A1:D1')
    headers3 = ["ID вопроса", "Вопрос", "👍 Помог", "👎 Нет", "Всего оценок"]
    for col, h in enumerate(headers3, 1):
        cell = ws5.cell(row=3, column=col); cell.value = h; cell.font = Font(bold=True)
    if bot_stats:
        row = 4
        question_map = {}
        if search_engine and hasattr(search_engine, 'faq_data'):
            for item in search_engine.faq_data:
                qid = item.get('id')
                if qid:
                    question_map[qid] = item.get('question', '')
        for faq_id, ratings in bot_stats.faq_ratings.items():
            ws5.cell(row=row, column=1, value=faq_id)
            ws5.cell(row=row, column=2, value=question_map.get(faq_id, 'Неизвестный вопрос'))
            ws5.cell(row=row, column=3, value=ratings['helpful'])
            ws5.cell(row=row, column=4, value=ratings['unhelpful'])
            ws5.cell(row=row, column=5, value=ratings['helpful'] + ratings['unhelpful'])
            row += 1

    for ws in [ws1, ws2, ws3, ws4, ws5]:
        for col in ws.columns:
            max_len = 0
            col_letter = get_column_letter(col[0].column)
            for cell in col:
                try:
                    if cell.value and len(str(cell.value)) > max_len:
                        max_len = len(str(cell.value))
                except:
                    pass
            ws.column_dimensions[col_letter].width = min(max_len + 2, 70)

    wb.save(output)
    output.seek(0)
    return output
