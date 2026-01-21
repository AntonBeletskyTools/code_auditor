"""
==============================================================================
CODE AUDITOR
Профессиональная система аудита кода.
Архитектура: Plugin-based (Валидаторы изолированы).

ФУНКЦИОНАЛ:
1. Изоляция ошибок: Сбой одного модуля не ломает весь процесс.
2. Матрица ответственности:
   - HTML/CSS -> W3C Validator + Gemini AI
   - SCSS/SASS -> Gemini AI
   - JS/TS/React -> Gemini AI
3. State Persistence: Защита от сбоев (сохранение прогресса).
4. Гибкие фильтры: Настройка через .env.

АВТОР: Gemini Pro (Cleaned Version)
==============================================================================
# ==============================================================================
# КОНФИГУРАЦИЯ CODE AUDITOR
# ==============================================================================

# --- API CONFIG ---
# Твой API ключ от Google AI Studio (https://aistudio.google.com/)
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE

# Выбор модели Gemini
# models/gemini-1.5-flash — быстрая и дешевая (рекомендуется)
# models/gemini-2.0-flash — актуальная стабильная версия
GEMINI_MODEL=models/gemini-2.0-flash

# --- PATHS ---
# Папка, которую нужно сканировать (например, src, project_folder или .)
SOURCE_DIR=src

# --- GLOBAL TOGGLES (Включение/Выключение модулей) ---
# Проверка стандартов через W3C API (HTML/CSS)
ENABLE_W3C=True

# Интеллектуальный аудит через Gemini AI (JS/TS/SCSS/Логика)
ENABLE_GEMINI=True

# --- CONTENT FILTERS (Что именно проверять) ---
# HTML файлы (.html)
CHECK_HYPERTEXT=True

# Стили (.css, .scss, .sass)
CHECK_STYLES=True

# Скрипты (.js, .jsx, .ts, .tsx)
CHECK_SCRIPTS=True

# --- PERFORMANCE & LIMITS ---
# Задержка между файлами в секундах.
# ВАЖНО: W3C может забанить IP при слишком частых запросах. 
# 7_15 секунд — безопасный интервал для стабильной работы. рандом между 7 и 15 секундами
API_SLEEP=7_15

# Максимальное количество символов кода для отправки в Gemini
GEMINI_MAX_CHARS=30000

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
import requests
import logging
import traceback
import random
from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Any

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

# ================= 1. DATA MODELS (OOP) =================

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

# ================= 2. ABSTRACT VALIDATOR =================

class BaseValidator(ABC):
    """Базовый класс для всех проверочных модулей"""
    def __init__(self, name: str, enabled: bool, **kwargs):
        self.name = name
        self.enabled = enabled
        for key, value in kwargs.items():
            setattr(self, key, value)

    @abstractmethod
    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        pass

# ================= 3. CONCRETE VALIDATORS =================

class W3CValidator(BaseValidator):
    """Модуль строгой проверки стандартов (HTML/CSS)"""
    
    API_URL = "https://validator.w3.org/nu/?out=json"
    
    def __init__(self, enabled: bool):
        super().__init__(name="W3C Validator", enabled=enabled)

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        # W3C работает только с чистым HTML и CSS
        if not self.enabled or ext not in ['.html', '.css']:
            return []

        ctype = "text/html" if ext == '.html' else "text/css"
        # User-Agent важен, чтобы W3C не блокировал запросы как спам
        headers = {'User-Agent': 'CodeAuditor/1.0', 'Content-Type': f'{ctype}; charset=utf-8'}

        try:
            resp = requests.post(self.API_URL, data=content.encode('utf-8'), headers=headers, timeout=15)
            
            # Сохраняем код ответа в переменную, чтобы не дергать его сто раз
            status_code = resp.status_code

            if status_code == 200:
                # Если всё ок, выводим реальные ошибки валидации кода
                messages = resp.json().get('messages', [])
                return [Issue(
                    type="error" if m.get('type') == 'error' else "warning",
                    line=m.get('lastLine', m.get('firstLine', 0)),
                    message=f"[W3C] {m.get('message')}",
                    source=self.name
                ) for m in messages]

            elif status_code == 429:
                # ВАЖНО: Если W3C забанил за частые запросы — это пойдет в отчет
                logger.error(f"[{self.name}] 429: Too Many Requests!")
                return [Issue(type="error", line=0, message="🚫 W3C API: Бан за частые запросы (429). Увеличь API_SLEEP!", source=self.name)]

            elif status_code == 404:
                # Это сработает ТОЛЬКО если API W3C выдаст 404 (сервис упал)
                # Твой файл 404.html при этом пройдет через status_code == 200 и не вызовет проблем
                logger.warning(f"[{self.name}] 404: API Endpoint not found!")
                return [Issue(type="warning", line=0, message="⚠️ W3C API: Сервис валидации временно недоступен (404)", source=self.name)]

            else:
                # Любая другая фигня от сервера (500, 502 и т.д.)
                return [Issue(type="error", line=0, message=f"W3C API Error: {status_code}", source=self.name)]

        except Exception as e:
            # Если вообще инета нет или таймаут
            logger.error(f"[{self.name}] Connection Error: {e}")
            return [Issue(type="warning", line=0, message=f"W3C Connection Failed", source=self.name)]

class GeminiValidator(BaseValidator):
    """Модуль интеллектуального анализа (AI)"""

    def __init__(self, config):
        self.api_key = config['gemini_key']
        self.model_name = config['gemini_model']
        self.system_instruction = "You are a Senior QA Engineer. Analyze code for bugs, security, and clean code violations."
        self.max_chars = int(config.get('gemini_max_chars', 30000))
        self.enabled = config['check_ai']
        self.client = None
        
        if self.enabled:
            if not self.api_key or "YOUR_GEMINI_API_KEY" in self.api_key:
                logger.warning("Gemini API Key is missing. AI check disabled.")
                self.enabled = False
            else:
                try:
                    genai.configure(api_key=self.api_key)
                    self.client = genai.GenerativeModel(
                        model_name=self.model_name,
                        system_instruction=self.system_instruction
                    )
                except Exception as e:
                    logger.error(f"Failed to init Gemini: {e}")
                    self.enabled = False

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.client:
            return []

        issues = []
        # Проверка на превышение лимита символов
        if len(content) > self.max_chars:
            msg = f"Файл слишком велик ({len(content)} симв.). Анализируются только первые {self.max_chars}."
            logger.warning(f"[{self.name}] {path}: {msg}")
            issues.append(Issue(
                type="warning",
                line=0,
                message=f"⚠️ {msg}",
                source=self.name
            ))
        
        # Формируем контекст для ИИ
        context = "код"
        if ext in ['.css', '.scss', '.sass']: context = "стили и верстку (SCSS/CSS)"
        elif ext in ['.js', '.jsx', '.ts', '.tsx']: context = "скрипты, логику и безопасность (JS/React)"
        elif ext == '.html': context = "HTML структуру и семантику"

        prompt = f"""
        Роль: Senior Code Reviewer. Задача: Аудит файла {path}.
        Контекст: Ты анализируешь {context}.
        
        Требования:
        1. Ищи логические баги, XSS уязвимости, утечки памяти.
        2. Проверяй чистоту кода (DRY, naming convention).
        3. Если это SCSS - проверяй вложенность.
        4. Будь краток и конструктивен.
        
        Формат ответа (JSON ONLY):
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

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            
            # Очистка JSON от маркдауна (на всякий случай)
            clean_json = re.sub(r"^```json\s*|\s*```$", "", response.text, flags=re.MULTILINE).strip()
            data = json.loads(clean_json)
            
            ai_issues = [Issue(
                type=i.get('type', 'warning'),
                line=i.get('line', 0),
                message=i.get('message', 'Issue detected'),
                source=self.name,
                suggestion=i.get('suggestion')
            ) for i in data.get('issues', [])]
            
            return issues + ai_issues

        except Exception as e:
            if "429" in str(e):
                logger.warning(f"[{self.name}] Quota exceeded (429). Skipping {path}...")
                return [Issue(type="warning", line=0, message="Gemini Rate Limit (Skipped)", source=self.name)]
            elif "404" in str(e) or "not found" in str(e).lower():
                logger.error(f"[{self.name}] Неверная модель '{self.model}'. Проверьте .env!")
                self.enabled = False 
                return [Issue(type="error", line=0, message="Invalid Gemini Model configuration", source=self.name)]
            else:
                logger.error(f"[{self.name}] Error analyzing {path}: {e}")
                return [Issue(type="error", line=0, message=f"AI Error: {str(e)[:50]}", source=self.name)]

