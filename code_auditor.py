"""
GEMINI CODE QUALITY AUDITOR (v2.0 - Production Ready)
=====================================================
Инструмент для автоматизированного аудита кода с использованием AI и W3C.

Требования:
    pip install google-genai requests python-dotenv

Использование:
    1. Создайте файл .env и добавьте туда: GEMINI_API_KEY=ваш_ключ
    2. Или вставьте ключ в переменную API_KEY ниже (менее безопасно).
    3. Запустите: python code_auditor.py
"""

import os
import time
import json
import re
import requests
import logging
from datetime import datetime
from typing import List, Dict, Any

# Попытка импорта новой SDK Google. Если нет - ошибка будет понятной.
try:
    from google import genai
    from google.genai import types
except ImportError:
    print("CRITICAL ERROR: Библиотека 'google-genai' не установлена.")
    print("Запустите: pip install google-genai")
    exit(1)

# ================= КОНФИГУРАЦИЯ =================
# Приоритет: Переменная окружения -> Хардкод
API_KEY = os.getenv("GEMINI_API_KEY", "ВСТАВЬТЕ_СЮДА_ВАШ_КЛЮЧ_ЕСЛИ_НЕТ_ENV")

CONFIG = {
    "source_dir": "src",              # Папка с исходным кодом
    "report_file": "audit_report.html", # Имя файла отчета
    "model_name": "gemini-1.5-flash",   # Актуальная быстрая модель
    "extensions": ('.html', '.css', '.js', '.jsx', '.ts', '.tsx', '.scss'), # Расширенный список
    "rpm_limit": 15,                    # Лимит запросов в минуту (Free Tier)
    "w3c_enabled": True                 # Включить классическую валидацию
}

