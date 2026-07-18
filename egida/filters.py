"""
Filtri strutturati per la rilevazione di HSD (Highly Sensitive Data).

v2.0 — Sistema di scoring/severity, esclusioni contestuali,
       magic byte detection, validazione entropia.

Egida — 4° strato di Oracle (guardrail HSD cross-layer).
"""

import base64
import json
import logging
import re
from dataclasses import dataclass, field
from enum import IntEnum
from pathlib import Path
from typing import Optional

from .config import EGIDA_QUARANTINE_THRESHOLD

# Backward compatibility alias
QUARANTINE_THRESHOLD = EGIDA_QUARANTINE_THRESHOLD

logger = logging.getLogger(__name__)


# ─── Severity scoring ───────────────────────────────────────────────

class Severity(IntEnum):
    """Peso di ciascun pattern. La quarantena scatta se score >= threshold."""
    INFO = 10
    LOW = 25
    MEDIUM = 50
    HIGH = 90
    CRITICAL = 100


@dataclass
class HSDMatch:
    """Risultato di un controllo HSD su un file con scoring."""

    file_path: str
    is_infected: bool = False
    matches: list[dict] = field(default_factory=list)
    score: int = 0

    def add_match(
        self,
        pattern_name: str,
        line: int,
        snippet: str,
        severity: Severity = Severity.MEDIUM,
    ) -> None:
        self.matches.append({
            "pattern": pattern_name,
            "line": line,
            "snippet": snippet[:120],
            "severity": severity.name,
        })
        self.score += severity.value
        if self.score >= EGIDA_QUARANTINE_THRESHOLD:
            self.is_infected = True


# ─── Helpers ────────────────────────────────────────────────────────

# Domini email chiaramente fittizi / didattici
_EMAIL_WHITELIST_DOMAINS: set[str] = {
    "example.com", "example.org", "example.net", "example.edu",
    "test.com", "test.org", "email.com", "domain.com",
    "mycompany.com", "company.com", "myapp.com", "sample.com",
    "demo.com", "fake.com", "placeholder.com", "noreply.com",
    "yourdomain.com", "yourapp.com", "mywebsite.com",
}

# Placeholder password comuni in codice sorgente / CI
_PASSWORD_PLACEHOLDERS: set[str] = {
    "password", "postgres", "admin", "root", "test", "secret",
    "changeme", "123456", "abcdef", "qwerty",
}

# Pattern UUID / hex-identifier (da escludere per telefono/CAP)
_UUID_RE = re.compile(
    r"[\da-f]{8}-[\da-f]{4}-[\da-f]{4}-[\da-f]{4}-[\da-f]{12}",
    re.IGNORECASE,
)

# Linee che contengono solo metadata / path
_FILE_ID_RE = re.compile(
    r"(?:file[_-]|user-)[\da-f]{8,}[^\s]{20,}",
    re.IGNORECASE,
)


def _is_binary(path: Path) -> bool:
    """
    Determina se un file è binario:
    1. Estensione conosciuta (fast path)
    2. Magic byte: null byte o >30% caratteri non stampabili
    """
    if path.suffix.lower() in _BINARY_EXTENSIONS:
        return True
    try:
        with open(path, "rb") as f:
            chunk = f.read(2048)
        if not chunk:
            return False
        if b"\x00" in chunk:
            return True
        non_printable = sum(
            1 for b in chunk
            if b < 0x09 or (0x0E <= b < 0x20) or b == 0x7F
        )
        if non_printable / len(chunk) > 0.30:
            return True
    except Exception:
        return True
    return False


def _has_entropy(value: str) -> bool:
    """
    Controlla se un valore ha entropia da password reale
    (almeno 2 categorie di caratteri fra: maiuscole, minuscole, cifre, speciali).
    """
    if len(value) < 6:
        return False
    categories = 0
    if any(c.isupper() for c in value):
        categories += 1
    if any(c.islower() for c in value):
        categories += 1
    if any(c.isdigit() for c in value):
        categories += 1
    if any(not c.isalnum() for c in value):
        categories += 1
    return categories >= 2


