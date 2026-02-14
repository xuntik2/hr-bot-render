# web_panel.py
from quart import Quart, request, jsonify, make_response, render_template_string
import asyncio
import logging
from datetime import datetime
from telegram import Update
from .utils import is_authorized

logger = logging.getLogger(__name__)

FAQ_MANAGER_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Управление FAQ — HR Бот Мечел</title>
<style>
body { font-family: Arial, sans-serif; margin: 20px; background-color: #f5f5f5; }
.container { max-width: 1200px; margin: 0 auto; background: white; padding: 20px; border-radius: 8px; box-shadow: 0 2px 10px rgba(0,0,0,0.1); }
h1 { color: #0B1C2F; }
.warning { background-color: #fff3cd; border: 1px solid #ffeeba; color: #856404; padding: 12px; border-radius: 4px; margin-bottom: 20px; }
.auth-form { background: #e9ecef; padding: 20px; border-radius: 5px; margin-bottom: 20px; }
.error { color: red; margin-top: 10px; }
</style>
</head>
<body>
<div class="container">
<h1>📚 Управление базой знаний FAQ</h1>
<div class="warning">⚠️ На бесплатном тарифе Render изменения будут потеряны при перезапуске сервиса.</div>
<!-- ... остальной HTML как в bot.py ... -->
</div>
<script>
// ... JS как в bot.py ...
</script>
</body>
</html>"""

MESSAGES_MANAGER_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Управление сообщениями — HR Бот Мечел</title>
<style>/* ... */</style>
</head>
<body><!-- ... --></body>
</html>"""

BROADCAST_PAGE_HTML = """<!DOCTYPE html>
<html lang="ru">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Рассылка подписчикам — HR Бот Мечел</title>
<style>/* ... */</style>
</head>
<body><!-- ... --></body>
</html>"""

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

        # 🔥 Безопасное получение cache_size и faq_count
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
            if not data:  # ✅ ИСПРАВЛЕНО
                logger.error("Получен пустой запрос вебхука")
                return 'Bad Request', 400
            update = Update.de_json(data, application.bot)
            await application.process_update(update)
            return 'OK', 200
        except Exception as e:
            logger.error(f"Webhook error: {e}", exc_info=True)
            return jsonify({'error': str(e)}), 500