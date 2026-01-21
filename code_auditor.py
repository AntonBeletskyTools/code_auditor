import os
import time
import json
import re
import requests
import logging
from datetime import datetime
from typing import List, Dict, Any

# --- Загрузка настроек ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    print("⚠️ Установите python-dotenv для работы с .env файлом")

try:
    from google import genai
    from google.genai import types
except ImportError:
    print("❌ Ошибка: pip install google-genai requests python-dotenv")
    exit(1)

# ================= КОНФИГУРАЦИЯ =================
def get_env_bool(key, default="True"):
    return os.getenv(key, default).lower() in ("true", "1", "yes", "on")

CONFIG = {
    "api_key": os.getenv("GEMINI_API_KEY"),
    "source_dir": "src",
    "report_file": "audit_report.html",
    "model_name": os.getenv("GEMINI_MODEL", "models/gemini-1.5-flash"),
    "extensions": ('.html', '.css', '.js', '.jsx', '.ts', '.tsx', '.scss'),
    "rpm_limit": 10,
    "gemini_enabled": get_env_bool("ENABLE_GEMINI", "True"),
    "w3c_enabled": get_env_bool("ENABLE_W3C", "True")
}

logging.basicConfig(level=logging.INFO, format='%(asctime)s - %(levelname)s - %(message)s')
logger = logging.getLogger(__name__)