def _is_valid_jwt(text: str) -> bool:
    """Verifica che il match sia un JWT decodificabile (header JSON valido)."""
    try:
        parts = text.split(".")
        if len(parts) != 3:
            return False
        header_b64 = parts[0] + "=" * (4 - len(parts[0]) % 4)
        decoded = base64.urlsafe_b64decode(header_b64)
        json.loads(decoded)
        return True
    except Exception:
        return False


def _is_likely_data_line(line: str) -> bool:
    """Linea che probabilmente contiene UUID / file-id (non testo narrativo)."""
    if _UUID_RE.search(line):
        return True
    if _FILE_ID_RE.search(line):
        return True
    return False


def _starts_like_url(snippet: str) -> bool:
    """Controlla se lo snippet inizia come un URL."""
    return bool(re.match(r"https?://", snippet, re.IGNORECASE))


# ─── Pattern ────────────────────────────────────────────────────────

# (nome, regex, severity, contesto_richiesto)
DEFAULT_PATTERNS: list[dict] = [
    # ── CRITICAL ──
    {
        "name": "AWS Access Key",
        "regex": r"\bAKIA[0-9A-Z]{16}\b",
        "severity": Severity.CRITICAL,
    },
    {
        "name": "Token GitHub (ghp_)",
        "regex": r"\bghp_[A-Za-z0-9]{36}\b",
        "severity": Severity.CRITICAL,
    },
    {
        "name": "Token GitHub (gho_)",
        "regex": r"\bgho_[A-Za-z0-9]{36}\b",
        "severity": Severity.CRITICAL,
    },
    {
        "name": "Token GitHub (ghu_)",
        "regex": r"\bghu_[A-Za-z0-9]{36}\b",
        "severity": Severity.CRITICAL,
    },
    {
        "name": "Token Hugging Face",
        "regex": r"\bhf_[A-Za-z0-9]{34}\b",
        "severity": Severity.CRITICAL,
    },
    {
        "name": "Chiave SSH privata (in linea)",
        "regex": r"-----BEGIN\s+(RSA|DSA|EC|OPENSSH)\s+PRIVATE\s+KEY-----",
        "severity": Severity.CRITICAL,
    },

    # ── HIGH ──
    {
        "name": "Codice Fiscale italiano",
        "regex": r"\b[A-Z]{6}\d{2}[A-Z]\d{2}[A-Z]\d{3}[A-Z]\b",
        "severity": Severity.HIGH,
    },

    # ── MEDIUM (scoring cumulativo) ──
    {
        "name": "Numero di telefono (formato internazionale)",
        "regex": (
            # Pattern con separatori (spazio o trattino TRA gruppi di cifre)
            r"(?<!\d)"
            r"(?:\+\d{1,3}[\s.-])?"    # prefisso +39, +1, etc.
            r"\d{2,4}"                       # primo gruppo
            r"[\s.-]"                         # SEPARATORE OBBLIGATORIO
            r"\d{2,4}"                        # secondo gruppo
            r"[\s.-]"                         # SEPARATORE OBBLIGATORIO
            r"\d{3,4}"                        # terzo gruppo
            r"(?!\d)"
            r"|"                                # OPPURE
            # Numero compatto con prefisso + (senza separatori, 8-15 cifre)
            r"(?<!\d)\+\d{8,15}(?!\d)"
        ),
        "severity": Severity.MEDIUM,
        "context_required": False,
    },
    {
        "name": "Indirizzo email",
        "regex": r"\b[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}\b",
        "severity": Severity.MEDIUM,
        "context_required": False,
    },

    # ── LOW (da soli non quarantenano) ──
    {
        "name": "CAP (codice avviamento postale italiano)",
        "regex": (
            r"(?<![\d.])"                  # non preceduto da cifra o punto decimale
            r"(?:00[1-9]\d{2}"            # 00100-00999
            r"|0[1-9]\d{3}"               # 01000-09999
            r"|[1-8]\d{4}"                # 10000-89999
            r"|9[0-7]\d{3}"               # 90000-97999
            r"|98[01]\d{2})"              # 98000-98199
            r"(?!\d)"                      # non seguito da cifra
        ),
        "severity": Severity.LOW,
        "context_required": False,
    },

    # ── Pattern con validazione post-match ──
    {
        "name": "API Key / Secret generico",
        "regex": (
            r"(?i)(api[_-]?key|apikey|api_secret|secret_key|app_secret|client_secret)"
            r"\s*[=:]\s*['\"]?([A-Za-z0-9_\-]{16,})['\"]?"
        ),
        "severity": Severity.HIGH,
        "context_required": False,
    },
    {
        "name": "Token JWT / Bearer",
        "regex": r"\b(eyJ[A-Za-z0-9\-_]+\.eyJ[A-Za-z0-9\-_]+\.[A-Za-z0-9\-_]+)\b",
        "severity": Severity.HIGH,
        "context_required": False,
    },
    {
        "name": "Password esplicita",
        "regex": (
            r"(?i)(password|passwd|pwd|passphrase)"
            r"\s*[=:]\s*['\"]?([^\s,;'\"']{6,})['\"]?"
        ),
        "severity": Severity.HIGH,
        "context_required": False,
    },
    {
        "name": "Token Discord / Slack (pattern lungo)",
        "regex": r"\b[A-Za-z0-9_\-]{24}\.[A-Za-z0-9_\-]{6}\.[A-Za-z0-9_\-]{27}\b",
        "severity": Severity.HIGH,
        "context_required": False,
    },
]


