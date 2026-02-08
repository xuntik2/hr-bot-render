#!/usr/bin/env python3
"""
КОРПОРАТИВНЫЙ БОТ МЕЧЕЛ ДЛЯ RENDER + POSTGRESQL
Оптимизированная версия с вебхуками и полной поддержкой базы данных
"""

import os
import logging
import time
from flask import Flask, request, jsonify
import telebot
from telebot import types

from config import config
from search_engine import SearchEngine
from handlers import CommandHandler

# Настройка логирования
logging.basicConfig(
    level=logging.INFO,
    format='%(asctime)s - %(name)s - %(levelname)s - %(message)s',
    handlers=[logging.StreamHandler()]
)
logger = logging.getLogger(__name__)

# Инициализация приложения Flask
app = Flask(__name__)

# Глобальные объекты
bot = None
search_engine = None
command_handler = None

def initialize_app():
    """Инициализация всех компонентов приложения"""
    global bot, search_engine, command_handler
    
    logger.info("=" * 60)
    logger.info("🚀 ИНИЦИАЛИЗАЦИЯ КОРПОРАТИВНОГО БОТА МЕЧЕЛ")
    logger.info("=" * 60)
    
    # Проверка конфигурации
    if not config.validate():
        raise RuntimeError("Конфигурация не прошла валидацию")
    
    # Инициализация бота
    bot = telebot.TeleBot(config.get_bot_token(), threaded=False)
    logger.info(f"✅ Бот инициализирован. Токен: {config.get_bot_token()[:10]}...")
    
    # Инициализация поискового движка
    try:
        search_engine = SearchEngine()
        logger.info(f"✅ Поисковый движок готов. FAQ: {len(search_engine.faq_data)}")
    except Exception as e:
        logger.error(f"❌ Ошибка поискового движка: {e}", exc_info=True)
        search_engine = None
    
    # Инициализация обработчиков
    command_handler = CommandHandler(search_engine) if search_engine else None
    
    # Регистрация обработчиков команд Telegram
    _register_bot_handlers()
    
    logger.info("✅ Приложение полностью инициализировано")
    return True

def _register_bot_handlers():
    """Регистрация обработчиков команд бота"""
    if not command_handler:
        return
    
    @bot.message_handler(commands=['start', 'help'])
    def send_welcome(message):
        logger.info(f"📝 /start от {message.from_user.id}")
        command_handler.handle_welcome(message, bot)
    
    @bot.message_handler(commands=['категории', 'categories'])
    def show_categories(message):
        logger.info(f"📝 /категории от {message.from_user.id}")
        command_handler.handle_categories(message, bot)
    
    @bot.message_handler(commands=['поиск', 'search'])
    def search_command(message):
        query = message.text.replace('/поиск', '').replace('/search', '').strip()
        logger.info(f"📝 /поиск от {message.from_user.id}: {query[:50]}")
        command_handler.handle_search(message, bot)
    
    @bot.message_handler(commands=['отзыв', 'feedback'])
    def feedback_command(message):
        logger.info(f"📝 /отзыв от {message.from_user.id}")
        if hasattr(command_handler, 'handle_feedback'):
            command_handler.handle_feedback(message, bot)
        else:
            bot.reply_to(message, "Функция отзывов временно недоступна")
    
    @bot.message_handler(commands=['статистика', 'stats'])
    def stats_command(message):
        """Статистика (только для админов)"""
        admin_ids = config.get_admin_ids()
        if admin_ids and message.from_user.id in admin_ids:
            try:
                stats = search_engine.get_stats() if search_engine else {}
                response = (
                    f"📊 Статистика бота:\n"
                    f"• FAQ в базе: {stats.get('total_faq', 0)}\n"
                    f"• Всего поисков: {stats.get('total_searches', 0)}\n"
                    f"• Уникальных слов: {stats.get('unique_words', 0)}"
                )
                bot.reply_to(message, response)
            except Exception as e:
                logger.error(f"Ошибка статистики: {e}")
                bot.reply_to(message, "❌ Не удалось получить статистику")
        else:
            bot.reply_to(message, "⚠️ Эта команда доступна только администраторам")
    
    # Обработка всех текстовых сообщений
    @bot.message_handler(func=lambda message: True)
    def handle_all_messages(message):
        logger.info(f"📝 Сообщение от {message.from_user.id}: {message.text[:100]}")
        try:
            if command_handler:
                command_handler.handle_text_message(message, bot)
            else:
                bot.reply_to(message, "⚠️ Бот временно не готов к работе. Попробуйте позже.")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки: {e}", exc_info=True)
            bot.reply_to(message, "❌ Произошла ошибка. Пожалуйста, попробуйте ещё раз.")