class UltraAuditor:
    def __init__(self, config: Dict):
        self.cfg = config
        if self.cfg["gemini_enabled"] and not self.cfg["api_key"]:
            raise ValueError("❌ GEMINI_API_KEY не найден в .env!")
        
        if self.cfg["gemini_enabled"]:
            self.client = genai.Client(api_key=self.cfg["api_key"])
            
        self.tasks = []
        self.results = []
        self.stats = {"errors": 0, "warnings": 0, "suggestions": 0, "total_files": 0}
        self.delay = (60.0 / self.cfg["rpm_limit"]) + 1.2

    def scan(self):
        """Поиск файлов"""
        if not os.path.exists(self.cfg['source_dir']):
            logger.error(f"Директория {self.cfg['source_dir']} не найдена!")
            return
        for root, _, files in os.walk(self.cfg['source_dir']):
            for f in files:
                if f.lower().endswith(self.cfg['extensions']):
                    self.tasks.append(os.path.join(root, f))
        self.stats["total_files"] = len(self.tasks)

    def check_w3c(self, content: str, ext: str) -> List[Dict]:
        """Тот самый мощный валидатор из первого отчета"""
        if not self.cfg['w3c_enabled'] or ext not in ['.html', '.css']:
            return []
        
        url = "https://validator.w3.org/nu/?out=json"
        headers = {
            'User-Agent': 'Mozilla/5.0',
            'Content-Type': f"{'text/html' if ext=='.html' else 'text/css'}; charset=utf-8"
        }
        try:
            r = requests.post(url, data=content.encode('utf-8'), headers=headers, timeout=15)
            if r.status_code == 200:
                msgs = r.json().get('messages', [])
                return [{
                    "type": "error" if m.get('type') == 'error' else "warning",
                    "line": m.get('lastLine', m.get('firstLine', 0)),
                    "message": f"[W3C] {m.get('message')}",
                    "source": "W3C Validator"
                } for m in msgs]
        except Exception as e:
            logger.warning(f"W3C Error: {e}")
        return []

    def check_ai(self, path: str, content: str) -> List[Dict]:
        """Дополнительный AI анализ"""
        if not self.cfg['gemini_enabled']: return []
        
        prompt = f"Senior Reviewer. Audit file: {path}. Return ONLY JSON: {{'issues': [{{'type': 'error'|'warning'|'suggestion', 'line': int, 'message': str, 'suggestion': str}}]}}. Code: {content[:25000]}"
        try:
            res = self.client.models.generate_content(
                model=self.cfg["model_name"],
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            # Очистка от markdown
            clean_text = re.sub(r"```json|```", "", res.text).strip()
            data = json.loads(clean_text)
            issues = data.get("issues", [])
            for i in issues: i["source"] = "Gemini AI"
            return issues
        except Exception as e:
            logger.error(f"AI Error for {path}: {e}")
            return []

    def run(self):
        self.scan()
        print(f"🚀 СТАРТ: {self.stats['total_files']} файлов")
        print(f"🛠️ Модули: W3C={'✅' if self.cfg['w3c_enabled'] else '❌'}, Gemini={'✅' if self.cfg['gemini_enabled'] else '❌'}\n")

        for idx, path in enumerate(self.tasks, 1):
            ext = os.path.splitext(path)[1].lower()
            print(f"[{idx}/{len(self.tasks)}] 🔍 {path}...", end="", flush=True)
            
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    code = f.read()
                
                all_issues = []
                # Важно: запускаем ОБА валидатора
                all_issues.extend(self.check_w3c(code, ext))
                all_issues.extend(self.check_ai(path, code))

                # Сбор статистики
                for i in all_issues:
                    key = f"{i['type']}s"
                    self.stats[key] = self.stats.get(key, 0) + 1
                
                self.results.append({"path": path, "issues": all_issues})
                print(f" Найдено: {len(all_issues)}")
                
                # Пауза только если AI включен
                if self.cfg['gemini_enabled'] and idx < len(self.tasks):
                    time.sleep(self.delay)
                    
            except Exception as e:
                print(f" ❌ Ошибка файла: {e}")

    def save_report(self):
        """Генерация красивого и полного HTML отчета"""
        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Advanced Code Audit</title>
            <style>
                body {{ font-family: sans-serif; background: #f4f7f6; color: #333; padding: 20px; }}
                .container {{ max-width: 1100px; margin: auto; }}
                .summary {{ display: grid; grid-template-columns: repeat(4, 1fr); gap: 15px; margin-bottom: 30px; }}
                .card {{ background: white; padding: 20px; border-radius: 8px; text-align: center; box-shadow: 0 2px 4px rgba(0,0,0,0.1); }}
                .val {{ font-size: 24px; font-weight: bold; }}
                .err {{ color: #d9534f; }} .wrn {{ color: #f0ad4e; }} .sug {{ color: #5bc0de; }}
                .file-box {{ background: white; margin-bottom: 20px; border-radius: 8px; overflow: hidden; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
                .file-header {{ padding: 15px; background: #e9ecef; display: flex; justify-content: space-between; font-weight: bold; border-left: 5px solid #ccc; }}
                .has-errors {{ border-left-color: #d9534f; }}
                .is-clean {{ border-left-color: #5cb85c; }}
                .issue {{ padding: 10px 20px; border-bottom: 1px solid #eee; display: flex; align-items: flex-start; gap: 10px; }}
                .tag {{ padding: 2px 8px; border-radius: 4px; font-size: 11px; font-weight: bold; text-transform: uppercase; }}
                .tag-error {{ background: #f2dede; color: #a94442; }}
                .tag-warning {{ background: #fcf8e3; color: #8a6d3b; }}
                .tag-source {{ background: #d9edf7; color: #31708f; margin-left: auto; }}
                .fix {{ background: #f9f9f9; padding: 8px; margin-top: 5px; border-radius: 4px; border-left: 3px solid #5bc0de; font-size: 13px; }}
            </style>
        </head>
        <body>
            <div class="container">
                <h1>🛡️ Результаты полного аудита</h1>
                <div class="summary">
                    <div class="card"><div class="val">{self.stats['total_files']}</div><div>Файлов</div></div>
                    <div class="card"><div class="val err">{self.stats.get('errors', 0)}</div><div>Ошибки</div></div>
                    <div class="card"><div class="val wrn">{self.stats.get('warnings', 0)}</div><div>Предупреждения</div></div>
                    <div class="card"><div class="val sug">{self.stats.get('suggestions', 0)}</div><div>Советы AI</div></div>
                </div>
        """
        for res in self.results:
            issues = res['issues']
            status = "has-errors" if issues else "is-clean"
            html += f'<div class="file-box"><div class="file-header {status}"><span>{res["path"]}</span><span>{len(issues)} проблем</span></div>'
            for i in issues:
                html += f"""
                <div class="issue">
                    <span class="tag tag-{i['type']}">{i['type']}</span>
                    <span style="color:#888; min-width:50px">Стр: {i['line']}</span>
                    <div style="flex:1">{i['message']}
                        {f'<div class="fix">💡 {i["suggestion"]}</div>' if i.get('suggestion') else ''}
                    </div>
                    <span class="tag tag-source">{i['source']}</span>
                </div>"""
            html += "</div>"
        
        html += "</div></body></html>"
        with open(self.cfg['report_file'], 'w', encoding='utf-8') as f:
            f.write(html)
        print(f"\n✨ Отчет создан: {self.cfg['report_file']}")

if __name__ == "__main__":
    auditor = UltraAuditor(CONFIG)
    auditor.run()
    auditor.save_report()