_BINARY_EXTENSIONS = frozenset({
    ".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".ico",
    ".mp4", ".avi", ".mkv", ".mov", ".wmv", ".flv",
    ".mp3", ".wav", ".flac", ".ogg", ".aac", ".wma",
    ".zip", ".rar", ".7z", ".tar", ".gz", ".bz2",
    ".exe", ".dll", ".so", ".dylib",
    ".pyc", ".pyd", ".pyo",
    ".db", ".sqlite", ".mdb",
    ".pdf", ".doc", ".docx", ".xls", ".xlsx", ".ppt", ".pptx",
    ".ttf", ".otf", ".woff", ".woff2",
    ".pkl", ".bin", ".dat", ".npy", ".npz",
    ".h5", ".hdf5", ".keras", ".ckpt", ".safetensors",
})


# ─── Filtro principale ──────────────────────────────────────────────

class HSDFilter:
    """
    Applica una serie di regex a un file di testo con sistema di scoring.

    Il file va in quarantena SOLO se lo score >= QUARANTINE_THRESHOLD.
    Pattern CRITICAL (100) quarantenano da soli.
    Pattern LOW (25) richiedono combinazione con altri match.
    """

    def __init__(self, patterns: Optional[list[dict]] = None):
        raw = patterns if patterns is not None else DEFAULT_PATTERNS
        self._compiled: list[dict] = []
        for p in raw:
            self._compiled.append({
                "name": p["name"],
                "regex": re.compile(p["regex"]),
                "severity": p.get("severity", Severity.MEDIUM),
                "context_required": p.get("context_required", False),
            })

    # ── check_file ───────────────────────────────────────────────

    def check_file(self, file_path: str | Path) -> HSDMatch:
        path = Path(file_path)
        result = HSDMatch(file_path=str(path))

        if _is_binary(path):
            return result

        try:
            content = path.read_text("utf-8", errors="replace")
        except Exception as e:
            logger.warning("Impossibile leggere %s: %s", path, e)
            return result

        lines = content.split("\n")
        for entry in self._compiled:
            name = entry["name"]
            regex = entry["regex"]
            severity = entry["severity"]

            for lineno, line in enumerate(lines, 1):
                # ── Esclusioni contestuali ─────────────────────
                # Salta righe con UUID/file-id per telefono e CAP
                if name.startswith(("Numero di telefono", "CAP")) and _is_likely_data_line(line):
                    continue

                m = regex.search(line)
                if not m:
                    continue

                snippet = line.strip()

                # ── Validazioni post-match ─────────────────────
                if name == "Token JWT / Bearer":
                    if _starts_like_url(snippet):
                        continue
                    if not _is_valid_jwt(m.group(1)):
                        continue

                elif name == "Password esplicita":
                    value = m.group(2).lower()
                    # Placeholder / CI defaults → declassa a LOW
                    if _is_password_placeholder(value):
                        severity = Severity.LOW

                elif name == "API Key / Secret generico":
                    value = m.group(2)
                    # Se è solo una parola senza entropia → declassa
                    if value.isalpha() or value.isdigit():
                        severity = Severity.LOW

                elif name == "Indirizzo email":
                    domain = snippet.split("@")[-1].split(">")[0].split('"')[0].split("'")[0].strip().lower()
                    # Estrai il dominio pulito
                    domain_match = re.search(
                        r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b", snippet
                    )
                    if domain_match:
                        dom = domain_match.group(1).lower()
                        if dom in _EMAIL_WHITELIST_DOMAINS:
                            severity = Severity.INFO

                result.add_match(name, lineno, snippet, severity)

        if result.is_infected:
            logger.info(
                "HSD rilevato in %s: %d match, score=%d",
                path, len(result.matches), result.score,
            )

        return result

    # ── check_text ──────────────────────────────────────────────

    def check_text(self, text: str) -> list[dict]:
        matches = []
        lines = text.split("\n")
        for entry in self._compiled:
            name = entry["name"]
            regex = entry["regex"]
            severity = entry["severity"]

            for lineno, line in enumerate(lines, 1):
                # ── Esclusioni contestuali ─────────────────────
                if name.startswith(("Numero di telefono", "CAP")) and _is_likely_data_line(line):
                    continue

                for m in regex.finditer(line):
                    snippet = m.group()[:120]
                    full_line = line.strip()

                    # ── Validazioni post-match ─────────────────
                    if name == "Token JWT / Bearer":
                        if _starts_like_url(full_line):
                            continue
                        if not _is_valid_jwt(m.group(1) if m.lastindex else m.group()):
                            continue

                    elif name == "Password esplicita":
                        value = (m.group(2) if m.lastindex else "").lower()
                        if _is_password_placeholder(value):
                            severity = Severity.LOW

                    elif name == "Indirizzo email":
                        domain_match = re.search(
                            r"@([A-Za-z0-9.-]+\.[A-Za-z]{2,})\b",
                            snippet, re.IGNORECASE,
                        )
                        if domain_match and domain_match.group(1).lower() in _EMAIL_WHITELIST_DOMAINS:
                            severity = Severity.INFO

                    elif name == "API Key / Secret generico":
                        value = m.group(2) if m.lastindex else ""
                        if value.isalpha() or value.isdigit():
                            severity = Severity.LOW

                    matches.append({
                        "pattern": name,
                        "start": m.start(),
                        "end": m.end(),
                        "snippet": snippet,
                        "severity": severity.name,
                    })
        return matches