# ================== FLASK РОУТЫ ==================

@app.route('/')
def index():
    """Главная страница"""
    faq_count = len(search_engine.faq_data) if search_engine else 0
    db_type = 'PostgreSQL' if os.getenv('DATABASE_URL') else 'SQLite'
    bot_username = config.get_bot_token().split(':')[0] if bot else ''
    
    return f'''
    <!DOCTYPE html>
    <html>
    <head>
        <title>🤖 HR Bot Мечел</title>
        <meta charset="utf-8">
        <meta name="viewport" content="width=device-width, initial-scale=1">
        <style>
            body {{ font-family: Arial, sans-serif; max-width: 800px; margin: 40px auto; padding: 20px; }}
            h1 {{ color: #333; }}
            .status {{ background: #f0f9ff; padding: 15px; border-radius: 8px; margin: 20px 0; }}
            .links a {{ display: inline-block; margin: 10px 15px 10px 0; padding: 10px 20px;
            background: #007bff; color: white; text-decoration: none; border-radius: 5px; }}
            .links a:hover {{ background: #0056b3; }}
        </style>
    </head>
    <body>
        <h1>🤖 HR Bot Мечел — Успешно работает!</h1>
        <div class="status">
            <h3>✅ Статус: Бот активен</h3>
            <p>Сервис запущен на Render с использованием вебхуков.</p>
            <p><strong>FAQ в базе:</strong> {faq_count}</p>
            <p><strong>Тип БД:</strong> {db_type}</p>
        </div>
        <div class="links">
            <h3>🔗 Полезные ссылки:</h3>
            <a href="/health">Health Check</a>
            <a href="/set_webhook">Установить вебхук</a>
            <a href="/webhook_info">Информация о вебхуке</a>
            <a href="https://t.me/{bot_username}">Написать боту</a>
        </div>
        <div style="margin-top: 30px; color: #666; font-size: 14px;">
            <p>Время запуска: {time.strftime('%Y-%m-%d %H:%M:%S')}</p>
        </div>
    </body>
    </html>
    ''', 200

@app.route('/health')
def health_check():
    """Health check endpoint для Render"""
    status = {
        "status": "healthy",
        "service": "hr-bot",
        "bot_initialized": bot is not None,
        "search_engine": search_engine is not None,
        "faq_count": len(search_engine.faq_data) if search_engine else 0,
        "database": "postgresql" if os.getenv('DATABASE_URL') else "sqlite"
    }
    return jsonify(status), 200

@app.route('/set_webhook', methods=['GET', 'POST'])
def set_webhook_endpoint():
    """Ручная установка вебхука (для отладки)"""
    if request.method == 'GET':
        return '''
        <h1>🔧 Установка вебхука</h1>
        <p>Нажмите кнопку ниже, чтобы установить вебхук вручную:</p>
        <form method="POST" style="margin: 20px 0;">
            <button type="submit" style="padding: 10px 20px; background: #28a745; color: white; border: none; border-radius: 5px;">
                🚀 Установить вебхук
            </button>
        </form>
        <p><a href="/">← Назад</a></p>
        '''
    
    # POST запрос - установка вебхука
    try:
        # Получаем домен из переменных окружения или используем текущий
        domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-render.onrender.com')
        if domain.startswith('https://'):
            domain = domain[8:]
        webhook_url = f"https://{domain}/webhook"
        logger.info(f"🔄 Установка вебхука на {webhook_url}")
        
        # Удаляем старый вебхук
        bot.remove_webhook()
        time.sleep(1)  # Небольшая пауза
        
        # Устанавливаем новый вебхук
        success = bot.set_webhook(
            url=webhook_url,
            max_connections=40,
            allowed_updates=['message', 'callback_query']
        )
        
        if success:
            msg = f"✅ Вебхук успешно установлен!<br>URL: <code>{webhook_url}</code>"
            logger.info("✅ Вебхук установлен")
            # Получаем информацию о вебхуке для проверки
            try:
                webhook_info = bot.get_webhook_info()
                msg += f"<br><br>📊 Информация от Telegram:<br>"
                msg += f"• Ожидающих обновлений: {webhook_info.pending_update_count}<br>"
                msg += f"• Последняя ошибка: {webhook_info.last_error_message or 'нет'}"
            except:
                pass
        else:
            msg = "❌ Не удалось установить вебхук. Проверьте токен бота и доступность сервера."
            logger.error("❌ Ошибка установки вебхука")
    except Exception as e:
        msg = f"❌ Ошибка: {str(e)}"
        logger.error(f"❌ Ошибка установки вебхука: {e}", exc_info=True)
    
    return f'''
    <h1>🔧 Результат установки вебхука</h1>
    <div style="padding: 20px; background: {'#d4edda' if '✅' in msg else '#f8d7da'}; border-radius: 8px;">
        {msg}
    </div>
    <p style="margin-top: 20px;"><a href="/">← На главную</a> | <a href="/webhook_info">ℹ️ Информация о вебхуке</a></p>
    ''', 200 if '✅' in msg else 500

