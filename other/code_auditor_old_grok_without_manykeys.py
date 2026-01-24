"""
==============================================================================
CODE AUDITOR PRO
Enterprise-grade Code Audit System

ФУНКЦИОНАЛ:
1. Изоляция ошибок: Сбой модуля не ломает процесс.
2. Матрица ответственности:
   - HTML/CSS -> W3C Validator + Gemini AI
   - SCSS/SASS -> Gemini AI
   - JS/TS/React -> Gemini AI
3. State Persistence: Атомарное сохранение и Resume.
4. Transparency: Четкое разделение Clean / Dirty / Not Checked.

CHANGELOG (New Design):
- Добавлено отслеживание покрытия (Coverage Tracking).
- Новый "Корпоративный" HTML отчет.
- Статус "Not Checked" для файлов без активных валидаторов.
- Визуальные бейджи активных инструментов для каждого файла.

АВТОР: Gemini Pro (Enterprise Edition)
==============================================================================
# ==============================================================================
# КОНФИГУРАЦИЯ
# ==============================================================================
# --- GEMINI API CONFIG ---
GEMINI_API_KEY=0
#GEMINI_MODEL=models/gemini-1.5-flash
#GEMINI_MODEL=models/gemini-3-flash-preview
#GEMINI_MODEL = "models/gemini-2.5-flash-lite"
GEMINI_MODEL=models/gemini-2.5-flash

# --- GROK API CONFIG ---
XAI_API_KEY=0
GROK_MODEL=grok-2-latest

GROK_MAX_CHARS=45000

# --- PATHS ---
SOURCE_DIR=src

# --- GLOBAL TOGGLES ---
#ENABLE_W3C=True
#ENABLE_W3C=False
#ENABLE_GEMINI=True
#ENABLE_GEMINI=False
ENABLE_GROK=True


# --- CONTENT FILTERS ---
# HTML файлы
CHECK_HYPERTEXT=True
# CSS, SCSS, SASS
CHECK_STYLES=True
# JS, JSX, TS, TSX
CHECK_SCRIPTS=True

# Задержка между файлами в секундах (чтобы не банил W3C)
API_SLEEP=7,30

# Максимальное количество символов кода для отправки в Gemini
GEMINI_MAX_CHARS=45000
# Продолжать ли аудит с места остановки (True/False)
RESUME_AUDIT=True
# Имя файла для хранения промежуточного прогресса
TEMP_STATE_FILE=audit_state.temp.json
==============================================================================
"""

import os
import json
import time
import re
from wsgiref.validate import validator
import requests
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Optional
import logging


# --- Инициализация окружения ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("❌ CRITICAL: pip install google-genai requests python-dotenv")
    exit(1)

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s | %(levelname)s | %(message)s', datefmt='%H:%M:%S')
logger = logging.getLogger("CodeAuditor")

# =================  DATA MODELS (OOP) =================

@dataclass
class Issue:
    """Единица найденной проблемы"""
    type: str       # error, warning, suggestion
    line: int
    message: str
    source: str     # Имя валидатора
    suggestion: Optional[str] = None

@dataclass
class FileReport:
    """Результат проверки одного файла"""
    path: str
    timestamp: str
    issues: List[Issue] = field(default_factory=list)
    # НОВОЕ: Список валидаторов, которые реально проверяли этот файл
    checked_by: List[str] = field(default_factory=list) 

# =================   Exception Class   =================

class APIBannedException(Exception):
    """Специальное исключение для сигнализации о блокировке API (429/Quota)"""
    pass



# =================   Headers Builder  =================

class HeadersBuilder:
    """
    Класс для генерации реалистичных HTTP-заголовков.
    Использует статические массивы данных для маскировки под реального пользователя.
    """

    # --- СТАТИЧЕСКИЕ ДАННЫЕ (База знаний билдера) ---
    
    # Популярные User-Agents (Windows, macOS, Linux - Chrome, Firefox, Safari)
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
        "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:109.0) Gecko/20100101 Firefox/121.0",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.1 Safari/605.1.15"
    ]

    # Варианты языковых настроек
    LANGUAGES = [
        "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
        "en-US,en;q=0.9",
        "ru-RU,ru;q=0.9",
        "en-GB,en;q=0.8,ru;q=0.6"
    ]

    # Варианты Accept (браузеры запрашивают разное, имитируем это)
    ACCEPT_TYPES = [
        "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
        "application/json, text/plain, */*",
        "*/*"
    ]

    @staticmethod
    def build_headers(ext: Optional[str] = None, custom_ctype: Optional[str] = None) -> dict:
        """
        Основной метод генерации заголовка.
        :param ext: Расширение файла (напр. '.html') для автоматического определения Content-Type.
        :param custom_ctype: Можно передать тип вручную (напр. 'application/json').
        :return: Словарь заголовков.
        """
        
        # 1. Определяем Content-Type
        if custom_ctype:
            ctype = custom_ctype
        elif ext:
            # Логика из вашего исходного кода
            ctype = "text/html" if ext == '.html' else "text/css"
        else:
            ctype = "text/plain"

        # 2. Собираем финальный словарь
        # Используем random.choice для выбора случайного элемента из статических списков
        headers = {
            'User-Agent': random.choice(HeadersBuilder.USER_AGENTS),
            'Accept-Language': random.choice(HeadersBuilder.LANGUAGES),
            'Accept': random.choice(HeadersBuilder.ACCEPT_TYPES),
            'Content-Type': f"{ctype}; charset=utf-8",
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'none',
            'Sec-Fetch-User': '?1',
            'DNT': '1'  # Do Not Track - добавляет человечности
        }

        return headers

