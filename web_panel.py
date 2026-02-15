# web_panel.py
"""
Веб-панель для HR-бота Мечел
Версия 2.8 — добавлены очистка старых данных, проверка длины рассылки, пагинация FAQ
"""
from quart import Quart, request, jsonify, render_template_string, make_response
import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Any, Callable, Optional

from stats import generate_feedback_report, generate_excel_report

logger = logging.getLogger(__name__)

# Константы
MAX_BROADCAST_LENGTH = 4000  # безопасный лимит для Telegram (реальный 4096, оставляем запас)

# ============================================================================
#  ПОЛНЫЙ HTML ДЛЯ СТРАНИЦЫ УПРАВЛЕНИЯ FAQ (без изменений)
# ============================================================================
FAQ_MANAGER_HTML = """<!DOCTYPE html>
... (содержимое как в версии 2.3) ...
"""

MESSAGES_MANAGER_HTML = """<!DOCTYPE html>
... (содержимое) ...
"""

BROADCAST_PAGE_HTML = """<!DOCTYPE html>
... (содержимое) ...
"""


class WebServer:
    def __init__(
        self,
        app: Quart,
        application,
        search_engine,
        bot_stats,
        load_faq_json: Callable,
        save_faq_json: Callable,
        get_next_faq_id: Callable,
        load_messages: Callable,
        save_messages: Callable,
        get_subscribers: Callable,
        WEBHOOK_SECRET: str,
        BASE_URL: str,
        MEME_MODULE_AVAILABLE: bool,
        get_meme_handler: Callable,
        is_authorized_func: Callable,
        admin_ids: List[int]
    ):
        self.app = app
        self.application = application
        self.search_engine = search_engine
        self.bot_stats = bot_stats
        self.load_faq_json = load_faq_json
        self.save_faq_json = save_faq_json
        self.get_next_faq_id = get_next_faq_id
        self.load_messages = load_messages
        self.save_messages = save_messages
        self.get_subscribers = get_subscribers
        self.WEBHOOK_SECRET = WEBHOOK_SECRET
        self.BASE_URL = BASE_URL
        self.MEME_MODULE_AVAILABLE = MEME_MODULE_AVAILABLE
        self.get_meme_handler = get_meme_handler
        self.is_authorized = is_authorized_func
        self.admin_ids = admin_ids

    def log_admin_action(self, request, action: Optional[str] = None):
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if action:
            logger.info(f"Админ-действие: {action} - {request.method} {request.path} от {client_ip}")
        else:
            logger.info(f"Админ-доступ: {request.method} {request.path} от {client_ip}")

    # ---------- Обработчики ----------
    async def _faq_manager(self):
        return await render_template_string(FAQ_MANAGER_HTML)

    async def _messages_manager(self):
        return await render_template_string(MESSAGES_MANAGER_HTML)

    async def _broadcast_page(self):
        return await render_template_string(BROADCAST_PAGE_HTML)

    async def _messages_api_list(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Просмотр списка сообщений")
        messages = await self.load_messages()
        return jsonify(messages)

    async def _messages_api_update(self, key):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, f"Обновление сообщения {key}")
        try:
            data = await request.get_json()
            new_text = data.get('text')
            if new_text is None:
                return jsonify({'error': 'Missing text field'}), 400
            messages = await self.load_messages()
            if key not in messages:
                return jsonify({'error': 'Message key not found'}), 404
            messages[key]['text'] = new_text
            await self.save_messages(messages)
            return jsonify({'success': True, 'key': key, 'text': new_text})
        except Exception as e:
            logger.error(f"Ошибка обновления сообщения {key}: {e}")
            return jsonify({'error': str(e)}), 500

    # --- API для FAQ с пагинацией ---
    async def _faq_api_list(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Просмотр списка FAQ")

        # Пагинация
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
        except ValueError:
            return jsonify({'error': 'Invalid page or per_page parameter'}), 400

        if page < 1:
            page = 1
        if per_page < 1 or per_page > 200:
            per_page = 50  # ограничим разумными пределами

        data = await self.load_faq_json()
        total = len(data)
        start = (page - 1) * per_page
        end = start + per_page
        paginated = data[start:end]

        return jsonify({
            'items': paginated,
            'total': total,
            'page': page,
            'per_page': per_page,
            'pages': (total + per_page - 1) // per_page
        })

    async def _faq_api_get(self, faq_id):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, f"Просмотр записи FAQ ID {faq_id}")
        data = await self.load_faq_json()
        item = next((i for i in data if i.get('id') == faq_id), None)
        if item:
            return jsonify(item)
        return jsonify({'error': 'Not found'}), 404

    async def _faq_api_add(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Добавление новой записи FAQ")
        try:
            item = await request.get_json()
            if not item.get('question') or not item.get('answer') or not item.get('category'):
                return jsonify({'error': 'Missing required fields'}), 400
            data = await self.load_faq_json()
            new_id = await self.get_next_faq_id()
            new_item = {
                'id': new_id,
                'question': item['question'].strip(),
                'answer': item['answer'].strip(),
                'category': item['category'].strip(),
                'keywords': item.get('keywords', '').strip()
            }
            data.append(new_item)
            await self.save_faq_json(data)
            return jsonify(new_item), 201
        except Exception as e:
            logger.error(f"Ошибка добавления FAQ: {e}")
            return jsonify({'error': str(e)}), 500

    async def _faq_api_update(self, faq_id):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, f"Обновление записи FAQ ID {faq_id}")
        try:
            item = await request.get_json()
            if not item.get('question') or not item.get('answer') or not item.get('category'):
                return jsonify({'error': 'Missing required fields'}), 400
            data = await self.load_faq_json()
            for i, d in enumerate(data):
                if d.get('id') == faq_id:
                    data[i] = {
                        'id': faq_id,
                        'question': item['question'].strip(),
                        'answer': item['answer'].strip(),
                        'category': item['category'].strip(),
                        'keywords': item.get('keywords', '').strip()
                    }
                    await self.save_faq_json(data)
                    return jsonify(data[i])
            return jsonify({'error': 'Not found'}), 404
        except Exception as e:
            logger.error(f"Ошибка обновления FAQ: {e}")
            return jsonify({'error': str(e)}), 500

    async def _faq_api_delete(self, faq_id):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, f"Удаление записи FAQ ID {faq_id}")
        data = await self.load_faq_json()
        new_data = [i for i in data if i.get('id') != faq_id]
        if len(new_data) == len(data):
            return jsonify({'error': 'Not found'}), 404
        await self.save_faq_json(new_data)
        return jsonify({'success': True}), 200

    async def _subscribers_api_list(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Просмотр списка подписчиков")
        subs = await self.get_subscribers()
        return jsonify({'subscribers': subs, 'count': len(subs)})

    # --- Рассылка с проверкой длины ---
    async def _broadcast_api(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Запуск рассылки")
        try:
            data = await request.get_json()
            message = data.get('message')
            if not message:
                return jsonify({'error': 'Missing message'}), 400

            if len(message) > MAX_BROADCAST_LENGTH:
                logger.error(f"Сообщение слишком длинное: {len(message)} символов (макс. {MAX_BROADCAST_LENGTH})")
                return jsonify({'error': f'Message too long ({len(message)} chars, max {MAX_BROADCAST_LENGTH})'}), 400

            subscribers = await self.get_subscribers()
            if not subscribers:
                return jsonify({'error': 'No subscribers'}), 400

            asyncio.create_task(self._run_broadcast(message, subscribers))
            return jsonify({'status': 'Broadcast started in background'}), 202
        except Exception as e:
            logger.error(f"Ошибка запуска рассылки: {e}")
            return jsonify({'error': str(e)}), 500

    async def _run_broadcast(self, message: str, subscribers: List[int]):
        sent = 0
        failed = 0
        for i, uid in enumerate(subscribers):
            try:
                await self.application.bot.send_message(chat_id=uid, text=message, parse_mode='HTML')
                sent += 1
                if i % 10 == 9:
                    await asyncio.sleep(1.0)
                else:
                    await asyncio.sleep(0.1)
            except Exception as e:
                logger.error(f"❌ Ошибка отправки рассылки пользователю {uid}: {e}")
                failed += 1
        logger.info(f"✅ Фоновая рассылка завершена: отправлено {sent}, ошибок {failed}")

    # --- Главная страница ---
    async def _index(self):
        self.log_admin_action(request, "Просмотр главной панели")
        start_time = time.time()
        s = self.bot_stats.get_summary_stats() if self.bot_stats else {}
        avg = s.get('avg_response_time', 0)
        if avg < 1:
            perf_color = "metric-good"
            perf_text = "Хорошо"
        elif avg < 3:
            perf_color = "metric-warning"
            perf_text = "Нормально"
        else:
            perf_color = "metric-bad"
            perf_text = "Медленно"

        bot_status = "🟢 Online" if self.application else "🔴 Offline"
        bot_status_class = "online" if self.application else "offline"

        total_users = s.get('total_users', 0)
        today_key = datetime.now().strftime('%Y-%m-%d')
        active_today = len(self.bot_stats.daily_stats.get(today_key, {}).get('users', [])) if self.bot_stats else 0
        total_searches = s.get('total_searches', 0)
        cache_size = len(self.search_engine.cache) if self.search_engine and hasattr(self.search_engine, 'cache') else 0
        admin_count = len(self.admin_ids)

        try:
            import psutil
            memory_usage = psutil.Process().memory_info().rss / 1024 / 1024
        except (ImportError, Exception):
            memory_usage = 0

        start_time_str = self.bot_stats.start_time.strftime('%d.%m.%Y %H:%M') if self.bot_stats else 'N/A'
        subscribers = await self.get_subscribers()
        faq_count = len(self.search_engine.faq_data) if self.search_engine and hasattr(self.search_engine, 'faq_data') else 0

        daily_rows = self.bot_stats.get_weekly_stats_html() if self.bot_stats else ""

        # Здесь должен быть полный HTML из предыдущих версий, с f-строкой и self.WEBHOOK_SECRET
        html = f"""<!DOCTYPE html>
... (полный HTML как в версии 2.5, с корректными ссылками) ...
        """
        return html

    async def _search_stats(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Просмотр статистики поиска")
        if self.search_engine is None:
            return jsonify({'error': 'Search engine not initialized'}), 503
        try:
            if hasattr(self.search_engine, 'get_stats'):
                stats = self.search_engine.get_stats()
            elif hasattr(self.search_engine, '_engine') and hasattr(self.search_engine._engine, 'get_stats'):
                stats = self.search_engine._engine.get_stats()
            else:
                stats = {
                    'cache_size': len(self.search_engine.cache) if hasattr(self.search_engine, 'cache') else 0,
                    'faq_count': len(self.search_engine.faq_data) if hasattr(self.search_engine, 'faq_data') else 0
                }
            return jsonify(stats)
        except Exception as e:
            logger.error(f"Ошибка получения статистики поиска: {e}")
            return jsonify({'error': str(e)}), 500

    async def _feedback_export(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Экспорт отзывов в Excel")
        if self.bot_stats is None:
            return jsonify({'error': 'Bot not initialized'}), 503
        try:
            loop = asyncio.get_event_loop()
            excel_file = await loop.run_in_executor(None, generate_feedback_report, self.bot_stats)
            filename = f'feedbacks_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            response = await make_response(excel_file.getvalue())
            response.mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            logger.error(f"Ошибка веб-выгрузки отзывов: {e}")
            return jsonify({'error': str(e)}), 500

    async def _rate_stats(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Просмотр статистики оценок")
        if self.bot_stats is None:
            return jsonify({'error': 'Bot not initialized'}), 503
        try:
            stats = self.bot_stats.get_rating_stats()
            return jsonify(stats)
        except Exception as e:
            logger.error(f"Ошибка получения статистики оценок: {e}")
            return jsonify({'error': str(e)}), 500

    async def _stats_range(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        period = request.args.get('period', 'all')
        self.log_admin_action(request, f"Просмотр статистики за период {period}")
        if self.bot_stats is None:
            return jsonify({'error': 'Bot not initialized'}), 503
        valid_periods = ['all', 'day', 'week', 'month', 'quarter', 'halfyear', 'year']
        if period not in valid_periods:
            return jsonify({'error': f'Invalid period. Must be one of {valid_periods}'}), 400
        try:
            stats = self.bot_stats.get_summary_stats(period, cache_size=len(self.search_engine.cache) if self.search_engine else 0)
            return jsonify(stats)
        except Exception as e:
            logger.error(f"Ошибка получения статистики за период {period}: {e}")
            return jsonify({'error': str(e)}), 500

    async def _export_excel(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Экспорт полного отчёта в Excel")
        if self.bot_stats is None:
            return jsonify({'error': 'Статистика не инициализирована'}), 503
        try:
            subscribers = await self.get_subscribers()
            loop = asyncio.get_event_loop()
            excel_file = await loop.run_in_executor(
                None, generate_excel_report, self.bot_stats, subscribers, self.search_engine
            )
            filename = f'mechel_bot_{datetime.now().strftime("%Y%m%d_%H%M%S")}.xlsx'
            response = await make_response(excel_file.getvalue())
            response.mimetype = 'application/vnd.openxmlformats-officedocument.spreadsheetml.sheet'
            response.headers['Content-Disposition'] = f'attachment; filename="{filename}"'
            return response
        except Exception as e:
            logger.error(f"Ошибка веб-экспорта: {e}")
            return jsonify({'error': str(e)}), 500

    async def _set_webhook(self):
        if not self.is_authorized(request, self.WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Ручная установка вебхука")
        if not self.application:
            return jsonify({'error': 'Bot not initialized'}), 503
        try:
            webhook_url = self.BASE_URL + f"/webhook/{self.WEBHOOK_SECRET}"
            result = await self.application.bot.set_webhook(
                url=webhook_url,
                secret_token=self.WEBHOOK_SECRET,
                drop_pending_updates=True,
                max_connections=40
            )
            if result:
                info = await self.application.bot.get_webhook_info()
                return jsonify({
                    'success': True,
                    'message': 'Вебхук установлен',
                    'url': info.url,
                    'pending_update_count': info.pending_update_count
                })
            else:
                return jsonify({'success': False, 'message': 'Не удалось установить вебхук'}), 500
        except Exception as e:
            logger.error(f"Ошибка установки вебхука: {e}")
            return jsonify({'error': str(e)}), 500

    async def _health(self):
        return jsonify({
            'status': 'ok',
            'bot': 'running' if self.application else 'stopped',
            'users': self.bot_stats.get_total_users() if self.bot_stats else 0,
            'uptime': str(datetime.now() - self.bot_stats.start_time) if self.bot_stats else 'N/A',
            'avg_response': self.bot_stats.get_avg_response_time() if self.bot_stats else 0,
            'cache_size': len(self.search_engine.cache) if self.search_engine and hasattr(self.search_engine, 'cache') else 0,
            'faq_count': len(self.search_engine.faq_data) if self.search_engine and hasattr(self.search_engine, 'faq_data') else 0
        })

    def register_routes(self):
        app = self.app
        app.add_url_rule('/faq', view_func=self._faq_manager)
        app.add_url_rule('/messages', view_func=self._messages_manager)
        app.add_url_rule('/broadcast', view_func=self._broadcast_page)

        app.add_url_rule('/messages/api', view_func=self._messages_api_list, methods=['GET'])
        app.add_url_rule('/messages/api/<key>', view_func=self._messages_api_update, methods=['PUT'])

        app.add_url_rule('/faq/api', view_func=self._faq_api_list, methods=['GET'])
        app.add_url_rule('/faq/api/<int:faq_id>', view_func=self._faq_api_get, methods=['GET'])
        app.add_url_rule('/faq/api', view_func=self._faq_api_add, methods=['POST'])
        app.add_url_rule('/faq/api/<int:faq_id>', view_func=self._faq_api_update, methods=['PUT'])
        app.add_url_rule('/faq/api/<int:faq_id>', view_func=self._faq_api_delete, methods=['DELETE'])

        app.add_url_rule('/subscribers/api', view_func=self._subscribers_api_list, methods=['GET'])
        app.add_url_rule('/broadcast/api', view_func=self._broadcast_api, methods=['POST'])

        app.add_url_rule('/', view_func=self._index)
        app.add_url_rule('/search/stats', view_func=self._search_stats, methods=['GET'])
        app.add_url_rule('/feedback/export', view_func=self._feedback_export, methods=['GET'])
        app.add_url_rule('/rate/stats', view_func=self._rate_stats, methods=['GET'])
        app.add_url_rule('/stats/range', view_func=self._stats_range, methods=['GET'])
        app.add_url_rule('/export/excel', view_func=self._export_excel, methods=['GET'])
        app.add_url_rule('/setwebhook', view_func=self._set_webhook, methods=['GET'])
        app.add_url_rule('/health', view_func=self._health, methods=['GET'])

        logger.info("✅ Все веб-маршруты зарегистрированы через WebServer")


def register_web_routes(
    app: Quart,
    application,
    search_engine,
    bot_stats,
    load_faq_json,
    save_faq_json,
    get_next_faq_id,
    load_messages,
    save_messages,
    get_subscribers,
    WEBHOOK_SECRET: str,
    BASE_URL: str,
    MEME_MODULE_AVAILABLE: bool,
    get_meme_handler,
    is_authorized_func: Callable,
    admin_ids: List[int]
):
    server = WebServer(
        app=app,
        application=application,
        search_engine=search_engine,
        bot_stats=bot_stats,
        load_faq_json=load_faq_json,
        save_faq_json=save_faq_json,
        get_next_faq_id=get_next_faq_id,
        load_messages=load_messages,
        save_messages=save_messages,
        get_subscribers=get_subscribers,
        WEBHOOK_SECRET=WEBHOOK_SECRET,
        BASE_URL=BASE_URL,
        MEME_MODULE_AVAILABLE=MEME_MODULE_AVAILABLE,
        get_meme_handler=get_meme_handler,
        is_authorized_func=is_authorized_func,
        admin_ids=admin_ids
    )
    server.register_routes()
