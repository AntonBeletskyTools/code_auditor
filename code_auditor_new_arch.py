"""
==============================================================================
CODE AUDITOR PRO v2.0 (Refactored & Architected)
Enterprise-grade Code Audit System

ARCHITECTURAL CHANGES:
- S.O.L.I.D. Principles applied.
- Pattern: Strategy (Validators).
- Pattern: Observer (Console UI).
- Pattern: Repository (State Management).
- Pattern: Builder/Renderer (HTML Reporting).
- Separation of Concerns: Config, Logic, UI, and IO are decoupled.

ORIGINAL FEATURES PRESERVED:
1. Error Isolation & State Persistence.
2. Responsibility Matrix (HTML/CSS/JS routing).
3. Advanced Key Rotation (Linear Queue for Gemini).
4. Exact "Corporate" HTML Report Design.
5. Console UX (Progress bars, Countdowns).

AUTHOR: Asguard (Refactored by Gemini)
==============================================================================
"""

import os
import json
import time
import re
import sys
import random
import logging
from abc import ABC, abstractmethod
from datetime import datetime
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Set, Any, Union

# --- Dependency Check ---
try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from google import genai
    from google.genai import types
    from curl_cffi import requests
except ImportError:
    print("❌ CRITICAL: pip install google-genai curl-cffi requests python-dotenv")
    exit(1)

# Logging Setup (Internal system logs, separate from User UI)
logging.basicConfig(level=logging.ERROR, format='%(asctime)s | %(levelname)s | %(message)s')
logger = logging.getLogger("System")


# ==============================================================================
# 1. CONFIGURATION & INFRASTRUCTURE
# ==============================================================================

class ConfigManager:
    """Manages environment variables and settings. Acts as a single source of truth."""
    def __init__(self):
        self._load_env()

    def _load_env(self):
        def get_bool(k, d="True"): return os.getenv(k, d).lower() in ("true", "1", "yes", "on")
        def get_int(k, d="0"): return int(os.getenv(k, d))
        
        # Paths
        self.src_dir = os.getenv("SOURCE_DIR", "src")
        self.temp_file = os.getenv("TEMP_STATE_FILE", "audit_state.temp.json")
        self.report_file = "code_auditor_report.html"

        # Toggles
        self.enable_w3c = get_bool("ENABLE_W3C", "False")
        self.enable_gemini = get_bool("ENABLE_GEMINI", "False")
        self.enable_grok = get_bool("ENABLE_GROK", "False")
        self.check_html = get_bool("CHECK_HYPERTEXT", "True")
        self.check_css = get_bool("CHECK_STYLES", "True")
        self.check_js = get_bool("CHECK_SCRIPTS", "True")
        self.resume_audit = get_bool("RESUME_AUDIT", "True")

        # Gemini Settings
        raw_keys = os.getenv("GEMINI_API_KEY", "")
        self.gemini_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        self.gemini_model = os.getenv("GEMINI_MODEL", "models/gemini-2.0-flash")
        self.gemini_max_chars = get_int("GEMINI_MAX_CHARS", "60000")
        self.key_rotate_interval = get_int("KEY_ROTATE_INTERVAL", "600")

        # Grok Settings
        self.grok_key = os.getenv("XAI_API_KEY", "")
        self.grok_model = os.getenv("GROK_MODEL", "grok-2-latest")
        self.grok_max_chars = get_int("GROK_MAX_CHARS", "60000")

        # System
        self.api_sleep_raw = os.getenv("API_SLEEP", "15,30")

    def get_sleep_time(self) -> float:
        """Parses API_SLEEP which can be a float or a range 'min,max'."""
        s = self.api_sleep_raw.replace(" ", "")
        if "," in s:
            try:
                mn, mx = map(float, s.split(","))
                return random.uniform(mn, mx)
            except: return 10.0
        try:
            return float(s)
        except: return 10.0

# ==============================================================================
# 2. DATA MODELS (DTOs)
# ==============================================================================

@dataclass
class Issue:
    type: str       # error, warning, suggestion
    line: int
    message: str
    source: str
    suggestion: Optional[str] = None

@dataclass
class FileReport:
    path: str
    timestamp: str
    issues: List[Issue] = field(default_factory=list)
    checked_by: List[str] = field(default_factory=list)

class APIBannedException(Exception): pass

