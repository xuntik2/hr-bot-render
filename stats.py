# stats.py
"""
Модуль статистики для HR-бота Мечел
Версия 2.4 – финальная
"""
import asyncio  # добавлено
import logging
from collections import defaultdict
from datetime import datetime, timedelta
from typing import Dict, List, Any, Tuple, Optional, Set

from database import (
    log_daily_stat,
    add_response_time,
    log_error,
    save_rating as db_save_rating,
    get_recent_response_times,
    get_daily_stats_for_last_days,
)

logger = logging.getLogger(__name__)

class BotStatistics:
    """
    Класс для сбора статистики с агрегацией в памяти и периодической записью в БД.
    Буфер ограничен 7 днями, сброс каждые 60 секунд.
    """

    def __init__(self, flush_interval: int = 60, max_buffer_days: int = 7):
        self.start_time = datetime.now()
        self.flush_interval = flush_interval
        self.max_buffer_days = max_buffer_days

        # Буферы для накопления статистики (in-memory)
        self._daily_buffer = defaultdict(lambda: {
            'messages': 0,
            'commands': 0,
            'searches': 0,
            'feedback': 0,
            'ratings_helpful': 0,
            'ratings_unhelpful': 0,
        })
        self._users_buffer = defaultdict(set)  # дата -> set user_id (для оперативного доступа)
        self._users_count_buffer = defaultdict(int)  # дата -> кол-во уникальных пользователей (из БД)
        self._response_times_cache = []  # последние 100 значений (для быстрого доступа)

        # Дополнительный буфер для точного подсчёта активных за 24ч
        self._user_last_active = {}  # user_id -> datetime последней активности

        # Загружаем последние 7 дней из БД для инициализации буфера
        asyncio.create_task(self._load_recent_stats())

        # Задача для периодического сброса
        self._flush_task: Optional[asyncio.Task] = None
        asyncio.create_task(self._start_flush_loop())

    async def _load_recent_stats(self):
        """Загружает статистику за последние 7 дней из БД."""
        try:
            stats = await get_daily_stats_for_last_days(self.max_buffer_days)
            for date, data in stats.items():
                self._daily_buffer[date]['messages'] = data['messages']
                self._daily_buffer[date]['commands'] = data['commands']
                self._daily_buffer[date]['searches'] = data['searches']
                self._daily_buffer[date]['feedback'] = data['feedback']
                self._daily_buffer[date]['ratings_helpful'] = data['ratings']['helpful']
                self._daily_buffer[date]['ratings_unhelpful'] = data['ratings']['unhelpful']
                self._users_count_buffer[date] = data['users_count']
            logger.info(f"✅ Загружена статистика за {len(stats)} дней из БД")
        except Exception as e:
            logger.error(f"❌ Ошибка загрузки статистики из БД: {e}")

    async def _start_flush_loop(self):
        """Запускает цикл периодического сброса данных в БД."""
        while True:
            await asyncio.sleep(self.flush_interval)
            await self.flush()

    async def flush(self):
        """Сбрасывает накопленные данные в БД и очищает старые дни."""
        logger.debug("Сброс статистики в БД...")
        for date, counts in list(self._daily_buffer.items()):
            for field, value in counts.items():
                if value > 0:
                    await log_daily_stat(date, field, value)
            counts.clear()

        for date, users in list(self._users_buffer.items()):
            if users:
                self._users_count_buffer[date] = len(users)
                await log_daily_stat(date, 'users_count', len(users))
            users.clear()

        cutoff = (datetime.now() - timedelta(days=self.max_buffer_days)).strftime("%Y-%m-%d")
        for date in list(self._daily_buffer.keys()):
            if date < cutoff:
                del self._daily_buffer[date]
        for date in list(self._users_buffer.keys()):
            if date < cutoff:
                del self._users_buffer[date]
        for date in list(self._users_count_buffer.keys()):
            if date < cutoff:
                del self._users_count_buffer[date]

        logger.debug("Сброс статистики завершён.")

    # --- Методы логирования ---
    async def log_message(self, user_id: int, username: str, msg_type: str, text: str = ""):
        now = datetime.now()
        date_key = now.strftime("%Y-%m-%d")

        # Обновляем время последней активности
        self._user_last_active[user_id] = now

        if msg_type == 'command':
            self._daily_buffer[date_key]['commands'] += 1
        elif msg_type == 'message':
            self._daily_buffer[date_key]['messages'] += 1
        elif msg_type == 'search':
            self._daily_buffer[date_key]['searches'] += 1
        elif msg_type == 'feedback':
            self._daily_buffer[date_key]['feedback'] += 1
            from database import save_feedback
            await save_feedback(user_id, username, text)
        elif msg_type == 'rating_helpful':
            self._daily_buffer[date_key]['ratings_helpful'] += 1
        elif msg_type == 'rating_unhelpful':
            self._daily_buffer[date_key]['ratings_unhelpful'] += 1

        self._users_buffer[date_key].add(user_id)

    def track_response_time(self, response_time: float):
        self._response_times_cache.append(response_time)
        if len(self._response_times_cache) > 100:
            self._response_times_cache.pop(0)
        asyncio.create_task(add_response_time(response_time))

    def get_avg_response_time(self) -> float:
        if not self._response_times_cache:
            return 0.0
        return sum(self._response_times_cache) / len(self._response_times_cache)

    def get_response_time_status(self) -> Tuple[str, str]:
        avg = self.get_avg_response_time()
        if avg < 1.0:
            return "Хорошо", "green"
        elif avg < 3.0:
            return "Нормально", "yellow"
        else:
            return "Медленно", "red"

    def log_error(self, error_type: str, error_msg: str, user_id: int = None):
        asyncio.create_task(log_error(error_type, error_msg, user_id))

    def record_rating(self, faq_id: int, is_helpful: bool):
        date_key = datetime.now().strftime("%Y-%m-%d")
        self._daily_buffer[date_key]['ratings_helpful' if is_helpful else 'ratings_unhelpful'] += 1
        asyncio.create_task(db_save_rating(faq_id, 0, is_helpful))

    async def get_rating_stats(self) -> Dict[str, Any]:
        from database import get_rating_stats as db_stats
        return await db_stats()

    def get_summary_stats(self, period: str = 'all', cache_size: int = 0) -> Dict[str, Any]:
        now = datetime.now()
        if period == 'all':
            total_users = sum(self._users_count_buffer.values())
            total_messages = sum(d['messages'] for d in self._daily_buffer.values())
            total_commands = sum(d['commands'] for d in self._daily_buffer.values())
            total_searches = sum(d['searches'] for d in self._daily_buffer.values())
            total_feedback = sum(d['feedback'] for d in self._daily_buffer.values())
            total_ratings_helpful = sum(d['ratings_helpful'] for d in self._daily_buffer.values())
            total_ratings_unhelpful = sum(d['ratings_unhelpful'] for d in self._daily_buffer.values())
            all_response_times = self._response_times_cache
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
            cutoff = (now - delta).date()
            total_users = 0
            total_messages = total_commands = total_searches = total_feedback = 0
            total_ratings_helpful = total_ratings_unhelpful = 0

            for date_str, users_cnt in self._users_count_buffer.items():
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if d >= cutoff:
                        total_users += users_cnt
                except:
                    continue

            for date_str, counts in self._daily_buffer.items():
                try:
                    d = datetime.strptime(date_str, "%Y-%m-%d").date()
                    if d >= cutoff:
                        total_messages += counts['messages']
                        total_commands += counts['commands']
                        total_searches += counts['searches']
                        total_feedback += counts['feedback']
                        total_ratings_helpful += counts['ratings_helpful']
                        total_ratings_unhelpful += counts['ratings_unhelpful']
                except:
                    continue

            all_response_times = self._response_times_cache

        avg_response_time = sum(all_response_times) / len(all_response_times) if all_response_times else 0
        status, color = self.get_response_time_status()

        # Подсчёт активных за 24 часа
        cutoff_24h = now - timedelta(hours=24)
        active_24h = sum(1 for last_active in self._user_last_active.values() if last_active >= cutoff_24h)

        return {
            'period': period,
            'uptime': str(now - self.start_time),
            'start_time': self.start_time.strftime("%Y-%m-%d %H:%M:%S"),
            'total_users': total_users,
            'active_users_24h': active_24h,
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
            'error_count': 0
        }

    def get_total_users(self) -> int:
        return sum(self._users_count_buffer.values())

    def get_weekly_stats_html(self) -> str:
        rows = []
        sorted_dates = sorted(self._daily_buffer.keys(), reverse=True)[:7]
        for date in sorted_dates:
            counts = self._daily_buffer[date]
            users = self._users_buffer[date]
            rows.append(f"""
                <tr>
                    <td>{date}</td>
                    <td>{len(users)}</td>
                    <td>{counts['messages']}</td>
                    <td>{counts['commands']}</td>
                    <td>{counts['searches']}</td>
                    <td>0.00с</td>
                    <td>{counts['ratings_helpful']}</td>
                    <td>{counts['ratings_unhelpful']}</td>
                </tr>
            """)
        return ''.join(rows)

    async def shutdown(self):
        await self.flush()
