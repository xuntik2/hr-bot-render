"""
Конфигурация для HR бота Мечел
Версия 1.4 - Оптимизированная для Render Free
"""

import os
import re
import logging
from typing import Optional, List

logger = logging.getLogger(__name__)

class Config:
    """Класс конфигурации с безопасной загрузкой переменных окружения"""
    
    # Паттерн для проверки формата токена бота Telegram
    TOKEN_PATTERN = r'^\d{8,11}:[A-Za-z0-9_-]{35,}$'
    
    def __init__(self):
        """Инициализация конфигурации"""
        self.token = self._find_bot_token()
        if not self.token:
            raise ValueError(
                "Токен бота не найден. Установите TELEGRAM_BOT_TOKEN или BOT_TOKEN"
            )
        
        # Директория данных
        self.data_dir = os.getenv('DATA_DIR', 'data')
        os.makedirs(self.data_dir, exist_ok=True)
        
        # Файлы данных
        self.faq_file = os.path.join(self.data_dir, os.getenv('FAQ_FILE', 'faq.csv'))
        
        # Порт сервера
        self.port = int(os.getenv('PORT', '10000'))
        
        # Уровень логирования
        self.log_level = os.getenv('LOG_LEVEL', 'INFO')
        
        # URL вебхука (опционально)
        self.webhook_url = os.getenv('WEBHOOK_URL')
        
        # Кэшированные значения
        self._admin_ids = None
        self._config_valid = False
    
    def _find_bot_token(self) -> Optional[str]:
        """Универсальный поиск токена бота"""
        possible_keys = ['TELEGRAM_BOT_TOKEN', 'BOT_TOKEN', 'BOTTOKEN']
        for key in possible_keys:
            token = os.getenv(key)
            if token and re.match(self.TOKEN_PATTERN, token):
                return token
        return None
    
    def get_bot_token(self) -> str:
        """Получение токена бота"""
        return self.token
    
    def get_faq_file(self) -> str:
        """Получение пути к файлу FAQ"""
        return self.faq_file
    
    def get_port(self) -> int:
        """Получение порта сервера"""
        return self.port
    
    def get_log_level(self) -> str:
        """Получение уровня логирования"""
        return self.log_level
    
    def get_webhook_url(self) -> Optional[str]:
        """Получение URL вебхука"""
        return self.webhook_url
    
    def get_db_connection(self):
        """
        Фиктивный метод для совместимости со старым кодом.
        В этой версии бота база данных НЕ используется.
        """
        return None  # Без логирования для производительности
    
    def get_admin_ids(self) -> List[int]:
        """Безопасное получение ID администраторов"""
        if self._admin_ids is not None:
            return self._admin_ids
        
        admin_ids_str = os.getenv('ADMIN_IDS', '')
        self._admin_ids = []
        
        if admin_ids_str:
            try:
                # Безопасный парсинг ID администраторов
                ids = []
                for id_str in admin_ids_str.split(','):
                    id_str_clean = id_str.strip()
                    if id_str_clean.isdigit():
                        ids.append(int(id_str_clean))
                    elif id_str_clean:
                        logger.warning(f"Некорректный ID администратора: '{id_str_clean}'")
                
                self._admin_ids = ids
                
            except Exception as e:
                logger.error(f"Ошибка парсинга ADMIN_IDS: {e}")
                self._admin_ids = []
        
        return self._admin_ids
    
    def validate(self) -> bool:
        """Безопасная валидация конфигурации"""
        try:
            # 1. Проверяем токен
            if not self._validate_token_format(self.token):
                logger.error("❌ Неверный формат токена бота")
                logger.info("💡 Токен должен быть в формате: 123456789:ABCdefGHIjklMNOpqrsTUVwxyZ")
                return False
            
            # 2. Проверяем порт
            if not 1 <= self.port <= 65535:
                logger.error(f"❌ Некорректный порт: {self.port}")
                return False
            
            # 3. Проверяем файл FAQ (предупреждаем, но не блокируем)
            if not os.path.exists(self.faq_file):
                logger.warning(f"⚠️ Файл FAQ не найден: {self.faq_file}")
                logger.info("💡 Бот будет использовать резервные данные из faq_data.py")
            
            self._config_valid = True
            logger.info("✅ Конфигурация валидна")
            return True
            
        except Exception as e:
            logger.error(f"❌ Ошибка валидации конфигурации: {e}")
            return False
    
    def _validate_token_format(self, token: str) -> bool:
        """Проверка формата токена бота Telegram"""
        if not token or len(token) < 40:
            return False
        
        # Проверяем по регулярному выражению
        return bool(re.match(self.TOKEN_PATTERN, token))
    
    def to_dict(self) -> dict:
        """Представление конфигурации в виде словаря (без токена)"""
        return {
            'port': self.port,
            'data_dir': self.data_dir,
            'faq_file': self.faq_file,
            'log_level': self.log_level,
            'webhook_url': self.webhook_url,
            'admin_ids_count': len(self.get_admin_ids()),
            'token_format_valid': self._validate_token_format(self.token) if self.token else False,
            'config_valid': self._config_valid
        }

# Глобальный экземпляр конфигурации
try:
    config = Config()
    if not config.validate():
        logger.warning("⚠️ Конфигурация имеет проблемы, приложение продолжит работу")
except Exception as e:
    logger.critical(f"❌ Критическая ошибка загрузки конфигурации: {e}")
    # Создаем минимальную конфигурацию для отображения ошибки
    class FallbackConfig:
        def get_bot_token(self): return "dummy_token"
        def get_faq_file(self): return "data/faq.csv"
        def get_port(self): return 10000
        def get_admin_ids(self): return []
        def validate(self): return False
        def to_dict(self): return {'error': 'Config failed to load'}
    
    config = FallbackConfig()
