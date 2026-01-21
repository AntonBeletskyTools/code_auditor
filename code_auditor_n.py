"""
TITAN CODE AUDITOR v7.0 (Production Ready)
=====================================================
Гибридный инструмент аудита: Строгие стандарты (W3C) + Искусственный интеллект (Gemini).

ОСОБЕННОСТИ:
1. Матричная проверка: HTML/CSS проверяются двумя движками, JS/SCSS — нейросетью.
2. State-Persistence: Создает временный файл прогресса. При сбое (интернет/свет)
   запустите скрипт снова, и он продолжит с прерванного места.
3. Гибкие фильтры: В .env можно отключить проверку стилей или скриптов.

ПРИМЕР СОДЕРЖИМОГО .env:
-----------------------------------------------------
# Ключи и доступы
GEMINI_API_KEY=AIzaSy...ВашКлюч
SOURCE_DIR=src
GEMINI_MODEL=models/gemini-1.5-flash

# Глобальные переключатели
ENABLE_W3C=True
ENABLE_GEMINI=True

# Фильтры типов файлов (что проверяем?)
CHECK_HYPERTEXT=True   # .html
CHECK_STYLES=True      # .css, .scss, .sass
CHECK_SCRIPTS=True     # .js, .jsx, .ts, .tsx
-----------------------------------------------------
"""

import os
import time
import json
import re
import requests
import logging
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Dict, Optional, Set

# --- Загрузка окружения ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ Внимание: python-dotenv не установлен. Настройки берутся из системных переменных.")

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("❌ КРИТИЧЕСКАЯ ОШИБКА: Не установлены библиотеки.")
    print("👉 Запустите: pip install google-genai requests python-dotenv")
    exit(1)

# ================= КОНФИГУРАЦИЯ =================
def get_env_bool(key, default="True"):
    """Безопасный парсинг булевых значений из .env"""
    return os.getenv(key, str(default)).lower() in ("true", "1", "yes", "on")

CONFIG = {
    "api_key": os.getenv("GEMINI_API_KEY"),
    "source_dir": os.getenv("SOURCE_DIR", "src"),
    "report_file": "titan_audit_report.html",
    "temp_file": "audit_progress.temp.json",
    "model": os.getenv("GEMINI_MODEL", "models/gemini-1.5-flash"),
    "rpm_limit": 10,  # Лимит запросов в минуту для Free Tier
    
    # Движки
    "enable_w3c": get_env_bool("ENABLE_W3C", "True"),
    "enable_gemini": get_env_bool("ENABLE_GEMINI", "True"),
    
    # Фильтры контента
    "check_html": get_env_bool("CHECK_HYPERTEXT", "True"),
    "check_styles": get_env_bool("CHECK_STYLES", "True"),
    "check_scripts": get_env_bool("CHECK_SCRIPTS", "True")
}

# Логирование
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger("TitanAuditor")

# ================= МОДЕЛИ ДАННЫХ (ООП) =================

@dataclass
class Issue:
    """Единица найденной проблемы."""
    type: str       # error, warning, suggestion
    line: int
    message: str
    source: str     # W3C Validator, Gemini AI
    suggestion: Optional[str] = None

@dataclass
class FileReport:
    """Полный отчет по одному файлу."""
    path: str
    timestamp: str
    issues: List[Issue] = field(default_factory=list)

@dataclass
class AuditTask:
    """Задание на проверку файла."""
    path: str
    engines: List[str]  # ['w3c', 'gemini']

# ================= ЯДРО АУДИТОРА =================

