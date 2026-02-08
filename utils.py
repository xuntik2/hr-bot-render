#!/usr/bin/env python3
"""
ВСПОМОГАТЕЛЬНЫЕ ФУНКЦИИ ДЛЯ БОТА
"""
import time
from datetime import datetime, timedelta
from typing import Dict, Tuple

# Глобальная переменная для хранения времени последних запросов
last_requests: Dict[int, float] = {}

def check_spam(user_id: int) -> Tuple[bool, int]:
    """
    Проверка на спам-запросы
    Возвращает (is_spam, wait_time_seconds)
    """
    current_time = time.time()
    
    if user_id in last_requests:
        time_since_last = current_time - last_requests[user_id]
        rate_limit = 2  # 2 секунды между запросами
        
        if time_since_last < rate_limit:
            wait_time = int(rate_limit - time_since_last)
            return True, wait_time
    
    # Обновляем время последнего запроса
    last_requests[user_id] = current_time
    
    # Очищаем старые записи (старше 1 часа)
    cleanup_old_requests()
    
    return False, 0

def cleanup_old_requests():
    """Очистка старых записей о запросах"""
    current_time = time.time()
    global last_requests
    
    # Удаляем записи старше 1 часа
    to_remove = []
    for user_id, last_time in last_requests.items():
        if current_time - last_time > 3600:  # 1 час
            to_remove.append(user_id)
    
    for user_id in to_remove:
        del last_requests[user_id]

def format_answer(text: str, max_length: int = 4000) -> str:
    """
    Форматирование ответа для Telegram
    Telegram имеет лимит 4096 символов на сообщение
    """
    if len(text) > max_length:
        # Обрезаем и добавляем сообщение
        text = text[:max_length - 100] + "\n\n📝 *Сообщение было сокращено из-за ограничений Telegram*"
    
    return text

def truncate_text(text: str, max_length: int = 100) -> str:
    """Обрезка текста с добавлением многоточия"""
    if len(text) <= max_length:
        return text
    return text[:max_length - 3] + "..."

def is_valid_query(query: str) -> bool:
    """Проверка валидности запроса"""
    if not query or len(query.strip()) < 3:
        return False
    
    # Проверяем, что запрос не состоит только из спецсимволов
    clean_query = ''.join(c for c in query if c.isalnum() or c.isspace())
    return len(clean_query.strip()) >= 3

def get_user_friendly_time(seconds: int) -> str:
    """Преобразование секунд в удобный формат времени"""
    if seconds < 60:
        return f"{seconds} секунд"
    elif seconds < 3600:
        minutes = seconds // 60
        return f"{minutes} минут"
    else:
        hours = seconds // 3600
        return f"{hours} часов"
