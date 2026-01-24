"""
==============================================================================
CODE AUDITOR PRO - Refactored Edition
Enterprise-grade Code Audit System

Architecture: Clean Architecture + SOLID Principles
Author: Asguard (Refactored)
==============================================================================
"""

import os
import json
import time
import re
import logging
from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict, field
from typing import List, Optional, Dict, Tuple, Protocol
from datetime import datetime
from enum import Enum
from pathlib import Path
import random

try:
    from dotenv import load_dotenv
    load_dotenv()
except ImportError:
    pass

try:
    from curl_cffi import requests
    from google import genai
    from google.genai import types
except ImportError as e:
    print(f"❌ CRITICAL: Missing dependency - {e}")
    exit(1)


# ==============================================================================
# LOGGING CONFIGURATION
# ==============================================================================

def setup_logging() -> logging.Logger:
    """Configure application logging"""
    logging.basicConfig(
        level=logging.INFO,
        format='%(asctime)s | %(levelname)-8s | %(message)s',
        datefmt='%H:%M:%S'
    )
    return logging.getLogger("CodeAuditor")

logger = setup_logging()


# ==============================================================================
# DOMAIN MODELS
# ==============================================================================

class IssueType(Enum):
    """Issue severity levels"""
    ERROR = "error"
    WARNING = "warning"
    SUGGESTION = "suggestion"


class FileStatus(Enum):
    """File audit status"""
    CLEAN = "clean"
    DIRTY = "dirty"
    NOT_CHECKED = "not_checked"


@dataclass
class Issue:
    """Single code issue"""
    type: str
    line: int
    message: str
    source: str
    suggestion: Optional[str] = None

    def to_dict(self) -> dict:
        return asdict(self)


@dataclass
class FileReport:
    """Audit report for single file"""
    path: str
    timestamp: str
    issues: List[Issue] = field(default_factory=list)
    checked_by: List[str] = field(default_factory=list)

    @property
    def status(self) -> FileStatus:
        if not self.checked_by:
            return FileStatus.NOT_CHECKED
        return FileStatus.DIRTY if self.issues else FileStatus.CLEAN

    def to_dict(self) -> dict:
        return {
            'path': self.path,
            'timestamp': self.timestamp,
            'issues': [i.to_dict() for i in self.issues],
            'checked_by': self.checked_by
        }


# ==============================================================================
# EXCEPTIONS
# ==============================================================================

class AuditorException(Exception):
    """Base exception for auditor"""
    pass


class APIQuotaExceededException(AuditorException):
    """API rate limit exceeded"""
    pass


class ValidationException(AuditorException):
    """Validation failed"""
    pass


# ==============================================================================
# PROTOCOLS (INTERFACES)
# ==============================================================================

class IValidator(Protocol):
    """Validator interface"""
    name: str
    short_name: str
    enabled: bool

    def can_check(self, ext: str) -> bool:
        """Check if validator supports file extension"""
        ...

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        """Perform validation"""
        ...


class IReporter(Protocol):
    """Reporter interface"""
    def generate(self, reports: List[FileReport], metadata: Dict) -> str:
        """Generate report and return path"""
        ...


class IStateManager(Protocol):
    """State persistence interface"""
    def save(self, reports: List[FileReport]) -> None:
        ...

    def restore(self) -> List[FileReport]:
        ...

    def clear(self) -> None:
        ...


# ==============================================================================
# CONFIGURATION
# ==============================================================================

