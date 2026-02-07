#!/usr/bin/env python3
"""
ОБРАБОТЧИК МЕМОВ ДЛЯ POSTGRESQL
С парсингом мемов и подписками
"""

import logging
import random
import time
import requests
from datetime import datetime
from typing import Optional, Dict, List

from config import config

logger = logging.getLogger(__name__)

class MemeHandler:
    """Обработчик мемов с поддержкой PostgreSQL"""
    
    def __init__(self):
        self.meme_sources = [
            "https://api.imgflip.com/get_memes",
            "https://meme-api.com/gimme",
            "https://some-random-api.com/meme"
        ]
        
        # Кэш мемов для быстрого доступа
        self.meme_cache: List[Dict] = []
        self.last_cache_update = 0
    
    def _get_db_connection(self):
        """Получить соединение с БД"""
        return config.get_db_connection()
    
    def handle_meme(self, message, bot):
        """Обработка команды /мем"""
        try:
            user_id = message.from_user.id
            
            # Проверяем, включены ли мемы
            if not config.is_meme_enabled():
                bot.reply_to(
                    message,
                    "🎭 *Мемы временно отключены*\n\n"
                    "Функция мемов в данный момент недоступна. "
                    "Следите за обновлениями!",
                    parse_mode='Markdown'
                )
                return
            
            # Показываем индикатор "печатает"
            bot.send_chat_action(message.chat.id, 'upload_photo')
            
            # Получаем мем
            meme_url = self._get_random_meme()
            
            if meme_url:
                # Отправляем мем
                bot.send_photo(
                    message.chat.id,
                    meme_url,
                    caption="🎭 *Случайный мем для поднятия настроения!*\n\n"
                           "Хочешь получать мемы каждый день? "
                           "Используй /мемподписка",
                    parse_mode='Markdown'
                )
                logger.info(f"Мем отправлен пользователю {user_id}")
            else:
                bot.reply_to(
                    message,
                    "😔 *Не удалось загрузить мем*\n\n"
                    "Попробуйте позже или проверьте подключение к интернету.",
                    parse_mode='Markdown'
                )
                
        except Exception as e:
            logger.error(f"Ошибка при отправке мема: {e}")
            bot.reply_to(
                message,
                "❌ Произошла ошибка при загрузке мема. Попробуйте позже.",
                parse_mode='Markdown'
            )
    
    def handle_subscribe(self, message, bot):
        """Обработка команды /мемподписка"""
        try:
            user_id = message.from_user.id
            
            if not config.is_meme_enabled():
                bot.reply_to(
                    message,
                    "🎭 *Подписка на мемы недоступна*\n\n"
                    "Функция мемов временно отключена.",
                    parse_mode='Markdown'
                )
                return
            
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # Проверяем, подписан ли уже пользователь
            cursor.execute("SELECT subscribed FROM meme_subscriptions WHERE user_id = %s", (user_id,))
            result = cursor.fetchone()
            
            if result:
                # Обновляем подписку
                cursor.execute(
                    "UPDATE meme_subscriptions SET subscribed = TRUE, subscribed_at = CURRENT_TIMESTAMP WHERE user_id = %s",
                    (user_id,)
                )
                action = "возобновлена"
            else:
                # Добавляем новую подписку
                cursor.execute(
                    "INSERT INTO meme_subscriptions (user_id, subscribed, subscribed_at) VALUES (%s, TRUE, CURRENT_TIMESTAMP)",
                    (user_id,)
                )
                action = "активирована"
            
            conn.commit()
            conn.close()
            
            bot.reply_to(
                message,
                f"✅ *Подписка на мемы {action}!*\n\n"
                f"Теперь вы будете получать случайные мемы каждый день в 10:00.\n\n"
                f"Чтобы отписаться, используйте /мемотписка",
                parse_mode='Markdown'
            )
            
            logger.info(f"Пользователь {user_id} подписался на мемы")
            
        except Exception as e:
            logger.error(f"Ошибка при подписке на мемы: {e}")
            bot.reply_to(
                message,
                "❌ Произошла ошибка при оформлении подписки. Попробуйте позже.",
                parse_mode='Markdown'
            )
    
    def handle_unsubscribe(self, message, bot):
        """Обработка команды /мемотписка"""
        try:
            user_id = message.from_user.id
            
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # Отписываем пользователя
            cursor.execute(
                "UPDATE meme_subscriptions SET subscribed = FALSE WHERE user_id = %s",
                (user_id,)
            )
            
            conn.commit()
            conn.close()
            
            bot.reply_to(
                message,
                "✅ *Вы отписались от рассылки мемов*\n\n"
                "Больше не будете получать ежедневные мемы.\n"
                "Если передумаете - используйте /мемподписка снова!",
                parse_mode='Markdown'
            )
            
            logger.info(f"Пользователь {user_id} отписался от мемов")
            
        except Exception as e:
            logger.error(f"Ошибка при отписке от мемов: {e}")
            bot.reply_to(
                message,
                "❌ Произошла ошибка при отписке. Попробуйте позже.",
                parse_mode='Markdown'
            )
    
    def send_daily_memes(self, bot):
        """Ежедневная рассылка мемов подписчикам"""
        try:
            if not config.is_meme_enabled():
                return
            
            logger.info("🚀 Запуск ежедневной рассылки мемов...")
            
            conn = self._get_db_connection()
            cursor = conn.cursor()
            
            # Получаем всех подписчиков
            cursor.execute("SELECT user_id FROM meme_subscriptions WHERE subscribed = TRUE")
            subscribers = cursor.fetchall()
            
            conn.close()
            
            if not subscribers:
                logger.info("Нет подписчиков для рассылки мемов")
                return
            
            meme_url = self._get_random_meme()
            
            if not meme_url:
                logger.error("Не удалось получить мем для рассылки")
                return
            
            sent_count = 0
            failed_count = 0
            
            for (user_id,) in subscribers:
                try:
                    bot.send_photo(
                        user_id,
                        meme_url,
                        caption="🎭 *Ежедневный мем для хорошего настроения!*\n\n"
                               "Хорошего дня! ☀️",
                        parse_mode='Markdown'
                    )
                    sent_count += 1
                    time.sleep(0.1)  # Задержка между отправками
                    
                except Exception as e:
                    failed_count += 1
                    logger.warning(f"Не удалось отправить мем пользователю {user_id}: {e}")
            
            logger.info(f"✅ Рассылка мемов завершена: {sent_count} отправлено, {failed_count} ошибок")
            
        except Exception as e:
            logger.error(f"Ошибка в ежедневной рассылке мемов: {e}")
    
    def _get_random_meme(self) -> Optional[str]:
        """Получить случайный мем из API"""
        try:
            # Используем кэш если он свежий
            current_time = time.time()
            if self.meme_cache and (current_time - self.last_cache_update < 3600):
                meme = random.choice(self.meme_cache)
                return meme.get('url')
            
            # Обновляем кэш
            self.meme_cache = []
            
            for source in self.meme_sources:
                try:
                    response = requests.get(source, timeout=5)
                    if response.status_code == 200:
                        data = response.json()
                        
                        if 'memes' in data:
                            # ImgFlip API
                            memes = data['memes']
                            meme = random.choice(memes)
                            self.meme_cache.append({
                                'url': meme['url'],
                                'title': meme.get('name', 'Мем')
                            })
                        elif 'url' in data:
                            # Meme API
                            self.meme_cache.append({
                                'url': data['url'],
                                'title': data.get('title', 'Мем')
                            })
                        
                        # Если нашли достаточно мемов, выходим
                        if len(self.meme_cache) >= 10:
                            break
                            
                except Exception as e:
                    logger.debug(f"Не удалось получить мемы из {source}: {e}")
                    continue
            
            self.last_cache_update = current_time
            
            if self.meme_cache:
                meme = random.choice(self.meme_cache)
                return meme['url']
            else:
                # Запасной вариант - статичные мемы
                fallback_memes = [
                    "https://i.imgflip.com/30b1gx.jpg",
                    "https://i.imgflip.com/1g8my4.jpg",
                    "https://i.imgflip.com/1ur9b0.jpg"
                ]
                return random.choice(fallback_memes)
                
        except Exception as e:
            logger.error(f"Ошибка при получении мема: {e}")
            return None