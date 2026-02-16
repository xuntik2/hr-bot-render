# web_panel.py
"""
Веб-панель для HR-бота Мечел
Версия 2.17 – рейт-лимитинг очистки, улучшенный refreshStats, эндпоинт /stats/rows
"""
import json
import asyncio
import logging
import time
from datetime import datetime
from typing import List, Dict, Any, Callable, Optional

from quart import Quart, request, jsonify, render_template_string, make_response

from stats import generate_feedback_report, generate_excel_report
from database import (
    get_faq_by_id, add_faq, update_faq, delete_faq,
    cleanup_old_errors, cleanup_old_feedback,
    load_all_faq,
    get_total_rows_count
)

logger = logging.getLogger(__name__)

MAX_BROADCAST_LENGTH = 4000

# ============================================================================
#  ПОЛНЫЙ HTML ДЛЯ СТРАНИЦЫ УПРАВЛЕНИЯ FAQ
# ============================================================================
FAQ_MANAGER_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Управление FAQ — HR Бот Мечел</title>
    <style>
        body { font-family: 'Segoe UI', Tahoma, Geneva, Verdana, sans-serif; margin: 0; padding: 20px; background: #f5f7fa; }
        .container { max-width: 1200px; margin: 0 auto; background: white; border-radius: 12px; box-shadow: 0 10px 25px rgba(0,0,0,0.1); padding: 25px; }
        h1 { color: #0B1C2F; margin-top: 0; border-bottom: 2px solid #e0e0e0; padding-bottom: 15px; }
        .warning { background: #fff3cd; border: 1px solid #ffeeba; color: #856404; padding: 12px 20px; border-radius: 8px; margin-bottom: 25px; font-weight: 500; }
        .auth-form { background: #e9ecef; padding: 20px; border-radius: 10px; margin-bottom: 25px; display: flex; gap: 10px; align-items: center; }
        .auth-form input { flex: 1; padding: 10px; border: 1px solid #ccc; border-radius: 6px; font-size: 16px; }
        .auth-form button { background: #0B1C2F; color: white; border: none; padding: 10px 20px; border-radius: 6px; cursor: pointer; font-size: 16px; }
        .auth-form button:hover { background: #1a2b3f; }
        .error { color: #dc3545; font-weight: 500; margin: 10px 0; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th { background: #0B1C2F; color: white; padding: 12px; text-align: left; font-weight: 500; }
        td { padding: 12px; border-bottom: 1px solid #e0e0e0; vertical-align: top; }
        tr:hover { background: #f8f9fa; }
        .btn { padding: 6px 12px; border: none; border-radius: 4px; cursor: pointer; font-size: 14px; margin: 0 2px; }
        .btn-edit { background: #007bff; color: white; }
        .btn-delete { background: #dc3545; color: white; }
        .btn-add { background: #28a745; color: white; padding: 10px 20px; font-size: 16px; margin-bottom: 20px; }
        .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); }
        .modal-content { background: white; margin: 10% auto; padding: 25px; border-radius: 10px; max-width: 600px; box-shadow: 0 5px 20px rgba(0,0,0,0.3); }
        .modal h2 { margin-top: 0; }
        .modal input, .modal textarea, .modal select { width: 100%; padding: 8px; margin: 8px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        .modal button { margin-top: 10px; }
        .pagination { margin: 20px 0; display: flex; gap: 5px; justify-content: center; }
        .pagination button { padding: 5px 10px; border: 1px solid #ccc; background: white; cursor: pointer; }
        .pagination button.active { background: #0B1C2F; color: white; border-color: #0B1C2F; }
        .pagination button:disabled { opacity: 0.5; cursor: not-allowed; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📚 Управление базой знаний FAQ</h1>
        <div class="warning">⚠️ На бесплатном тарифе Render изменения будут потеряны при перезапуске сервиса. Рекомендуется регулярно делать бэкап.</div>

        <div class="auth-form" id="authDiv">
            <input type="password" id="secretKey" placeholder="Введите секретный ключ (WEBHOOK_SECRET)">
            <button onclick="auth()">Авторизоваться</button>
        </div>

        <div id="content" style="display: none;">
            <button class="btn btn-add" onclick="openAddModal()">➕ Добавить вопрос</button>
            <div id="errorMsg" class="error"></div>
            
            <div id="paginationTop" class="pagination"></div>
            
            <table id="faqTable">
                <thead>
                    <tr><th>ID</th><th>Вопрос</th><th>Ответ</th><th>Категория</th><th>Ключевые слова</th><th>Действия</th></tr>
                </thead>
                <tbody id="faqBody"></tbody>
            </table>
            
            <div id="paginationBottom" class="pagination"></div>
        </div>
    </div>

    <div id="modal" class="modal">
        <div class="modal-content">
            <h2 id="modalTitle">Добавить вопрос</h2>
            <input type="hidden" id="editId">
            <input type="text" id="question" placeholder="Вопрос" required>
            <textarea id="answer" placeholder="Ответ" rows="3" required></textarea>
            <input type="text" id="category" placeholder="Категория" required>
            <input type="text" id="keywords" placeholder="Ключевые слова (через запятую)">
            <button id="modalSave" class="btn btn-add">Сохранить</button>
            <button onclick="closeModal()" class="btn" style="background:#6c757d; color:white;">Отмена</button>
        </div>
    </div>

    <script>
        let apiKey = '';
        let currentPage = 1;
        let totalPages = 1;
        let perPage = 50;

        function auth() {
            apiKey = document.getElementById('secretKey').value.trim();
            if (!apiKey) { alert('Введите ключ'); return; }
            fetch('/faq/api?page=1&per_page=' + perPage, { headers: { 'X-Secret-Key': apiKey } })
                .then(r => { if (r.ok) { document.getElementById('authDiv').style.display = 'none'; document.getElementById('content').style.display = 'block'; loadFaq(1); } else { alert('Неверный ключ'); } });
        }

        function loadFaq(page) {
            currentPage = page;
            fetch(`/faq/api?page=${page}&per_page=${perPage}`, { headers: { 'X-Secret-Key': apiKey } })
                .then(r => r.json())
                .then(data => {
                    renderFaqTable(data.items);
                    totalPages = data.pages;
                    renderPagination();
                })
                .catch(err => console.error(err));
        }

        function renderFaqTable(items) {
            let tbody = document.getElementById('faqBody');
            tbody.innerHTML = '';
            items.forEach(item => {
                let tr = document.createElement('tr');
                tr.innerHTML = `
                    <td>${item.id}</td>
                    <td>${escapeHtml(item.question)}</td>
                    <td>${escapeHtml(item.answer.substring(0,50))}${item.answer.length>50?'...':''}</td>
                    <td>${escapeHtml(item.category)}</td>
                    <td>${escapeHtml(item.keywords)}</td>
                    <td>
                        <button class="btn btn-edit" onclick="editItem(${item.id})">✏️</button>
                        <button class="btn btn-delete" onclick="deleteItem(${item.id})">🗑️</button>
                    </td>
                `;
                tbody.appendChild(tr);
            });
        }

        function renderPagination() {
            let paginationHTML = '';
            if (totalPages > 1) {
                paginationHTML += `<button onclick="loadFaq(1)" ${currentPage === 1 ? 'disabled' : ''}>«</button>`;
                paginationHTML += `<button onclick="loadFaq(${currentPage-1})" ${currentPage === 1 ? 'disabled' : ''}>‹</button>`;
                let start = Math.max(1, currentPage - 2);
                let end = Math.min(totalPages, currentPage + 2);
                for (let i = start; i <= end; i++) {
                    paginationHTML += `<button onclick="loadFaq(${i})" class="${i === currentPage ? 'active' : ''}">${i}</button>`;
                }
                paginationHTML += `<button onclick="loadFaq(${currentPage+1})" ${currentPage === totalPages ? 'disabled' : ''}>›</button>`;
                paginationHTML += `<button onclick="loadFaq(${totalPages})" ${currentPage === totalPages ? 'disabled' : ''}>»</button>`;
            }
            document.getElementById('paginationTop').innerHTML = paginationHTML;
            document.getElementById('paginationBottom').innerHTML = paginationHTML;
        }

        function escapeHtml(unsafe) {
            if (!unsafe) return '';
            return unsafe.replace(/[&<>"]/g, function(m) {
                if(m === '&') return '&amp;'; if(m === '<') return '&lt;'; if(m === '>') return '&gt;'; if(m === '"') return '&quot;';
                return m;
            });
        }

        function openAddModal() {
            document.getElementById('modalTitle').innerText = 'Добавить вопрос';
            document.getElementById('editId').value = '';
            document.getElementById('question').value = '';
            document.getElementById('answer').value = '';
            document.getElementById('category').value = '';
            document.getElementById('keywords').value = '';
            document.getElementById('modal').style.display = 'block';
        }

        function closeModal() {
            document.getElementById('modal').style.display = 'none';
        }

        document.getElementById('modalSave').onclick = function() {
            let id = document.getElementById('editId').value;
            let data = {
                question: document.getElementById('question').value,
                answer: document.getElementById('answer').value,
                category: document.getElementById('category').value,
                keywords: document.getElementById('keywords').value
            };
            if (!data.question || !data.answer || !data.category) {
                alert('Заполните обязательные поля'); return;
            }
            let url = '/faq/api';
            let method = 'POST';
            if (id) { url += '/' + id; method = 'PUT'; }
            fetch(url, {
                method: method,
                headers: { 'Content-Type': 'application/json', 'X-Secret-Key': apiKey },
                body: JSON.stringify(data)
            }).then(r => { if (r.ok) { closeModal(); loadFaq(currentPage); } else { alert('Ошибка'); } });
        };

        function editItem(id) {
            fetch(`/faq/api/${id}`, { headers: { 'X-Secret-Key': apiKey } })
                .then(r => r.json())
                .then(item => {
                    document.getElementById('modalTitle').innerText = 'Редактировать вопрос';
                    document.getElementById('editId').value = item.id;
                    document.getElementById('question').value = item.question;
                    document.getElementById('answer').value = item.answer;
                    document.getElementById('category').value = item.category;
                    document.getElementById('keywords').value = item.keywords;
                    document.getElementById('modal').style.display = 'block';
                });
        }

        function deleteItem(id) {
            if (!confirm('Удалить запись?')) return;
            fetch(`/faq/api/${id}`, { method: 'DELETE', headers: { 'X-Secret-Key': apiKey } })
                .then(r => { if (r.ok) loadFaq(currentPage); else alert('Ошибка'); });
        }
    </script>
</body>
</html>"""

# ============================================================================
#  ПОЛНЫЙ HTML ДЛЯ СТРАНИЦЫ УПРАВЛЕНИЯ СООБЩЕНИЯМИ
# ============================================================================
MESSAGES_MANAGER_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Управление сообщениями — HR Бот Мечел</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #0B1C2F; }
        .auth-form { background: #e9ecef; padding: 20px; border-radius: 5px; margin-bottom: 20px; display: flex; gap: 10px; }
        .auth-form input { flex: 1; padding: 8px; }
        .auth-form button { padding: 8px 16px; background: #0B1C2F; color: white; border: none; border-radius: 4px; cursor: pointer; }
        table { width: 100%; border-collapse: collapse; margin-top: 20px; }
        th { background: #0B1C2F; color: white; padding: 10px; text-align: left; }
        td { padding: 10px; border-bottom: 1px solid #ddd; }
        .btn-edit { background: #007bff; color: white; border: none; padding: 5px 10px; border-radius: 4px; cursor: pointer; }
        .modal { display: none; position: fixed; z-index: 1000; left: 0; top: 0; width: 100%; height: 100%; background: rgba(0,0,0,0.5); }
        .modal-content { background: white; margin: 10% auto; padding: 20px; border-radius: 8px; max-width: 600px; }
        .modal textarea { width: 100%; height: 150px; margin: 10px 0; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📝 Управление текстами сообщений</h1>
        <div class="auth-form">
            <input type="password" id="secretKey" placeholder="Секретный ключ">
            <button onclick="auth()">Войти</button>
        </div>
        <div id="content" style="display:none;">
            <table id="messagesTable">
                <thead><tr><th>Ключ</th><th>Заголовок</th><th>Текст</th><th>Действия</th></tr></thead>
                <tbody id="messagesBody"></tbody>
            </table>
        </div>
    </div>
    <div id="modal" class="modal">
        <div class="modal-content">
            <h3 id="modalTitle">Редактирование сообщения</h3>
            <input type="hidden" id="editKey">
            <textarea id="editText" placeholder="Текст сообщения"></textarea>
            <button onclick="saveMessage()">Сохранить</button>
            <button onclick="closeModal()">Отмена</button>
        </div>
    </div>
    <script>
        let apiKey = '';
        function auth() {
            apiKey = document.getElementById('secretKey').value.trim();
            if (!apiKey) return alert('Введите ключ');
            fetch('/messages/api', { headers: { 'X-Secret-Key': apiKey } })
                .then(r => { if (r.ok) { document.querySelector('.auth-form').style.display = 'none'; document.getElementById('content').style.display = 'block'; loadMessages(); } else alert('Ошибка авторизации'); });
        }
        function loadMessages() {
            fetch('/messages/api', { headers: { 'X-Secret-Key': apiKey } })
                .then(r => r.json())
                .then(data => {
                    let tbody = document.getElementById('messagesBody');
                    tbody.innerHTML = '';
                    for (let key in data) {
                        let tr = document.createElement('tr');
                        tr.innerHTML = `
                            <td>${key}</td>
                            <td>${data[key].title || ''}</td>
                            <td>${(data[key].text || '').substring(0, 50)}...</td>
                            <td><button class="btn-edit" onclick="editMessage('${key}', '${data[key].text.replace(/'/g, "\\'")}')">✏️</button></td>
                        `;
                        tbody.appendChild(tr);
                    }
                });
        }
        function editMessage(key, text) {
            document.getElementById('editKey').value = key;
            document.getElementById('editText').value = text;
            document.getElementById('modal').style.display = 'block';
        }
        function closeModal() {
            document.getElementById('modal').style.display = 'none';
        }
        function saveMessage() {
            let key = document.getElementById('editKey').value;
            let text = document.getElementById('editText').value;
            fetch(`/messages/api/${key}`, {
                method: 'PUT',
                headers: { 'Content-Type': 'application/json', 'X-Secret-Key': apiKey },
                body: JSON.stringify({ text: text })
            }).then(r => { if (r.ok) { closeModal(); loadMessages(); } else alert('Ошибка'); });
        }
    </script>
</body>
</html>"""

# ============================================================================
#  ПОЛНЫЙ HTML ДЛЯ СТРАНИЦЫ РАССЫЛКИ
# ============================================================================
BROADCAST_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>Рассылка подписчикам — HR Бот Мечел</title>
    <style>
        body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
        .container { max-width: 800px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
        h1 { color: #0B1C2F; }
        .auth-form { background: #e9ecef; padding: 20px; border-radius: 5px; margin-bottom: 20px; display: flex; gap: 10px; }
        .auth-form input { flex: 1; padding: 8px; }
        .auth-form button { padding: 8px 16px; background: #0B1C2F; color: white; border: none; border-radius: 4px; cursor: pointer; }
        textarea { width: 100%; padding: 10px; margin: 10px 0; border: 1px solid #ccc; border-radius: 4px; box-sizing: border-box; }
        button { background: #28a745; color: white; border: none; padding: 10px 20px; border-radius: 4px; cursor: pointer; }
        #result { margin-top: 20px; padding: 10px; border-radius: 4px; display: none; }
        .success { background: #d4edda; color: #155724; border: 1px solid #c3e6cb; }
        .error { background: #f8d7da; color: #721c24; border: 1px solid #f5c6cb; }
    </style>
</head>
<body>
    <div class="container">
        <h1>📢 Рассылка подписчикам</h1>
        <div class="auth-form">
            <input type="password" id="secretKey" placeholder="Секретный ключ">
            <button onclick="auth()">Войти</button>
        </div>
        <div id="content" style="display:none;">
            <textarea id="messageText" rows="5" placeholder="Текст сообщения (можно использовать HTML)"></textarea>
            <button onclick="sendBroadcast()">Отправить</button>
            <div id="result"></div>
        </div>
    </div>
    <script>
        let apiKey = '';
        function auth() {
            apiKey = document.getElementById('secretKey').value.trim();
            if (!apiKey) return alert('Введите ключ');
            fetch('/subscribers/api', { headers: { 'X-Secret-Key': apiKey } })
                .then(r => { if (r.ok) { document.querySelector('.auth-form').style.display = 'none'; document.getElementById('content').style.display = 'block'; } else alert('Ошибка авторизации'); });
        }
        async function sendBroadcast() {
            let message = document.getElementById('messageText').value.trim();
            if (!message) return alert('Введите сообщение');
            document.getElementById('result').style.display = 'none';
            let response = await fetch('/broadcast/api', {
                method: 'POST',
                headers: { 'Content-Type': 'application/json', 'X-Secret-Key': apiKey },
                body: JSON.stringify({ message: message })
            });
            let result = await response.json();
            let resultDiv = document.getElementById('result');
            resultDiv.style.display = 'block';
            if (response.ok) {
                resultDiv.className = 'success';
                resultDiv.innerHTML = `✅ Отправлено: ${result.sent}, ошибок: ${result.failed}`;
            } else {
                resultDiv.className = 'error';
                resultDiv.innerHTML = `❌ Ошибка: ${result.error}`;
            }
        }
    </script>
</body>
</html>"""


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

        # Кэш для подсчёта строк (чтобы не дёргать БД слишком часто)
        self._last_rows_check = 0
        self._cached_rows_count = None

        # Для рейт-лимитинга очистки
        self._last_cleanup_time = 0

    def log_admin_action(self, request, action: Optional[str] = None):
        client_ip = request.headers.get('X-Forwarded-For', request.remote_addr)
        if action:
            logger.info(f"Админ-действие: {action} - {request.method} {request.path} от {client_ip}")
        else:
            logger.info(f"Админ-доступ: {request.method} {request.path} от {client_ip}")

    async def _check_token(self, request) -> bool:
        """Проверяет токен в заголовке, параметрах URL или в POST-форме."""
        if request.headers.get('X-Secret-Key') == self.WEBHOOK_SECRET:
            return True
        if request.args.get('key') == self.WEBHOOK_SECRET:
            return True
        if request.method == 'POST':
            form = await request.form
            if form.get('token') == self.WEBHOOK_SECRET:
                return True
        return False

    # ======================== Обработчики ========================

    # --- Страницы ---
    async def _faq_manager(self):
        return await render_template_string(FAQ_MANAGER_HTML)

    async def _messages_manager(self):
        return await render_template_string(MESSAGES_MANAGER_HTML)

    async def _broadcast_page(self):
        return await render_template_string(BROADCAST_PAGE_HTML)

    # --- API сообщений ---
    async def _messages_api_list(self):
        if not await self._check_token(request):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Просмотр списка сообщений")
        messages = await self.load_messages()
        return jsonify(messages)

    async def _messages_api_update(self, key):
        if not await self._check_token(request):
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
            await self.save_messages(key, new_text, messages[key].get('title', ''))
            return jsonify({'success': True, 'key': key, 'text': new_text})
        except Exception as e:
            logger.error(f"Ошибка обновления сообщения {key}: {e}")
            return jsonify({'error': str(e)}), 500

    # --- API FAQ ---
    async def _faq_api_list(self):
        if not await self._check_token(request):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Просмотр списка FAQ")
        try:
            page = int(request.args.get('page', 1))
            per_page = int(request.args.get('per_page', 50))
        except ValueError:
            return jsonify({'error': 'Invalid page or per_page parameter'}), 400
        if page < 1:
            page = 1
        if per_page < 1 or per_page > 200:
            per_page = 50
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
        if not await self._check_token(request):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, f"Просмотр записи FAQ ID {faq_id}")
        item = await get_faq_by_id(faq_id)
        if item:
            return jsonify(item)
        return jsonify({'error': 'Not found'}), 404

    async def _faq_api_add(self):
        if not await self._check_token(request):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Добавление новой записи FAQ")
        try:
            item = await request.get_json()
            if not item.get('question') or not item.get('answer') or not item.get('category'):
                return jsonify({'error': 'Missing required fields'}), 400
            new_id = await add_faq(
                question=item['question'].strip(),
                answer=item['answer'].strip(),
                category=item['category'].strip(),
                keywords=item.get('keywords', '').strip(),
                priority=0
            )
            new_item = {
                'id': new_id,
                'question': item['question'].strip(),
                'answer': item['answer'].strip(),
                'category': item['category'].strip(),
                'keywords': item.get('keywords', '').strip()
            }

            # После успешного добавления обновляем локальную резервную копию
            await self._update_faq_backup()

            return jsonify(new_item), 201
        except Exception as e:
            logger.error(f"Ошибка добавления FAQ: {e}")
            return jsonify({'error': str(e)}), 500

    async def _faq_api_update(self, faq_id):
        if not await self._check_token(request):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, f"Обновление записи FAQ ID {faq_id}")
        try:
            item = await request.get_json()
            if not item.get('question') or not item.get('answer') or not item.get('category'):
                return jsonify({'error': 'Missing required fields'}), 400
            await update_faq(
                faq_id=faq_id,
                question=item['question'].strip(),
                answer=item['answer'].strip(),
                category=item['category'].strip(),
                keywords=item.get('keywords', '').strip(),
                priority=0
            )

            # После успешного обновления обновляем локальную резервную копию
            await self._update_faq_backup()

            return jsonify({'success': True}), 200
        except Exception as e:
            logger.error(f"Ошибка обновления FAQ: {e}")
            return jsonify({'error': str(e)}), 500

    async def _faq_api_delete(self, faq_id):
        if not await self._check_token(request):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, f"Удаление записи FAQ ID {faq_id}")
        await delete_faq(faq_id)

        # После успешного удаления обновляем локальную резервную копию
        await self._update_faq_backup()

        return jsonify({'success': True}), 200

    async def _update_faq_backup(self):
        """Обновляет локальный файл faq_backup.json актуальными данными из БД."""
        try:
            faq_data = await load_all_faq()
            with open('faq_backup.json', 'w', encoding='utf-8') as f:
                json.dump(faq_data, f, ensure_ascii=False, indent=2)
            logger.info("💾 Резервная копия FAQ обновлена после изменения")
        except Exception as e:
            logger.warning(f"⚠️ Не удалось обновить резервную копию FAQ: {e}")

    # --- API подписчиков и рассылки ---
    async def _subscribers_api_list(self):
        if not await self._check_token(request):
            return jsonify({'error': 'Forbidden'}), 403
        self.log_admin_action(request, "Просмотр списка подписчиков")
        subs = await self.get_subscribers()
        return jsonify({'subscribers': subs, 'count': len(subs)})

    async def _broadcast_api(self):
        if not await self._check_token(request):
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

        # Получаем информацию о занятых строках в БД с кэшированием на 60 секунд и обработкой ошибок
        try:
            current_time = time.time()
            if current_time - self._last_rows_check < 60 and self._cached_rows_count is not None:
                total_rows = self._cached_rows_count
            else:
                total_rows = await get_total_rows_count()
                self._cached_rows_count = total_rows
                self._last_rows_check = current_time
        except Exception as e:
            logger.error(f"Ошибка подсчёта строк: {e}")
            total_rows = None  # Не падаем, показываем N/A

        if total_rows is not None:
            limit_usage = f"{total_rows}/20000"
            if total_rows > 18000:
                limit_class = "metric-bad"
                limit_status = "КРИТИЧНО"
            elif total_rows > 15000:
                limit_class = "metric-warning"
                limit_status = "Внимание"
            else:
                limit_class = "metric-good"
                limit_status = "Норма"
        else:
            limit_usage = "N/A"
            limit_class = ""
            limit_status = ""

        buttons_html = f"""
        <div style="display: flex; gap: 1rem; margin-bottom: 2rem; flex-wrap: wrap;">
            <form method="POST" action="/export/excel" style="display: inline;">
                <input type="hidden" name="token" value="{self.WEBHOOK_SECRET}">
                <button type="submit" class="btn">📥 Экспорт в Excel</button>
            </form>
            <a href="/health" class="btn" style="background: #2E5C4E;">🩺 Health Check</a>
            <form method="POST" action="/search/stats" style="display: inline;">
                <input type="hidden" name="token" value="{self.WEBHOOK_SECRET}">
                <button type="submit" class="btn" style="background: #5C3E6E;">🔍 Поиск Статистика</button>
            </form>
            <form method="POST" action="/feedback/export" style="display: inline;">
                <input type="hidden" name="token" value="{self.WEBHOOK_SECRET}">
                <button type="submit" class="btn" style="background: #9C27B0;">📝 Отзывы (выгрузка)</button>
            </form>
            <form method="POST" action="/rate/stats" style="display: inline;">
                <input type="hidden" name="token" value="{self.WEBHOOK_SECRET}">
                <button type="submit" class="btn" style="background: #FF9800;">⭐ Оценки</button>
            </form>
            <form method="POST" action="/cleanup" style="display: inline;">
                <input type="hidden" name="token" value="{self.WEBHOOK_SECRET}">
                <button type="submit" class="btn" style="background: #6f42c1;">🧹 Очистить старые данные</button>
            </form>
            <a href="/faq" class="btn" style="background: #17a2b8;">📚 Редактор FAQ</a>
            <a href="/messages" class="btn" style="background: #28a745;">💬 Редактор сообщений</a>
            <a href="/subscribers/api?key={self.WEBHOOK_SECRET}" class="btn" style="background: #6f42c1;">📬 Подписчики (JSON)</a>
            <a href="/broadcast" class="btn" style="background: #fd7e14;">📨 Рассылка</a>
            <form method="POST" action="/setwebhook" style="display: inline;">
                <input type="hidden" name="token" value="{self.WEBHOOK_SECRET}">
                <button type="submit" class="btn" style="background: #007bff;">🔧 Установить вебхук</button>
            </form>
        </div>
        """

        html = f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>HR Бот Мечел — Метрики</title>
    <style>
        :root {{
            --bg-dark: #0B1C2F;
            --bg-card: #152A3A;
            --accent: #3E7B91;
            --good: #4CAF50;
            --warning: #FF9800;
            --bad: #F44336;
            --text-light: #E0E7F0;
        }}
        body {{
            font-family: 'Inter', -apple-system, BlinkMacSystemFont, sans-serif;
            background: var(--bg-dark);
            color: var(--text-light);
            margin: 0;
            padding: 2rem;
            line-height: 1.6;
        }}
        .container {{
            max-width: 1200px;
            margin: 0 auto;
        }}
        h1 {{
            font-weight: 600;
            font-size: 2.2rem;
            margin-bottom: 0.5rem;
            color: white;
        }}
        .subtitle {{
            color: #A0C0D0;
            margin-bottom: 2rem;
            font-size: 1.1rem;
        }}
        .grid {{
            display: grid;
            grid-template-columns: repeat(auto-fit, minmax(300px, 1fr));
            gap: 1.5rem;
            margin-bottom: 2rem;
        }}
        .card {{
            background: var(--bg-card);
            border-radius: 16px;
            padding: 1.5rem;
            box-shadow: 0 8px 24px rgba(0,0,0,0.3);
            border: 1px solid #2A4C5E;
        }}
        .stat-value {{
            font-size: 2.8rem;
            font-weight: 700;
            color: white;
            line-height: 1.2;
            margin-bottom: 0.5rem;
        }}
        .metric-badge {{
            display: inline-block;
            padding: 0.25rem 0.75rem;
            border-radius: 20px;
            font-size: 0.85rem;
            font-weight: 600;
            margin-left: 0.5rem;
        }}
        .metric-good {{ background: var(--good); color: white; }}
        .metric-warning {{ background: var(--warning); color: black; }}
        .metric-bad {{ background: var(--bad); color: white; }}
        .status-online {{ color: var(--good); font-weight: 600; }}
        .status-offline {{ color: var(--bad); font-weight: 600; }}
        .btn {{
            background: var(--accent);
            color: white;
            border: none;
            padding: 0.8rem 1.8rem;
            border-radius: 40px;
            font-size: 1rem;
            font-weight: 600;
            cursor: pointer;
            transition: 0.2s;
            text-decoration: none;
            display: inline-block;
            margin-top: 1rem;
        }}
        .btn:hover {{
            background: #4F9DB0;
            transform: translateY(-2px);
        }}
        table {{
            width: 100%;
            border-collapse: collapse;
            background: var(--bg-card);
            border-radius: 12px;
            overflow: hidden;
        }}
        th {{
            background: #1E3A47;
            padding: 0.75rem;
            text-align: left;
        }}
        td {{
            padding: 0.75rem;
            border-bottom: 1px solid #2A4C5E;
        }}
        .footer {{
            margin-top: 3rem;
            color: #809AA8;
            font-size: 0.9rem;
            text-align: center;
        }}
    </style>
</head>
<body>
    <div class="container">
        <h1>🤖 HR Бот «Мечел»</h1>
        <div class="subtitle">Версия 2.17 · Расширенная веб-панель с мониторингом лимита</div>

        <div class="grid">
            <div class="card">
                <h3>⚙️ Производительность</h3>
                <div class="stat-value" id="stat-avg">{avg:.2f}с</div>
                <p>Ср. время ответа (100 запросов)
                    <span class="metric-badge {perf_color}">{perf_text}</span>
                </p>
                <p>Кэш поиска: {cache_size} записей</p>
                <p>Запущен: {start_time_str}</p>
            </div>
            <div class="card">
                <h3>📊 Аудитория</h3>
                <div class="stat-value">{total_users}</div>
                <p>Уникальных пользователей (всего)</p>
                <p>Активных сегодня: {active_today}</p>
                <p>Всего запросов: {total_searches}</p>
                <p>📬 Подписчиков: {len(subscribers)}</p>
                <p>📚 Вопросов в базе: {faq_count}</p>
            </div>
            <div class="card" id="limit-card">
                <h3>📊 Лимит Supabase</h3>
                <div class="stat-value" id="limit-usage">{limit_usage}</div>
                <p>Строк использовано <span class="metric-badge {limit_class}" id="limit-status">{limit_status}</span></p>
                <p>Рекомендуется очистка при >18000 строк</p>
                <button onclick="refreshStats()" class="btn" style="background:#6f42c1; margin-top:10px;">🔄 Обновить</button>
            </div>
            <div class="card">
                <h3>🔌 Система</h3>
                <div class="stat-value">
                    <span class="status-{bot_status_class}" id="bot-status">{bot_status}</span>
                </div>
                <p>Администраторы: {admin_count}</p>
                <p>Память: {memory_usage:.1f} МБ</p>
            </div>
        </div>

        {buttons_html}

        <h2>📈 Статистика за последние 7 дней</h2>
        <table>
            <thead>
                <tr>
                    <th>Дата</th>
                    <th>Пользователи</th>
                    <th>Сообщения</th>
                    <th>Команды</th>
                    <th>Поиски</th>
                    <th>Время ответа</th>
                    <th>👍 Оценки</th>
                    <th>👎 Оценки</th>
                </tr>
            </thead>
            <tbody>
                {daily_rows}
            </tbody>
        </table>
        <div class="footer">
            Время генерации: {(time.time() - start_time)*1000:.1f} мс · 
            {datetime.now().strftime('%d.%m.%Y %H:%M:%S')}
        </div>
    </div>

    <script>
    function refreshStats() {{
        fetch('/stats/rows')
            .then(response => response.json())
            .then(data => {{
                document.getElementById('limit-usage').textContent = data.usage;
                const statusSpan = document.getElementById('limit-status');
                statusSpan.textContent = data.status_text;
                statusSpan.className = 'metric-badge ' + data.status_class;
            }})
            .catch(error => console.error('Ошибка обновления:', error));
    }}
    </script>
</body>
</html>"""
        return html

    # --- Новый эндпоинт для получения статистики строк ---
    async def _stats_rows(self):
        """Возвращает JSON с информацией о занятых строках в БД."""
        try:
            # Используем кэш, чтобы не грузить БД при каждом обновлении
            current_time = time.time()
            if current_time - self._last_rows_check < 60 and self._cached_rows_count is not None:
                total_rows = self._cached_rows_count
            else:
                total_rows = await get_total_rows_count()
                self._cached_rows_count = total_rows
                self._last_rows_check = current_time

            if total_rows is not None:
                usage = f"{total_rows}/20000"
                if total_rows > 18000:
                    status_class = "metric-bad"
                    status_text = "КРИТИЧНО"
                elif total_rows > 15000:
                    status_class = "metric-warning"
                    status_text = "Внимание"
                else:
                    status_class = "metric-good"
                    status_text = "Норма"
            else:
                usage = "N/A"
                status_class = ""
                status_text = ""

            return jsonify({
                'usage': usage,
                'status_class': status_class,
                'status_text': status_text,
                'rows': total_rows
            })
        except Exception as e:
            logger.error(f"Ошибка в /stats/rows: {e}")
            return jsonify({'error': str(e)}), 500

    # --- Обработчики экспорта и статистики ---
    async def _export_excel(self):
        if not await self._check_token(request):
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

    async def _search_stats(self):
        if not await self._check_token(request):
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
        if not await self._check_token(request):
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
        if not await self._check_token(request):
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
        if not await self._check_token(request):
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

    async def _set_webhook(self):
        if not await self._check_token(request):
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

    # ===== ЭНДПОИНТ ДЛЯ ОЧИСТКИ =====
    async def _cleanup_endpoint(self):
        """Эндпоинт для принудительной очистки старых данных (для экономии ресурсов Supabase)"""
        if not await self._check_token(request):
            return jsonify({'error': 'Forbidden'}), 403

        # Рейт-лимитинг: не чаще 1 раза в 5 минут
        if time.time() - self._last_cleanup_time < 300:
            return jsonify({'error': 'Очистка доступна не чаще 1 раза в 5 минут'}), 429
        self._last_cleanup_time = time.time()

        logger.info("🧹 Запуск очистки старых данных через веб-эндпоинт...")
        try:
            errors_cleaned = await cleanup_old_errors(days=30)
            feedback_cleaned = await cleanup_old_feedback(days=90)

            # Проверка типов на случай, если функции вернули не int
            if not isinstance(errors_cleaned, int):
                errors_cleaned = 0
            if not isinstance(feedback_cleaned, int):
                feedback_cleaned = 0

            # Сбрасываем кэш, чтобы статистика обновилась
            self._cached_rows_count = None

            logger.info(f"✅ Очистка завершена: удалено {errors_cleaned} ошибок и {feedback_cleaned} отзывов")
            return jsonify({
                'status': 'cleaned',
                'errors_cleaned': errors_cleaned,
                'feedback_cleaned': feedback_cleaned,
                'message': f'Удалено: ошибок {errors_cleaned}, отзывов {feedback_cleaned}'
            }), 200
        except Exception as e:
            logger.error(f"❌ Ошибка при очистке: {e}")
            return jsonify({'error': str(e)}), 500

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
        app.add_url_rule('/stats/rows', view_func=self._stats_rows, methods=['GET'])  # новый эндпоинт
        app.add_url_rule('/search/stats', view_func=self._search_stats, methods=['GET', 'POST'])
        app.add_url_rule('/feedback/export', view_func=self._feedback_export, methods=['GET', 'POST'])
        app.add_url_rule('/rate/stats', view_func=self._rate_stats, methods=['GET', 'POST'])
        app.add_url_rule('/stats/range', view_func=self._stats_range, methods=['GET', 'POST'])
        app.add_url_rule('/export/excel', view_func=self._export_excel, methods=['GET', 'POST'])
        app.add_url_rule('/setwebhook', view_func=self._set_webhook, methods=['GET', 'POST'])
        app.add_url_rule('/health', view_func=self._health, methods=['GET'])
        app.add_url_rule('/cleanup', view_func=self._cleanup_endpoint, methods=['POST'])

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