# --- ПРИМЕР ИСПОЛЬЗОВАНИЯ В АУДИТОРЕ ---

# Где-то в коде W3CValidator:
# builder = HeadersBuilder()
# current_headers = builder.build_headers(ext='.html')

# =================  ABSTRACT VALIDATOR =================

class BaseValidator(ABC):
    """Базовый класс для всех проверочных модулей"""
    def __init__(self, name: str, short_name: str, enabled: bool, **kwargs):
        self.name = name
        self.short_name = short_name # Короткое имя для бейджиков (напр. "AI", "W3C")
        self.enabled = enabled
        for key, value in kwargs.items():
            setattr(self, key, value)

    @abstractmethod
    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        """Запуск проверки"""
        pass

    @abstractmethod
    def can_check(self, ext: str) -> bool:
        """Проверка применимости валидатора к расширению файла"""
        pass

# =================  CONCRETE VALIDATORS =================

class W3CValidator(BaseValidator):
    """Модуль строгой проверки стандартов (HTML/CSS)"""
    
    API_URL = "https://validator.w3.org/nu/?out=json"
    
    def __init__(self, enabled: bool):
        super().__init__(name="W3C Validator", short_name="W3C", enabled=enabled)
        self.url = "https://validator.w3.org/nu/?out=json"
        self.disabled = False  # Флаг для мягкого отключения при 429 ошибке

    def can_check(self, ext: str) -> bool:
        return ext in ['.html', '.css']

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.can_check(ext):
            return []

        """ old header way 
        ctype = "text/html" if ext == '.html' else "text/css"
        
        headers = {
                'User-Agent': 'Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36',
                'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
                'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8',
                'Content-Type': f'{ctype}; charset=utf-8'
            }
        """
        
        #create http header 
        builder = HeadersBuilder()
        headers = builder.build_headers(ext='.html')
        

        try:
            resp = requests.post(self.API_URL, data=content.encode('utf-8'), headers=headers, timeout=15)
            status_code = resp.status_code

            if status_code == 200:
                messages = resp.json().get('messages', [])
                return [Issue(
                    type="error" if m.get('type') == 'error' else "warning",
                    line=m.get('lastLine', m.get('firstLine', 0)),
                    message=f"[Standard] {m.get('message')}",
                    source=self.name
                ) for m in messages]

            elif status_code == 429:
                self.enabled = False # Мягкое отключение внутри объекта
                logger.error(f"\n   [{self.name}] 429: Too Many Requests!")
                return [Issue(type="error", line=0, message="🚫 W3C API Rate Limit (429)", source=self.name)]
            elif status_code == 404:
                return [Issue(type="warning", line=0, message="⚠️ W3C API Unavailable (404)", source=self.name)]
            else:
                return [Issue(type="error", line=0, message=f"W3C API Error: {status_code}", source=self.name)]

        except Exception as e:
            logger.error(f"\n   [{self.name}] Connection Error: {e}")
            return [Issue(type="warning", line=0, message=f"W3C Connection Failed", source=self.name)]

