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

    def __init__(self, enabled: bool, api_key: str, model: str):
        super().__init__(name="Gemini AI", enabled=enabled)
        self.model = model
        self.client = None
        
        if self.enabled:
            if not api_key:
                logger.error(f"[{self.name}] API Key не найден! Модуль отключен.")
                self.enabled = False
            else:
                try:
                    self.client = genai.Client(api_key=api_key)
                except Exception as e:
                    logger.error(f"[{self.name}] Ошибка инициализации клиента: {e}")
                    self.enabled = False

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.client:
            return []
        
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
        {content[:30000]}
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
            
            return [Issue(
                type=i.get('type', 'warning'),
                line=i.get('line', 0),
                message=i.get('message', 'Issue detected'),
                source=self.name,
                suggestion=i.get('suggestion')
            ) for i in data.get('issues', [])]

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
        self.temp_file = "audit_state.temp.json"
        self.validators: List[BaseValidator] = []
        self.reports: List[FileReport] = []
        
        # Регистрация плагинов
        self.validators.append(W3CValidator(enabled=self.cfg['w3c_on']))
        self.validators.append(GeminiValidator(
            enabled=self.cfg['gemini_on'], 
            api_key=self.cfg['api_key'], 
            model=self.cfg['model']
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
            'api_sleep': os.getenv("API_SLEEP", "10.0"), # Дефолт из запроса пользователя
        }

    def print_config(self):
        """Красивый вывод настроек"""
        print("\n" + "="*50)
        print("🛠  CODE AUDITOR CONFIGURATION")
        print("="*50)
        print(f"📂 Источник (Source):   {self.cfg['src']}")
        print(f"🤖 AI Модель:           {self.cfg['model']}")
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

    def restore_state(self) -> List[str]:
        """Загрузка прогресса (Persistence)"""
        if os.path.exists(self.temp_file):
            try:
                with open(self.temp_file, 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    self.reports = [FileReport(path=r['path'], timestamp=r['timestamp'], 
                                    issues=[Issue(**i) for i in r['issues']]) for r in data]
                # Метод должен возвращать список объектов FileReport
                if os.path.exists(self.temp_file):
                    with open(self.temp_file, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                        return [FileReport(path=r['path'], timestamp=r['timestamp'], 
                                issues=[Issue(**i) for i in r['issues']]) for r in data]
                return []
            except Exception:
                return []
        return []

    def save_state(self):
        """Инкрементальное сохранение"""
        with open(self.temp_file, 'w', encoding='utf-8') as f:
            json.dump([asdict(r) for r in self.reports], f, ensure_ascii=False, indent=2)

    def run(self):
        print(f"🚀 ЗАПУСК CODE AUDITOR...")
        self.print_config()
        
        # 1. Сканируем файлы
        all_files = self.scan()
        if not all_files:
            print(f"⚠️  Файлы для проверки не найдены в '{self.cfg['src']}'. Проверьте настройки фильтров или пути.")
            return

        # 2. Восстанавливаем состояние
        
        # Принудительно загружаем старые отчеты в список перед началом
        # 2.1. Сначала загружаем объекты отчетов в self.reports
        self.reports = self.restore_state() 
        # 2.2. Создаем множество ПУТЕЙ из этих отчетов
        processed_paths = {r.path for r in self.reports}
        # 2.3. Формируем очередь из тех файлов, путей которых НЕТ в списке проверенных
        queue = [f for f in all_files if f not in processed_paths]
        
        # 3. Вывод статистики ПЕРЕД работой
        print(f"📊 СТАТИСТИКА ЗАДАЧИ:")
        print(f"   • Всего файлов:   {len(all_files)}")
        print(f"   • Уже проверено:  {len(processed_paths)}")
        print(f"   • В очереди:      {len(queue)}")
        print("-" * 50)
        
        if not queue:
            print("✅ Все файлы уже проверены! Генерация отчета...")
            self.generate_report()
            return

        # 4. Основной цикл
        for idx, path in enumerate(queue, 1):
            filename = os.path.basename(path)
            print(f"👉 [{idx}/{len(queue)}] {filename}...", end="", flush=True)
            
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()
                
                ext = os.path.splitext(path)[1].lower()
                
                # --- ПОЛИМОРФНЫЙ ЗАПУСК ВАЛИДАТОРОВ ---
                api_called = False
                
                # Инициализация списка для сбора ошибок текущего файла
                # #### (Затыкаем дырку: теперь переменная точно объявлена)
                file_issues = [] 

                for validator in self.validators:
                    try:
                        # Получаем список проблем от конкретного валидатора
                        issues = validator.check(path, content, ext)
                        
                        # Если проблемы найдены — добавляем их в общий список файла
                        if issues:
                            file_issues.extend(issues)
                        
                        # Проверяем, нужно ли делать паузу (только для внешних API)
                        if validator.enabled and isinstance(validator, (W3CValidator, GeminiValidator)):
                            api_called = True
                            
                    except Exception as e:
                        logger.error(f"Сбой модуля {validator.name}: {e}")

                # Сохранение результата в отчет
                # #### (Затыкаем дырку: передаем собранный список)
                new_report = FileReport(path, datetime.now().strftime("%H:%M"), file_issues)
                self.reports.append(new_report)

                # Мгновенная запись на диск (защита от вылета на 404.html)
                try:
                    with open(self.temp_file, 'w', encoding='utf-8') as f:
                        json.dump([asdict(r) for r in self.reports], f, ensure_ascii=False)
                except Exception as e:
                    logger.error(f"Ошибка сохранения прогресса: {e}")
                
                # Вывод статуса в терминал
                msg_status = "✅ OK" if not file_issues else f"⚠️ {len(file_issues)} issues"
                print(f" {msg_status}")
                
                # Пауза перед следующим файлом (Rate Limiting)
                # --- УМНАЯ ЗАДЕРЖКА (Rate Limiting) ---
                if api_called:
                    sleep_cfg = str(self.cfg['api_sleep'])
                    
                    if "_" in sleep_cfg:
                        # Если формат 7_10, выбираем рандомное число
                        try:
                            low, high = map(float, sleep_cfg.split("_"))
                            sleep_time = random.uniform(low, high)
                        except Exception:
                            sleep_time = 10.0 # Страховка, если в .env написали дичь
                    else:
                        # Если просто число (например 10.0)
                        try:
                            sleep_time = float(sleep_cfg)
                        except Exception:
                            sleep_time = 10.0

                    print(f" ⏳ Пауза {sleep_time:.1f}с...")
                    time.sleep(sleep_time)

            except Exception as e:
                print(f"\n❌ ФАТАЛЬНАЯ ОШИБКА ФАЙЛА {path}: {e}")
                logger.exception(e)
        
        self.generate_report()
        # Удаляем временный файл только если все прошло успешно
        if os.path.exists(self.temp_file): 
            os.remove(self.temp_file)
            print("🧹 Временный файл очищен.")

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