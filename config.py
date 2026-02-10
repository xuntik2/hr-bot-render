"""
Конфигурация для HR бота
Версия 9.3.4 - Улучшенная валидация с проверкой формата токена
"""

import os
import json
import re
from typing import Optional, List
from dataclasses import dataclass

@dataclass
class BotConfig:
    """Конфигурация бота"""
    token: str
    admin_ids: list[int]
    data_dir: str
    faq_file: str
    content_file: str
    port: int
    log_level: str
    webhook_url: Optional[str] = None

class Config:
    """Класс для работы с конфигурацией с улучшенной валидацией"""
    
    # Паттерн для проверки формата токена бота Telegram
    # Формат: 1234567890:ABCdefGHIjklMNOpqrsTUVwxyZ-0123456789
    TOKEN_PATTERN = r'^\d{8,11}:[A-Za-z0-9_-]{35,}$'
    
    def __init__(self):
        self._config = self._load_config()
    
    def _load_config(self) -> BotConfig:
        """Загрузка конфигурации из переменных окружения"""
        
        # Универсальный поиск токена бота (поддерживает все варианты)
        token = self._find_bot_token()
        if not token:
            raise ValueError(
                "Токен бота не найден. Установите одну из переменных окружения:\n"
                "- TELEGRAM_BOT_TOKEN (рекомендуется)\n"
                "- BOT_TOKEN\n"
                "- BOTTOKEN\n\n"
                "В Render.com добавьте переменную в разделе Environment."
            )
        
        # Проверяем формат токена
        if not self._validate_token_format(token):
            raise ValueError(
                f"Неверный формат токена бота.\n"
                f"Токен должен быть в формате: 123456789:ABCdefGHIjklMNOpqrsTUVwxyZ\n"
                f"Получено: {token[:10]}... (длина: {len(token)} символов)\n\n"
                f"Проверьте токен в @BotFather и убедитесь, что он скопирован полностью."
            )
        
        # ID администраторов (опционально)
        admin_ids_str = os.getenv('ADMIN_IDS', '')
        admin_ids = []
        if admin_ids_str:
            try:
                admin_ids = [int(id.strip()) for id in admin_ids_str.split(',')]
            except ValueError:
                print("⚠️ Неверный формат ADMIN_IDS. Используйте: 123456,789012")
        
        # Директория с данными
        data_dir = os.getenv('DATA_DIR', 'data')
        os.makedirs(data_dir, exist_ok=True)
        
        # Файлы с данными
        faq_file = os.getenv('FAQ_FILE', 'faq.csv')
        content_file = os.getenv('CONTENT_FILE', 'контент.xlsx')
        
        # Порт сервера (Render использует PORT из окружения)
        port = int(os.getenv('PORT', '10000'))
        
        # Уровень логирования
        log_level = os.getenv('LOG_LEVEL', 'INFO')
        
        # URL вебхука (опционально, обычно генерируется автоматически)
        webhook_url = os.getenv('WEBHOOK_URL')
        
        return BotConfig(
            token=token,
            admin_ids=admin_ids,
            data_dir=data_dir,
            faq_file=os.path.join(data_dir, faq_file),
            content_file=os.path.join(data_dir, content_file),
            port=port,
            log_level=log_level,
            webhook_url=webhook_url
        )
    
    def _find_bot_token(self) -> Optional[str]:
        """Универсальный поиск токена бота среди всех возможных переменных"""
        possible_keys = [
            'TELEGRAM_BOT_TOKEN',  # Стандартное имя в Render
            'BOT_TOKEN',           # Альтернативное имя
            'BOTTOKEN',            # Еще один вариант
            'TELEGRAM_TOKEN',      # Для обратной совместимости
            'TOKEN'                # Минималистичный вариант
        ]
        
        for key in possible_keys:
            token = os.getenv(key)
            if token:
                # Проверяем формат токена сразу при поиске
                if self._validate_token_format(token):
                    if key != 'TELEGRAM_BOT_TOKEN':
                        print(f"✅ Используется токен из переменной: {key}")
                    return token
                else:
                    print(f"⚠️ Неверный формат токена в переменной {key}")
        
        return None
    
    def _validate_token_format(self, token: str) -> bool:
        """Проверка формата токена бота Telegram"""
        if not token or len(token) < 40:
            return False
        
        # Проверяем по регулярному выражению
        pattern_matched = bool(re.match(self.TOKEN_PATTERN, token))
        
        # Дополнительные проверки
        has_correct_format = ':' in token
        parts = token.split(':')
        has_numeric_id = len(parts) == 2 and parts[0].isdigit()
        has_secret = len(parts) == 2 and len(parts[1]) >= 35
        
        return pattern_matched and has_correct_format and has_numeric_id and has_secret
    
    def get_bot_token(self) -> str:
        """Получение токена бота"""
        return self._config.token
    
    def get_admin_ids(self) -> list[int]:
        """Получение ID администраторов"""
        return self._config.admin_ids
    
    def get_data_dir(self) -> str:
        """Получение директории с данными"""
        return self._config.data_dir
    
    def get_faq_file(self) -> str:
        """Получение пути к файлу FAQ"""
        return self._config.faq_file
    
    def get_content_file(self) -> str:
        """Получение пути к файлу контента"""
        return self._config.content_file
    
    def get_port(self) -> int:
        """Получение порта сервера"""
        return self._config.port
    
    def get_log_level(self) -> str:
        """Получение уровня логирования"""
        return self._config.log_level
    
    def get_webhook_url(self) -> Optional[str]:
        """Получение URL вебхука"""
        return self._config.webhook_url
    
    def validate(self) -> bool:
        """Проверка конфигурации"""
        try:
            # Проверяем токен
            if not self._validate_token_format(self._config.token):
                print("❌ Неверный формат токена бота")
                print("💡 Токен должен быть в формате: 123456789:ABCdefGHIjklMNOpqrsTUVwxyZ")
                print(f"   Получено: {self._config.token[:10]}... (длина: {len(self._config.token)})")
                return False
            
            # Проверяем существование файлов
            if not os.path.exists(self._config.faq_file):
                print(f"⚠️ Файл FAQ не найден: {self._config.faq_file}")
                print("💡 Создайте файл с данными в формате CSV:")
                print("   Категория,Вопрос,Ответ")
                print("   HR,Как получить отпуск?,Обратитесь к менеджеру")
                print("   Или поместите существующий файл faq.csv в папку data/")
                
                # Предлагаем создать файл только в интерактивном режиме
                if __name__ == "__main__":
                    response = input("Создать пустой файл FAQ? (y/N): ")
                    if response.lower() == 'y':
                        os.makedirs(os.path.dirname(self._config.faq_file), exist_ok=True)
                        with open(self._config.faq_file, 'w', encoding='utf-8') as f:
                            f.write('Категория,Вопрос,Ответ\n')
                            f.write('HR,Как получить отпуск?,Обратитесь к менеджеру\n')
                        print(f"✅ Создан пример файла FAQ: {self._config.faq_file}")
                    else:
                        return False
                else:
                    # В неинтерактивном режиме не создаем файл
                    return False
            
            # Проверяем порт
            if not 1 <= self._config.port <= 65535:
                print(f"⚠️ Некорректный порт: {self._config.port}")
                return False
            
            # Проверяем наличие директории данных
            if not os.path.exists(self._config.data_dir):
                print(f"✅ Создана директория данных: {self._config.data_dir}")
                os.makedirs(self._config.data_dir, exist_ok=True)
            
            print("✅ Конфигурация валидна")
            return True
            
        except Exception as e:
            print(f"❌ Ошибка валидации конфигурации: {e}")
            return False
    
    def to_dict(self) -> dict:
        """Представление конфигурации в виде словаря (без токена)"""
        return {
            'admin_ids': self._config.admin_ids,
            'data_dir': self._config.data_dir,
            'faq_file': self._config.faq_file,
            'content_file': self._config.content_file,
            'port': self._config.port,
            'log_level': self._config.log_level,
            'webhook_url': self._config.webhook_url,
            'token_length': len(self._config.token) if self._config.token else 0,
            'token_format_valid': self._validate_token_format(self._config.token) if self._config.token else False,
            'token_source': self._find_token_source()
        }
    
    def _find_token_source(self) -> str:
        """Определение источника токена"""
        possible_keys = ['TELEGRAM_BOT_TOKEN', 'BOT_TOKEN', 'BOTTOKEN', 'TELEGRAM_TOKEN', 'TOKEN']
        for key in possible_keys:
            if os.getenv(key):
                return key
        return 'unknown'
    
    def get_token_source(self) -> str:
        """Публичный метод для получения источника токена"""
        return self._find_token_source()

# Глобальный экземпляр конфигурации
config = Config()

# Валидация при импорте
if __name__ == "__main__":
    if config.validate():
        print("✅ Конфигурация загружена успешно")
        print(json.dumps(config.to_dict(), indent=2, ensure_ascii=False))
    else:
        print("❌ Ошибка в конфигурации")
        sys.exit(1)
else:
    # При импорте модуля просто загружаем конфигурацию
    try:
        config.validate()
    except Exception as e:
        print(f"⚠️ Предупреждение при валидации конфигурации: {e}")