class GeminiValidator(BaseValidator):
    """Модуль интеллектуального анализа (AI)"""

    def __init__(self, enabled: bool, api_keys: list, model: str, max_chars: int = 30000):
        super().__init__(name="Gemini AI", short_name="AI", enabled=enabled)
        self.model = model
        self.max_chars = max_chars
        self.api_keys = api_keys # Список всех ключей
        self.call_count = 0      # Счетчик вызовов
        
        if self.enabled and not self.api_keys:
            logger.warning(f"\n   Gemini API Keys are missing. AI disabled.")
            self.enabled = False
            
        """
        больше не актуально, тк у нас не один ключ, а массив ключей
        else:
             
            try:
                self.client = genai.Client(api_key=api_key)
            except Exception as e:
                logger.error(f"\n   Failed to init Gemini: {e}")
                self.enabled = False
                """

    def can_check(self, ext: str) -> bool:
        # AI может проверять почти всё, что текстовое
        return ext in ['.html', '.css', '.scss', '.sass', '.js', '.jsx', '.ts', '.tsx']

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.can_check(ext):
            return []

        issues = []
        if len(content) > self.max_chars:
            msg = f"File truncated ({len(content)} > {self.max_chars} chars)."
            issues.append(Issue(type="warning", line=0, message=f"⚠️ {msg}", source=self.name))
        
        context = "code"
        if ext in ['.css', '.scss', '.sass']: context = "styles (SCSS/CSS)"
        elif ext in ['.js', '.jsx', '.ts', '.tsx']: context = "logic & security (JS/React)"
        elif ext == '.html': context = "HTML structure & semantics"

        prompt = f"""
        Role: Senior Code Reviewer. Task: Audit file {path}.
        Context: Analyzing {context}.
        
        Rules:
        1. Find logical bugs, security risks (XSS), memory leaks.
        2. Check for DRY, clean code, naming conventions.
        3. Be concise. High signal-to-noise ratio.
        
        Response JSON format:
        {{
            "issues": [
                {{
                    "type": "error"|"warning"|"suggestion",
                    "line": <int>,
                    "message": "<text_ru>",
                    "suggestion": "<fix_code_snippet_if_needed>"
                }}
            ]
        }}
        
        CODE:
        {content[:self.max_chars]}
        """

        # ФОРМУЛА РОТАЦИИ:
        # Берем индекс по остатку от деления (напр. 9 файлов % 4 ключа)
        current_idx = self.call_count % len(self.api_keys)
        current_key = self.api_keys[current_idx]
        
        attempts = 0
        max_attempts = len(self.api_keys) # Пытаемся не больше раз, чем у нас есть ключей
        
        while attempts < max_attempts:
            current_idx = self.call_count % len(self.api_keys)
            current_key = self.api_keys[current_idx]
            
            try:
                # Создаем клиента именно под выбранный ключ
                temp_client = genai.Client(api_key=current_key)

                response = temp_client.models.generate_content(
                    model=self.model,
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )          

                # УСПЕХ: сдвигаем счетчик для СЛЕДУЮЩЕГО файла и выходим из цикла попыток
                self.call_count += 1

                clean_json = re.sub(r"^```json\s*|\s*```$", "", response.text, flags=re.MULTILINE).strip()
                data = json.loads(clean_json)
                
                # Собираем список из JSON
                ai_list = [Issue(
                    type=i.get('type', 'warning'),
                    line=i.get('line', 0),
                    message=i.get('message', 'Issue detected'),
                    source=self.name,
                    suggestion=i.get('suggestion')
                ) for i in data.get('issues', [])]
                
                # Возвращаем всё вместе: и наши локальные issues, и то что прислал ИИ
                return issues + ai_list
                    
            except Exception as e:
                
                err_msg = str(e).lower()
                
                # 1. Ошибка лимитов - пробуем следующий ключ для ЭТОГО ЖЕ файла
                if "429" in err_msg or "quota" in err_msg:
                    logger.warning(f"  ⚠️ Ключ #{current_idx + 1} исчерпан. Повторная попытка с другим ключом...")
                    self.call_count += 1 
                    attempts += 1 # Считаем попытку
                    continue      # Возвращаемся в начало цикла while

                # 2. Ошибка модели - выключаем модуль совсем
                elif "404" in err_msg:
                    self.enabled = False 
                    logger.error(f"  ❌ [{self.name}] Неверная модель. ИИ отключен.")
                    return issues + [Issue(type="error", line=0, message="Неверная модель Gemini", source=self.name)]

                # 3. Все остальные ошибки (сеть, JSON и т.д.)
                else:
                    logger.error(f"  ❌ Ошибка ключа #{current_idx + 1}: {e}")
                    # Пробуем другой ключ при любой ошибке:
                    self.call_count += 1
                    attempts += 1
                    continue
                
                # Если цикл закончился, а мы ничего не вернули — значит все ключи сдохли
        logger.error(f"  💀 Все ключи исчерпаны для файла: {path}")
        return [Issue(type="error", line=0, message="Все ключи Gemini достигли лимита квоты", source=self.name)]
                

