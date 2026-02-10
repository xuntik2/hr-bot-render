import os

bind = f"0.0.0.0:{os.getenv('PORT', '10000')}"
worker_class = "gevent"
workers = 1
threads = 2
timeout = 120
loglevel = os.getenv("GUNICORN_LOG_LEVEL", "info")
accesslog = "-"
errorlog = "-"

print(f"🚀 Gunicorn запущен с конфигурацией:")
print(f"   Воркер: {worker_class}")
print(f"   Воркеров: {workers}")
print(f"   Порт: {bind}")
print(f"   Таймаут: {timeout} сек")