@dataclass
class AuditorConfig:
    """Application configuration"""
    source_dir: str
    temp_file: str
    
    # Validators
    w3c_enabled: bool
    gemini_enabled: bool
    grok_enabled: bool
    
    # Gemini settings
    gemini_keys: List[str]
    gemini_model: str
    gemini_max_chars: int
    
    # Grok settings
    grok_key: str
    grok_model: str
    grok_max_chars: int
    
    # Filters
    check_html: bool
    check_css: bool
    check_js: bool
    
    # Misc
    api_sleep: str
    resume_audit: bool
    key_rotate_interval: int

    @classmethod
    def from_env(cls) -> 'AuditorConfig':
        """Load configuration from environment"""
        def get_bool(key: str, default: str = "True") -> bool:
            return os.getenv(key, default).lower() in ("true", "1", "yes", "on")
        
        raw_keys = os.getenv("GEMINI_API_KEY", "")
        gemini_keys = [k.strip() for k in raw_keys.split(",") if k.strip()]
        
        return cls(
            source_dir=os.getenv("SOURCE_DIR", "src"),
            temp_file=os.getenv("TEMP_STATE_FILE", "audit_state.temp.json"),
            w3c_enabled=get_bool("ENABLE_W3C"),
            gemini_enabled=get_bool("ENABLE_GEMINI"),
            grok_enabled=get_bool("ENABLE_GROK"),
            gemini_keys=gemini_keys,
            gemini_model=os.getenv("GEMINI_MODEL", "models/gemini-2.0-flash"),
            gemini_max_chars=int(os.getenv("GEMINI_MAX_CHARS", "30000")),
            grok_key=os.getenv("XAI_API_KEY", ""),
            grok_model=os.getenv("GROK_MODEL", "grok-2-latest"),
            grok_max_chars=int(os.getenv("GROK_MAX_CHARS", "45000")),
            check_html=get_bool("CHECK_HYPERTEXT"),
            check_css=get_bool("CHECK_STYLES"),
            check_js=get_bool("CHECK_SCRIPTS"),
            api_sleep=os.getenv("API_SLEEP", "10.0"),
            resume_audit=get_bool("RESUME_AUDIT"),
            key_rotate_interval=int(os.getenv("KEY_ROTATE_INTERVAL", "300"))
        )


# ==============================================================================
# INFRASTRUCTURE - HTTP HEADERS
# ==============================================================================

class HeadersBuilder:
    """HTTP headers generator for realistic requests"""
    
    USER_AGENTS = [
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36",
        "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/119.0.0.0 Safari/537.36",
    ]

    @staticmethod
    def build(ext: Optional[str] = None, is_post: bool = False) -> dict:
        """Build HTTP headers"""
        content_type = {
            '.html': 'text/html',
            '.css': 'text/css'
        }.get(ext, 'text/plain')
        
        headers = {
            'User-Agent': HeadersBuilder.USER_AGENTS[0],
            'Accept': 'text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8',
            'Accept-Language': 'ru-RU,ru;q=0.9,en-US;q=0.8,en;q=0.7',
            'Connection': 'keep-alive',
            'Upgrade-Insecure-Requests': '1',
            'Sec-Fetch-Dest': 'document',
            'Sec-Fetch-Mode': 'navigate',
            'Sec-Fetch-Site': 'cross-site' if is_post else 'none',
            'DNT': '1'
        }
        
        if is_post:
            headers['Content-Type'] = f"{content_type}; charset=utf-8"
            headers['Origin'] = "https://validator.w3.org"
            
        return headers


# ==============================================================================
# INFRASTRUCTURE - STATE PERSISTENCE
# ==============================================================================