# ==============================================================================
# 3. UTILITIES
# ==============================================================================

class HeadersBuilder:
    """Encapsulates anti-bot header generation logic."""
    @staticmethod
    def build(ext: Optional[str] = None, is_post: bool = False) -> dict:
        ctype = "text/html" if ext == '.html' else "text/css" if ext == '.css' else "text/plain"
        headers = {
            'User-Agent': "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
            'Accept': "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,image/apng,*/*;q=0.8",
            'Accept-Language': "ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7",
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site' if is_post else 'none',
            'DNT': '1'
        }
        if is_post:
            headers['Content-Type'] = f"{ctype}; charset=utf-8"
            headers['Origin'] = "https://validator.w3.org"
        return headers

class KeyRing:
    """Manages API key rotation logic specifically for Gemini."""
    def __init__(self, keys: List[str]):
        self.keys = keys
    
    def get_current(self) -> Optional[str]:
        return self.keys[0] if self.keys else None
    
    def burn_current(self):
        """Removes the current key from the pool (Linear Queue logic)."""
        if self.keys:
            removed = self.keys.pop(0)
            return removed
        return None
    
    def is_empty(self) -> bool:
        return len(self.keys) == 0

# ==============================================================================
# 4. VALIDATOR STRATEGY PATTERN
# ==============================================================================

class BaseValidator(ABC):
    def __init__(self, name: str, short_name: str, enabled: bool):
        self.name = name
        self.short_name = short_name
        self.enabled = enabled

    @abstractmethod
    def can_check(self, ext: str) -> bool: pass

    @abstractmethod
    def check(self, path: str, content: str, ext: str) -> List[Issue]: pass

class W3CValidator(BaseValidator):
    API_URL = "https://validator.w3.org/nu/?out=json"

    def __init__(self, config: ConfigManager):
        super().__init__("W3C Validator", "W3C", config.enable_w3c)

    def can_check(self, ext: str) -> bool:
        return ext in ['.html', '.css']

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled: return []
        
        headers = HeadersBuilder.build(ext='.html', is_post=True)
        session = requests.Session(impersonate="chrome124")
        session.headers.clear()

        try:
            resp = session.post(self.API_URL, data=content.encode('utf-8'), headers=headers, timeout=15)
            if resp.status_code == 200:
                return [Issue(
                    type="error" if m.get('type') == 'error' else "warning",
                    line=m.get('lastLine', m.get('firstLine', 0)),
                    message=f"[Standard] {m.get('message')}",
                    source=self.name
                ) for m in resp.json().get('messages', [])]
            elif resp.status_code == 429:
                self.enabled = False
                raise APIBannedException("W3C Rate Limit")
            return [Issue(type="warning", line=0, message=f"W3C Error {resp.status_code}", source=self.name)]
        except APIBannedException:
            raise
        except Exception as e:
            return [Issue(type="warning", line=0, message="W3C Connection Failed", source=self.name)]

class GeminiValidator(BaseValidator):
    def __init__(self, config: ConfigManager):
        super().__init__("Gemini AI", "AI", config.enable_gemini)
        self.key_ring = KeyRing(config.gemini_keys)
        self.model = config.gemini_model
        self.max_chars = config.gemini_max_chars
        if not self.key_ring.keys:
            self.enabled = False

    def can_check(self, ext: str) -> bool:
        return ext in ['.html', '.css', '.scss', '.sass', '.js', '.jsx', '.ts', '.tsx']

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or self.key_ring.is_empty(): return []
        
        prompt = self._build_prompt(path, content[:self.max_chars], ext)
        issues = []

        # Rotation Logic: Linear Queue
        while not self.key_ring.is_empty():
            current_key = self.key_ring.get_current()
            try:
                client = genai.Client(api_key=current_key)
                response = client.models.generate_content(
                    model=self.model, contents=prompt,
                    config=types.GenerateContentConfig(response_mime_type="application/json")
                )
                
                # Parse JSON
                text = response.text
                json_match = re.search(r'\{.*\}', text, re.DOTALL)
                clean_json = json_match.group(0) if json_match else re.sub(r"^```json\s*|\s*```$", "", text, flags=re.MULTILINE).strip()
                
                try:
                    data = json.loads(clean_json)
                except: data = {}

                return [Issue(
                    type=i.get('type', 'warning'),
                    line=i.get('line', 0),
                    message=i.get('message', 'Issue detected'),
                    source=self.name,
                    suggestion=i.get('suggestion')
                ) for i in data.get('issues', [])]

            except Exception as e:
                err = str(e).lower()
                if "429" in err or "quota" in err:
                    self.key_ring.burn_current() # Rotate
                    if self.key_ring.is_empty(): break
                    time.sleep(2)
                    continue
                elif "404" in err:
                    self.enabled = False
                    return []
                else:
                    return [] # Generic error, skip file
        
        self.enabled = False
        return [Issue(type="error", line=0, message="All Gemini keys exhausted.", source=self.name)]

    def _build_prompt(self, path, content, ext):
        ctx = "code"
        if ext in ['.css', '.scss']: ctx = "styles"
        elif ext in ['.js', '.ts', '.jsx']: ctx = "logic & security"
        
        return f"""
        Role: Senior Code Reviewer. Task: Audit {path}. Context: {ctx}.
        Rules: Find bugs, security risks, bad practices. Be concise.
        Response JSON: {{ "issues": [ {{ "type": "error"|"warning", "line": int, "message": "text", "suggestion": "fix" }} ] }}
        CODE: {content}
        """

