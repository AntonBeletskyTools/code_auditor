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

# --- API CONFIG ---
GEMINI_API_KEY=YOUR_GEMINI_API_KEY_HERE
GEMINI_MODEL=models/gemini-2.0-flash

# --- PATHS ---
SOURCE_DIR=src

# --- GLOBAL TOGGLES (Включение модулей) ---
ENABLE_W3C=True
ENABLE_GEMINI=True

# --- FILTERS ---
CHECK_HYPERTEXT=True
CHECK_STYLES=True
CHECK_SCRIPTS=True

# --- PERFORMANCE ---
API_SLEEP=7_15
GEMINI_MAX_CHARS=30000

# --- SYSTEM ---
RESUME_AUDIT=True
TEMP_STATE_FILE=audit_state.temp.json
==============================================================================
"""

import os
import json
import time
import re
import requests
import logging
import random
from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Optional

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
    # НОВОЕ: Список валидаторов, которые реально проверяли этот файл
    checked_by: List[str] = field(default_factory=list) 

# ================= 2. ABSTRACT VALIDATOR =================

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

# ================= 3. CONCRETE VALIDATORS =================

class W3CValidator(BaseValidator):
    """Модуль строгой проверки стандартов (HTML/CSS)"""
    
    API_URL = "https://validator.w3.org/nu/?out=json"
    
    def __init__(self, enabled: bool):
        super().__init__(name="W3C Validator", short_name="W3C", enabled=enabled)

    def can_check(self, ext: str) -> bool:
        return ext in ['.html', '.css']

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.can_check(ext):
            return []

        ctype = "text/html" if ext == '.html' else "text/css"
        headers = {'User-Agent': 'CodeAuditor/1.0', 'Content-Type': f'{ctype}; charset=utf-8'}

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
                logger.error(f"[{self.name}] 429: Too Many Requests!")
                return [Issue(type="error", line=0, message="🚫 W3C API Rate Limit (429)", source=self.name)]
            elif status_code == 404:
                return [Issue(type="warning", line=0, message="⚠️ W3C API Unavailable (404)", source=self.name)]
            else:
                return [Issue(type="error", line=0, message=f"W3C API Error: {status_code}", source=self.name)]

        except Exception as e:
            logger.error(f"[{self.name}] Connection Error: {e}")
            return [Issue(type="warning", line=0, message=f"W3C Connection Failed", source=self.name)]

class GeminiValidator(BaseValidator):
    """Модуль интеллектуального анализа (AI)"""

    def __init__(self, enabled: bool, api_key: str, model: str, max_chars: int = 30000):
        super().__init__(name="Gemini AI", short_name="AI", enabled=enabled)
        self.model = model
        self.max_chars = max_chars
        self.client = None
        
        if self.enabled:
            if not api_key or "YOUR_GEMINI_API_KEY" in api_key:
                logger.warning("Gemini API Key is missing. AI disabled.")
                self.enabled = False
            else:
                try:
                    self.client = genai.Client(api_key=api_key)
                except Exception as e:
                    logger.error(f"Failed to init Gemini: {e}")
                    self.enabled = False

    def can_check(self, ext: str) -> bool:
        # AI может проверять почти всё, что текстовое
        return ext in ['.html', '.css', '.scss', '.sass', '.js', '.jsx', '.ts', '.tsx']

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.client or not self.can_check(ext):
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

        try:
            response = self.client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            
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
                logger.warning(f"[{self.name}] Quota exceeded (429).")
                return [Issue(type="warning", line=0, message="Gemini Rate Limit (Skipped)", source=self.name)]
            elif "404" in str(e):
                self.enabled = False 
                return [Issue(type="error", line=0, message="Invalid Gemini Model", source=self.name)]
            else:
                logger.error(f"[{self.name}] Error: {e}")
                return [Issue(type="error", line=0, message=f"AI Error: {str(e)[:50]}", source=self.name)]

# ================= 4. ENGINE (ORCHESTRATOR) =================

class AuditEngine:
    """Главный класс-оркестратор"""
    
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
        def get_bool(k, d="True"): return os.getenv(k, d).lower() in ("true", "1", "yes", "on")
        self.cfg = {
            'src': os.getenv("SOURCE_DIR", "src"),
            'api_key': os.getenv("GEMINI_API_KEY"),
            'model': os.getenv("GEMINI_MODEL", "models/gemini-2.0-flash"),
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
        print("\n" + "="*50)
        print("🛠  CODE AUDITOR: CONFIGURATION")
        print("="*50)
        print(f"📂 Source:       {self.cfg['src']}")
        print(f"🤖 AI Model:     {self.cfg['model']}")
        print(f"⏱  Sleep:        {self.cfg['api_sleep']}s")
        print("-" * 50)
        print(f"🔌 Modules:")
        for v in self.validators:
            print(f"   • {v.name:<15} {'✅ ON' if v.enabled else '⬜ OFF'}")
        print("="*50 + "\n")

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
                logger.warning(f"Resume failed: {e}")
        return []
    
    def save_state(self):
        temp_shadow = self.temp_file + ".tmp"
        try:
            with open(temp_shadow, 'w', encoding='utf-8') as f:
                json.dump([asdict(r) for r in self.reports], f, ensure_ascii=False, indent=2)
            if os.path.exists(temp_shadow):
                os.replace(temp_shadow, self.temp_file)
        except Exception as e:
            logger.error(f"State save error: {e}")

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
        
        for idx, path in enumerate(queue, 1):
            filename = os.path.basename(path)
            print(f"👉 [{idx}/{len(queue)}] {filename}...", end="", flush=True)
            
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
                
                for validator in self.validators:
                    # Проверяем, включен ли валидатор И подходит ли он для этого типа файла
                    if validator.enabled and validator.can_check(ext):
                        try:
                            # Добавляем в список "покрытия"
                            checked_by_modules.append(validator.short_name)
                            
                            issues = validator.check(path, content, ext)
                            if issues:
                                file_issues.extend(issues)
                            
                            if isinstance(validator, (W3CValidator, GeminiValidator)):
                                api_called = True
                        except Exception as ve:
                            logger.error(f"Error {validator.name}: {ve}")

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
                    sleep_cfg = str(self.cfg['api_sleep'])
                    if "_" in sleep_cfg:
                        try:
                            mn, mx = map(float, sleep_cfg.split("_"))
                            st = random.uniform(mn, mx)
                        except: st = 10.0
                    else:
                        st = float(sleep_cfg)
                    time.sleep(st)

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
            
            .badges { display: flex; gap: 8px; }
            .badge { padding: 3px 8px; border-radius: 4px; font-size: 0.7rem; font-weight: bold; text-transform: uppercase; }
            .bdg-tool { background: #e2e8f0; color: #475569; }
            .bdg-clean { background: #d1fae5; color: #065f46; }
            .bdg-dirty { background: #fee2e2; color: #991b1b; }
            .bdg-skipped { background: #e2e8f0; color: #64748b; text-decoration: line-through; }

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
            # Определение статуса
            if not rep.checked_by:
                status_cls = "st-skipped"
                badge_html = '<span class="badge bdg-skipped">NOT CHECKED</span>'
                tool_badges = ""
            elif not rep.issues:
                status_cls = "st-clean"
                badge_html = '<span class="badge bdg-clean">CLEAN</span>'
                tool_badges = "".join([f'<span class="badge bdg-tool">{t}</span>' for t in rep.checked_by])
            else:
                status_cls = "st-dirty"
                badge_html = f'<span class="badge bdg-dirty">{len(rep.issues)} ISSUES</span>'
                tool_badges = "".join([f'<span class="badge bdg-tool">{t}</span>' for t in rep.checked_by])

            html += f"""
            <div class="file-block">
                <div class="file-header {status_cls}">
                    <span style="font-family:monospace; font-size:1rem;">{rep.path}</span>
                    <div class="badges">
                        {tool_badges}
                        {badge_html}
                    </div>
                </div>
            """
            
            if rep.issues:
                html += '<div class="issues-list">'
                # Сортировка проблем по тяжести
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
                html += '</div>'
            
            html += "</div>"

        html += "</div></body></html>"
        with open("code_auditor_report.html", "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n✨ ОТЧЕТ СГЕНЕРИРОВАН: code_auditor_report.html")

if __name__ == "__main__":
    AuditEngine().run()