class JSONStateManager:
    """JSON-based state persistence"""
    
    def __init__(self, filepath: str):
        self.filepath = Path(filepath)
        self.temp_filepath = self.filepath.with_suffix('.tmp')

    def save(self, reports: List[FileReport]) -> None:
        """Save state to file with atomic write"""
        try:
            data = [r.to_dict() for r in reports]
            
            # Write to temp file first
            with open(self.temp_filepath, 'w', encoding='utf-8') as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
            
            # Atomic rename
            self.temp_filepath.replace(self.filepath)
        except Exception as e:
            logger.error(f"State save error: {e}")

    def restore(self) -> List[FileReport]:
        """Restore state from file"""
        if not self.filepath.exists():
            return []
        
        try:
            with open(self.filepath, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return [
                    FileReport(
                        path=r['path'],
                        timestamp=r['timestamp'],
                        issues=[Issue(**i) for i in r['issues']],
                        checked_by=r.get('checked_by', [])
                    ) for r in data
                ]
        except Exception as e:
            logger.warning(f"Resume failed: {e}")
            return []

    def clear(self) -> None:
        """Remove state file"""
        try:
            if self.filepath.exists():
                self.filepath.unlink()
        except Exception as e:
            logger.error(f"Clear state error: {e}")


# ==============================================================================
# VALIDATORS - BASE
# ==============================================================================

class BaseValidator(ABC):
    """Abstract base validator"""
    
    def __init__(self, name: str, short_name: str, enabled: bool):
        self.name = name
        self.short_name = short_name
        self.enabled = enabled

    @abstractmethod
    def can_check(self, ext: str) -> bool:
        """Check if validator supports extension"""
        pass

    @abstractmethod
    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        """Perform validation"""
        pass


# ==============================================================================
# VALIDATORS - W3C
# ==============================================================================

class W3CValidator(BaseValidator):
    """W3C standards validator"""
    
    API_URL = "https://validator.w3.org/nu/?out=json"
    SUPPORTED_EXTENSIONS = {'.html', '.css'}
    
    def __init__(self, enabled: bool):
        super().__init__(name="W3C Validator", short_name="W3C", enabled=enabled)
        self._session = None

    def can_check(self, ext: str) -> bool:
        return ext in self.SUPPORTED_EXTENSIONS

    def _get_session(self):
        """Lazy session initialization"""
        if self._session is None:
            self._session = requests.Session(impersonate="chrome124")
            self._session.headers.clear()
        return self._session

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.can_check(ext):
            return []

        headers = HeadersBuilder.build(ext=ext, is_post=True)
        session = self._get_session()
        
        try:
            resp = session.post(
                self.API_URL,
                data=content.encode('utf-8'),
                headers=headers,
                timeout=15
            )
            
            return self._process_response(resp)
            
        except Exception as e:
            logger.error(f"[{self.name}] Connection Error: {e}")
            return [Issue(
                type=IssueType.WARNING.value,
                line=0,
                message="W3C Connection Failed",
                source=self.name
            )]

    def _process_response(self, resp) -> List[Issue]:
        """Process W3C API response"""
        status = resp.status_code
        
        if status == 200:
            messages = resp.json().get('messages', [])
            return [
                Issue(
                    type=IssueType.ERROR.value if m.get('type') == 'error' else IssueType.WARNING.value,
                    line=m.get('lastLine', m.get('firstLine', 0)),
                    message=f"[Standard] {m.get('message')}",
                    source=self.name
                ) for m in messages
            ]
        elif status == 429:
            self.enabled = False
            logger.error(f"[{self.name}] 429: Too Many Requests!")
            return [Issue(
                type=IssueType.ERROR.value,
                line=0,
                message="🚫 W3C API Rate Limit (429)",
                source=self.name
            )]
        else:
            return [Issue(
                type=IssueType.ERROR.value,
                line=0,
                message=f"W3C API Error: {status}",
                source=self.name
            )]


# ==============================================================================
# VALIDATORS - GEMINI AI
# ==============================================================================

class APIKeyManager:
    """Manages API key rotation"""
    
    def __init__(self, keys: List[str]):
        self._keys = list(keys)  # Create a copy
        self._current_index = 0

    @property
    def current_key(self) -> Optional[str]:
        """Get current active key"""
        return self._keys[0] if self._keys else None

    @property
    def has_keys(self) -> bool:
        """Check if any keys available"""
        return len(self._keys) > 0

    @property
    def keys_count(self) -> int:
        """Get total keys count"""
        return len(self._keys)

    def remove_current(self) -> None:
        """Remove current key from pool"""
        if self._keys:
            removed = self._keys.pop(0)
            logger.warning(f"⚠️ Key ...{removed[-4:]} exhausted. Remaining: {len(self._keys)}")


class GeminiValidator(BaseValidator):
    """Gemini AI validator"""
    
    SUPPORTED_EXTENSIONS = {'.html', '.css', '.scss', '.sass', '.js', '.jsx', '.ts', '.tsx'}
    
    def __init__(
        self,
        enabled: bool,
        api_keys: List[str],
        model: str,
        max_chars: int,
        api_sleep: float,
        key_rotate: int
    ):
        super().__init__(name="Gemini AI", short_name="AI", enabled=enabled)
        self.model = model
        self.max_chars = max_chars
        self.api_sleep = api_sleep
        self.key_rotate = key_rotate
        self.call_count = 0
        
        self._key_manager = APIKeyManager(api_keys)
        
        if self.enabled and not self._key_manager.has_keys:
            logger.warning("Gemini API Keys missing. AI disabled.")
            self.enabled = False

    def can_check(self, ext: str) -> bool:
        return ext in self.SUPPORTED_EXTENSIONS

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.can_check(ext):
            return []

        issues = self._check_content_length(content)
        prompt = self._build_prompt(path, content, ext)
        
        while self._key_manager.has_keys:
            try:
                result = self._call_api(prompt)
                self.call_count += 1
                return issues + result
            except APIQuotaExceededException:
                self._key_manager.remove_current()
                if not self._key_manager.has_keys:
                    break
                time.sleep(2)
                continue
            except Exception as e:
                logger.error(f"❌ Gemini Error: {e}")
                return issues

        # All keys exhausted
        logger.error("💀 All Gemini keys exhausted.")
        self.enabled = False
        return issues + [Issue(
            type=IssueType.ERROR.value,
            line=0,
            message="All Gemini keys quota exceeded. Module disabled.",
            source=self.name
        )]

    def _check_content_length(self, content: str) -> List[Issue]:
        """Check if content exceeds max length"""
        if len(content) > self.max_chars:
            return [Issue(
                type=IssueType.WARNING.value,
                line=0,
                message=f"⚠️ File truncated ({len(content)} > {self.max_chars} chars).",
                source=self.name
            )]
        return []

    def _build_prompt(self, path: str, content: str, ext: str) -> str:
        """Build AI prompt"""
        context_map = {
            '.css': "styles (SCSS/CSS)",
            '.scss': "styles (SCSS/CSS)",
            '.sass': "styles (SCSS/CSS)",
            '.js': "logic & security (JS/React)",
            '.jsx': "logic & security (JS/React)",
            '.ts': "logic & security (JS/React)",
            '.tsx': "logic & security (JS/React)",
            '.html': "HTML structure & semantics"
        }
        context = context_map.get(ext, "code")
        
        return f"""
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

    def _call_api(self, prompt: str) -> List[Issue]:
        """Make API call"""
        current_key = self._key_manager.current_key
        if not current_key:
            raise APIQuotaExceededException("No keys available")

        client = genai.Client(api_key=current_key)
        
        try:
            response = client.models.generate_content(
                model=self.model,
                contents=prompt,
                config=types.GenerateContentConfig(response_mime_type="application/json")
            )
            
            return self._parse_response(response.text)
            
        except Exception as e:
            err_msg = str(e).lower()
            if "429" in err_msg or "quota" in err_msg:
                raise APIQuotaExceededException(f"Quota exceeded: {e}")
            elif "404" in err_msg:
                self.enabled = False
                logger.error(f"❌ [{self.name}] Invalid model. AI disabled.")
                return []
            else:
                raise

    def _parse_response(self, text: str) -> List[Issue]:
        """Parse AI response"""
        json_match = re.search(r'\{.*\}', text, re.DOTALL)
        clean_json = json_match.group(0) if json_match else text
        
        try:
            data = json.loads(clean_json)
        except json.JSONDecodeError:
            return []
        
        return [
            Issue(
                type=i.get('type', IssueType.WARNING.value),
                line=i.get('line', 0),
                message=i.get('message', 'Issue detected'),
                source=self.name,
                suggestion=i.get('suggestion')
            ) for i in data.get('issues', [])
        ]


# ==============================================================================
# VALIDATORS - GROK
# ==============================================================================

class GrokValidator(BaseValidator):
    """Grok AI dual-mode validator"""
    
    SUPPORTED_EXTENSIONS = {'.html', '.css', '.scss', '.js', '.jsx', '.ts', '.tsx', '.py', '.php'}
    API_URL = "https://api.x.ai/v1/chat/completions"
    
    def __init__(self, enabled: bool, api_key: str, model: str, max_chars: int):
        super().__init__(name="Grok Dual", short_name="GROK", enabled=enabled)
        self.api_key = api_key
        self.model = model
        self.max_chars = max_chars

    def can_check(self, ext: str) -> bool:
        return ext in self.SUPPORTED_EXTENSIONS

    def check(self, path: str, content: str, ext: str) -> List[Issue]:
        if not self.enabled or not self.api_key:
            return []

        prompt = self._build_prompt(path, content, ext)
        
        try:
            return self._call_api(prompt)
        except Exception as e:
            logger.error(f"[{self.name}] Error: {e}")
            return [Issue(
                type=IssueType.WARNING.value,
                line=0,
                message=f"Grok Failed: {str(e)[:50]}",
                source=self.name
            )]

    def _build_prompt(self, path: str, content: str, ext: str) -> str:
        """Build Grok prompt"""
        role = ("Strict W3C Standards Emulator & Frontend Architect" 
                if ext in {'.html', '.css', '.scss'}
                else "Senior Security Engineer & Polyglot Programmer")
        
        return f"""
IMPORTANT: Be an aggressive reviewer. Look for:
- Performance bottlenecks
- Security vulnerabilities
- Code style inconsistencies

Role: {role}.
Task: DUAL-LAYER AUDIT for: {path}

LAYER 1: STANDARDS & SYNTAX
LAYER 2: LOGIC & INTELLIGENCE

Return JSON:
{{ "issues": [ {{ "type": "error"|"warning"|"suggestion", "line": <int>, "message": "(in Russian)", "suggestion": "fix" }} ] }}

CODE:
{content[:self.max_chars]}
"""

    def _call_api(self, prompt: str) -> List[Issue]:
        """Call Grok API"""
        headers = {
            "Authorization": f"Bearer {self.api_key}",
            "Content-Type": "application/json"
        }
        
        payload = {
            "model": self.model,
            "messages": [
                {"role": "system", "content": "You are an automated code auditor. Output strict JSON."},
                {"role": "user", "content": prompt}
            ],
            "response_format": {"type": "json_object"},
            "temperature": 0.2
        }
        
        resp = requests.post(self.API_URL, json=payload, headers=headers, timeout=40)
        
        if resp.status_code == 200:
            data = resp.json()
            raw_content = data['choices'][0]['message']['content']
            parsed = json.loads(raw_content)
            return [
                Issue(
                    type=i.get('type', IssueType.WARNING.value),
                    line=i.get('line', 0),
                    message=i.get('message'),
                    source=self.name,
                    suggestion=i.get('suggestion')
                ) for i in parsed.get('issues', [])
            ]
        elif resp.status_code == 403:
            self.enabled = False
            logger.error(f"🚫 [{self.name}] Access error (403)")
            raise APIQuotaExceededException("Grok permission denied")
        elif resp.status_code == 429:
            self.enabled = False
            logger.error(f"🛑 [{self.name}] Rate limit (429)")
            raise APIQuotaExceededException("Grok rate limit")
        else:
            raise Exception(f"API Error: {resp.status_code}")


# ==============================================================================
# SERVICES - FILE SCANNER
# ==============================================================================

class FileScanner:
    """Scans directories for code files"""
    
    EXTENSION_MAP = {
        'html': ['.html'],
        'css': ['.css', '.scss', '.sass'],
        'js': ['.js', '.jsx', '.ts', '.tsx']
    }
    
    def __init__(self, config: AuditorConfig):
        self.config = config

    def scan(self) -> List[str]:
        """Scan source directory for files"""
        files = []
        source_path = Path(self.config.source_dir)
        
        if not source_path.exists():
            logger.warning(f"Source directory not found: {source_path}")
            return []
        
        for root, _, filenames in os.walk(source_path):
            for filename in filenames:
                ext = Path(filename).suffix.lower()
                filepath = os.path.join(root, filename)
                
                if self._should_check(ext):
                    files.append(filepath)
        
        return files

    def _should_check(self, ext: str) -> bool:
        """Check if extension should be audited"""
        if ext in self.EXTENSION_MAP['html'] and self.config.check_html:
            return True
        if ext in self.EXTENSION_MAP['css'] and self.config.check_css:
            return True
        if ext in self.EXTENSION_MAP['js'] and self.config.check_js:
            return True
        return False


# ==============================================================================
# SERVICES - FILE AUDITOR
# ==============================================================================

class FileAuditor:
    """Audits individual files"""
    
    def __init__(self, validators: List[IValidator], api_sleep: str):
        self.validators = validators
        self.api_sleep = api_sleep

    def audit(self, filepath: str) -> FileReport:
        """Audit single file"""
        try:
            with open(filepath, 'r', encoding='utf-8', errors='ignore') as f:
                content = f.read()
        except Exception as e:
            logger.error(f"Failed to read {filepath}: {e}")
            return FileReport(filepath, datetime.now().strftime("%H:%M"))

        if not content.strip():
            return FileReport(filepath, datetime.now().strftime("%H:%M"))

        ext = Path(filepath).suffix.lower()
        issues = []
        checked_by = []
        api_called = False

        for validator in self.validators:
            if validator.enabled and validator.can_check(ext):
                print(f"[{validator.short_name}..", end="", flush=True)
                
                try:
                    checked_by.append(validator.short_name)
                    result = validator.check(filepath, content, ext)
                    issues.extend(result)
                    api_called = True
                    print("ok] ", end="", flush=True)
                except APIQuotaExceededException:
                    print("quota] ", end="", flush=True)
                    continue
                except Exception as e:
                    print("err] ", end="", flush=True)
                    logger.error(f"❌ {validator.name}: {e}")

        if api_called:
            self._sleep()

        return FileReport(
            path=filepath,
            timestamp=datetime.now().strftime("%H:%M"),
            issues=issues,
            checked_by=checked_by
        )

    def _sleep(self) -> None:
        """Sleep between API calls"""
        sleep_cfg = self.api_sleep.replace(" ", "")
        
        if "," in sleep_cfg:
            try:
                mn, mx = map(float, sleep_cfg.split(","))
                duration = random.uniform(mn, mx)
            except:
                duration = 10.0
        else:
            try:
                duration = float(sleep_cfg)
            except:
                duration = 10.0

        for remaining in range(int(duration), 0, -1):
            print(f" ⏳ Wait: {remaining}s...   ", end="\r", flush=True)
            time.sleep(1)
        
        print(" " * 40, end="\r")


# ==============================================================================
# REPORTERS - HTML REPORT
# ==============================================================================

class HTMLReportGenerator:
    """Generates HTML audit reports"""
    
    def __init__(self):
        self.output_path = "code_auditor_report.html"

    def generate(self, reports: List[FileReport], metadata: Dict) -> str:
        """Generate HTML report"""
        stats = self._calculate_stats(reports)
        html = self._build_html(reports, stats, metadata)
        
        with open(self.output_path, 'w', encoding='utf-8') as f:
            f.write(html)
        
        return self.output_path

    def _calculate_stats(self, reports: List[FileReport]) -> Dict:
        """Calculate statistics"""
        stats = {
            'total': len(reports),
            'clean': 0,
            'dirty': 0,
            'skipped': 0,
            'errors': 0,
            'warnings': 0
        }
        
        for report in reports:
            if report.status == FileStatus.NOT_CHECKED:
                stats['skipped'] += 1
            elif report.status == FileStatus.DIRTY:
                stats['dirty'] += 1
                for issue in report.issues:
                    if issue.type == IssueType.ERROR.value:
                        stats['errors'] += 1
                    else:
                        stats['warnings'] += 1
            else:
                stats['clean'] += 1
        
        return stats

    def _build_html(self, reports: List[FileReport], stats: Dict, metadata: Dict) -> str:
        """Build HTML content"""
        css = self._get_css()
        header = self._build_header(stats, metadata)
        stats_grid = self._build_stats_grid(stats)
        files_section = self._build_files_section(reports)
        
        return f"""<!DOCTYPE html>
<html lang="ru">
<head>
    <meta charset="UTF-8">
    <title>Code Audit Report</title>
    <style>{css}</style>
</head>
<body>
    <div class="container">
        {header}
        {stats_grid}
        {files_section}
    </div>
</body>
</html>"""

    def _get_css(self) -> str:
        """Get CSS styles"""
        return """
:root { 
    --bg-body: #f8fafc; --bg-card: #ffffff; 
    --text-main: #334155; --text-muted: #64748b;
    --color-success: #10b981; --color-danger: #ef4444; 
    --color-warning: #f59e0b; --color-neutral: #94a3b8;
    --border: #e2e8f0;
}
body { 
    font-family: 'Segoe UI', system-ui, sans-serif; 
    background: var(--bg-body); 
    color: var(--text-main); 
    margin: 0; 
    padding: 40px; 
    line-height: 1.5; 
}
.container { max-width: 1200px; margin: 0 auto; }
.header { 
    background: #1e293b; 
    color: white; 
    padding: 30px; 
    border-radius: 12px; 
    margin-bottom: 30px; 
    box-shadow: 0 4px 6px -1px rgba(0,0,0,0.1); 
}
.header h1 { margin: 0; font-size: 1.5rem; }
.stats { 
    display: grid; 
    grid-template-columns: repeat(4, 1fr); 
    gap: 20px; 
    margin-bottom: 40px; 
}
.stat-card { 
    background: var(--bg-card); 
    padding: 20px; 
    border-radius: 10px; 
    text-align: center; 
    border: 1px solid var(--border); 
}
.stat-val { font-size: 2.2rem; font-weight: 700; display: block; }
.stat-label { color: var(--text-muted); font-size: 0.9rem; }
.file-block { 
    background: var(--bg-card); 
    border-radius: 8px; 
    margin-bottom: 16px; 
    border: 1px solid var(--border); 
}
.file-header { 
    padding: 12px 20px; 
    display: flex; 
    justify-content: space-between; 
    background: #f1f5f9; 
    font-weight: 600; 
}
.st-clean { border-left: 5px solid var(--color-success); }
.st-dirty { border-left: 5px solid var(--color-danger); }
.st-skipped { border-left: 5px solid var(--color-neutral); }
.badge { 
    padding: 4px 10px; 
    border-radius: 4px; 
    font-size: 0.75rem; 
    font-weight: bold; 
    color: white; 
    margin-left: 8px; 
}
.bdg-w3c { background: #0284c7; }
.bdg-ai { background: #8b5cf6; }
.bdg-grok { background: #000000; }
.bdg-clean { background: #10b981; }
.bdg-dirty { background: #ef4444; }
.bdg-skipped { background: #64748b; }
.issues-list { border-top: 1px solid var(--border); }
.issue-row { 
    padding: 12px 20px; 
    display: flex; 
    gap: 15px; 
    border-bottom: 1px solid #f1f5f9; 
}
.severity { 
    padding: 4px 8px; 
    border-radius: 6px; 
    font-size: 0.75rem; 
    font-weight: 800; 
    min-width: 80px; 
    text-align: center; 
}
.sv-error { background: #fee2e2; color: #ef4444; }
.sv-warning { background: #fef3c7; color: #d97706; }
.sv-suggestion { background: #dbeafe; color: #2563eb; }
.line-num { font-family: monospace; color: var(--text-muted); min-width: 40px; }
.msg-content { flex-grow: 1; }
"""

    def _build_header(self, stats: Dict, metadata: Dict) -> str:
        """Build header section"""
        active_tools = metadata.get('active_tools', [])
        tools_html = ''.join([f'<span class="badge bdg-{t.lower()}">{t}</span>' for t in active_tools])
        
        return f"""
<div class="header">
    <h1>🛡️ Code Security & Quality Audit</h1>
    <div>Enabled: {tools_html if tools_html else '<span style="color:#f87171">NONE</span>'}</div>
    <div style="margin-top:10px; color:#94a3b8">
        Date: {datetime.now().strftime("%Y-%m-%d %H:%M")} | 
        Source: {metadata.get('source_dir', 'N/A')}
    </div>
</div>"""

    def _build_stats_grid(self, stats: Dict) -> str:
        """Build statistics grid"""
        return f"""
<div class="stats">
    <div class="stat-card">
        <span class="stat-val">{stats['total']}</span>
        <span class="stat-label">Total</span>
    </div>
    <div class="stat-card" style="border-bottom:4px solid var(--color-success)">
        <span class="stat-val" style="color:var(--color-success)">{stats['clean']}</span>
        <span class="stat-label">Clean</span>
    </div>
    <div class="stat-card" style="border-bottom:4px solid var(--color-danger)">
        <span class="stat-val" style="color:var(--color-danger)">{stats['dirty']}</span>
        <span class="stat-label">Issues</span>
    </div>
    <div class="stat-card" style="border-bottom:4px solid var(--color-neutral)">
        <span class="stat-val" style="color:var(--color-neutral)">{stats['skipped']}</span>
        <span class="stat-label">Skipped</span>
    </div>
</div>"""

    def _build_files_section(self, reports: List[FileReport]) -> str:
        """Build files section"""
        # Sort: dirty first, clean middle, skipped last
        sorted_reports = sorted(reports, key=lambda r: (
            0 if r.status == FileStatus.DIRTY else 
            1 if r.status == FileStatus.CLEAN else 2
        ))
        
        html = ""
        for report in sorted_reports:
            html += self._build_file_block(report)
        return html

    def _build_file_block(self, report: FileReport) -> str:
        """Build single file block"""
        status_class = {
            FileStatus.CLEAN: "st-clean",
            FileStatus.DIRTY: "st-dirty",
            FileStatus.NOT_CHECKED: "st-skipped"
        }[report.status]
        
        status_badge = {
            FileStatus.CLEAN: '<span class="badge bdg-clean">CLEAN</span>',
            FileStatus.DIRTY: f'<span class="badge bdg-dirty">{len(report.issues)} ISSUES</span>',
            FileStatus.NOT_CHECKED: '<span class="badge bdg-skipped">NOT CHECKED</span>'
        }[report.status]
        
        tools_html = ''.join([
            f'<span class="badge bdg-{t.lower()}">{t}</span>' 
            for t in report.checked_by
        ])
        
        issues_html = ""
        if report.issues:
            issues_html = '<div class="issues-list">'
            sorted_issues = sorted(report.issues, key=lambda i: 
                0 if i.type == 'error' else 1 if i.type == 'warning' else 2
            )
            for issue in sorted_issues:
                issues_html += f"""
<div class="issue-row">
    <span class="severity sv-{issue.type}">{issue.type}</span>
    <span class="line-num">L:{issue.line}</span>
    <div class="msg-content">
        {issue.message}
        {f'<div style="margin-top:8px;background:#f8fafc;padding:8px">💡 {issue.suggestion}</div>' if issue.suggestion else ''}
    </div>
</div>"""
            issues_html += '</div>'
        
        return f"""
<div class="file-block">
    <div class="file-header {status_class}">
        <span style="font-family:monospace">{report.path}</span>
        <div>{tools_html} {status_badge}</div>
    </div>
    {issues_html}
</div>"""


# ==============================================================================
# SERVICES - AUDIT ENGINE
# ==============================================================================

class AuditEngine:
    """Main audit orchestrator"""
    
    def __init__(self, config: AuditorConfig):
        self.config = config
        self.state_manager = JSONStateManager(config.temp_file)
        self.file_scanner = FileScanner(config)
        self.validators = self._initialize_validators()
        self.file_auditor = FileAuditor(self.validators, config.api_sleep)
        self.reporter = HTMLReportGenerator()
        self.reports: List[FileReport] = []

    def _initialize_validators(self) -> List[IValidator]:
        """Initialize all validators"""
        validators = []
        
        # W3C
        validators.append(W3CValidator(enabled=self.config.w3c_enabled))
        
        # Gemini
        validators.append(GeminiValidator(
            enabled=self.config.gemini_enabled,
            api_keys=self.config.gemini_keys,
            model=self.config.gemini_model,
            max_chars=self.config.gemini_max_chars,
            api_sleep=float(self.config.api_sleep.split(',')[0]),
            key_rotate=self.config.key_rotate_interval
        ))
        
        # Grok
        validators.append(GrokValidator(
            enabled=self.config.grok_enabled,
            api_key=self.config.grok_key,
            model=self.config.grok_model,
            max_chars=self.config.grok_max_chars
        ))
        
        return validators

    def run(self) -> None:
        """Run audit process"""
        print("\n💾 CODE AUDITOR PRO - STARTING...\n")
        self._print_config()
        
        # Scan files
        all_files = self.file_scanner.scan()
        if not all_files:
            print("⚠️ No files found.")
            return

        # Resume or start fresh
        processed = self._handle_resume()
        queue = [f for f in all_files if f not in processed]
        
        print(f"📊 Queue: {len(queue)} files.\n")
        
        # Audit files
        self._audit_files(queue)
        
        # Generate report
        self._generate_report()
        
        # Cleanup
        self.state_manager.clear()

    def _print_config(self) -> None:
        """Print configuration"""
        print("="*100)
        print("🛠  CODE AUDITOR: CONFIGURATION")
        print("="*100)
        print(f"📂 Source:        {self.config.source_dir}")
        print(f"🔑 Gemini Keys:   {len(self.config.gemini_keys)}")
        print(f"📄 Resume:        {'✅' if self.config.resume_audit else '⬜'}")
        print("-" * 100)
        print("📌 Validators:")
        for v in self.validators:
            print(f"   • {v.name:<20} {'✅ ON' if v.enabled else '⬜ OFF'}")
        print("="*100 + "\n")

    def _handle_resume(self) -> set:
        """Handle resume logic"""
        if self.config.resume_audit:
            self.reports = self.state_manager.restore()
            processed = {r.path for r in self.reports}
            if processed:
                print(f"📄 Resumed: {len(processed)} files from cache.")
            return processed
        return set()

    def _audit_files(self, queue: List[str]) -> None:
        """Audit all files in queue"""
        total = len(queue)
        
        for idx, filepath in enumerate(queue, 1):
            filename = Path(filepath).name
            display_name = (filename[:67] + "...") if len(filename) > 70 else filename.ljust(70, ".")
            
            print(f"👉 [{idx:>3}/{total:<3}] {display_name} ", end="", flush=True)
            
            # Check if any validators still active
            if not any(v.enabled for v in self.validators):
                print("\n\n❌ CRITICAL: All validators disabled/exhausted.")
                print("🏁 Early termination. Saving report...")
                break
            
            # Audit file
            report = self.file_auditor.audit(filepath)
            self.reports.append(report)
            self.state_manager.save(self.reports)
            
            # Print status
            self._print_file_status(report)

    def _print_file_status(self, report: FileReport) -> None:
        """Print file audit status"""
        if report.status == FileStatus.NOT_CHECKED:
            print(" ⚪ Not Checked")
        elif report.status == FileStatus.CLEAN:
            tools = ', '.join(report.checked_by)
            print(f" ✅ Clean [{tools}]")
        else:
            print(f" ⚠️ {len(report.issues):>3} issues")

    def _generate_report(self) -> None:
        """Generate final report"""
        metadata = {
            'active_tools': [v.short_name for v in self.validators if v.enabled],
            'source_dir': self.config.source_dir
        }
        
        output = self.reporter.generate(self.reports, metadata)
        print(f"\n✨ REPORT GENERATED: {output}")


# ==============================================================================
# MAIN ENTRY POINT
# ==============================================================================

def main():
    """Main application entry point"""
    try:
        config = AuditorConfig.from_env()
        engine = AuditEngine(config)
        engine.run()
    except KeyboardInterrupt:
        print("\n\n⚠️ Interrupted by user.")
    except Exception as e:
        logger.error(f"Fatal error: {e}", exc_info=True)
        exit(1)


if __name__ == "__main__":
    main()