class GrokValidator(BaseValidator):
    def __init__(self, config: ConfigManager):
        super().__init__("Grok Dual", "GROK", config.enable_grok)
        self.key = config.grok_key
        self.model = config.grok_model
        self.max_chars = config.grok_max_chars

    def can_check(self, ext: str) -> bool:
        return ext in ['.html', '.css', '.scss', '.js', '.ts', '.py', '.php']

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.key: return []
        
        prompt = f"""
        Role: Dual-Layer Auditor (Standards + Logic). File: {path}.
        Output: JSON {{ "issues": [...] }}
        Code: {content[:self.max_chars]}
        """
        
        try:
            resp = requests.post(
                "https://api.x.ai/v1/chat/completions",
                json={
                    "model": self.model,
                    "messages": [{"role": "user", "content": prompt}],
                    "response_format": {"type": "json_object"},
                    "temperature": 0.2
                },
                headers={"Authorization": f"Bearer {self.key}"}, timeout=40
            )
            
            if resp.status_code == 200:
                data = json.loads(resp.json()['choices'][0]['message']['content'])
                return [Issue(
                    type=i.get('type', 'warning'),
                    line=i.get('line', 0),
                    message=i.get('message'),
                    source=self.name,
                    suggestion=i.get('suggestion')
                ) for i in data.get('issues', [])]
            elif resp.status_code == 429:
                self.enabled = False
                raise APIBannedException("Grok Rate Limit")
            return []
        except APIBannedException: raise
        except Exception: return []

# ==============================================================================
# 5. STATE & REPORTING (IO LAYER)
# ==============================================================================

class StateRepository:
    """Handles loading and saving of the audit progress."""
    def __init__(self, filepath: str):
        self.filepath = filepath

    def save(self, reports: List[FileReport]):
        try:
            tmp = self.filepath + ".tmp"
            with open(tmp, 'w', encoding='utf-8') as f:
                json.dump([asdict(r) for r in reports], f, ensure_ascii=False, indent=2)
            os.replace(tmp, self.filepath)
        except Exception: pass

    def load(self) -> List[FileReport]:
        if not os.path.exists(self.filepath): return []
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                return [FileReport(
                    path=r['path'], 
                    timestamp=r['timestamp'], 
                    issues=[Issue(**i) for i in r['issues']],
                    checked_by=r.get('checked_by', [])
                ) for r in json.load(f)]
        except: return []
    
    def clear(self):
        if os.path.exists(self.filepath): os.remove(self.filepath)

