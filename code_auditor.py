"""
ULTRA CODE AUDITOR v3.0 - FULL EDITION
=====================================================
Комбинирует W3C (строгий стандарт) + Gemini AI (логика).

ПРИМЕР .env ФАЙЛА:
-----------------------------------------------------
GEMINI_API_KEY=ваш_ключ
ENABLE_GEMINI=True
ENABLE_W3C=True
GEMINI_MODEL=models/gemini-1.5-flash
SOURCE_DIR=src
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

# Загружаем настройки
load_dotenv()

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("❌ Ошибка: установите зависимости командой:")
    print("pip install google-genai requests python-dotenv")
    exit(1)

# ================= КОНФИГУРАЦИЯ =================
def get_env_bool(key, default="True"):
    val = os.getenv(key, str(default)).lower()
    return val in ("true", "1", "yes", "on")

CONFIG = {
    "api_key": os.getenv("GEMINI_API_KEY"),
    "source_dir": os.getenv("SOURCE_DIR", "src"),
    "report_file": "full_audit_report.html",
    "model_name": os.getenv("GEMINI_MODEL", "models/gemini-1.5-flash"),
    "extensions": ('.html', '.css', '.js', '.jsx', '.ts', '.tsx', '.scss'),
    "rpm_limit": 10, # Безопасно для бесплатного Gemini
    "gemini_enabled": get_env_bool("ENABLE_GEMINI"),
    "w3c_enabled": get_env_bool("ENABLE_W3C")
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class UltraAuditor:
    def __init__(self, cfg: Dict):
        self.cfg = cfg
        self.results = []
        self.tasks = []
        self.stats = {"errors": 0, "warnings": 0, "suggestions": 0, "total_files": 0}
        
        if cfg["gemini_enabled"]:
            if not cfg["api_key"]:
                raise ValueError("❌ GEMINI_API_KEY не указан в .env!")
            self.client = genai.Client(api_key=cfg["api_key"])
        
        # Задержка между файлами для Gemini (60 сек / RPM)
        self.delay = (60.0 / cfg["rpm_limit"]) + 0.5

    def scan_files(self):
        """Собирает все файлы в указанной папке."""
        if not os.path.exists(self.cfg["source_dir"]):
            logger.error(f"Папка {self.cfg['source_dir']} не найдена!")
            return
        
        for root, _, files in os.walk(self.cfg["source_dir"]):
            for f in files:
                if f.lower().endswith(self.cfg["extensions"]):
                    self.tasks.append(os.path.join(root, f))
        
        self.stats["total_files"] = len(self.tasks)
        logger.info(f"Найдено файлов для аудита: {self.stats['total_files']}")

    def run_w3c(self, content: str, ext: str) -> List[Dict]:
        """Тот самый строгий валидатор из первого отчета."""
        if not self.cfg["w3c_enabled"] or ext not in ('.html', '.css'):
            return []

        url = "https://validator.w3.org/nu/?out=json"
        ctype = "text/html" if ext == '.html' else "text/css"
        headers = {'User-Agent': 'Mozilla/5.0', 'Content-Type': f'{ctype}; charset=utf-8'}

        try:
            r = requests.post(url, data=content.encode('utf-8'), headers=headers, timeout=15)
            if r.status_code == 200:
                messages = r.json().get('messages', [])
                return [{
                    "type": "error" if m.get('type') == 'error' else "warning",
                    "line": m.get('lastLine', m.get('firstLine', 0)),
                    "message": f"[W3C] {m.get('message')}",
                    "source": "W3C Validator"
                } for m in messages]
        except Exception as e:
            logger.warning(f"Ошибка W3C: {e}")
        return []

    def run_gemini(self, path: str, content: str) -> List[Dict]:
        """AI-анализ логики и чистоты кода."""
        if not self.cfg["gemini_enabled"]:
            return []

        prompt = f"""
        Ты Senior Code Reviewer. Проведи аудит файла: {path}
        Ищи логические ошибки, проблемы безопасности и плохие практики.
        
        Верни ответ ТОЛЬКО в формате JSON:
        {{
            "issues": [
                {{
                    "type": "error" | "warning" | "suggestion",
                    "line": <число>,
                    "message": "<описание>",
                    "suggestion": "<как исправить>"
                }}
            ]
        }}
        Код:
        {content[:25000]}
        """

        try:
            res = self.client.models.generate_content(
                model=self.cfg["model_name"],
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            # Очистка текста от возможных markdown-тегов
            text = re.sub(r"```json|```", "", res.text).strip()
            data = json.loads(text)
            issues = data.get("issues", [])
            for i in issues:
                i["source"] = "Gemini AI"
            return issues
        except Exception as e:
            logger.error(f"Ошибка Gemini для {path}: {e}")
            return []

    def execute(self):
        self.scan_files()
        print(f"\n🚀 СТАРТ ПОЛНОГО АУДИТА: {len(self.tasks)} файлов")
        print(f"📡 Модули: W3C={'✅' if self.cfg['w3c_enabled'] else '❌'} | Gemini={'✅' if self.cfg['gemini_enabled'] else '❌'}\n")

        for idx, path in enumerate(self.tasks, 1):
            name = os.path.basename(path)
            ext = os.path.splitext(path)[1].lower()
            print(f"[{idx}/{len(self.tasks)}] Анализ {name}...", end="", flush=True)
            
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()

                file_issues = []
                # Запускаем оба валидатора независимо
                file_issues.extend(self.run_w3c(content, ext))
                file_issues.extend(self.run_gemini(path, content))

                # Статистика
                for i in file_issues:
                    key = f"{i['type']}s"
                    self.stats[key] = self.stats.get(key, 0) + 1
                
                self.results.append({"path": path, "issues": file_issues})
                print(f" {'✅ Ок' if not file_issues else f' ⚠️ Найдено: {len(file_issues)}'}")

                # Пауза для лимитов API, если AI включен
                if self.cfg["gemini_enabled"] and idx < len(self.tasks):
                    time.sleep(self.delay)

            except Exception as e:
                print(f" ❌ Ошибка чтения: {e}")

    def generate_report(self):
        """Создает детальный HTML отчет со всеми найденными ошибками."""
        report_path = self.cfg["report_file"]
        
        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Advanced Code Audit Report</title>
            <style>
                :root {{ --bg: #f8fafc; --white: #ffffff; --text: #1e293b; --err: #ef4444; --wrn: #f59e0b; --sug: #3b82f6; --ok: #10b981; }}
                body {{ font-family: -apple-system, sans-serif; background: var(--bg); color: var(--text); padding: 40px; margin: 0; }}
                .container {{ max-width: 1100px; margin: 0 auto; }}
                .header-flex {{ display: flex; justify-content: space-between; align-items: baseline; border-bottom: 2px solid #e2e8f0; margin-bottom: 30px; }}
                .stats-grid {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 20px; margin-bottom: 40px; }}
                .card {{ background: var(--white); padding: 20px; border-radius: 12px; box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); text-align: center; }}
                .card b {{ font-size: 2.2rem; display: block; }}
                .file-card {{ background: var(--white); border-radius: 12px; margin-bottom: 25px; box-shadow: 0 1px 3px rgba(0,0,0,0.1); overflow: hidden; }}
                .file-head {{ padding: 15px 20px; background: #f1f5f9; display: flex; justify-content: space-between; font-weight: bold; border-left: 6px solid #cbd5e1; }}
                .clean {{ border-left-color: var(--ok); }}
                .dirty {{ border-left-color: var(--err); }}
                .issue {{ padding: 12px 20px; border-bottom: 1px solid #f1f5f9; display: flex; gap: 15px; align-items: flex-start; }}
                .badge {{ padding: 3px 10px; border-radius: 6px; font-size: 0.7rem; font-weight: 800; text-transform: uppercase; }}
                .b-error {{ background: #fee2e2; color: #991b1b; }}
                .b-warning {{ background: #fef3c7; color: #92400e; }}
                .b-suggestion {{ background: #dbeafe; color: #1e40af; }}
                .source-tag {{ background: #f1f5f9; color: #64748b; margin-left: auto; border: 1px solid #e2e8f0; }}
                .fix-tip {{ margin-top: 8px; font-size: 0.9rem; padding: 10px; background: #f8fafc; border-left: 3px solid var(--sug); border-radius: 4px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <div class="header-flex">
                    <h1>🛡️ Полный аудит проекта</h1>
                    <p>{datetime.now().strftime("%d.%m.%Y %H:%M")}</p>
                </div>
                
                <div class="stats-grid">
                    <div class="card"><b>{self.stats['total_files']}</b> Файлов</div>
                    <div class="card"><b style="color:var(--err)">{self.stats.get('errors', 0)}</b> Ошибок</div>
                    <div class="card"><b style="color:var(--wrn)">{self.stats.get('warnings', 0)}</b> Варнингов</div>
                    <div class="card"><b style="color:var(--sug)">{self.stats.get('suggestions', 0)}</b> Советов AI</div>
                </div>
        """

        for res in self.results:
            issues = res['issues']
            is_clean = len(issues) == 0
            status_class = "clean" if is_clean else "dirty"
            
            html += f"""
            <div class="file-card">
                <div class="file-head {status_class}">
                    <span>{res['path']}</span>
                    <span>{'✅ Чисто' if is_clean else f'🛑 Найдено: {len(issues)}'}</span>
                </div>
            """
            
            if not is_clean:
                # Сортируем: сначала ошибки, потом варнинги
                sorted_issues = sorted(issues, key=lambda x: 0 if x['type'] == 'error' else 1)
                for i in sorted_issues:
                    t = i['type']
                    html += f"""
                    <div class="issue">
                        <span class="badge b-{t}">{t}</span>
                        <span style="font-family:monospace; color:#64748b;">L:{i.get('line', '?')}</span>
                        <div style="flex:1;">
                            <div>{i['message']}</div>
                            {f'<div class="fix-tip">💡 <b>Совет:</b> {i["suggestion"]}</div>' if i.get('suggestion') else ''}
                        </div>
                        <span class="badge source-tag">{i['source']}</span>
                    </div>
                    """
            html += "</div>"

        html += "</div></body></html>"
        
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(html)
        print(f"\n✨ Отчет успешно создан: {os.path.abspath(report_path)}")

# --- Запуск ---
if __name__ == "__main__":
    try:
        auditor = UltraAuditor(CONFIG)
        auditor.execute()
        auditor.generate_report()
    except Exception as e:
        print(f"\n❌ Критическая ошибка: {e}")