# migrate_to_supabase.py
import asyncio
import json
import os
from datetime import datetime

# Импортируем все функции для работы с БД
from database import (
    init_db,
    add_subscriber,
    save_message,
    add_faq,
    add_meme_history,
    add_meme_subscriber,
    save_feedback,
    save_rating,
    DATABASE_URL
)

async def migrate():
    print("🚀 Начинаем миграцию данных в Supabase...")
    if not DATABASE_URL:
        print("❌ Ошибка: DATABASE_URL не установлен в переменных окружения!")
        return

    # 1. Инициализируем таблицы
    await init_db()
    print("✅ Таблицы созданы/проверены.")

    # 2. Перенос подписчиков на рассылку (subscribers.json)
    try:
        with open('subscribers.json', 'r', encoding='utf-8') as f:
            subscribers = json.load(f)
        for uid in subscribers:
            await add_subscriber(uid)
        print(f"✅ Перенесено {len(subscribers)} подписчиков на рассылку.")
    except FileNotFoundError:
        print("⚠️ subscribers.json не найден, пропускаем.")
    except Exception as e:
        print(f"❌ Ошибка при переносе подписчиков: {e}")

    # 3. Перенос системных сообщений (messages.json)
    try:
        with open('messages.json', 'r', encoding='utf-8') as f:
            messages = json.load(f)
        for key, msg in messages.items():
            text = msg if isinstance(msg, str) else msg.get('text', '')
            title = msg.get('title', '') if isinstance(msg, dict) else ''
            await save_message(key, text, title)
        print(f"✅ Перенесено {len(messages)} системных сообщений.")
    except FileNotFoundError:
        print("⚠️ messages.json не найден, пропускаем.")
    except Exception as e:
        print(f"❌ Ошибка при переносе сообщений: {e}")

    # 4. Перенос базы знаний FAQ (faq.json)
    try:
        with open('faq.json', 'r', encoding='utf-8') as f:
            faq_list = json.load(f)
        for item in faq_list:
            # В faq.json есть поля: id, priority, question, answer, keywords, category
            await add_faq(
                question=item['question'],
                answer=item['answer'],
                category=item.get('category', 'Без категории'),
                keywords=item.get('keywords', ''),
                priority=item.get('priority', 0)
            )
        print(f"✅ Перенесено {len(faq_list)} записей FAQ.")
    except FileNotFoundError:
        print("⚠️ faq.json не найден, пропускаем.")
    except Exception as e:
        print(f"❌ Ошибка при переносе FAQ: {e}")

    # 5. Перенос данных мемов (meme_data.json)
    try:
        with open('meme_data.json', 'r', encoding='utf-8') as f:
            meme_data = json.load(f)
        # Перенос истории мемов
        for user_id_str, timestamps in meme_data.get('meme_history', {}).items():
            user_id = int(user_id_str)
            for ts_str in timestamps:
                # В БД сохраняем только факт получения, без пути к мему (можно улучшить)
                await add_meme_history(user_id, '')
        # Перенос подписчиков на мемы
        for uid in meme_data.get('subscribers', []):
            await add_meme_subscriber(uid)
        print(f"✅ Перенесена история мемов для {len(meme_data.get('meme_history', {}))} пользователей и {len(meme_data.get('subscribers', []))} подписчиков.")
    except FileNotFoundError:
        print("⚠️ meme_data.json не найден, пропускаем.")
    except Exception as e:
        print(f"❌ Ошибка при переносе мемов: {e}")

    # 6. Перенос отзывов (из статистики в памяти? Увы, они не сохранялись в JSON.
    #    Поэтому отзывы, которые были до миграции, потеряны. Но новые будут сохраняться.
    print("⚠️ Отзывы и оценки не были сохранены в JSON, поэтому они не переносятся.")

    print("\n🎉 Миграция завершена! Проверьте данные в Supabase.")

if __name__ == '__main__':
    asyncio.run(migrate())