class GrokDualValidator(BaseValidator):
    """
    Модуль xAI Grok (Dual Mode).
    Выполняет роль и валидатора стандартов (как W3C), и логического анализатора.
    """
    def __init__(self, enabled: bool, api_key: str, model: str, max_chars: int):
        super().__init__(name="Grok Dual", short_name="GROK", enabled=enabled)
        self.model = model
        self.api_key = api_key
        self.max_chars = max_chars
        self.base_url = "https://api.x.ai/v1/chat/completions"

    def can_check(self, ext: str) -> bool:
        # Grok проверяет и фронтенд, и бэкенд
        return ext in ['.html', '.css', '.scss', '.js', '.jsx', '.ts', '.tsx', '.py', '.php']

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.api_key: return []

        # Настройка роли в зависимости от типа файла
        if ext in ['.html', '.css', '.scss']:
            role_desc = "Strict W3C Standards Emulator & Frontend Architect"
        else:
            role_desc = "Senior Security Engineer & Polyglot Programmer"

        prompt = f"""
        IMPORTANT: Be an aggressive reviewer. Even if the code works, look for:
        - Performance bottlenecks.
        - Potential security vulnerabilities.
        - Code style inconsistencies.
        If the code is perfect, explain WHY in a suggestion, but try to find at least one improvement.
        
        Role: {role_desc}.
        Task: Perform a DUAL-LAYER AUDIT for file: {path}.
        
        LAYER 1: STANDARDS & SYNTAX
        - Act as a strict validator (like W3C or ESLint). Report syntax errors, deprecated tags.
        
        LAYER 2: LOGIC & INTELLIGENCE
        - Analyze logic flows, security risks, complexity.

        Return a single JSON object with a list of issues.
        JSON Schema:
        {{ "issues": [ {{ "type": "error"|"warning"|"suggestion", "line": <int>, "message": "[Standards]... OR [Logic]... (in Russian)", "suggestion": "fix" }} ] }}

        CODE CONTENT:
        {content[:self.max_chars]}
        """
        

        headers = { "Authorization": f"Bearer {self.api_key}", "Content-Type": "application/json" }
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are an automated code auditor. Output strict JSON."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }

        try:
            resp = requests.post(self.base_url, json=payload, headers=headers, timeout=40)
            status_code = resp.status_code

            if status_code == 200:
                data = resp.json()
                raw_content = data['choices'][0]['message']['content']
                parsed = json.loads(raw_content)
                return [Issue(
                    type=i.get('type', 'warning'),
                    line=i.get('line', 0),
                    message=i.get('message'),
                    source=self.name,
                    suggestion=i.get('suggestion')
                ) for i in parsed.get('issues', [])]

            elif status_code == 403:
                # Обработка ошибки доступа (как у тебя с балансом)
                self.enabled = False
                logger.error(f"\n   🚫 [{self.name}] Ошибка доступа (403): Проверьте баланс или токены.")
                return [Issue(type="error", line=0, message="🚫 Grok API No Permission / No Credits", source=self.name)]

            elif status_code == 429:
                self.enabled = False
                logger.error(f"\n   🛑 [{self.name}] Лимит запросов (429). Модуль отключен.")
                raise APIBannedException("Grok Rate Limit")

            else:
                logger.error(f"\n   ❌ [{self.name}] API Error: {status_code}")
                return [Issue(type="error", line=0, message=f"Grok API Error: {status_code}", source=self.name)]

        except Exception as e:
            logger.error(f"\n   [{self.name}] Connection/Parse Error: {e}")
            return [Issue(type="warning", line=0, message=f"Grok Failed: {str(e)[:50]}", source=self.name)]
        
# =================  ENGINE (ORCHESTRATOR) =================