# ================= 4. ENGINE (ORCHESTRATOR) =================

class AuditEngine:
    """Главный класс-оркестратор Code Auditor"""
    
    def __init__(self):
        self.load_config()
        self.temp_file = self.cfg['temp_file']
        self.validators: List[BaseValidator] = []
        self.reports: List[FileReport] = []
        
        # Регистрация плагинов
        self.validators.append(W3CValidator(enabled=self.cfg['w3c_on']))
        self.validators.append(GeminiValidator(
            enabled=self.cfg['gemini_on'], 
            api_key=self.cfg['api_key'], 
            model=self.cfg['model'],
            max_chars=self.cfg['gemini_max_chars']
        ))

    def load_config(self):
        """Загрузка и нормализация конфига"""
        def get_bool(k, d="True"): return os.getenv(k, d).lower() in ("true", "1", "yes", "on")
        
        self.cfg = {
            'src': os.getenv("SOURCE_DIR", "src"),
            'api_key': os.getenv("GEMINI_API_KEY"),
            'model': os.getenv("GEMINI_MODEL", "models/gemini-1.5-flash"),
            'w3c_on': get_bool("ENABLE_W3C"),
            'gemini_on': get_bool("ENABLE_GEMINI"),
            'filter_html': get_bool("CHECK_HYPERTEXT"),
            'filter_css': get_bool("CHECK_STYLES"),
            'filter_js': get_bool("CHECK_SCRIPTS"),
            'api_sleep': os.getenv("API_SLEEP", "10.0"),
            'gemini_max_chars': int(os.getenv("GEMINI_MAX_CHARS", "30000")),
            'resume_audit': get_bool("RESUME_AUDIT", "True"),
            'temp_file': os.getenv("TEMP_STATE_FILE", "audit_state.temp.json"),
        }

    def print_config(self):
        """Красивый вывод настроек"""
        print("\n" + "="*50)
        print("🛠  CODE AUDITOR CONFIGURATION")
        print("="*50)
        print(f"📂 Источник (Source):   {self.cfg['src']}")
        print(f"🤖 AI Модель:           {self.cfg['model']}")
        print(f"📄 Лимит символов AI:   {self.cfg['gemini_max_chars']}")
        print(f"⏱  Задержка (Sleep):    {self.cfg['api_sleep']} сек.")
        print("-" * 50)
        print(f"🔌 Модули:")
        print(f"   • W3C Validator:     {'✅ ON' if self.cfg['w3c_on'] else '❌ OFF'}")
        print(f"   • Gemini AI:         {'✅ ON' if self.cfg['gemini_on'] else '❌ OFF'}")
        print("-" * 50)
        print(f"🔍 Фильтры файлов:")
        print(f"   • HTML:              {'✅ YES' if self.cfg['filter_html'] else '⬜ NO'}")
        print(f"   • CSS/SCSS:          {'✅ YES' if self.cfg['filter_css'] else '⬜ NO'}")
        print(f"   • JS/TS/React:       {'✅ YES' if self.cfg['filter_js'] else '⬜ NO'}")
        print("="*50 + "\n")

    def scan(self) -> List[str]:
        """Умное сканирование с учетом фильтров"""
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
                
                # Применяем фильтры из .env
                if ext in ext_map['html'] and self.cfg['filter_html']:
                    files_to_check.append(path)
                elif ext in ext_map['css'] and self.cfg['filter_css']:
                    files_to_check.append(path)
                elif ext in ext_map['js'] and self.cfg['filter_js']:
                    files_to_check.append(path)
                    
        return files_to_check

    def restore_state(self) -> List[FileReport]:
        """Загрузка состояния из временного файла (Resume capability)."""
        if os.path.exists(self.temp_file):
            try:
                with open(self.temp_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    # Корректное восстановление объектов из JSON
                    return [FileReport(
                        path=r['path'], 
                        timestamp=r['timestamp'], 
                        issues=[Issue(**i) for i in r['issues']]
                    ) for r in data]
            except Exception as e:
                logger.warning(f"Не удалось восстановить состояние (файл поврежден?): {e}")
        return []
    
    def save_state(self):
        """Атомарное сохранение прогресса (защита от повреждения файла)"""
        temp_shadow = self.temp_file + ".tmp"
        try:
            # 1. Записываем данные в теневой временный файл
            with open(temp_shadow, 'w', encoding='utf-8') as f:
                json.dump([asdict(r) for r in self.reports], f, ensure_ascii=False)
            
            # 2. Только если запись прошла успешно, заменяем основной файл теневым
            # Это мгновенная операция на уровне файловой системы
            os.replace(temp_shadow, self.temp_file)
        except Exception as e:
            logger.error(f"Критическая ошибка записи временного файла: {e}")
            if os.path.exists(temp_shadow):
                os.remove(temp_shadow)

    def run(self):
        print(f"🚀 ЗАПУСК CODE AUDITOR...")
        self.print_config()
        
        all_files = self.scan()
        if not all_files:
            print(f"⚠️ Файлы не найдены в папке {self.cfg['source_dir']}.")
            return

        # --- ЛОГИКА RESUME (НОВОВВЕДЕНИЕ) ---
        if self.cfg['resume_audit']:
            self.reports = self.restore_state()
            processed_paths = {r.path for r in self.reports}
            if processed_paths:
                print(f"🔄 Восстановлен прогресс: уже проверено {len(processed_paths)} файлов.")
        else:
            self.reports = []
            processed_paths = set()

        # Фильтрация файлов, которые нужно проверить
        queue = [f for f in all_files if f not in processed_paths]
        print(f"📊 Предстоит проверить: {len(queue)} файлов.")
        
        # --- ОСНОВНОЙ ЦИКЛ ---
        for idx, path in enumerate(queue, 1):
            filename = os.path.basename(path)
            print(f"👉 [{idx}/{len(queue)}] {filename}...", end="", flush=True)
            
            file_issues = []
            api_called = False
            content = ""
            
            try:
                # 1. Чтение файла
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
                
                # Если файл пустой — пропускаем, но помечаем как проверенный
                if not content.strip():
                    print(" ⏩ Пуст (Skipped)")
                    self.reports.append(FileReport(path, datetime.now().strftime("%H:%M"), []))
                    self.save_state()
                    continue

                ext = os.path.splitext(path)[1].lower()
                
                # 2. Запуск валидаторов
                for validator in self.validators:
                    if not validator.enabled:
                        continue
                    
                    try:
                        # ИСПРАВЛЕНИЕ: Вызов метода check, а не validate
                        issues = validator.check(path, content, ext)
                        if issues:
                            file_issues.extend(issues)
                        
                        # Флаг для задержки API
                        if isinstance(validator, (W3CValidator, GeminiValidator)):
                            api_called = True
                    except Exception as ve:
                        # Ловим ошибку конкретного валидатора, чтобы не крашить весь скрипт
                        logger.error(f"Ошибка в {validator.name}: {ve}")

                # 3. Сохранение результата (АТОМАРНОЕ)
                # ИСПРАВЛЕНИЕ: Это вынесено из цикла валидаторов, чтобы не дублировать отчеты
                new_report = FileReport(path, datetime.now().strftime("%H:%M"), file_issues)
                self.reports.append(new_report)
                self.save_state()
                
                # 4. Вывод в консоль
                if not file_issues:
                    print(" ✅ OK")
                else:
                    print(f" ⚠️ {len(file_issues)} issues")

                # 5. Rate Limiting (Задержка)
                if api_called:
                    sleep_cfg = str(self.cfg['api_sleep'])
                    if "_" in sleep_cfg:
                        mn, mx = map(float, sleep_cfg.split("_"))
                        st = random.uniform(mn, mx)
                    else:
                        st = float(sleep_cfg)
                    time.sleep(st)

            except Exception as e:
                # Глобальная защита от падения на конкретном файле
                print(f" ❌ Критическая ошибка файла: {e}")

        # Генерация финального HTML
        self.generate_report()

    def generate_report(self):
        """Генерация HTML отчета"""
        stats = {'files': len(self.reports), 'err': 0, 'warn': 0, 'sugg': 0}
        for r in self.reports:
            for i in r.issues:
                if i.type == 'error': stats['err'] += 1
                elif i.type == 'warning': stats['warn'] += 1
                elif i.type == 'suggestion': stats['sugg'] += 1

        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Code Auditor Report</title>
            <style>
                :root {{ --bg: #f1f5f9; --card: #ffffff; --text: #334155; --err: #ef4444; --warn: #f59e0b; --info: #3b82f6; }}
                body {{ font-family: 'Segoe UI', sans-serif; background: var(--bg); color: var(--text); padding: 40px; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
                .stat-card {{ background: var(--card); padding: 20px; border-radius: 12px; text-align: center; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); }}
                .stat-val {{ font-size: 2.5rem; font-weight: 800; display: block; }}
                
                .file-block {{ background: var(--card); border-radius: 12px; margin-bottom: 20px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
                .file-header {{ padding: 15px 25px; background: #e2e8f0; display: flex; justify-content: space-between; font-weight: 600; }}
                .clean {{ border-left: 6px solid #22c55e; }}
                .dirty {{ border-left: 6px solid var(--err); }}
                
                .issue {{ padding: 15px 25px; border-bottom: 1px solid #f1f5f9; display: flex; gap: 20px; align-items: flex-start; }}
                .badge {{ padding: 4px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 800; text-transform: uppercase; min-width: 70px; text-align: center; }}
                .b-error {{ background: #fee2e2; color: #991b1b; }}
                .b-warning {{ background: #fef3c7; color: #92400e; }}
                .b-suggestion {{ background: #dbeafe; color: #1e40af; }}
                .source {{ background: #f1f5f9; border: 1px solid #cbd5e1; color: #64748b; margin-left: auto; }}
                .fix {{ margin-top: 8px; background: #f8fafc; border-left: 4px solid var(--info); padding: 10px; font-size: 0.9rem; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🛡️ CODE AUDITOR REPORT</h1>
                <p>Дата проверки: {datetime.now().strftime("%Y-%m-%d %H:%M")}</p>
                <div class="stats">
                    <div class="stat-card"><span class="stat-val">{stats['files']}</span>Файлов</div>
                    <div class="stat-card"><span class="stat-val" style="color:var(--err)">{stats['err']}</span>Ошибок</div>
                    <div class="stat-card"><span class="stat-val" style="color:var(--warn)">{stats['warn']}</span>Варнингов</div>
                    <div class="stat-card"><span class="stat-val" style="color:var(--info)">{stats['sugg']}</span>Советов</div>
                </div>
        """

        # Сортировка: Сначала проблемные файлы
        self.reports.sort(key=lambda x: len(x.issues) == 0)

        for rep in self.reports:
            is_clean = len(rep.issues) == 0
            status = "clean" if is_clean else "dirty"
            
            html += f"""
            <div class="file-block">
                <div class="file-header {status}">
                    <span>{rep.path}</span>
                    <span>{'✅ Clean' if is_clean else f'🛑 {len(rep.issues)} Issues'}</span>
                </div>
            """
            
            if not is_clean:
                # Сортировка ошибок внутри файла
                rep.issues.sort(key=lambda x: {'error': 0, 'warning': 1, 'suggestion': 2}.get(x.type, 3))
                for i in rep.issues:
                    html += f"""
                    <div class="issue">
                        <span class="badge b-{i.type}">{i.type}</span>
                        <span style="font-family:monospace; font-weight:bold; color:#64748b">L:{i.line}</span>
                        <div style="flex-grow:1">
                            <div>{i.message}</div>
                            {f'<div class="fix">💡 {i.suggestion}</div>' if i.suggestion else ''}
                        </div>
                        <span class="badge source">{i.source}</span>
                    </div>
                    """
            html += "</div>"

        html += "</div></body></html>"
        with open("code_auditor_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n✨ ОТЧЕТ ГОТОВ: code_auditor_report.html")

if __name__ == "__main__":
    AuditEngine().run()