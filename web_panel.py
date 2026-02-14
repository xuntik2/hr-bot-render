# web_panel.py
from quart import Quart, request, jsonify, render_template_string
import asyncio
import logging
from datetime import datetime
from telegram import Update
from utils import is_authorized

logger = logging.getLogger(__name__)

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
            <table id="faqTable">
                <thead>
                    <tr><th>ID</th><th>Вопрос</th><th>Ответ</th><th>Категория</th><th>Ключевые слова</th><th>Действия</th></tr>
                </thead>
                <tbody id="faqBody"></tbody>
            </table>
        </div>
    </div>

    <!-- Модальное окно для добавления/редактирования -->
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

        function auth() {
            apiKey = document.getElementById('secretKey').value.trim();
            if (!apiKey) { alert('Введите ключ'); return; }
            fetch('/faq/api', { headers: { 'X-Secret-Key': apiKey } })
                .then(r => { if (r.ok) { document.getElementById('authDiv').style.display = 'none'; document.getElementById('content').style.display = 'block'; loadFaq(); } else { alert('Неверный ключ'); } });
        }

        function loadFaq() {
            fetch('/faq/api', { headers: { 'X-Secret-Key': apiKey } })
                .then(r => r.json())
                .then(data => {
                    let tbody = document.getElementById('faqBody');
                    tbody.innerHTML = '';
                    data.forEach(item => {
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
                });
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
            }).then(r => { if (r.ok) { closeModal(); loadFaq(); } else { alert('Ошибка'); } });
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
                .then(r => { if (r.ok) loadFaq(); else alert('Ошибка'); });
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
    <style>/* аналогично FAQ, но для сообщений */</style>
</head>
<body>
    <div class="container">
        <h1>📝 Управление текстами сообщений</h1>
        <div class="auth-form">...</div>
        <div id="content" style="display:none;">
            <table id="messagesTable">
                <thead><tr><th>Ключ</th><th>Заголовок</th><th>Текст</th><th>Действия</th></tr></thead>
                <tbody id="messagesBody"></tbody>
            </table>
        </div>
    </div>
    <script>/* аналогичная логика для загрузки/сохранения сообщений */</script>
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
    <style>/* ... */</style>
</head>
<body>
    <div class="container">
        <h1>📢 Рассылка подписчикам</h1>
        <div class="auth-form">...</div>
        <div id="content" style="display:none;">
            <textarea id="messageText" rows="5" placeholder="Текст сообщения (можно использовать HTML)" style="width:100%; padding:10px;"></textarea>
            <button onclick="sendBroadcast()" class="btn btn-add">Отправить</button>
            <div id="result"></div>
        </div>
    </div>
    <script>
        async function sendBroadcast() {
            // логика отправки
        }
    </script>
</body>
</html>"""

# ============================================================================
#  РЕГИСТРАЦИЯ ВСЕХ ВЕБ-МАРШРУТОВ
# ============================================================================
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
    get_meme_handler
):
    @app.route('/faq')
    async def faq_manager():
        return await render_template_string(FAQ_MANAGER_HTML)

    @app.route('/messages')
    async def messages_manager():
        return await render_template_string(MESSAGES_MANAGER_HTML)

    @app.route('/broadcast')
    async def broadcast_page():
        return await render_template_string(BROADCAST_PAGE_HTML)

    @app.route('/messages/api', methods=['GET'])
    async def messages_api_list():
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        messages = await load_messages()
        return jsonify(messages)

    @app.route('/messages/api/<key>', methods=['PUT'])
    async def messages_api_update(key):
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        try:
            data = await request.get_json()
            new_text = data.get('text')
            if new_text is None:
                return jsonify({'error': 'Missing text field'}), 400
            messages = await load_messages()
            if key not in messages:
                return jsonify({'error': 'Message key not found'}), 404
            messages[key]['text'] = new_text
            await save_messages(messages)
            return jsonify({'success': True, 'key': key, 'text': new_text})
        except Exception as e:
            logger.error(f"Ошибка обновления сообщения {key}: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/faq/api', methods=['GET'])
    async def faq_api_list():
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        data = await load_faq_json()
        return jsonify(data)

    @app.route('/faq/api/<int:faq_id>', methods=['GET'])
    async def faq_api_get(faq_id):
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        data = await load_faq_json()
        item = next((i for i in data if i.get('id') == faq_id), None)
        if item:
            return jsonify(item)
        return jsonify({'error': 'Not found'}), 404

    @app.route('/faq/api', methods=['POST'])
    async def faq_api_add():
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        try:
            item = await request.get_json()
            if not item.get('question') or not item.get('answer') or not item.get('category'):
                return jsonify({'error': 'Missing required fields'}), 400
            data = await load_faq_json()
            new_id = await get_next_faq_id()
            new_item = {
                'id': new_id,
                'question': item['question'].strip(),
                'answer': item['answer'].strip(),
                'category': item['category'].strip(),
                'keywords': item.get('keywords', '').strip()
            }
            data.append(new_item)
            await save_faq_json(data)
            return jsonify(new_item), 201
        except Exception as e:
            logger.error(f"Ошибка добавления FAQ: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/faq/api/<int:faq_id>', methods=['PUT'])
    async def faq_api_update(faq_id):
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        try:
            item = await request.get_json()
            if not item.get('question') or not item.get('answer') or not item.get('category'):
                return jsonify({'error': 'Missing required fields'}), 400
            data = await load_faq_json()
            for i, d in enumerate(data):
                if d.get('id') == faq_id:
                    data[i] = {
                        'id': faq_id,
                        'question': item['question'].strip(),
                        'answer': item['answer'].strip(),
                        'category': item['category'].strip(),
                        'keywords': item.get('keywords', '').strip()
                    }
                    await save_faq_json(data)
                    return jsonify(data[i])
            return jsonify({'error': 'Not found'}), 404
        except Exception as e:
            logger.error(f"Ошибка обновления FAQ: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/faq/api/<int:faq_id>', methods=['DELETE'])
    async def faq_api_delete(faq_id):
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        data = await load_faq_json()
        new_data = [i for i in data if i.get('id') != faq_id]
        if len(new_data) == len(data):
            return jsonify({'error': 'Not found'}), 404
        await save_faq_json(new_data)
        return jsonify({'success': True}), 200

    @app.route('/subscribers/api', methods=['GET'])
    async def subscribers_api_list():
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        subs = await get_subscribers()
        return jsonify({'subscribers': subs, 'count': len(subs)})

    @app.route('/broadcast/api', methods=['POST'])
    async def broadcast_api():
        if not is_authorized(request, WEBHOOK_SECRET):
            return jsonify({'error': 'Forbidden'}), 403
        try:
            data = await request.get_json()
            message = data.get('message')
            if not message:
                return jsonify({'error': 'Missing message'}), 400
            subscribers = await get_subscribers()
            if not subscribers:
                return jsonify({'error': 'No subscribers'}), 400
            sent = 0
            failed = 0
            for i, uid in enumerate(subscribers):
                try:
                    await application.bot.send_message(chat_id=uid, text=message, parse_mode='HTML')
                    sent += 1
                    if i % 10 == 9:
                        await asyncio.sleep(1.0)
                    else:
                        await asyncio.sleep(0.1)
                except Exception as e:
                    logger.error(f"❌ Ошибка отправки рассылки пользователю {uid}: {e}")
                    failed += 1
            return jsonify({'success': True, 'sent': sent, 'failed': failed})
        except Exception as e:
            logger.error(f"Ошибка рассылки: {e}")
            return jsonify({'error': str(e)}), 500

    @app.route('/health')
    async def health_check():
        meme_subscribers = 0
        if MEME_MODULE_AVAILABLE:
            handler = get_meme_handler()
            meme_stats = handler.get_stats()
            meme_subscribers = meme_stats['subscribers_count']

        cache_size = 0
        faq_count = 0
        if search_engine:
            cache_size = len(getattr(search_engine, 'cache', {}))
            faq_data = getattr(search_engine, 'faq_data', [])
            faq_count = len(faq_data)

        return jsonify({
            'status': 'ok',
            'bot': 'running' if application else 'stopped',
            'users': bot_stats.get_total_users() if bot_stats else 0,
            'uptime': str(datetime.now() - bot_stats.start_time) if bot_stats else 'N/A',
            'avg_response': bot_stats.get_avg_response_time() if bot_stats else 0,
            'cache_size': cache_size,
            'faq_count': faq_count,
            'meme_subscribers': meme_subscribers
        })

    @app.route(f"/webhook/{WEBHOOK_SECRET}", methods=['POST'])
    async def webhook():
        if request.headers.get('X-Telegram-Bot-Api-Secret-Token') != WEBHOOK_SECRET:
            return 'Forbidden', 403
        if not application:
            return jsonify({'error': 'Bot not initialized'}), 503
        try:
            data = await request.get_json()
            if not data:
                logger.error("Получен пустой запрос вебхука")
                return 'Bad Request', 400
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return 'OK', 200
        except Exception as e:
            logger.error(f"Webhook error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500
