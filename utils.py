import re
from datetime import datetime
from typing import Optional

def is_greeting(text: str) -> bool:
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
    if len(question) <= max_len:
        return question
    return question[:max_len - 3] + "..."

def parse_period_argument(arg: str) -> str:
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

def is_authorized(request, webhook_secret: str) -> bool:
    secret = request.headers.get('X-Secret-Key')
    if secret == webhook_secret:
        return True
    key = request.args.get('key')
    if key == webhook_secret:
        return True
    return False
