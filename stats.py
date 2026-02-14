# stats.py
import io
import logging
from collections import defaultdict, deque
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional
from openpyxl import Workbook  # ✅ Единственный импорт
from openpyxl.styles import Font
from openpyxl.utils import get_column_letter

logger = logging.getLogger(__name__)

class BotStatistics:
    def __init__(self, max_history_days: int = 90):
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
            'response_times': [],
            'ratings': {'helpful': 0, 'unhelpful': 0}
        })
        self.feedback_list = []
        self.max_feedback = 10000
        self.error_log = deque(maxlen=1000)
        self.response_times = deque(maxlen=100)
        self.faq_ratings = defaultdict(lambda: {'helpful': 0, 'unhelpful': 0})

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
            'cache_size': cache_size,  # ✅ Теперь точный!
            'error_count': len(self.error_log)
        }

    def get_total_users(self) -> int:
        all_users = set()
        for day in self.daily_stats.values():
            all_users.update(day['users'])
        return len(all_users)

    def get_feedback_list(self, limit: int = 1000) -> List[Dict]:
        return sorted(self.feedback_list, key=lambda x: x['timestamp'], reverse=True)[:limit]

def generate_feedback_report(bot_stats) -> io.BytesIO:
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

async def generate_excel_report(bot_stats, subscribers: List[int]) -> io.BytesIO:
    """
    Исправлено: search_engine больше не передаётся.
    Исправлено: удалён лишний импорт Workbook.
    """
    output = io.BytesIO()
    wb = Workbook()  # ✅ Используется глобальный импорт
    stats = bot_stats.get_summary_stats(cache_size=0)  # cache_size не нужен здесь
    rating_stats = bot_stats.get_rating_stats() if bot_stats else {}

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

    for ws in [ws1]:
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