@app.route('/webhook_info')
def webhook_info():
    """Страница с информацией о текущем вебхуке"""
    try:
        info = bot.get_webhook_info()
        status = {
            "url": info.url or "Не установлен",
            "has_custom_certificate": info.has_custom_certificate,
            "pending_update_count": info.pending_update_count,
            "last_error_date": info.last_error_date,
            "last_error_message": info.last_error_message or "Нет ошибок",
            "max_connections": info.max_connections,
            "allowed_updates": info.allowed_updates
        }
        return f'''
        <h1>ℹ️ Информация о вебхуке</h1>
        <div style="background: #f8f9fa; padding: 20px; border-radius: 8px;">
            <p><strong>URL:</strong> <code>{status['url']}</code></p>
            <p><strong>Ожидающих обновлений:</strong> {status['pending_update_count']}</p>
            <p><strong>Последняя ошибка:</strong> {status['last_error_message']}</p>
            <p><strong>Макс. соединений:</strong> {status['max_connections']}</p>
        </div>
        <p style="margin-top: 20px;">
            <a href="/">← На главную</a> |
            <a href="/set_webhook">🔧 Установить вебхук</a>
        </p>
        ''', 200
    except Exception as e:
        return f'<h1>❌ Ошибка</h1><p>Не удалось получить информацию: {e}</p>', 500

@app.route('/webhook', methods=['POST'])
def telegram_webhook():
    """Основной эндпоинт для получения обновлений от Telegram"""
    if request.headers.get('content-type') == 'application/json':
        try:
            # Получаем и декодируем JSON
            json_string = request.get_data().decode('utf-8')
            update = types.Update.de_json(json_string)
            
            # Логируем получение обновления
            if update.message:
                logger.info(f"📨 Получено сообщение от {update.message.from_user.id}")
            
            # Передаём обновление боту на обработку
            bot.process_new_updates([update])
            return '', 200
        except Exception as e:
            logger.error(f"❌ Ошибка обработки вебхука: {e}", exc_info=True)
            return 'Internal Server Error', 500
    return 'Bad Request', 400

# ================== ИНИЦИАЛИЗАЦИЯ ПРИ ЗАПУСКЕ ==================
# Инициализируем приложение сразу при импорте
try:
    initialize_app()
    
    # Автоматическая установка вебхука при запуске
    AUTO_SET_WEBHOOK = os.getenv('AUTO_SET_WEBHOOK', 'true').lower() == 'true'
    if AUTO_SET_WEBHOOK and bot:
        try:
            domain = os.getenv('RENDER_EXTERNAL_URL', 'https://hr-bot-render.onrender.com')
            if domain.startswith('https://'):
                domain = domain[8:]
            webhook_url = f"https://{domain}/webhook"
            bot.remove_webhook()
            success = bot.set_webhook(url=webhook_url)
            if success:
                logger.info(f"✅ Вебхук автоматически установлен на {webhook_url}")
            else:
                logger.warning("⚠️ Не удалось установить вебхук автоматически")
        except Exception as e:
            logger.warning(f"⚠️ Ошибка автоматической установки вебхука: {e}")
    
    logger.info("✅ Приложение готово к работе на порту %s", os.getenv('PORT', 10000))
except Exception as e:
    logger.critical(f"❌ КРИТИЧЕСКАЯ ОШИБКА ПРИ ИНИЦИАЛИЗАЦИИ: {e}", exc_info=True)
    raise

# ================== ЗАПУСК ДЛЯ ЛОКАЛЬНОЙ РАЗРАБОТКИ ==================
if __name__ == '__main__':
    # Этот блок выполняется только при локальном запуске python bot.py
    # НЕ используется на Render в продакшене!
    logger.warning("⚠️ ЛОКАЛЬНЫЙ ЗАПУСК - только для разработки!")
    port = int(os.environ.get('PORT', 10000))
    logger.info(f"🌐 Запуск Flask сервера на порту {port} (ТОЛЬКО ДЛЯ РАЗРАБОТКИ!)")
    app.run(host='0.0.0.0', port=port, debug=False)