# ─── Helper per password placeholder ─────────────────────────────────

def _is_password_placeholder(value: str) -> bool:
    """
    Determina se il valore catturato è un placeholder e non una vera password.
    Casi riconosciuti:
      - Parole comuni: 'password', 'postgres', 'admin', 'test', ...
      - Type hint Python: 'Optional[str]', ': None'
      - Attribute access: 'user.hashed_password', 'module.field'
      - Nomi di variabile: 'hashed_password', 'new_password', 'user_password'
    """
    v = value.strip("'\"")
    v_lower = v.lower()

    # Parole placeholder comuni
    if v_lower in _PASSWORD_PLACEHOLDERS:
        return True
    if v_lower in ("none", "null", "undefined", "empty", "''", '""'):
        return True

    # Type hint: Optional[str], Optional[SomeType], List[str], etc.
    if "[" in v or "]" in v:
        return True
    # Type annotation pura: : str, : int dopo il match
    if v_lower in ("str", "int", "float", "bool", "bytes"):
        return True

    # Attribute access (module.variable.path) → riferimento, non password
    if "." in v:
        return True

    # Nomi variabile che contengono 'password' ma sono placeholder
    if re.match(
        r"^(hashed_?password|new_?password|user_?password|"
        r"old_?password|temp_?password|default_?password)$",
        v,
        re.IGNORECASE,
    ):
        return True

    return False


# ─── Comodo per test ────────────────────────────────────────────────

def quick_scan(text: str) -> list[str]:
    """Scansione rapida: restituisce lista di nomi di pattern trovati."""
    f = HSDFilter()
    return [m["pattern"] for m in f.check_text(text)]
