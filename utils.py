# utils.py
"""
Вспомогательные функции для HR-бота Мечел
Версия 1.1 — упрощена функция is_authorized (только заголовок X-Secret-Key)
"""
import re
from datetime import datetime
from typing import Optional

def is_greeting(text: str) -> bool:
    """Проверяет, является ли текст приветствием"""
    text_clean = text.lower().strip()
    greetings = {
        'привет', 'здравствуй', 'здравствуйте', 'здорово', 'hello', 'hi', 'hey',
        'добрый день', 'доброе утро', 'добрый вечер', 'доброй ночи', 'доброго времени суток',
        'ку', 'салют', 'хай', 'хелло', 'хэллоу'
    }
    emoji_greetings = {'👋', '🙋', '🙌', '🤝', '✋', '🖐', '👐', '🤗', '😊', '😀', '😄', '😁', '😃'}
    
    for greet in greetings:
        if greet in text_clean or text_clean == greet:
            return True
    for emoji in emoji_greetings:
        if emoji in text:
            return True
    return False

def truncate_question(question: str, max_len: int = 50) -> str:
    """Обрезает вопрос до максимальной длины, добавляя многоточие"""
    if len(question) <= max_len:
        return question
    return question[:max_len - 3] + "..."

def parse_period_argument(arg: str) -> str:
    """Преобразует аргумент команды в стандартный период для статистики"""
    arg = arg.lower().strip()
    mapping = {
        'day': 'day', 'd': 'day', '1d': 'day',
        'week': 'week', 'w': 'week', '7d': 'week',
        'month': 'month', 'm': 'month', '30d': 'month',
        'quarter': 'quarter', 'q': 'quarter', '3m': 'quarter', '90d': 'quarter',
        'halfyear': 'halfyear', 'hy': 'halfyear', '6m': 'halfyear', '180d': 'halfyear',
        'year': 'year', 'y': 'year', '12m': 'year', '365d': 'year',
        'all': 'all'
    }
    return mapping.get(arg, 'all')

def is_authorized(request, expected_secret: str) -> bool:
    """
    Проверяет, содержит ли заголовок X-Secret-Key ожидаемый секрет.
    Используется для защиты административных эндпоинтов.
    """
    secret = request.headers.get('X-Secret-Key', '')
    return secret == expected_secret