class TitanAuditor:
    def __init__(self, cfg: Dict):
        self.cfg = cfg
        
        # Инициализация AI клиента
        if self.cfg["enable_gemini"]:
            if not self.cfg["api_key"]:
                raise ValueError("❌ ОШИБКА: В .env не указан GEMINI_API_KEY!")
            self.client = genai.Client(api_key=self.cfg["api_key"])
        
        self.tasks: List[AuditTask] = []
        self.processed_reports: List[FileReport] = []
        
        # Задержка для защиты от 429 ошибки (60 сек / RPM + буфер)
        self.delay = (60.0 / self.cfg["rpm_limit"]) + 1.0

    # --- 1. ПЛАНИРОВАНИЕ (SCANNING) ---
    def scan_and_map(self):
        """Сканирует папку и распределяет файлы по движкам (Матрица ответственности)."""
        logger.info(f"Сканирование папки '{self.cfg['source_dir']}'...")
        
        if not os.path.exists(self.cfg['source_dir']):
            logger.error("Папка исходного кода не найдена!")
            return

        # Правила распределения
        matrix = {
            '.html': {'cat': 'html', 'engines': ['w3c', 'gemini']},
            '.css':  {'cat': 'styles', 'engines': ['w3c', 'gemini']},
            '.scss': {'cat': 'styles', 'engines': ['gemini']}, # W3C не умеет SCSS
            '.sass': {'cat': 'styles', 'engines': ['gemini']},
            '.js':   {'cat': 'scripts', 'engines': ['gemini']},
            '.jsx':  {'cat': 'scripts', 'engines': ['gemini']},
            '.ts':   {'cat': 'scripts', 'engines': ['gemini']},
            '.tsx':  {'cat': 'scripts', 'engines': ['gemini']},
        }

        for root, _, files in os.walk(self.cfg['source_dir']):
            for f in files:
                ext = os.path.splitext(f)[1].lower()
                if ext in matrix:
                    rule = matrix[ext]
                    
                    # Проверка флагов из .env (CHECK_STYLES и т.д.)
                    is_allowed = False
                    if rule['cat'] == 'html' and self.cfg['check_html']: is_allowed = True
                    if rule['cat'] == 'styles' and self.cfg['check_styles']: is_allowed = True
                    if rule['cat'] == 'scripts' and self.cfg['check_scripts']: is_allowed = True
                    
                    if is_allowed:
                        # Формируем список активных движков для этого файла
                        active_engines = []
                        if 'w3c' in rule['engines'] and self.cfg['enable_w3c']:
                            active_engines.append('w3c')
                        if 'gemini' in rule['engines'] and self.cfg['enable_gemini']:
                            active_engines.append('gemini')
                        
                        if active_engines:
                            self.tasks.append(AuditTask(path=os.path.join(root, f), engines=active_engines))

        logger.info(f"Очередь задач сформирована: {len(self.tasks)} файлов.")

    # --- 2. СОХРАНЕНИЕ СОСТОЯНИЯ (PERSISTENCE) ---
    def load_state(self):
        """Загружает прогресс из временного файла."""
        if os.path.exists(self.cfg['temp_file']):
            try:
                with open(self.cfg['temp_file'], 'r', encoding='utf-8') as f:
                    data = json.load(f)
                    for item in data:
                        # Восстанавливаем объекты из JSON
                        issues = [Issue(**i) for i in item['issues']]
                        self.processed_reports.append(FileReport(path=item['path'], timestamp=item['timestamp'], issues=issues))
                logger.info(f"🔄 Восстановлено {len(self.processed_reports)} готовых отчетов из кэша.")
            except Exception as e:
                logger.warning(f"Ошибка чтения кэша: {e}")

    def save_state(self):
        """Сбрасывает текущий прогресс на диск."""
        data = [asdict(r) for r in self.processed_reports]
        with open(self.cfg['temp_file'], 'w', encoding='utf-8') as f:
            json.dump(data, f, ensure_ascii=False, indent=2)

    # --- 3. ДВИЖКИ ВАЛИДАЦИИ ---
    def run_w3c_validator(self, content: str, ext: str) -> List[Issue]:
        """Строгая проверка (возвращает те самые 300+ ошибок)."""
        url = "https://validator.w3.org/nu/?out=json"
        ctype = "text/html" if ext == '.html' else "text/css"
        headers = {'User-Agent': 'TitanAuditor/7.0', 'Content-Type': f'{ctype}; charset=utf-8'}
        
        try:
            # Таймаут побольше для больших файлов
            resp = requests.post(url, data=content.encode('utf-8'), headers=headers, timeout=20)
            if resp.status_code == 200:
                messages = resp.json().get('messages', [])
                return [Issue(
                    type="error" if m.get('type') == 'error' else "warning",
                    line=m.get('lastLine', m.get('firstLine', 0)),
                    message=f"[W3C] {m.get('message')}",
                    source="W3C Validator",
                    suggestion=None
                ) for m in messages]
        except Exception as e:
            logger.error(f"W3C API Error: {e}")
        return []

    def run_gemini_ai(self, path: str, content: str, ext: str) -> List[Issue]:
        """Интеллектуальный анализ контекста."""
        # Адаптивный промпт
        context_role = "стили и препроцессоры" if ext in ['.css', '.scss', '.sass'] else "код и логику"
        if ext in ['.js', '.ts', '.jsx', '.tsx']: context_role = "скрипты, React-компоненты и безопасность"

        prompt = f"""
        Ты Senior Lead Developer. Твоя задача — провести жесткое Code Review файла: {path}.
        Контекст: Ты проверяешь {context_role}.
        
        Критерии:
        1. Логические ошибки и баги (Critical).
        2. Безопасность (XSS, утечки памяти, eval).
        3. Best Practices (DRY, SOLID, чистота кода).
        4. Для SCSS: проверяй вложенность и миксины.
        
        Верни ответ СТРОГО в формате JSON:
        {{
            "issues": [
                {{
                    "type": "error" | "warning" | "suggestion",
                    "line": <номер строки (int)>,
                    "message": "<описание проблемы на русском>",
                    "suggestion": "<конкретный код или совет как исправить>"
                }}
            ]
        }}
        
        КОД ФАЙЛА:
        {content[:28000]} 
        """
        # Лимит токенов Gemini ~30k символов на вход

        try:
            response = self.client.models.generate_content(
                model=self.cfg["model"],
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            # Очистка от markdown-оберток
            raw_json = re.sub(r"^```json\s*|\s*```$", "", response.text, flags=re.MULTILINE).strip()
            data = json.loads(raw_json)
            
            return [Issue(
                type=i.get('type', 'warning'),
                line=i.get('line', 0),
                message=i.get('message', 'Issue found'),
                source="Gemini AI",
                suggestion=i.get('suggestion')
            ) for i in data.get("issues", [])]

        except Exception as e:
            if "429" in str(e):
                logger.warning("Gemini 429 (Quota). Пауза 60 сек...")
                time.sleep(60)
            else:
                logger.error(f"Gemini Error ({path}): {e}")
            return []

    # --- 4. ОСНОВНОЙ ЦИКЛ (PIPELINE) ---
    def execute(self):
        self.scan_and_map()
        self.load_state() # Восстановление после сбоя
        
        # Определяем, что осталось сделать
        completed_paths = {r.path for r in self.processed_reports}
        todo_queue = [t for t in self.tasks if t.path not in completed_paths]
        
        if not todo_queue:
            print("\n✅ Все файлы уже проверены! Генерирую отчет...")
            self.generate_html_report()
            return

        print(f"\n🚀 ЗАПУСК АУДИТА. Осталось задач: {len(todo_queue)} из {len(self.tasks)}")
        print(f"⚙️  Конфиг: W3C={self.cfg['enable_w3c']} | Gemini={self.cfg['enable_gemini']}")

        for i, task in enumerate(todo_queue, 1):
            print(f"[{i}/{len(todo_queue)}] 🔍 {os.path.basename(task.path)} ", end="", flush=True)
            
            file_issues = []
            file_content = ""
            ext = os.path.splitext(task.path)[1].lower()

            try:
                with open(task.path, 'r', encoding='utf-8') as f:
                    file_content = f.read()
                
                # --- W3C (Строгость) ---
                if 'w3c' in task.engines:
                    print("📡W3C..", end="", flush=True)
                    w3c_res = self.run_w3c_validator(file_content, ext)
                    file_issues.extend(w3c_res)

                # --- Gemini (Интеллект) ---
                if 'gemini' in task.engines:
                    print("🧠AI..", end="", flush=True)
                    ai_res = self.run_gemini_ai(task.path, file_content, ext)
                    file_issues.extend(ai_res)
                    # Пауза нужна только если мы дергали AI
                    time.sleep(self.delay)

                # Сохраняем результат
                report = FileReport(
                    path=task.path,
                    timestamp=datetime.now().strftime("%Y-%m-%d %H:%M"),
                    issues=file_issues
                )
                self.processed_reports.append(report)
                self.save_state() # ! ВАЖНО: Сохраняем после каждого файла
                
                err_cnt = sum(1 for x in file_issues if x.type == 'error')
                print(f" ✅ Готово (Проблем: {len(file_issues)}, Крит: {err_cnt})")

            except Exception as e:
                print(f" ❌ ОШИБКА ФАЙЛА: {e}")

        # Финал
        self.generate_html_report()
        # Удаляем временный файл только при успешном завершении всего цикла
        if os.path.exists(self.cfg['temp_file']):
            os.remove(self.cfg['temp_file'])
            print("🧹 Временные файлы очищены.")

    # --- 5. ГЕНЕРАЦИЯ ОТЧЕТА ---
    def generate_html_report(self):
        """Создает красивый HTML отчет."""
        # Подсчет статистики
        total_err = sum(sum(1 for i in r.issues if i.type == 'error') for r in self.processed_reports)
        total_warn = sum(sum(1 for i in r.issues if i.type == 'warning') for r in self.processed_reports)
        total_sugg = sum(sum(1 for i in r.issues if i.type == 'suggestion') for r in self.processed_reports)
        
        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Titan Code Audit</title>
            <style>
                :root {{ --bg: #f8fafc; --surface: #ffffff; --text: #0f172a; --err: #ef4444; --warn: #f59e0b; --info: #3b82f6; --ok: #22c55e; --border: #e2e8f0; }}
                body {{ font-family: 'Segoe UI', system-ui, sans-serif; background: var(--bg); color: var(--text); margin: 0; padding: 40px; }}
                .container {{ max-width: 1200px; margin: 0 auto; }}
                
                .header {{ display: flex; justify-content: space-between; align-items: center; margin-bottom: 30px; border-bottom: 2px solid var(--border); padding-bottom: 20px; }}
                .stats {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 40px; }}
                .card {{ background: var(--surface); padding: 25px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.05); text-align: center; }}
                .card-val {{ font-size: 2.5rem; font-weight: 800; display: block; margin-bottom: 5px; }}
                
                .file-group {{ background: var(--surface); border-radius: 12px; margin-bottom: 20px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden; }}
                .file-head {{ padding: 15px 25px; background: #f1f5f9; display: flex; justify-content: space-between; font-weight: 600; cursor: pointer; }}
                .file-head.clean {{ border-left: 6px solid var(--ok); }}
                .file-head.dirty {{ border-left: 6px solid var(--err); }}
                
                .issues {{ padding: 0; margin: 0; list-style: none; }}
                .issue {{ padding: 15px 25px; border-bottom: 1px solid var(--border); display: flex; gap: 20px; align-items: flex-start; }}
                .issue:last-child {{ border-bottom: none; }}
                
                .badge {{ padding: 4px 10px; border-radius: 6px; font-size: 0.75rem; font-weight: 700; text-transform: uppercase; min-width: 60px; text-align: center; }}
                .b-error {{ background: #fee2e2; color: #991b1b; }}
                .b-warning {{ background: #fef3c7; color: #92400e; }}
                .b-suggestion {{ background: #dbeafe; color: #1e40af; }}
                
                .source-badge {{ background: #e2e8f0; color: #475569; font-size: 0.7rem; padding: 2px 8px; border-radius: 4px; margin-left: auto; white-space: nowrap; }}
                
                .fix-box {{ margin-top: 10px; background: #f8fafc; border-left: 4px solid var(--info); padding: 10px 15px; font-size: 0.9rem; color: #334155; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header">
                    <div>
                        <h1>🛡️ TITAN AUDIT REPORT</h1>
                        <small>{datetime.now().strftime("%d.%m.%Y %H:%M")} | v7.0</small>
                    </div>
                </div>
                
                <div class="stats">
                    <div class="card"><span class="card-val">{len(self.processed_reports)}</span>Файлов</div>
                    <div class="card"><span class="card-val" style="color:var(--err)">{total_err}</span>Ошибок</div>
                    <div class="card"><span class="card-val" style="color:var(--warn)">{total_warn}</span>Варнингов</div>
                    <div class="card"><span class="card-val" style="color:var(--info)">{total_sugg}</span>Советов</div>
                </div>

                <h2>Детализация по файлам</h2>
        """

        # Сортировка: сначала файлы с ошибками
        sorted_reports = sorted(self.processed_reports, key=lambda r: len(r.issues) == 0)

        for rep in sorted_reports:
            is_clean = len(rep.issues) == 0
            status = "clean" if is_clean else "dirty"
            
            html += f"""
            <div class="file-group">
                <div class="file-head {status}">
                    <span>{rep.path}</span>
                    <span>{'✅ Чисто' if is_clean else f'🛑 {len(rep.issues)} проблем'}</span>
                </div>
            """
            
            if not is_clean:
                html += '<ul class="issues">'
                # Сортировка внутри файла: Error -> Warning -> Suggestion
                order = {'error': 0, 'warning': 1, 'suggestion': 2}
                rep.issues.sort(key=lambda x: order.get(x.type, 3))
                
                for i in rep.issues:
                    html += f"""
                    <li class="issue">
                        <span class="badge b-{i.type}">{i.type}</span>
                        <div style="font-family: monospace; color: #64748b; font-weight: bold;">L:{i.line}</div>
                        <div style="flex-grow: 1;">
                            <div>{i.message}</div>
                            {f'<div class="fix-box">💡 <b>Совет:</b> {i.suggestion}</div>' if i.suggestion else ''}
                        </div>
                        <span class="source-badge">{i.source}</span>
                    </li>
                    """
                html += '</ul>'
            html += "</div>"

        html += "</div></body></html>"
        
        with open(self.cfg['report_file'], 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"\n✨ ОТЧЕТ ГОТОВ: {os.path.abspath(self.cfg['report_file'])}")

if __name__ == "__main__":
    try:
        auditor = TitanAuditor(CONFIG)
        auditor.execute()
    except KeyboardInterrupt:
        print("\n⚠️ Прервано пользователем. Прогресс сохранен в .temp файле.")
    except Exception as e:
        print(f"\n❌ Непредвиденная ошибка: {e}")