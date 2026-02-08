#!/usr/bin/env python3
"""
Файл для запуска на Render через Gunicorn
"""
from bot import app

if __name__ == "__main__":
    app.run()