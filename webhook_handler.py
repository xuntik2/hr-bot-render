#!/usr/bin/env python3
"""
Обработчик вебхуков для Python-Telegram-Bot 20.3+
"""
import logging
from telegram import Update
from telegram.ext import Application

logger = logging.getLogger(__name__)

class WebhookHandler:
    def __init__(self, application: Application):
        self.application = application
    
    async def process_update(self, update_data: dict) -> None:
        """Асинхронная обработка обновления"""
        try:
            # Создаем объект Update из данных
            update = Update.de_json(update_data, self.application.bot)
            if update:
                # Обрабатываем обновление
                await self.application.process_update(update)
                logger.debug(f"✅ Обработано обновление {update.update_id}")
            else:
                logger.error("❌ Не удалось создать Update объект")
        except Exception as e:
            logger.error(f"❌ Ошибка обработки обновления: {e}")