# Настройка логирования
logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class CodeAuditor:
    """
    Класс-оркестратор для проверки качества кода.
    """
    def __init__(self, api_key: str, config: Dict):
        if not api_key or "ВСТАВЬТЕ" in api_key:
            raise ValueError("❌ API Key не найден! Установите GEMINI_API_KEY в .env или в скрипте.")
        
        self.client = genai.Client(api_key=api_key)
        self.config = config
        self.tasks: List[str] = []
        self.results: List[Dict] = []
        self.stats = {"errors": 0, "warnings": 0, "suggestions": 0, "total_files": 0}
        
        # Расчет безопасной задержки (60 сек / RPM + буфер)
        self.delay = (60.0 / config["rpm_limit"]) + 1.0

    def scan_directory(self) -> None:
        """Сканирует директорию на наличие целевых файлов."""
        logger.info(f"Сканирование папки '{self.config['source_dir']}'...")
        
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
        """Очищает ответ AI от Markdown-оберток (```json ... ```)."""
        cleaned = re.sub(r"^```json\s*", "", text, flags=re.MULTILINE)
        cleaned = re.sub(r"\s*```$", "", cleaned, flags=re.MULTILINE)
        return cleaned.strip()

    def check_w3c(self, content: str, ext: str) -> List[Dict]:
        """Отправляет HTML/CSS на валидатор W3C."""
        if not self.config['w3c_enabled'] or ext not in ['.html', '.css']:
            return []

        # W3C Nu Validator API
        url = "[https://validator.w3.org/nu/?out=json](https://validator.w3.org/nu/?out=json)"
        content_type = "text/html" if ext == '.html' else "text/css"
        headers = {'User-Agent': 'CodeAuditor/2.0', 'Content-Type': f'{content_type}; charset=utf-8'}

        try:
            resp = requests.post(url, data=content.encode('utf-8'), headers=headers, timeout=10)
            if resp.status_code == 200:
                messages = resp.json().get('messages', [])
                # Преобразуем формат W3C в наш формат
                return [{
                    "type": "error" if m.get('type') == 'error' else "warning",
                    "line": m.get('lastLine', 0),
                    "message": f"[W3C] {m.get('message')}",
                    "source": "W3C Validator"
                } for m in messages]
        except Exception as e:
            logger.warning(f"W3C Validator недоступен: {e}")
        
        return []

    def check_ai_gemini(self, file_path: str, content: str) -> List[Dict]:
        """Анализирует код через Gemini с механизмом повторных попыток (Retry)."""
        prompt = f"""
        Ты Senior Code Reviewer. Проведи аудит кода файла: {file_path}
        
        Критерии анализа:
        1. Логические ошибки и баги (Critical).
        2. Безопасность (XSS, SQLi, Secrets).
        3. Чистота кода (DRY, именование, форматирование).
        4. Производительность.

        Верни ответ СТРОГО в валидном JSON формате без лишнего текста:
        {{
            "issues": [
                {{
                    "type": "error" | "warning" | "suggestion",
                    "line": <номер строки числом>,
                    "message": "<краткое описание проблемы на русском>",
                    "suggestion": "<как исправить>"
                }}
            ]
        }}
        
        Если проблем нет, верни "issues": [].
        
        КОД:
        {content[:30000]} 
        """
        # Обрезаем контент, если файл гигантский, чтобы влезть в контекст (30к символов ~ 7-8к токенов)

        max_retries = 3
        for attempt in range(max_retries):
            try:
                response = self.client.models.generate_content(
                    model=self.config["model_name"],
                    contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                
                raw_text = self._clean_json_response(response.text)
                data = json.loads(raw_text)
                
                # Добавляем метку источника
                issues = data.get("issues", [])
                for i in issues:
                    i["source"] = "Gemini AI"
                return issues

            except Exception as e:
                error_str = str(e)
                if "429" in error_str:
                    wait_time = (attempt + 1) * 20
                    logger.warning(f"⚠️ Лимит квот (429). Ждем {wait_time} сек...")
                    time.sleep(wait_time)
                else:
                    logger.error(f"Ошибка AI анализа для {file_path}: {e}")
                    return []
        
        return []

    def run_pipeline(self):
        """Запуск основного цикла проверки."""
        if not self.tasks:
            self.scan_directory()
        
        if self.stats["total_files"] == 0:
            logger.warning("Файлы для анализа не найдены.")
            return

        print(f"\n🚀 Запуск анализа {self.stats['total_files']} файлов...")
        print(f"📡 Модель: {self.config['model_name']} | 🐢 Задержка: {self.delay:.1f} сек/файл")

        for idx, path in enumerate(self.tasks, 1):
            file_name = os.path.basename(path)
            ext = os.path.splitext(path)[1].lower()
            
            print(f"[{idx}/{self.stats['total_files']}] 🔍 {file_name}...", end="", flush=True)

            file_issues = []
            
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # 1. AI Проверка (Все файлы)
                ai_results = self.check_ai_gemini(path, content)
                file_issues.extend(ai_results)
                
                # 2. W3C Проверка (Только HTML/CSS)
                w3c_results = self.check_w3c(content, ext)
                file_issues.extend(w3c_results)

                # Агрегация статистики
                for issue in file_issues:
                    key = f"{issue.get('type', 'warning')}s" # errors/warnings/suggestions
                    self.stats[key] = self.stats.get(key, 0) + 1

                self.results.append({
                    "path": path,
                    "issues": file_issues
                })
                
                # Визуальный фидбек
                if not file_issues:
                    print(" ✅ OK")
                else:
                    err_count = sum(1 for i in file_issues if i['type'] == 'error')
                    print(f" ⚠️ Найдено: {len(file_issues)} (Крит: {err_count})")

                # Соблюдение лимитов
                if idx < self.stats["total_files"]:
                    time.sleep(self.delay)

            except UnicodeDecodeError:
                print(" ❌ Skipped (Binary)")
            except Exception as e:
                print(f" ❌ Error: {e}")

    def generate_report(self):
        """Генерирует современный HTML отчет."""
        report_path = self.config['report_file']
        
        # HTML Шаблон (CSS внутри)
        html_template = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Code Audit Report</title>
            <style>
                :root {{ --bg: #f4f6f9; --white: #ffffff; --text: #2d3748; --danger: #e53e3e; --warn: #dd6b20; --info: #3182ce; --success: #38a169; }}
                body {{ font-family: -apple-system, BlinkMacSystemFont, "Segoe UI", Roboto, sans-serif; background: var(--bg); color: var(--text); padding: 40px; line-height: 1.6; }}
                .container {{ max-width: 1000px; margin: 0 auto; }}
                h1 {{ color: #1a202c; border-bottom: 2px solid #cbd5e0; padding-bottom: 15px; }}
                
                .stats-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 40px; }}
                .stat-card {{ background: var(--white); padding: 20px; border-radius: 8px; box-shadow: 0 2px 4px rgba(0,0,0,0.05); text-align: center; }}
                .stat-value {{ font-size: 2.5rem; font-weight: bold; }}
                .stat-label {{ color: #718096; text-transform: uppercase; font-size: 0.875rem; letter-spacing: 1px; }}
                .c-error {{ color: var(--danger); }} .c-warn {{ color: var(--warn); }}
                
                .file-block {{ background: var(--white); border-radius: 8px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); margin-bottom: 20px; overflow: hidden; }}
                .file-header {{ padding: 15px 20px; background: #edf2f7; border-bottom: 1px solid #e2e8f0; display: flex; justify-content: space-between; align-items: center; font-weight: 600; font-family: monospace; }}
                .clean-file {{ border-left: 5px solid var(--success); }}
                .dirty-file {{ border-left: 5px solid var(--danger); }}
                
                .issues-list {{ list-style: none; padding: 0; margin: 0; }}
                .issue-item {{ padding: 15px 20px; border-bottom: 1px solid #edf2f7; display: flex; gap: 15px; }}
                .issue-item:last-child {{ border-bottom: none; }}
                
                .badge {{ padding: 2px 8px; border-radius: 4px; font-size: 0.75rem; font-weight: bold; text-transform: uppercase; height: fit-content; }}
                .badge-error {{ background: #fed7d7; color: #9b2c2c; }}
                .badge-warning {{ background: #feebc8; color: #9c4221; }}
                .badge-suggestion {{ background: #bee3f8; color: #2c5282; }}
                .badge-source {{ background: #e2e8f0; color: #4a5568; margin-left: auto; }}
                
                .issue-line {{ font-family: monospace; font-weight: bold; color: #718096; min-width: 60px; }}
                .issue-content {{ flex-grow: 1; }}
                .issue-desc {{ font-weight: 500; }}
                .issue-fix {{ display: block; margin-top: 5px; color: #4a5568; font-size: 0.9em; background: #f7fafc; padding: 8px; border-radius: 4px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🛡️ Отчет аудита кода</h1>
                <p>Дата проверки: {datetime.now().strftime("%d.%m.%Y %H:%M")}</p>
                
                <div class="stats-grid">
                    <div class="stat-card"><div class="stat-value">{self.stats['total_files']}</div><div class="stat-label">Файлов</div></div>
                    <div class="stat-card"><div class="stat-value c-error">{self.stats.get('errors', 0)}</div><div class="stat-label">Ошибок</div></div>
                    <div class="stat-card"><div class="stat-value c-warn">{self.stats.get('warnings', 0)}</div><div class="stat-label">Варнингов</div></div>
                    <div class="stat-card"><div class="stat-value" style="color:#3182ce">{self.stats.get('suggestions', 0)}</div><div class="stat-label">Советов</div></div>
                </div>

                <h2>Детализация по файлам</h2>
        """

        for res in self.results:
            issues = res['issues']
            has_issues = len(issues) > 0
            is_clean = not has_issues
            status_class = "clean-file" if is_clean else "dirty-file"
            
            html_template += f"""
            <div class="file-block {status_class}">
                <div class="file-header">
                    <span>{res['path']}</span>
                    <span>{'✅ Clean' if is_clean else f'🛑 {len(issues)} Issues'}</span>
                </div>
            """
            
            if has_issues:
                html_template += '<ul class="issues-list">'
                # Сортировка: Сначала ошибки, потом остальное
                sorted_issues = sorted(issues, key=lambda x: 0 if x.get('type') == 'error' else 1)
                
                for i in sorted_issues:
                    i_type = i.get('type', 'warning')
                    html_template += f"""
                    <li class="issue-item">
                        <span class="badge badge-{i_type}">{i_type}</span>
                        <span class="issue-line">L:{i.get('line', '?')}</span>
                        <div class="issue-content">
                            <div class="issue-desc">{i.get('message')}</div>
                            {f'<span class="issue-fix">💡 {i.get("suggestion")}</span>' if i.get('suggestion') else ''}
                        </div>
                        <span class="badge badge-source">{i.get('source', 'AI')}</span>
                    </li>
                    """
                html_template += '</ul>'
            
            html_template += "</div>"

        html_template += """
            </div>
        </body>
        </html>
        """
        
        try:
            with open(report_path, "w", encoding="utf-8") as f:
                f.write(html_template)
            print(f"\n✨ Отчет успешно сгенерирован: {os.path.abspath(report_path)}")
        except Exception as e:
            logger.error(f"Не удалось записать отчет: {e}")

# ================= ЗАПУСК =================
if __name__ == "__main__":
    # Пример использования
    # 1. Убедитесь, что папка 'src' существует.
    # 2. Установите зависимости.
    
    print("--- ЗАПУСК CODE AUDITOR 2.0 ---")
    
    auditor = CodeAuditor(API_KEY, CONFIG)
    auditor.run_pipeline()
    auditor.generate_report()