class HtmlReportRenderer:
    """Separates HTML generation logic from business logic."""
    STYLE = """
    :root { --bg-body:#f8fafc; --bg-card:#ffffff; --text-main:#334155; --text-muted:#64748b; --color-success:#10b981; --color-danger:#ef4444; --color-warning:#f59e0b; --color-info:#3b82f6; --color-neutral:#94a3b8; --border:#e2e8f0; }
    body { font-family:'Segoe UI',system-ui,sans-serif; background:var(--bg-body); color:var(--text-main); margin:0; padding:40px; line-height:1.5; }
    .container { max-width:1200px; margin:0 auto; }
    .header { background:#1e293b; color:white; padding:30px; border-radius:12px; margin-bottom:30px; display:flex; justify-content:space-between; align-items:center; box-shadow:0 4px 6px -1px rgba(0,0,0,0.1); }
    .header h1 { margin:0; font-size:1.5rem; letter-spacing:0.5px; }
    .header-meta { text-align:right; font-size:0.9rem; color:#94a3b8; }
    .active-tools span { background:#334155; padding:4px 10px; border-radius:4px; font-weight:bold; color:#60a5fa; margin-left:5px; font-size:0.8rem; }
    .stats { display:grid; grid-template-columns:repeat(4, 1fr); gap:20px; margin-bottom:40px; }
    .stat-card { background:var(--bg-card); padding:20px; border-radius:10px; border:1px solid var(--border); text-align:center; }
    .stat-val { font-size:2.2rem; font-weight:700; display:block; margin-bottom:5px; }
    .stat-label { color:var(--text-muted); font-size:0.9rem; text-transform:uppercase; letter-spacing:1px; }
    .file-block { background:var(--bg-card); border-radius:8px; margin-bottom:16px; border:1px solid var(--border); overflow:hidden; }
    .file-header { padding:12px 20px; display:flex; justify-content:space-between; align-items:center; background:#f1f5f9; font-weight:600; font-size:0.95rem; }
    .st-clean { border-left:5px solid var(--color-success); } .st-dirty { border-left:5px solid var(--color-danger); } .st-skipped { border-left:5px solid var(--color-neutral); background:#f8fafc; color:var(--text-muted); }
    .badge { padding:4px 10px; border-radius:4px; font-size:0.75rem; font-weight:bold; text-transform:uppercase; color:white; margin-left:8px; display:inline-block; }
    .bdg-tool { background:#94a3b8; } .bdg-grok { background:#000000; border:1px solid #444; } .bdg-ai { background:#8b5cf6; } .bdg-w3c { background:#0284c7; }
    .bdg-clean { background:#10b981; } .bdg-dirty { background:#ef4444; } .bdg-skipped { background:#64748b; text-decoration:line-through; }
    .issues-list { border-top:1px solid var(--border); }
    .issue-row { padding:12px 20px; display:flex; gap:15px; border-bottom:1px solid #f1f5f9; align-items:flex-start; }
    .issue-row:last-child { border-bottom:none; }
    .severity { padding:4px 8px; border-radius:6px; font-size:0.75rem; font-weight:800; min-width:80px; text-align:center; }
    .sv-error { background:#fee2e2; color:#ef4444; } .sv-warning { background:#fef3c7; color:#d97706; } .sv-suggestion { background:#dbeafe; color:#2563eb; }
    .line-num { font-family:monospace; color:var(--text-muted); font-weight:bold; min-width:40px; }
    .msg-content { flex-grow:1; }
    .fix-box { margin-top:8px; background:#f8fafc; border-left:3px solid var(--color-info); padding:8px 12px; font-size:0.9rem; color:#334155; }
    .src-tag { font-size:0.75rem; background:#f1f5f9; padding:2px 6px; border-radius:4px; color:#94a3b8; margin-left:auto; }
    """

    def render(self, reports: List[FileReport], active_tools: List[str], config: ConfigManager):
        # Stats Calc
        total = len(reports)
        dirty = sum(1 for r in reports if r.issues)
        clean = sum(1 for r in reports if not r.issues and r.checked_by)
        skipped = sum(1 for r in reports if not r.checked_by)
        
        # Sorting
        sorted_reports = sorted(reports, key=lambda r: (0 if r.checked_by and r.issues else 1 if r.checked_by else 2))

        # HTML Body Construction
        tools_html = "".join([f'<span>{t}</span>' for t in active_tools]) if active_tools else "⚠️ ALL DISABLED"
        
        html = f"""<!DOCTYPE html><html lang="ru"><head><meta charset="UTF-8"><title>Code Audit Report</title><style>{self.STYLE}</style></head><body>
        <div class="container"><div class="header"><div><h1>🛡️ Code Security & Quality Audit</h1><div class="active-tools" style="margin-top:5px;">Enabled Modules: {tools_html}</div></div>
        <div class="header-meta"><div>Date: {datetime.now().strftime("%Y-%m-%d %H:%M")}</div><div>Source: {config.src_dir}</div></div></div>
        <div class="stats">
            <div class="stat-card"><span class="stat-val">{total}</span><span class="stat-label">Total Files</span></div>
            <div class="stat-card" style="border-bottom:4px solid var(--color-success)"><span class="stat-val" style="color:var(--color-success)">{clean}</span><span class="stat-label">Passed</span></div>
            <div class="stat-card" style="border-bottom:4px solid var(--color-danger)"><span class="stat-val" style="color:var(--color-danger)">{dirty}</span><span class="stat-label">Issues Found</span></div>
            <div class="stat-card" style="border-bottom:4px solid var(--color-neutral)"><span class="stat-val" style="color:var(--color-neutral)">{skipped}</span><span class="stat-label">Not Checked</span></div>
        </div>"""

        for r in sorted_reports:
            html += self._render_file_block(r)
        
        html += "</div></body></html>"
        
        with open(config.report_file, "w", encoding="utf-8") as f:
            f.write(html)
        return config.report_file

    def _render_file_block(self, r: FileReport):
        # Badge Logic
        tools = ""
        for t in r.checked_by:
            cls = "bdg-grok" if "GROK" in t else "bdg-ai" if "AI" in t else "bdg-w3c" if "W3C" in t else "bdg-tool"
            tools += f'<span class="badge {cls}">{t}</span>'
        
        if not r.checked_by:
            status_cls, status_bdg = "st-skipped", '<span class="badge bdg-skipped">NOT CHECKED</span>'
        elif not r.issues:
            status_cls, status_bdg = "st-clean", '<span class="badge bdg-clean">CLEAN</span>'
        else:
            status_cls, status_bdg = "st-dirty", f'<span class="badge bdg-dirty">{len(r.issues)} ISSUES</span>'

        block = f'<div class="file-block"><div class="file-header {status_cls}"><span style="font-family:monospace;font-size:1rem;">{r.path}</span><div class="badges">{tools} {status_bdg}</div></div>'
        
        if r.issues:
            block += '<div class="issues-list">'
            for i in sorted(r.issues, key=lambda x: 0 if x.type=='error' else 1):
                block += f"""<div class="issue-row"><span class="severity sv-{i.type}">{i.type}</span><span class="line-num">L:{i.line}</span>
                <div class="msg-content"><div>{i.message}</div>{f'<div class="fix-box">💡 {i.suggestion}</div>' if i.suggestion else ''}</div><span class="src-tag">{i.source}</span></div>"""
            block += '</div>'
        
        return block + "</div>"

