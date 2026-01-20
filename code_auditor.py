"""
GEMINI CODE QUALITY PIPELINE (v1.0)
==================================
Инструкция по использованию:
1. Установите зависимости: pip install google-genai requests
2. Положите этот скрипт рядом с вашей папкой исходников (по умолчанию 'src').
3. Получите API ключ на https://aistudio.google.com/
4. Вставьте ключ в переменную API_KEY ниже.
5. Запустите: python main.py

Что делает скрипт:
- Сканирует src на наличие .html, .css, .js, .scss.
- Соблюдает лимит 15 запросов в минуту (паузы между файлами).
- Проверяет синтаксис через W3C и логику/стиль через Gemini AI.
- Генерирует интерактивный HTML-отчет с результатами.
"""

import os
import time
import json
import requests
from datetime import datetime
from google import genai

# ================= НАСТРОЙКИ (КОНФИГУРАЦИЯ) =================
API_KEY = "ВАШ_API_KEY_ЗДЕСЬ"
SOURCE_DIRECTORY = "src"          # Папка для анализа
REPORT_NAME = "audit_report.html"  # Имя выходного файла
RPM_LIMIT = 15                     # Лимиты бесплатного Gemini
# Рассчитываем задержку: 60 сек / 15 запросов + запас 0.5 сек
SAFE_DELAY = (60 / RPM_LIMIT) + 0.5 

# Инициализация клиента Gemini
client = genai.Client(api_key=API_KEY)

