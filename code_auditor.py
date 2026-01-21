"""
GEMINI CODE QUALITY AUDITOR (v2.1 - FULL VERSION)
=====================================================
Инструмент для автоматизированного аудита кода.

ПРИМЕР ФАЙЛА .env (создайте его рядом со скриптом):
-----------------------------------------------------
GEMINI_API_KEY=AIzaSy... (ваш ключ)
ENABLE_GEMINI=True
ENABLE_W3C=True
GEMINI_MODEL=models/gemini-1.5-flash
-----------------------------------------------------
"""

import os
import time
import json
import re
import requests
import logging
from datetime import datetime
from typing import List, Dict, Any
from dotenv import load_dotenv

# Загружаем настройки из .env
load_dotenv()

# Попытка импорта SDK Google
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("CRITICAL ERROR: Библиотека 'google-genai' не установлена.")
    print("Запустите: pip install google-genai requests python-dotenv")
    exit(1)

# ================= КОНФИГУРАЦИЯ =================
API_KEY = os.getenv("GEMINI_API_KEY")

def get_env_bool(key, default="True"):
    """Читает True/False из переменной окружения."""
    val = os.getenv(key, default).lower()
    return val in ("true", "1", "yes", "on")

CONFIG = {
    "source_dir": "src",              
    "report_file": "audit_report.html", 
    "model_name": os.getenv("GEMINI_MODEL", "models/gemini-1.5-flash"),
    "extensions": ('.html', '.css', '.js', '.jsx', '.ts', '.tsx', '.scss'),
    "rpm_limit": 10,
    "gemini_enabled": get_env_bool("ENABLE_GEMINI"),
    "w3c_enabled": get_env_bool("ENABLE_W3C")
}

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CodeAuditor:
    def __init__(self, api_key: str, config: Dict):
        if not api_key or "AIza" not in api_key:
            raise ValueError("❌ API Key не найден! Проверьте файл .env или переменную GEMINI_API_KEY.")
        
        self.client = genai.Client(api_key=api_key)
        self.config = config
        self.tasks: List[str] = []
        self.results: List[Dict] = []
        self.stats = {"errors": 0, "warnings": 0, "suggestions": 0, "total_files": 0}
        
        # Расчет задержки для соблюдения лимитов (RPM)
        self.delay = (60.0 / config["rpm_limit"]) + 0.5

    def scan_directory(self) -> None:
        """Сканирует директорию на наличие файлов."""
        if not os.path.exists(self.config['source_dir']):
            logger.error(f"Папка '{self.config['source_dir']}' не найдена.")
            return

        for root, _, files in os.walk(self.config['source_dir']):
            for file in files:
                if file.lower().endswith(self.config['extensions']):
                    self.tasks.append(os.path.join(root, file))
        
        self.stats["total_files"] = len(self.tasks)
        logger.info(f"Найдено файлов для анализа: {self.stats['total_files']}")

    def _clean_json_response(self, text: str) -> str:
        """Очищает ответ AI от Markdown-оберток."""
        cleaned = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
        return cleaned.strip()

    def check_w3c(self, content: str, ext: str) -> List[Dict]:
        """Проверка через W3C Validator."""
        if not self.config['w3c_enabled'] or ext not in ['.html', '.css']:
            return []

        url = "https://validator.w3.org/nu/?out=json"
        content_type = "text/html" if ext == '.html' else "text/css"
        headers = {'User-Agent': 'CodeAuditor/2.1', 'Content-Type': f'{content_type}; charset=utf-8'}

        try:
            resp = requests.post(url, data=content.encode('utf-8'), headers=headers, timeout=10)
            if resp.status_code == 200:
                messages = resp.json().get('messages', [])
                return [{
                    "type": "error" if m.get('type') == 'error' else "warning",
                    "line": m.get('lastLine', 0),
                    "message": f"[W3C] {m.get('message')}",
                    "source": "W3C Validator"
                } for m in messages]
        except Exception as e:
            logger.warning(f"W3C недоступен: {e}")
        return []

    def check_ai_gemini(self, file_path: str, content: str) -> List[Dict]:
        """Анализ кода через Gemini AI."""
        if not self.config["gemini_enabled"]:
            return []

        prompt = f"""
        Ты Senior Code Reviewer. Проведи аудит кода файла: {file_path}
        Верни ответ СТРОГО в JSON формате:
        {{
            "issues": [
                {{
                    "type": "error" | "warning" | "suggestion",
                    "line": <число>,
                    "message": "<описание на русском>",
                    "suggestion": "<как исправить>"
                }}
            ]
        }}
        КОД:
        {content[:25000]}
        """

        try:
            response = self.client.models.generate_content(
                model=self.config["model_name"],
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            raw_text = self._clean_json_response(response.text)
            data = json.loads(raw_text)
            issues = data.get("issues", [])
            for i in issues:
                i["source"] = "Gemini AI"
            return issues
        except Exception as e:
            logger.error(f"Ошибка AI для {file_path}: {e}")
            return []

    def run_pipeline(self):
        """Запуск процесса аудита."""
        self.scan_directory()
        if not self.tasks:
            return

        print(f"\n🚀 Запуск аудита: {self.stats['total_files']} файлов")
        print(f"📡 Модель: {self.config['model_name']}")
        print(f"⚙️ Валидаторы: Gemini={'✅' if self.config['gemini_enabled'] else '❌'}, W3C={'✅' if self.config['w3c_enabled'] else '❌'}")

        for idx, path in enumerate(self.tasks, 1):
            file_name = os.path.basename(path)
            ext = os.path.splitext(path)[1].lower()
            print(f"[{idx}/{self.stats['total_files']}] 🔍 {file_name}...", end="", flush=True)

            file_issues = []
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Вызов включенных валидаторов
                if self.config["gemini_enabled"]:
                    file_issues.extend(self.check_ai_gemini(path, content))
                
                if self.config["w3c_enabled"] and ext in ['.html', '.css']:
                    file_issues.extend(self.check_w3c(content, ext))

                # Сбор статистики
                for issue in file_issues:
                    key = f"{issue.get('type', 'warning')}s"
                    self.stats[key] = self.stats.get(key, 0) + 1

                self.results.append({"path": path, "issues": file_issues})
                
                if not file_issues:
                    print(" ✅ Clean")
                else:
                    print(f" ⚠️ {len(file_issues)} проблем")

                # Пауза для лимитов API
                if self.config["gemini_enabled"] and idx < self.stats["total_files"]:
                    time.sleep(self.delay)

            except Exception as e:
                print(f" ❌ Ошибка: {e}")

    def generate_report(self):
        """Генерация финального HTML-отчета."""
        report_path = self.config['report_file']
        
        html_template = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Code Audit Report</title>
            <style>
                :root {{ --bg: #f4f6f9; --white: #ffffff; --text: #2d3748; --danger: #e53e3e; --warn: #dd6b20; --info: #3182ce; --success: #38a169; }}
                body {{ font-family: sans-serif; background: var(--bg); color: var(--text); padding: 40px; }}
                .container {{ max-width: 1100px; margin: 0 auto; }}
                .stats-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 30px; }}
                .stat-card {{ background: var(--white); padding: 20px; border-radius: 10px; text-align: center; box-shadow: 0 2px 5px rgba(0,0,0,0.1); }}
                .stat-value {{ font-size: 2rem; font-weight: bold; }}
                .file-block {{ background: var(--white); border-radius: 8px; margin-bottom: 20px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
                .file-header {{ padding: 15px; background: #edf2f7; display: flex; justify-content: space-between; font-weight: bold; }}
                .issue-item {{ padding: 15px; border-bottom: 1px solid #edf2f7; display: flex; gap: 15px; align-items: flex-start; }}
                .badge {{ padding: 3px 10px; border-radius: 4px; font-size: 11px; font-weight: bold; text-transform: uppercase; }}
                .badge-error {{ background: #fed7d7; color: #9b2c2c; }}
                .badge-warning {{ background: #feebc8; color: #9c4221; }}
                .badge-suggestion {{ background: #bee3f8; color: #2c5282; }}
                .badge-source {{ background: #e2e8f0; color: #4a5568; margin-left: auto; }}
                .issue-fix {{ display: block; margin-top: 8px; color: #4a5568; font-size: 13px; font-style: italic; background: #f7fafc; padding: 10px; border-radius: 5px; border-left: 3px solid #3182ce; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🛡️ Отчет аудита кода</h1>
                <p>Дата: {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>
                
                <div class="stats-grid">
                    <div class="stat-card"> <div class="stat-value">{self.stats['total_files']}</div> <div>Файлов</div> </div>
                    <div class="stat-card"> <div class="stat-value" style="color:var(--danger)">{self.stats.get('errors', 0)}</div> <div>Ошибок</div> </div>
                    <div class="stat-card"> <div class="stat-value" style="color:var(--warn)">{self.stats.get('warnings', 0)}</div> <div>Предупреждений</div> </div>
                    <div class="stat-card"> <div class="stat-value" style="color:var(--info)">{self.stats.get('suggestions', 0)}</div> <div>Советов</div> </div>
                </div>

                <h2>Результаты по файлам</h2>
        """

        for res in self.results:
            issues = res['issues']
            is_clean = len(issues) == 0
            
            html_template += f"""
            <div class="file-block">
                <div class="file-header" style="border-left: 6px solid {'var(--success)' if is_clean else 'var(--danger)'}">
                    <span>{res['path']}</span>
                    <span>{ '✅ Чисто' if is_clean else f'🛑 {len(issues)} проблем' }</span>
                </div>
            """
            
            if not is_clean:
                for i in sorted(issues, key=lambda x: x.get('type')):
                    i_type = i.get('type', 'warning')
                    html_template += f"""
                    <div class="issue-item">
                        <span class="badge badge-{i_type}">{i_type}</span>
                        <div style="flex:1">
                            <strong>Строка {i.get('line', '?')}:</strong> {i.get('message')}
                            {f'<span class="issue-fix">💡 Рекомендация: {i.get("suggestion")}</span>' if i.get('suggestion') else ''}
                        </div>
                        <span class="badge badge-source">{i.get('source', 'AI')}</span>
                    </div>
                    """
            html_template += "</div>"

        html_template += "</div></body></html>"
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html_template)
        print(f"\n✨ Отчет успешно создан: {os.path.abspath(report_path)}")

# ================= ЗАПУСК =================
if __name__ == "__main__":
    try:
        auditor = CodeAuditor(API_KEY, CONFIG)
        auditor.run_pipeline()
        auditor.generate_report()
    except Exception as e:
        print(f"Критическая ошибка: {e}")