class AuditEngine:
    """Главный класс-оркестратор"""
    
    def __init__(self):
        self.load_config()
        self.temp_file = self.cfg['temp_file']
        self.validators: List[BaseValidator] = []
        self.reports: List[FileReport] = []
        
        # Регистрация плагинов
        
        # W3C Validator
        self.validators.append(W3CValidator(enabled=self.cfg['w3c_on']))
        # Gemini AI Validator
        self.validators.append(GeminiValidator(
            enabled=self.cfg['gemini_on'], 
            api_keys=self.cfg['gemini_keys'], # Передаем весь список ключей
            model=self.cfg['gemini_model'],
            max_chars=self.cfg['gemini_max_chars']
        ))
        # Grok Dual Validator
        self.validators.append(GrokDualValidator(
            enabled=self.cfg['grok_enabled'],
            api_key=self.cfg['grok_key'],
            model=self.cfg['grok_model'],
            max_chars=self.cfg['grok_limit']
        ))

    def load_config(self):
        def get_bool(k, d="True"): return os.getenv(k, d).lower() in ("true", "1", "yes", "on")
        
        # берем массив ключей вместо ключа 
        raw_keys = os.getenv("GEMINI_API_KEY", "0")
        gemini_keys_list = [k.strip() for k in raw_keys.split(",") if k.strip()]
        gemini_keys_count = len(gemini_keys_list)
        
        self.cfg = {
            'src': os.getenv("SOURCE_DIR", "src"),
            'gemini_keys': gemini_keys_list,
            'gemini_keys_count': gemini_keys_count,
            'gemini_model': os.getenv("GEMINI_MODEL", "models/gemini-2.0-flash"),
            'w3c_on': get_bool("ENABLE_W3C"),
            'gemini_on': get_bool("ENABLE_GEMINI"),
            'filter_html': get_bool("CHECK_HYPERTEXT"),
            'filter_css': get_bool("CHECK_STYLES"),
            'filter_js': get_bool("CHECK_SCRIPTS"),
            'api_sleep': os.getenv("API_SLEEP", "10.0"),
            'gemini_max_chars': int(os.getenv("GEMINI_MAX_CHARS", "30000")),
            'resume_audit': get_bool("RESUME_AUDIT", "True"),
            'temp_file': os.getenv("TEMP_STATE_FILE", "audit_state.temp.json"),
            'grok_enabled': get_bool("ENABLE_GROK"),
            'grok_key': os.getenv("XAI_API_KEY"),
            'grok_model': os.getenv("GROK_MODEL", "grok-2-latest"),
            'grok_limit': int(os.getenv("GROK_MAX_CHARS", "45000")),
        }

    def print_config(self):
        print("\n" + "="*100)
        print("🛠  CODE AUDITOR: CONFIGURATION")
        print("="*100)
        print(f"📂 Source:        {self.cfg['src']}")
        print(f"🌐 W3 Validator:  {self.cfg['w3c_on']}")
        print(f"🤖 Gemini Model:  {self.cfg['gemini_model']}")
        print(f"🔑 Gemini Keys:   {self.cfg['gemini_keys_count']}")
        print(f"🧠 Grok Model:    {self.cfg['grok_model']}")
        print(f"⏱  Sleep:         {self.cfg['api_sleep']}s")
        print(f"🔄 Resume Audit:  {'✅ Yes' if self.cfg['resume_audit'] else '⬜ No'}")
        print("-" * 100)
        print(f"🔌 Modules:")

        for v in self.validators:
            print(f"   • {v.name:<15} {'✅ ON' if v.enabled else '⬜ OFF'}")

        print("\n")

        # Секция модулей (валидаторов)
        print(f"🔌 Modules Status:")
        for v in self.validators:
            # Определяем статус на основе конфига
            status = "✅ ON" if v.enabled else "⬜ OFF"
            
            # Добавляем лимиты символов для AI моделей в скобках
            extra = ""
            if "Gemini" in v.name:
                extra = f" ({self.cfg['gemini_max_chars']} chars)"
            elif "Grok" in v.name:
                extra = f" ({self.cfg['grok_limit']} chars)"
                
            print(f"   • {v.name:<15} {status}{extra}")
            
        print("="*100 + "\n")

    def scan(self) -> List[str]:
        files_to_check = []
        ext_map = {
            'html': ['.html'],
            'css': ['.css', '.scss', '.sass'],
            'js': ['.js', '.jsx', '.ts', '.tsx']
        }
        
        for root, _, files in os.walk(self.cfg['src']):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                path = os.path.join(root, f)
                if ext in ext_map['html'] and self.cfg['filter_html']: files_to_check.append(path)
                elif ext in ext_map['css'] and self.cfg['filter_css']: files_to_check.append(path)
                elif ext in ext_map['js'] and self.cfg['filter_js']: files_to_check.append(path)
        return files_to_check

    def restore_state(self) -> List[FileReport]:
        if os.path.exists(self.temp_file):
            try:
                with open(self.temp_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Используем .get для checked_by для совместимости со старыми файлами
                    return [FileReport(
                        path=r['path'], 
                        timestamp=r['timestamp'], 
                        issues=[Issue(**i) for i in r['issues']],
                        checked_by=r.get('checked_by', [])
                    ) for r in data]
            except Exception as e:
                logger.warning(f"\n   Resume failed: {e}")
        return []
    
    def save_state(self):
        temp_shadow = self.temp_file + ".tmp"
        try:
            with open(temp_shadow, 'w', encoding='utf-8') as f:
                json.dump([asdict(r) for r in self.reports], f, ensure_ascii=False, indent=2)
            if os.path.exists(temp_shadow):
                os.replace(temp_shadow, self.temp_file)
        except Exception as e:
            logger.error(f"\n   State save error: {e}")

    def run(self):
        print(f"🚀 ЗАПУСК CODE AUDITOR PRO...")
        self.print_config()
        
        all_files = self.scan()
        if not all_files:
            print(f"⚠️ Файлы не найдены.")
            return

        # Resume Logic
        if self.cfg['resume_audit']:
            self.reports = self.restore_state()
            processed_paths = {r.path for r in self.reports}
            if processed_paths:
                print(f"🔄 Resume: {len(processed_paths)} файлов загружено из кэша.")
        else:
            self.reports = []
            processed_paths = set()

        queue = [f for f in all_files if f not in processed_paths]
        print(f"📊 Очередь: {len(queue)} файлов.")

        # Указываем желаемую ширину колонки для имени файла 
        FILENAME_WIDTH = 70
        
        for idx, path in enumerate(queue, 1):
            filename = os.path.basename(path)
            
            # Если имя файла слишком длинное, обрезаем его и добавляем многоточие
            if len(filename) > FILENAME_WIDTH:
                display_name = filename[:FILENAME_WIDTH-3] + "..."
            else:
                # Дополняем точками до фиксированной ширины
                display_name = filename.ljust(FILENAME_WIDTH, ".")

            print(f"👉 [{idx}/{len(queue)}] {display_name} ", end="", flush=True)
            
            file_issues = []
            checked_by_modules = [] # Кто реально проверил этот файл
            api_called = False
            
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                if not content.strip():
                    print(" ⏩ Empty (Skipped)")
                    self.reports.append(FileReport(path, datetime.now().strftime("%H:%M"), [], []))
                    self.save_state()
                    continue

                ext = os.path.splitext(path)[1].lower()
                
                # 1. Проверяем, остались ли живые валидаторы вообще
                alive_any = any(v.enabled for v in self.validators)
                if not alive_any:
                    print("\n\n❌ КРИТИЧЕСКАЯ ОШИБКА: Все API заблокированы или исчерпаны.")
                    print("🏁 Досрочное завершение. Сохранение отчета...")
                    break # Выход из цикла файлов (переход к генерации отчета)
                
                for validator in self.validators:
                    # 2. Проверяем включен ли он (is_banned переключает enabled в False)
                    if validator.enabled and validator.can_check(ext):
                        try:
                            checked_by_modules.append(validator.short_name)
                            issues = validator.check(path, content, ext)
                            if issues:
                                file_issues.extend(issues)
                            api_called = True
                        except APIBannedException:
                            # Модуль только что себя отключил, переходим к следующему в этом файле
                            continue
                        except Exception as ve:
                            print() # Принудительный переход на новую строку
                            logger.error(f"\n   Error {validator.name}: {ve}")
                            

                # Сохраняем отчет (Clean, Dirty или Not Checked)
                new_report = FileReport(
                    path=path, 
                    timestamp=datetime.now().strftime("%H:%M"), 
                    issues=file_issues,
                    checked_by=checked_by_modules # <-- Сохраняем покрытие
                )
                self.reports.append(new_report)
                self.save_state()
                
                # Вывод статуса в консоль
                if not checked_by_modules:
                    print(" ⚪ Not Checked (No active validators)")
                elif not file_issues:
                    print(f" ✅ Clean [{', '.join(checked_by_modules)}]")
                else:
                    print(f" ⚠️ {len(file_issues)} issues")
                    
                if api_called:
                    # Читаем конфигурацию и очищаем от лишних символов
                    sleep_cfg = str(self.cfg['api_sleep']).replace(" ", "").replace("\xa0", "")
                    
                    if "," in sleep_cfg:
                        try:
                            # Парсим диапазон (например, "3,7")
                            mn, mx = map(float, sleep_cfg.split(","))
                            st = random.uniform(mn, mx)
                        except Exception: 
                            st = 10.0
                    else:
                        try:
                            st = float(sleep_cfg)
                        except:
                            st = 10.0

                    # Живой обратный отсчет в одной строке
                    for remaining in range(int(st), 0, -1):
                        # \r возвращает курсор в начало строки, flush=True принудительно выводит текст
                        print(f" ⏳ Ожидание: {remaining}s...   ", end="\r", flush=True)
                        time.sleep(1)
                    
                    # Полная очистка строки перед переходом к следующему файлу
                    print(" " * 40, end="\r")

            except Exception as e:
                print(f" ❌ Fatal Error: {e}")


        self.generate_report()
        
        if os.path.exists(self.temp_file):
            try:
                os.remove(self.temp_file)
            except: pass

    def generate_report(self):
        """Генерация профессионального отчета с дизайном"""
        
        # Статистика
        stats = {
            'total': len(self.reports),
            'dirty': 0,
            'clean': 0,
            'skipped': 0, # Не проверялись
            'errors': 0,
            'warnings': 0
        }
        
        active_tools = [v.short_name for v in self.validators if v.enabled]

        for r in self.reports:
            if not r.checked_by:
                stats['skipped'] += 1
            elif r.issues:
                stats['dirty'] += 1
                for i in r.issues:
                    if i.type == 'error': stats['errors'] += 1
                    else: stats['warnings'] += 1
            else:
                stats['clean'] += 1

        # CSS Styles (Corporate Design)
        style = """
            :root { 
                --bg-body: #f8fafc; --bg-card: #ffffff; 
                --text-main: #334155; --text-muted: #64748b;
                --color-success: #10b981; --color-danger: #ef4444; 
                --color-warning: #f59e0b; --color-info: #3b82f6;
                --color-neutral: #94a3b8;
                --border: #e2e8f0;
            }
            body { font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg-body); color: var(--text-main); margin: 0; padding: 40px; line-height: 1.5; }
            .container { max-width: 1200px; margin: 0 auto; }
            
            /* Header */
            .header { background: #1e293b; color: white; padding: 30px; border-radius: 12px; margin-bottom: 30px; display: flex; justify-content: space-between; align-items: center; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }
            .header h1 { margin: 0; font-size: 1.5rem; letter-spacing: 0.5px; }
            .header-meta { text-align: right; font-size: 0.9rem; color: #94a3b8; }
            .active-tools span { background: #334155; padding: 4px 10px; border-radius: 4px; font-weight: bold; color: #60a5fa; margin-left: 5px; font-size: 0.8rem; }
            .no-tools { color: #f87171; font-weight: bold; }

            /* Stats Grid */
            .stats { display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 40px; }
            .stat-card { background: var(--bg-card); padding: 20px; border-radius: 10px; border: 1px solid var(--border); text-align: center; }
            .stat-val { font-size: 2.2rem; font-weight: 700; display: block; margin-bottom: 5px; }
            .stat-label { color: var(--text-muted); font-size: 0.9rem; text-transform: uppercase; letter-spacing: 1px; }
            
            /* File List */
            .file-block { background: var(--bg-card); border-radius: 8px; margin-bottom: 16px; border: 1px solid var(--border); overflow: hidden; }
            
            /* Status Indicators */
            .file-header { padding: 12px 20px; display: flex; justify-content: space-between; align-items: center; background: #f1f5f9; font-weight: 600; font-size: 0.95rem; }
            .st-clean { border-left: 5px solid var(--color-success); }
            .st-dirty { border-left: 5px solid var(--color-danger); }
            .st-skipped { border-left: 5px solid var(--color-neutral); background: #f8fafc; color: var(--text-muted); }
            
            /* Общие стили бейджей */
            .badge { 
                padding: 4px 10px; 
                border-radius: 4px; 
                font-size: 0.75rem; 
                font-weight: bold; 
                text-transform: uppercase; 
                color: white; 
                margin-left: 8px;
                display: inline-block;
            }
            
            /* Цвета инструментов (Матрица ответственности) */
            .bdg-tool  { background: #94a3b8; }
            .bdg-grok  { background: #000000; border: 1px solid #444; } /* Черный для Grok */
            .bdg-ai    { background: #8b5cf6; } /* Фиолетовый для Gemini */
            .bdg-w3c   { background: #0284c7; } /* Синий для W3C */
            
            /* Цвета статусов файла */
            .bdg-clean { background: #10b981; }
            .bdg-dirty { background: #ef4444; }
            .bdg-skipped { background: #64748b; text-decoration: line-through; }

            /* Issues */
            .issues-list { border-top: 1px solid var(--border); }
            .issue-row { padding: 12px 20px; display: flex; gap: 15px; border-bottom: 1px solid #f1f5f9; align-items: flex-start; }
            .issue-row:last-child { border-bottom: none; }
            
            .severity { padding: 4px 8px; border-radius: 6px; font-size: 0.75rem; font-weight: 800; min-width: 80px; text-align: center; }
            .sv-error { background: #fee2e2; color: #ef4444; }
            .sv-warning { background: #fef3c7; color: #d97706; }
            .sv-suggestion { background: #dbeafe; color: #2563eb; }
            
            .line-num { font-family: monospace; color: var(--text-muted); font-weight: bold; min-width: 40px; }
            .msg-content { flex-grow: 1; }
            .fix-box { margin-top: 8px; background: #f8fafc; border-left: 3px solid var(--color-info); padding: 8px 12px; font-size: 0.9rem; color: #334155; }
            .src-tag { font-size: 0.75rem; background: #f1f5f9; padding: 2px 6px; border-radius: 4px; color: #94a3b8; margin-left: auto; }
        """

        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Code Audit Report</title>
            <style>{style}</style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div>
                        <h1>🛡️ Code Security & Quality Audit</h1>
                        <div class="active-tools" style="margin-top:5px;">
                            Enabled Modules: 
                            {f"{''.join([f'<span>{t}</span>' for t in active_tools])}" if active_tools else '<span class="no-tools">⚠️ ALL MODULES DISABLED</span>'}
                        </div>
                    </div>
                    <div class="header-meta">
                        <div>Date: {datetime.now().strftime("%Y-%m-%d %H:%M")}</div>
                        <div>Source: {self.cfg['src']}</div>
                    </div>
                </div>

                <div class="stats">
                    <div class="stat-card">
                        <span class="stat-val">{stats['total']}</span>
                        <span class="stat-label">Total Files</span>
                    </div>
                    <div class="stat-card" style="border-bottom: 4px solid var(--color-success)">
                        <span class="stat-val" style="color:var(--color-success)">{stats['clean']}</span>
                        <span class="stat-label">Passed</span>
                    </div>
                    <div class="stat-card" style="border-bottom: 4px solid var(--color-danger)">
                        <span class="stat-val" style="color:var(--color-danger)">{stats['dirty']}</span>
                        <span class="stat-label">Issues Found</span>
                    </div>
                    <div class="stat-card" style="border-bottom: 4px solid var(--color-neutral)">
                        <span class="stat-val" style="color:var(--color-neutral)">{stats['skipped']}</span>
                        <span class="stat-label">Not Checked</span>
                    </div>
                </div>
        """
        
        # Сортировка: Сначала Ошибки, потом Clean, в самом низу Not Checked
        def sort_key(report):
            if not report.checked_by: return 3 # Not Checked -> вниз
            if report.issues: return 1         # Dirty -> вверх
            return 2                           # Clean -> середина
            
        self.reports.sort(key=sort_key)

        for rep in self.reports:
            # 1. ГЕНЕРАЦИЯ ЦВЕТНЫХ БЕЙДЖИКОВ ИНСТРУМЕНТОВ
            # Теперь инструменты будут цветными всегда (и в CLEAN, и в ISSUES)
            tool_badges_html = ""
            if rep.checked_by:
                badges_list = []
                for tool_name in rep.checked_by:
                    css_class = "bdg-tool" # Дефолтный серый
                    
                    tool_upper = tool_name.upper()
                    if "GROK" in tool_upper: css_class = "bdg-grok"
                    elif "AI" in tool_upper or "GEMINI" in tool_upper: css_class = "bdg-ai"
                    elif "W3C" in tool_upper: css_class = "bdg-w3c"
                    else: css_class = "bdg-tool"
                    badges_list.append(f'<span class="badge {css_class}">{tool_name}</span>')
                tool_badges_html = "".join(badges_list)

            # 2. ОПРЕДЕЛЕНИЕ СТАТУСА ФАЙЛА
            if not rep.checked_by:
                status_cls = "st-skipped"
                badge_html = '<span class="badge bdg-skipped">NOT CHECKED</span>'
            elif not rep.issues:
                status_cls = "st-clean"
                badge_html = '<span class="badge bdg-clean">CLEAN</span>'
            else:
                status_cls = "st-dirty"
                badge_html = f'<span class="badge bdg-dirty">{len(rep.issues)} ISSUES</span>'

            # 3. ШАПКА БЛОКА ФАЙЛА
            html += f"""
            <div class="file-block">
                <div class="file-header {status_cls}">
                    <span style="font-family:monospace; font-size:1rem;">{rep.path}</span>
                    <div class="badges">
                        {tool_badges_html} {badge_html}
                    </div>
                </div>
            """
            
            # 4. СПИСОК ОШИБОК (Блок, который был "похерен" во втором цикле)
            if rep.issues:
                html += '<div class="issues-list">'
                # Сортировка проблем по тяжести (Error -> Warning -> Suggestion)
                rep.issues.sort(key=lambda x: {'error': 0, 'warning': 1, 'suggestion': 2}.get(x.type, 3))
                
                for i in rep.issues:
                    html += f"""
                    <div class="issue-row">
                        <span class="severity sv-{i.type}">{i.type}</span>
                        <span class="line-num">L:{i.line}</span>
                        <div class="msg-content">
                            <div>{i.message}</div>
                            {f'<div class="fix-box">💡 {i.suggestion}</div>' if i.suggestion else ''}
                        </div>
                        <span class="src-tag">{i.source}</span>
                    </div>
                    """
                html += '</div>' # Закрытие issues-list
            
            html += "</div>" # Закрытие последнего file-block внутри цикла
        
        # Конец основного цикла
        html += """
                </div> </body>
        </html>
        """
        
        with open("code_auditor_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        
        print(f"\n✨ ОТЧЕТ СГЕНЕРИРОВАН: code_auditor_report.html")

if __name__ == "__main__":
    AuditEngine().run()
    