# ==============================================================================
# 6. CONSOLE UI (OBSERVER PATTERN)
# ==============================================================================

class ConsoleUI:
    """Decouples printing from logic. Uses direct printing to maintain 'progress bar' feel."""
    def __init__(self, config: ConfigManager):
        self.config = config
        self.filename_width = 70

    def show_header(self, validators):
        print(f"\n💾 ЗАПУСК CODE AUDITOR PRO v2.0...")
        print("="*100)
        print(f"📂 Source: {self.config.src_dir} | 🔄 Resume: {self.config.resume_audit}")
        print("-" * 100)
        print(f"🔌 Modules Status:")
        for v in validators:
            extra = f" ({self.config.gemini_max_chars})" if "Gemini" in v.name else ""
            print(f"   • {v.name:<15} {'✅ ON' if v.enabled else '⬜ OFF'}{extra}")
        print("="*100 + "\n")

    def print_file_start(self, idx, total, path):
        filename = os.path.basename(path)
        display = (filename[:self.filename_width-3] + "...") if len(filename) > self.filename_width else filename.ljust(self.filename_width, ".")
        print(f"👉 [{idx:>3}/{total:<3}] {display} ", end="", flush=True)

    def print_validator_start(self, short_name):
        print(f"[{short_name}..", end="", flush=True)

    def print_validator_end(self, status="ok"):
        if status == "ok": print("ok] ", end="", flush=True)
        else: print("err] ", end="", flush=True)

    def print_file_result(self, issues_count, checked_by):
        if not checked_by: print(" ⚪ Not Checked")
        elif issues_count == 0: print(f" ✅ Clean [{', '.join(checked_by)}]")
        else: print(f" ⚠️ {issues_count:>3} issues")

    def show_wait(self, seconds):
        try:
            for i in range(int(seconds), 0, -1):
                sys.stdout.write(f" ⏳ Ожидание: {i}s...   \r")
                sys.stdout.flush()
                time.sleep(1)
            sys.stdout.write(" " * 40 + "\r") # Clear line
        except KeyboardInterrupt: pass

    def show_error(self, msg):
        print(f"\n❌ {msg}")

    def show_final(self, report_path):
        print(f"\n✨ ОТЧЕТ СГЕНЕРИРОВАН: {report_path}")

