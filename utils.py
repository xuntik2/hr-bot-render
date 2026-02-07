#!/usr/bin/env python3
"""
ПОЛНЫЕ УТИЛИТЫ ДЛЯ HR-БОТА
Со всеми функциями из оригинала
"""

import time
import re
from typing import Dict, Tuple, Optional
from datetime import datetime, timedelta

# Глобальные переменные для защиты от спама
_user_last_request: Dict[int, float] = {}

def check_spam(user_id: int, rate_limit_seconds: int = 3) -> Tuple[bool, int]:
    """
    Проверка на спам-запросы
    """
    current_time = time.time()
    
    if user_id in _user_last_request:
        last_request_time = _user_last_request[user_id]
        time_since_last = current_time - last_request_time
        
        if time_since_last < rate_limit_seconds:
            wait_time = int(rate_limit_seconds - time_since_last)
            return True, wait_time
    
    _user_last_request[user_id] = current_time
    
    # Очистка старых записей
    cleanup_time = current_time - 3600
    users_to_remove = [uid for uid, t in _user_last_request.items() if t < cleanup_time]
    for uid in users_to_remove:
        del _user_last_request[uid]
    
    return False, 0

def is_valid_query(query: str) -> bool:
    """
    Проверка, является ли запрос валидным для поиска
    """
    if not query or len(query.strip()) < 3:
        return False
    
    query = query.strip()
    
    if len(query) < 3:
        return False
    
    stop_words = {
        'а', 'и', 'но', 'или', 'в', 'на', 'с', 'у', 'о', 'по', 'для', 'как',
        'из', 'от', 'до', 'за', 'же', 'ли', 'бы', 'то', 'не', 'ни', 'же',
        'что', 'это', 'так', 'вот', 'ну', 'да', 'нет'
    }
    
    query_words = [word.lower() for word in query.split() if word.lower() not in stop_words]
    
    if not query_words:
        return False
    
    if not any(any(c.isalpha() for c in word) for word in query_words):
        return False
    
    if all(len(word) < 2 for word in query_words):
        return False
    
    return True

def format_answer(text: str) -> str:
    """
    Форматирование ответа для Telegram
    """
    if not text:
        return ""
    
    text = re.sub(r'\n{3,}', '\n\n', text.strip())
    text = text.replace('\n\n\n', '\n\n')
    lines = [line.strip() for line in text.split('\n')]
    text = '\n'.join(lines)
    
    return text

def truncate_text(text: str, max_length: int = 100) -> str:
    """
    Обрезать текст до указанной длины
    """
    if not text:
        return ""
    
    if len(text) <= max_length:
        return text
    
    truncated = text[:max_length].rstrip()
    
    if len(text) > max_length and text[max_length] != ' ':
        last_space = truncated.rfind(' ')
        if last_space > 0:
            truncated = truncated[:last_space]
    
    return truncated + "..."

def get_user_friendly_time(seconds: int) -> str:
    """
    Преобразовать секунды в понятное для пользователя время
    """
    if seconds < 60:
        return f"{seconds} секунд"
    
    minutes = seconds // 60
    remaining_seconds = seconds % 60
    
    if minutes < 60:
        if remaining_seconds == 0:
            return f"{minutes} минут"
        else:
            return f"{minutes} минут {remaining_seconds} секунд"
    
    hours = minutes // 60
    remaining_minutes = minutes % 60
    
    if remaining_minutes == 0:
        return f"{hours} часов"
    else:
        return f"{hours} часов {remaining_minutes} минут"

def normalize_text(text: str) -> str:
    """
    Нормализация текста для поиска
    """
    if not text:
        return ""
    
    text = text.lower()
    text = re.sub(r'[^\w\s]', ' ', text)
    text = re.sub(r'\s+', ' ', text)
    
    return text.strip()

def extract_keywords(text: str) -> list:
    """
    Извлечение ключевых слов из текста
    """
    if not text:
        return []
    
    normalized = normalize_text(text)
    
    stop_words = {
        'и', 'в', 'на', 'с', 'по', 'для', 'как', 'что', 'это',
        'а', 'но', 'или', 'у', 'о', 'же', 'ли', 'бы', 'то'
    }
    
    words = normalized.split()
    keywords = [word for word in words if word not in stop_words and len(word) > 2]
    
    return keywords

def safe_int(value, default=0) -> int:
    """
    Безопасное преобразование в целое число
    """
    try:
        return int(value)
    except (ValueError, TypeError):
        return default

def get_current_timestamp() -> str:
    """
    Получить текущую временную метку в строковом формате
    """
    return datetime.now().isoformat()

def is_within_time_range(start_time: str, end_time: str) -> bool:
    """
    Проверка, находится ли текущее время в указанном диапазоне
    """
    try:
        now = datetime.now().time()
        
        start = datetime.strptime(start_time, "%H:%M").time()
        end = datetime.strptime(end_time, "%H:%M").time()
        
        return start <= now <= end
    except Exception:
        return False

def validate_email(email: str) -> bool:
    """
    Валидация email адреса
    """
    if not email:
        return False
    
    pattern = r'^[a-zA-Z0-9._%+-]+@[a-zA-Z0-9.-]+\.[a-zA-Z]{2,}$'
    return bool(re.match(pattern, email))

def validate_phone(phone: str) -> bool:
    """
    Валидация номера телефона
    """
    if not phone:
        return False
    
    cleaned = re.sub(r'[^\d+]', '', phone)
    
    if cleaned.startswith('+7') and len(cleaned) == 12:
        return True
    elif cleaned.startswith('8') and len(cleaned) == 11:
        return True
    elif cleaned.startswith('7') and len(cleaned) == 11:
        return True
    elif cleaned.startswith('+') and 10 <= len(cleaned) <= 15:
        return True
    
    return False

def format_date(date_str: str, format_from: str = "%Y-%m-%d", format_to: str = "%d.%m.%Y") -> str:
    """
    Форматирование даты из одного формата в другой
    """
    try:
        date_obj = datetime.strptime(date_str, format_from)
        return date_obj.strftime(format_to)
    except Exception:
        return ""