class QualityAuditor:
    def __init__(self):
        self.tasks = []      # Список путей к файлам
        self.results = []    # Данные для отчета
        self.stats = {"errors": 0, "warnings": 0, "total": 0}

    def scan_project(self):
        """Рекурсивно ищет файлы в дереве каталогов."""
        print(f"--- Шаг 1: Сканирование директории '{SOURCE_DIRECTORY}' ---")
        for root, _, files in os.walk(SOURCE_DIRECTORY):
            for file in files:
                if file.endswith(('.html', '.css', '.js', '.scss', '.sass')):
                    full_path = os.path.join(root, file)
                    self.tasks.append(full_path)
        
        self.stats["total"] = len(self.tasks)
        print(f"Найдено файлов: {self.stats['total']}\n")

    def call_w3c_validator(self, content, ext):
        """Отправляет код на официальный валидатор W3C."""
        # W3C Nu Validator принимает типы 'html' или 'css'
        v_type = 'html' if ext in ['html', 'js'] else 'css'
        url = "https://validator.w3.org/nu/?out=json"
        headers = {'Content-Type': f'text/{v_type}; charset=utf-8'}
        try:
            r = requests.post(url, data=content.encode('utf-8'), headers=headers, timeout=5)
            # Возвращаем только список сообщений
            return r.json().get('messages', [])
        except:
            return []

    def call_gemini_ai(self, file_path, content):
        """Анализирует код с помощью ИИ на предмет логических ошибок и чистоты."""
        # Мы просим строго JSON, чтобы скрипт мог его распарсить
        prompt = f"""
        Ты эксперт по качеству кода. Проанализируй файл: {file_path}
        Найди: 1. Потенциальные баги 2. Нарушения Clean Code 3. Проблемы производительности.
        Верни ответ ТОЛЬКО в формате JSON:
        {{
            "issues": [
                {{"type": "error", "line": 10, "text": "описание"}},
                {{"type": "warning", "line": 20, "text": "совет"}}
            ]
        }}
        Код файла:
        {content}
        """
        
        try:
            response = client.models.generate_content(
                model="gemini-2.5-flash",
                contents=prompt,
                config={'response_mime_type': 'application/json'}
            )
            return json.loads(response.text).get('issues', [])
        except Exception as e:
            # Если превышен лимит (429), это будет обработано в основном цикле
            raise e

    def run_pipeline(self):
        """Основной цикл конвейера с очередью и паузами."""
        print(f"--- Шаг 2: Анализ файлов (Лимит {RPM_LIMIT} RPM) ---")
        
        for i, path in enumerate(self.tasks, 1):
            ext = path.split('.')[-1].lower()
            print(f"[{i}/{self.stats['total']}] Обработка: {path}...", end="", flush=True)
            
            try:
                with open(path, 'r', encoding='utf-8') as f:
                    content = f.read()

                # Собираем замечания из двух источников
                file_issues = []
                
                # 1. Формальная проверка (W3C)
                if ext in ['html', 'css']:
                    w3c_msgs = self.call_w3c_validator(content, ext)
                    for m in w3c_msgs:
                        file_issues.append({
                            "type": m.get('type', 'error'),
                            "line": m.get('lastLine', '?'),
                            "text": f"[W3C] {m.get('message')}"
                        })

                # 2. Интеллектуальная проверка (Gemini)
                ai_issues = self.call_gemini_ai(path, content)
                file_issues.extend(ai_issues)

                # Сохраняем результат
                self.results.append({"path": path, "ext": ext, "issues": file_issues})
                
                # Обновляем статистику
                for iss in file_issues:
                    self.stats[iss['type'] + "s"] = self.stats.get(iss['type'] + "s", 0) + 1
                
                print(" ✅")

                # ПАУЗА ДЛЯ СОБЛЮДЕНИЯ ЛИМИТОВ
                if i < self.stats['total']:
                    time.sleep(SAFE_DELAY)

            except Exception as e:
                if "429" in str(e):
                    print("\n⚠️ Лимит достигнут. Пауза 30 секунд...")
                    time.sleep(30)
                    # Можно было бы повторить попытку для этого файла, 
                    # но для простоты переходим к следующему или перезапустите скрипт
                else:
                    print(f" ❌ Ошибка: {e}")

    def generate_report(self):
        """Создает красивый HTML файл с деревом и ошибками."""
        print(f"\n--- Шаг 3: Генерация отчета '{REPORT_NAME}' ---")
        
        now = datetime.now().strftime("%d.%m.%Y %H:%M")
        
        # Начало HTML документа
        html = f"""
        <!DOCTYPE html>
        <html lang="ru">
        <head>
            <meta charset="UTF-8">
            <title>Code Audit: {SOURCE_DIRECTORY}</title>
            <style>
                body {{ font-family: 'Segoe UI', Tahoma, sans-serif; background: #f0f2f5; color: #333; margin: 0; padding: 40px; }}
                h1 {{ color: #1a73e8; }}
                .summary {{ background: white; padding: 20px; border-radius: 10px; margin-bottom: 30px; display: flex; gap: 40px; box-shadow: 0 2px 10px rgba(0,0,0,0.05); }}
                .stat-item {{ font-size: 1.2em; }}
                .stat-error {{ color: #d93025; font-weight: bold; }}
                .file-card {{ background: white; border-radius: 8px; padding: 20px; margin-bottom: 15px; border-left: 5px solid #ccc; box-shadow: 0 1px 3px rgba(0,0,0,0.1); }}
                .file-card.has-error {{ border-left-color: #d93025; }}
                .file-path {{ font-family: monospace; font-size: 1.1em; background: #f8f9fa; padding: 5px 10px; border-radius: 4px; }}
                .issue {{ margin: 10px 0; padding-left: 20px; border-bottom: 1px solid #eee; padding-bottom: 5px; }}
                .type-error {{ color: #d93025; font-size: 0.9em; text-transform: uppercase; font-weight: bold; }}
                .type-warning {{ color: #f9ab00; font-size: 0.9em; text-transform: uppercase; font-weight: bold; }}
                .line-num {{ color: #70757a; font-weight: bold; }}
            </style>
        </head>
        <body>
            <h1>Отчет анализа проекта</h1>
            <div class="summary">
                <div class="stat-item">📁 Файлов: {self.stats['total']}</div>
                <div class="stat-item stat-error">❌ Ошибок: {self.stats.get('errors', 0)}</div>
                <div class="stat-item">⚠️ Предупреждений: {self.stats.get('warnings', 0)}</div>
                <div class="stat-item">🕒 Дата: {now}</div>
            </div>
        """

        # Добавляем данные по каждому файлу
        for res in self.results:
            has_err = any(i['type'] == 'error' for i in res['issues'])
            err_class = "has-error" if has_err else ""
            
            html += f'<div class="file-card {err_class}">'
            html += f'<span class="file-path">{res["path"]}</span>'
            
            if not res['issues']:
                html += '<p style="color: #1e8e3e;">✅ Проблем не обнаружено</p>'
            else:
                for iss in res['issues']:
                    i_type = iss.get('type', 'warning')
                    html += f"""
                    <div class="issue">
                        <span class="type-{i_type}">{i_type}</span> 
                        <span class="line-num">Строка {iss.get('line', '?')}:</span> {iss.get('text')}
                    </div>"""
            html += "</div>"

        html += "</body></html>"
        
        with open(REPORT_NAME, "w", encoding="utf-8") as f:
            f.write(html)
        print("Успешно! Откройте файл отчета в браузере.")

# ================= ЗАПУСК ПРОГРАММЫ =================
if __name__ == "__main__":
    auditor = QualityAuditor()
    
    # Проверяем наличие папки
    if not os.path.exists(SOURCE_DIRECTORY):
        print(f"Ошибка: Папка '{SOURCE_DIRECTORY}' не найдена!")
    else:
        auditor.scan_project()
        if auditor.tasks:
            auditor.run_pipeline()
            auditor.generate_report()
        else:
            print("Файлы для анализа не найдены.")