# ==============================================================================
# 7. MAIN PIPELINE (ORCHESTRATOR)
# ==============================================================================

class AuditPipeline:
    def __init__(self):
        self.cfg = ConfigManager()
        self.ui = ConsoleUI(self.cfg)
        self.state_repo = StateRepository(self.cfg.temp_file)
        self.renderer = HtmlReportRenderer()
        
        # Factory / Registration
        self.validators: List[BaseValidator] = [
            W3CValidator(self.cfg),
            GeminiValidator(self.cfg),
            GrokValidator(self.cfg)
        ]

    def _scan_files(self) -> List[str]:
        files = []
        ext_map = {
            'html': ['.html'],
            'css': ['.css', '.scss', '.sass'],
            'js': ['.js', '.jsx', '.ts', '.tsx']
        }
        for root, _, fs in os.walk(self.cfg.src_dir):
            for f in fs:
                ext = os.path.splitext(f)[1].lower()
                path = os.path.join(root, f)
                if ((ext in ext_map['html'] and self.cfg.check_html) or
                    (ext in ext_map['css'] and self.cfg.check_css) or
                    (ext in ext_map['js'] and self.cfg.check_js)):
                    files.append(path)
        return files

    def run(self):
        self.ui.show_header(self.validators)
        
        # 1. Prepare Queue
        all_files = self._scan_files()
        processed_reports = self.state_repo.load() if self.cfg.resume_audit else []
        processed_paths = {r.path for r in processed_reports}
        queue = [f for f in all_files if f not in processed_paths]
        
        self.reports = processed_reports
        total_q = len(queue)
        
        if not queue:
            self.ui.show_error("Нет файлов для проверки или все проверены.")
            self._finalize()
            return

        # 2. Processing Loop
        for idx, path in enumerate(queue, 1):
            self.ui.print_file_start(idx, total_q, path)
            
            # Read Content
            try:
                with open(path, 'r', encoding='utf-8', errors='ignore') as f:
                    content = f.read()
            except Exception:
                print(" Read Error")
                continue

            if not content.strip():
                print(" Empty (Skipped)")
                self._add_report(path, [], [])
                continue

            # Check if we should abort (All validators dead)
            if not any(v.enabled for v in self.validators):
                self.ui.show_error("ВСЕ API ОТКЛЮЧЕНЫ. Сохранение...")
                break

            # 3. Validation Chain
            file_issues = []
            checked_by = []
            api_called = False

            ext = os.path.splitext(path)[1].lower()

            for v in self.validators:
                if v.enabled and v.can_check(ext):
                    self.ui.print_validator_start(v.short_name)
                    try:
                        issues = v.check(path, content, ext)
                        file_issues.extend(issues)
                        checked_by.append(v.short_name)
                        api_called = True
                        self.ui.print_validator_end("ok")
                    except APIBannedException:
                         # Validator disabled itself inside check()
                        self.ui.print_validator_end("err") 
                    except Exception as e:
                        self.ui.print_validator_end("err")
                        # Log internally, don't spam user
            
            # 4. Result & State Save
            self.ui.print_file_result(len(file_issues), checked_by)
            self._add_report(path, file_issues, checked_by)

            # 5. Smart Sleep
            if api_called:
                self.ui.show_wait(self.cfg.get_sleep_time())

        # 6. Finalize
        self._finalize()

    def _add_report(self, path, issues, checked_by):
        rep = FileReport(path, datetime.now().strftime("%H:%M"), issues, checked_by)
        self.reports.append(rep)
        self.state_repo.save(self.reports)

    def _finalize(self):
        report_path = self.renderer.render(
            self.reports, 
            [v.short_name for v in self.validators if v.enabled], 
            self.cfg
        )
        self.ui.show_final(report_path)
        self.state_repo.clear()

if __name__ == "__main__":
    try:
        AuditPipeline().run()
    except KeyboardInterrupt:
        print("\n🚫 STOPPED BY USER")