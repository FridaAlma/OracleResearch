"""
┌──────────────────────────────────────────────────────────────┐
│  WebAccess -- Accesso Internet Robusto e Sicuro per Oracle    │
├──────────────────────────────────────────────────────────────┤
│  * GET/POST/DOWNLOAD con retry automatico                   │
│  * Protezione SSRF (blocco IP privati)                      │
│  * Rate limiting e timeout configurabili                    │
│  * Scraping HTML (testo, link, tag per selettore CSS)       │
│  * Caching intelligente con TTL                             │
│  * Modalità: CLI, Python API, streaming                     │
└──────────────────────────────────────────────────────────────┘
"""

import argparse
import hashlib
import json
import logging
import os
import re
import sqlite3
import sys
import threading
import time
import urllib.parse
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from email.utils import parsedate_to_datetime
from io import BytesIO
from pathlib import Path
from typing import Any, Callable, Optional
from urllib.parse import urlparse

import requests
from requests.adapters import HTTPAdapter
from urllib3.util.retry import Retry

# ── Tenta import opzionali ────────────────────────────────────
try:
    from bs4 import BeautifulSoup
    HAS_BEAUTIFULSOUP = True
except ImportError:
    HAS_BEAUTIFULSOUP = False

try:
    from validators import url as validate_url
    HAS_VALIDATORS = True
except ImportError:
    HAS_VALIDATORS = False


# ═══════════════════════════════════════════════════════════════
#  LOGGING
# ═══════════════════════════════════════════════════════════════
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
    datefmt="%H:%M:%S",
)
log = logging.getLogger("web_access")


# ═══════════════════════════════════════════════════════════════
#  CONFIGURAZIONE
# ═══════════════════════════════════════════════════════════════

@dataclass
class WebConfig:
    """Configurazione globale per WebAccess."""
    # Timeout (secondi)
    connect_timeout: float = 15.0
    read_timeout: float = 30.0
    total_timeout: float = 60.0

    # Retry
    max_retries: int = 3
    backoff_factor: float = 0.5  # 0.5, 1.0, 2.0, ...

    # Limiti di sicurezza
    max_response_size: int = 50 * 1024 * 1024  # 50 MB
    max_redirects: int = 10

    # Rate limiting (richieste al secondo)
    rate_limit: float = 5.0

    # User-Agent predefinito
    user_agent: str = (
        "Mozilla/5.0 (Windows NT 10.0; Win64; x64) "
        "AppleWebKit/537.36 (KHTML, like Gecko) "
        "Chrome/125.0.0.0 Safari/537.36 "
        "OracleCodingAgent/1.0"
    )

    # Cache
    cache_enabled: bool = True
    cache_ttl_seconds: int = 300  # 5 minuti
    cache_db: str = ""

    # Sicurezza
    block_private_ips: bool = True
    allowed_schemes: tuple = ("http", "https")
    max_url_length: int = 8192

    def __post_init__(self):
        if not self.cache_db:
            base = Path(__file__).resolve().parent.parent
            self.cache_db = str(base / "data" / "web_cache.db")
        Path(self.cache_db).parent.mkdir(parents=True, exist_ok=True)


# Istanza globale di default
CONFIG = WebConfig()


# ═══════════════════════════════════════════════════════════════
#  CACHE SU SQLITE
# ═══════════════════════════════════════════════════════════════

class ResponseCache:
    """Cache persistente per risposte HTTP usando SQLite."""

    def __init__(self, db_path: str, ttl: int):
        self.db_path = db_path
        self.ttl = ttl
        self._conn: Optional[sqlite3.Connection] = None
        self._lock = threading.Lock()
        self._init_db()

    def _init_db(self):
        conn = self._get_conn()
        conn.execute("""
            CREATE TABLE IF NOT EXISTS web_cache (
                cache_key TEXT PRIMARY KEY,
                url TEXT NOT NULL,
                status_code INTEGER NOT NULL,
                headers TEXT NOT NULL,
                content BLOB,
                encoding TEXT,
                elapsed REAL,
                cached_at TEXT NOT NULL
            )
        """)
        conn.execute("""
            CREATE INDEX IF NOT EXISTS idx_web_cache_url
            ON web_cache(url)
        """)
        # Pulisci vecchie entry
        conn.execute(
            "DELETE FROM web_cache WHERE cached_at < ?",
            ((datetime.utcnow() - timedelta(seconds=self.ttl)).isoformat(),)
        )
        conn.commit()

    def _get_conn(self) -> sqlite3.Connection:
        if self._conn is None:
            self._conn = sqlite3.connect(self.db_path, timeout=5, check_same_thread=False)
            self._conn.row_factory = sqlite3.Row
        return self._conn

    def _make_key(self, method: str, url: str, params: Optional[dict] = None,
                  data: Optional[Any] = None) -> str:
        """Genera una chiave univoca per la richiesta."""
        raw = f"{method}:{url}:{json.dumps(params or {}, sort_keys=True)}:{json.dumps(data or {}, sort_keys=True)}"
        return hashlib.sha256(raw.encode()).hexdigest()

    def get(self, method: str, url: str, params: Optional[dict] = None,
            data: Optional[Any] = None) -> Optional["CachedResponse"]:
        """Recupera una risposta cached se valida."""
        key = self._make_key(method, url, params, data)
        with self._lock:
            conn = self._get_conn()
            row = conn.execute(
                "SELECT * FROM web_cache WHERE cache_key = ?", (key,)
            ).fetchone()
            if row is None:
                return None
            # Verifica TTL
            cached_time = datetime.fromisoformat(row["cached_at"])
            if datetime.utcnow() - cached_time > timedelta(seconds=self.ttl):
                conn.execute("DELETE FROM web_cache WHERE cache_key = ?", (key,))
                conn.commit()
                return None
            return CachedResponse(
                status_code=row["status_code"],
                headers=json.loads(row["headers"]),
                content=row["content"],
                encoding=row["encoding"],
                elapsed=row["elapsed"],
                cached_at=row["cached_at"],
            )

    def set(self, method: str, url: str, params: Optional[dict],
            data: Optional[Any], response: "CachedResponse"):
        """Salva una risposta nella cache."""
        key = self._make_key(method, url, params, data)
        with self._lock:
            conn = self._get_conn()
            conn.execute("""
                INSERT OR REPLACE INTO web_cache
                (cache_key, url, status_code, headers, content, encoding, elapsed, cached_at)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                key, url, response.status_code,
                json.dumps(dict(response.headers)),
                response.content, response.encoding,
                response.elapsed, response.cached_at,
            ))
            conn.commit()

    def invalidate(self, url_pattern: Optional[str] = None):
        """Invalida cache: specifico URL o tutta."""
        with self._lock:
            conn = self._get_conn()
            if url_pattern:
                conn.execute("DELETE FROM web_cache WHERE url LIKE ?",
                             (f"%{url_pattern}%",))
            else:
                conn.execute("DELETE FROM web_cache")
            conn.commit()

    def stats(self) -> dict:
        """Statistiche della cache."""
        with self._lock:
            conn = self._get_conn()
            total = conn.execute("SELECT COUNT(*) FROM web_cache").fetchone()[0]
            size = conn.execute(
                "SELECT SUM(LENGTH(content)) FROM web_cache"
            ).fetchone()[0] or 0
            oldest = conn.execute(
                "SELECT MIN(cached_at) FROM web_cache"
            ).fetchone()[0]
            newest = conn.execute(
                "SELECT MAX(cached_at) FROM web_cache"
            ).fetchone()[0]
        return {
            "total_entries": total,
            "total_size_bytes": size,
            "oldest": oldest,
            "newest": newest,
        }

    def close(self):
        with self._lock:
            if self._conn:
                self._conn.close()
                self._conn = None


@dataclass
class CachedResponse:
    """Rappresenta una risposta cached."""
    status_code: int
    headers: dict
    content: bytes
    encoding: Optional[str]
    elapsed: float
    cached_at: str


# ═══════════════════════════════════════════════════════════════
#  SICUREZZA -- PROTEZIONE SSRF
# ═══════════════════════════════════════════════════════════════

# Blocchi IP privati (RFC 1918, RFC 4193, etc.)
PRIVATE_IP_PATTERNS = [
    re.compile(r"^127\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),       # loopback
    re.compile(r"^10\.\d{1,3}\.\d{1,3}\.\d{1,3}$"),        # classe A privata
    re.compile(r"^172\.(1[6-9]|2\d|3[01])\.\d{1,3}\.\d{1,3}$"),  # classe B privata
    re.compile(r"^192\.168\.\d{1,3}\.\d{1,3}$"),            # classe C privata
    re.compile(r"^169\.254\.\d{1,3}\.\d{1,3}$"),            # link-local
    re.compile(r"^0\.0\.0\.0$"),
    re.compile(r"^::1$"),                                    # IPv6 loopback
    re.compile(r"^fc00:"),                                   # IPv6 unique-local
    re.compile(r"^fe80:"),                                   # IPv6 link-local
    re.compile(r"^localhost$", re.IGNORECASE),
]


def is_private_ip(hostname: str) -> bool:
    """Verifica se un hostname punta a un IP privato."""
    import socket
    try:
        # Risolvi hostname a IP
        addr = socket.getaddrinfo(hostname, 80, socket.AF_INET, socket.SOCK_STREAM)
        for family, _, _, _, sockaddr in addr:
            ip = sockaddr[0]
            for pattern in PRIVATE_IP_PATTERNS:
                if pattern.match(ip):
                    return True
        return False
    except (socket.gaierror, OSError):
        # Se non risolve, meglio bloccare per sicurezza
        return True


def is_safe_url(url: str, config: WebConfig = CONFIG) -> tuple[bool, str]:
    """Valida un URL per sicurezza. Restituisce (safe, reason)."""
    # Limite lunghezza
    if len(url) > config.max_url_length:
        return False, f"URL troppo lungo ({len(url)} > {config.max_url_length})"

    # Parsing
    try:
        parsed = urlparse(url)
    except Exception as e:
        return False, f"URL malformato: {e}"

    # Schema consentito
    if parsed.scheme not in config.allowed_schemes:
        return False, f"Schema '{parsed.scheme}' non consentito (usa http/https)"

    # Host presente
    if not parsed.netloc:
        return False, "URL senza host"

    # Blocca IP privati
    if config.block_private_ips:
        # Estrai hostname (senza porta)
        hostname = parsed.hostname or ""
        if is_private_ip(hostname):
            return False, f"Host '{hostname}' è un IP privato/bloccato (SSRF protection)"

    # Blocca URL con user:password (credential leaking)
    if parsed.username or parsed.password:
        return False, "URL con credenziali incorporate non consentito"

    return True, "OK"


# ═══════════════════════════════════════════════════════════════
#  RATE LIMITER
# ═══════════════════════════════════════════════════════════════

class RateLimiter:
    """Rate limiter token bucket semplice."""

    def __init__(self, requests_per_second: float):
        self.rate = requests_per_second
        self.min_interval = 1.0 / requests_per_second if requests_per_second > 0 else 0
        self._last_call: float = 0.0

    def wait(self):
        """Attende se necessario per rispettare il rate limit."""
        if self.min_interval <= 0:
            return
        elapsed = time.time() - self._last_call
        if elapsed < self.min_interval:
            time.sleep(self.min_interval - elapsed)
        self._last_call = time.time()


# ═══════════════════════════════════════════════════════════════
#  RISPOSTA
# ═══════════════════════════════════════════════════════════════

@dataclass
class WebResponse:
    """Risposta normalizzata di WebAccess."""
    url: str
    status_code: int
    headers: dict
    content: bytes
    text: str
    encoding: str
    elapsed: float
    redirected: bool
    redirect_chain: list[str] = field(default_factory=list)
    cached: bool = False
    error: Optional[str] = None
    success: bool = True

    def json(self) -> Any:
        """Decodifica il contenuto come JSON."""
        try:
            return json.loads(self.text)
        except json.JSONDecodeError as e:
            raise ValueError(f"Risposta non è JSON valido: {e}")

    def soup(self) -> Optional[Any]:
        """Restituisce BeautifulSoup object se disponibile."""
        if not HAS_BEAUTIFULSOUP:
            raise ImportError("BeautifulSoup non installato. pip install beautifulsoup4")
        return BeautifulSoup(self.content, "lxml")

    def raise_for_status(self):
        """Solleva eccezione se status code è errore."""
        if 400 <= self.status_code < 600:
            raise requests.HTTPError(
                f"HTTP {self.status_code} per {self.url}",
                response=self,
            )


@dataclass
class WebResult:
    """Risultato strutturato di WebAccess, tornato sempre."""
    success: bool
    data: Optional[WebResponse] = None
    error: Optional[str] = None
    error_type: Optional[str] = None
    duration: float = 0.0

    def __bool__(self):
        return self.success


# ═══════════════════════════════════════════════════════════════
#  CORE -- WebAccess Engine
# ═══════════════════════════════════════════════════════════════

class WebAccess:
    """Motore principale per accesso internet robusto e sicuro.

    Usage:
        wa = WebAccess()
        result = wa.get("https://example.com")
        if result:
            print(result.data.text[:500])
    """

    def __init__(self, config: Optional[WebConfig] = None):
        self.config = config or CONFIG

        # Rate limiter
        self._rate_limiter = RateLimiter(self.config.rate_limit)

        # Cache
        self._cache: Optional[ResponseCache] = None
        if self.config.cache_enabled:
            self._cache = ResponseCache(
                self.config.cache_db,
                self.config.cache_ttl_seconds,
            )

        # Sessione HTTP (con lock per thread safety)
        self._session = self._build_session()
        self._session_lock = threading.Lock()

    def _build_session(self) -> requests.Session:
        """Costruisce una sessione requests con retry e pool."""
        session = requests.Session()

        # Retry strategy
        retry_strategy = Retry(
            total=self.config.max_retries,
            backoff_factor=self.config.backoff_factor,
            status_forcelist=[429, 500, 502, 503, 504],
            allowed_methods=["GET", "POST", "HEAD"],
            raise_on_status=False,
        )

        # Adapter con pool di connessioni
        adapter = HTTPAdapter(
            max_retries=retry_strategy,
            pool_connections=20,
            pool_maxsize=50,
        )
        session.mount("https://", adapter)
        session.mount("http://", adapter)

        # Headers di default
        session.headers.update({
            "User-Agent": self.config.user_agent,
            "Accept": "text/html,application/json,*/*",
            "Accept-Language": "it-IT,it;q=0.9,en;q=0.8",
            "Accept-Encoding": "gzip, deflate, br",
        })

        return session

    # ── Metodi pubblici ───────────────────────────────────────

    def get(
        self,
        url: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        allow_redirects: bool = True,
        stream: bool = False,
        use_cache: Optional[bool] = None,
    ) -> WebResult:
        """Esegue una richiesta GET.

        Args:
            url: URL da contattare
            params: Parametri query string
            headers: Headers HTTP aggiuntivi
            timeout: Timeout totale in secondi (default: config.total_timeout)
            allow_redirects: Seguire i redirect
            stream: Streaming mode (per download grandi)
            use_cache: Usa cache (None = usa default config)
        """
        return self._request(
            "GET", url, params=params, headers=headers,
            timeout=timeout, allow_redirects=allow_redirects,
            stream=stream, use_cache=use_cache,
        )

    def post(
        self,
        url: str,
        data: Optional[Any] = None,
        json_data: Optional[dict] = None,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        allow_redirects: bool = True,
    ) -> WebResult:
        """Esegue una richiesta POST.

        Args:
            url: URL da contattare
            data: Corpo della richiesta (form-encoded)
            json_data: Corpo JSON
            params: Parametri query string
            headers: Headers HTTP aggiuntivi
            timeout: Timeout totale in secondi
            allow_redirects: Seguire i redirect
        """
        return self._request(
            "POST", url, params=params, data=data,
            json_data=json_data, headers=headers,
            timeout=timeout, allow_redirects=allow_redirects,
            use_cache=False,  # POST non cached
        )

    def download(
        self,
        url: str,
        dest_path: str,
        params: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        chunk_size: int = 8192,
        show_progress: bool = False,
    ) -> WebResult:
        """Scarica un file salvandolo su disco.

        Args:
            url: URL del file
            dest_path: Percorso dove salvare
            params: Parametri query string
            headers: Headers HTTP aggiuntivi
            timeout: Timeout totale
            chunk_size: Dimensione chunk per download
            show_progress: Mostra barra di progresso

        Returns:
            WebResult con data.content = path del file salvato
        """
        start = time.time()
        try:
            # Validazione sicurezza
            safe, reason = is_safe_url(url, self.config)
            if not safe:
                return WebResult(
                    success=False, error=reason,
                    error_type="SECURITY", duration=time.time() - start,
                )

            # Rate limiting
            self._rate_limiter.wait()

            # Prepara destinazione
            dest = Path(dest_path)
            dest.parent.mkdir(parents=True, exist_ok=True)

            temp_path = dest.with_suffix(dest.suffix + ".tmp")

            # Headers custom
            req_headers = dict(self._session.headers)
            if headers:
                req_headers.update(headers)

            # Lock per thread safety
            with self._session_lock:
                resp = self._session.get(
                    url,
                    params=params,
                    headers=req_headers,
                    timeout=(self.config.connect_timeout, timeout or self.config.read_timeout),
                    stream=True,
                    allow_redirects=True,
                )
            resp.raise_for_status()

            total = int(resp.headers.get("content-length", 0))
            downloaded = 0

            with open(temp_path, "wb") as f:
                for chunk in resp.iter_content(chunk_size=chunk_size):
                    if chunk:
                        f.write(chunk)
                        downloaded += len(chunk)
                        if show_progress and total > 0:
                            pct = (downloaded / total) * 100
                            sys.stdout.write(f"\r  [SAVE]  Scaricato {downloaded}/{total} bytes ({pct:.1f}%)")
                            sys.stdout.flush()
                        # Limite sicurezza
                        if downloaded > self.config.max_response_size:
                            if show_progress:
                                sys.stdout.write("\n")
                            temp_path.unlink(missing_ok=True)
                            return WebResult(
                                success=False,
                                error=f"File troppo grande (> {self.config.max_response_size // (1024*1024)} MB)",
                                error_type="SIZE_LIMIT",
                                duration=time.time() - start,
                            )

            if show_progress and total > 0:
                sys.stdout.write("\n")

            # Rinomina da .tmp a nome finale
            temp_path.rename(dest)

            return WebResult(
                success=True,
                data=WebResponse(
                    url=str(resp.url),
                    status_code=resp.status_code,
                    headers=dict(resp.headers),
                    content=str(dest),
                    text=str(dest),
                    encoding="utf-8",
                    elapsed=time.time() - start,
                    redirected=len(resp.history) > 0,
                    redirect_chain=[r.url for r in resp.history],
                ),
                duration=time.time() - start,
            )

        except requests.exceptions.Timeout:
            return WebResult(
                success=False, error="Timeout durante il download",
                error_type="TIMEOUT", duration=time.time() - start,
            )
        except requests.exceptions.RequestException as e:
            return WebResult(
                success=False, error=f"Errore download: {e}",
                error_type="REQUEST", duration=time.time() - start,
            )
        except OSError as e:
            return WebResult(
                success=False, error=f"Errore I/O: {e}",
                error_type="IO", duration=time.time() - start,
            )

    def scrape(
        self,
        url: str,
        selector: Optional[str] = None,
        extract: str = "text",
        params: Optional[dict] = None,
        timeout: Optional[float] = None,
    ) -> WebResult:
        """Scraping HTML con selettori CSS.

        Args:
            url: URL della pagina
            selector: Selettore CSS (es. "h1", ".content", "#main"). Se None, estrae tutto.
            extract: Cosa estrarre: "text" (testo), "href" (link), "src" (immagini),
                     "html" (HTML interno), "tag" (tag name)
            params: Parametri query
            timeout: Timeout

        Returns:
            WebResult con data.content = lista di stringhe estratte
        """
        if not HAS_BEAUTIFULSOUP:
            return WebResult(
                success=False,
                error="BeautifulSoup non installato. pip install beautifulsoup4 lxml",
                error_type="DEPENDENCY",
            )

        result = self.get(url, params=params, timeout=timeout)
        if not result:
            return result

        try:
            soup = result.data.soup()
            results: list[str] = []

            if selector:
                elements = soup.select(selector)
            else:
                elements = [soup]

            for el in elements:
                if extract == "text":
                    txt = el.get_text(strip=True)
                    if txt:
                        results.append(txt)
                elif extract == "href":
                    href = el.get("href")
                    if href:
                        results.append(urllib.parse.urljoin(url, href))
                elif extract == "src":
                    src = el.get("src")
                    if src:
                        results.append(urllib.parse.urljoin(url, src))
                elif extract == "html":
                    results.append(str(el))
                elif extract == "tag":
                    results.append(el.name)
                else:
                    results.append(el.get_text(strip=True))

            return WebResult(
                success=True,
                data=WebResponse(
                    url=result.data.url,
                    status_code=result.data.status_code,
                    headers=result.data.headers,
                    content=json.dumps(results, ensure_ascii=False).encode(),
                    text="\n".join(results),
                    encoding="utf-8",
                    elapsed=result.data.elapsed,
                    redirected=result.data.redirected,
                    cached=result.data.cached,
                ),
                duration=result.duration,
            )

        except Exception as e:
            return WebResult(
                success=False,
                error=f"Errore scraping: {e}",
                error_type="SCRAPE",
                duration=result.duration,
            )

    def scrape_links(
        self,
        url: str,
        selector: str = "a[href]",
        params: Optional[dict] = None,
    ) -> WebResult:
        """Scarica tutti i link da una pagina. Scorciatoia per scrape(..., extract='href')."""
        return self.scrape(url, selector=selector, extract="href", params=params)

    def parallel_get(
        self,
        urls: list[str],
        max_workers: int = 5,
        timeout: Optional[float] = None,
    ) -> list[WebResult]:
        """Esegue GET parallele su più URL.

        Args:
            urls: Lista di URL
            max_workers: Numero massimo di connessioni parallele
            timeout: Timeout per ogni richiesta

        Returns:
            Lista di WebResult (stesso ordine degli URL)
        """
        results: list[WebResult] = []

        def _fetch(u: str) -> WebResult:
            return self.get(u, timeout=timeout)

        with ThreadPoolExecutor(max_workers=max_workers) as pool:
            results = list(pool.map(_fetch, urls))

        return results

    # ── Cache management ─────────────────────────────────────

    def clear_cache(self, url_pattern: Optional[str] = None):
        """Pulisce la cache."""
        if self._cache:
            self._cache.invalidate(url_pattern)
            log.info(f"Cache invalidata{' per ' + url_pattern if url_pattern else ''}")

    def cache_stats(self) -> dict:
        """Statistiche cache."""
        if self._cache:
            return self._cache.stats()
        return {"enabled": False}

    # ── Metodi interni ────────────────────────────────────────

    def _request(
        self,
        method: str,
        url: str,
        params: Optional[dict] = None,
        data: Optional[Any] = None,
        json_data: Optional[dict] = None,
        headers: Optional[dict] = None,
        timeout: Optional[float] = None,
        allow_redirects: bool = True,
        stream: bool = False,
        use_cache: Optional[bool] = None,
    ) -> WebResult:
        """Esegue una richiesta HTTP con tutte le protezioni."""
        start = time.time()

        # ── 1. Validazione sicurezza ──
        safe, reason = is_safe_url(url, self.config)
        if not safe:
            return WebResult(
                success=False, error=reason,
                error_type="SECURITY", duration=time.time() - start,
            )

        # ── 2. Cache check ──
        should_cache = (
            use_cache if use_cache is not None
            else self.config.cache_enabled
        )
        if should_cache and method == "GET" and self._cache:
            cached = self._cache.get(method, url, params, data)
            if cached:
                log.info(f"[OK] Cache HIT per {url}")
                resp = WebResponse(
                    url=url,
                    status_code=cached.status_code,
                    headers=cached.headers,
                    content=cached.content,
                    text=cached.content.decode(cached.encoding or "utf-8", errors="replace"),
                    encoding=cached.encoding or "utf-8",
                    elapsed=cached.elapsed,
                    redirected=False,
                    cached=True,
                )
                return WebResult(success=True, data=resp, duration=0.0)

        # ── 3. Rate limiting ──
        self._rate_limiter.wait()

        # ── 4. Preparazione richiesta ──
        req_headers = dict(self._session.headers)
        if headers:
            req_headers.update(headers)

        req_timeout = timeout or self.config.total_timeout
        connect_to = self.config.connect_timeout
        read_to = req_timeout - connect_to if req_timeout > connect_to else req_timeout

        try:
            # ── 5. Esecuzione (con lock per thread safety) ──
            with self._session_lock:
                response = self._session.request(
                    method=method,
                    url=url,
                    params=params,
                    data=data,
                    json=json_data,
                    headers=req_headers,
                    timeout=(connect_to, read_to),
                    allow_redirects=allow_redirects,
                    stream=stream,
                )

            elapsed = time.time() - start

            # ── 6. Limite dimensione risposta ──
            if not stream:
                content_length = response.headers.get("content-length")
                if content_length:
                    try:
                        if int(content_length) > self.config.max_response_size:
                            return WebResult(
                                success=False,
                                error=f"Risposta troppo grande ({content_length} bytes > {self.config.max_response_size} bytes)",
                                error_type="SIZE_LIMIT",
                                duration=elapsed,
                            )
                    except ValueError:
                        pass

                content = response.content
                if len(content) > self.config.max_response_size:
                    return WebResult(
                        success=False,
                        error=f"Risposta troppo grande ({len(content)} bytes > {self.config.max_response_size} bytes)",
                        error_type="SIZE_LIMIT",
                        duration=elapsed,
                    )
            else:
                content = b""

            # ── 7. Encoding detection ──
            encoding = response.apparent_encoding or response.encoding or "utf-8"

            # ── 8. Testo decodificato ──
            if not stream:
                try:
                    text = response.text
                except (UnicodeDecodeError, LookupError):
                    text = content.decode("utf-8", errors="replace")
            else:
                text = ""

            # ── 9. Costruzione risposta ──
            web_resp = WebResponse(
                url=str(response.url),
                status_code=response.status_code,
                headers=dict(response.headers),
                content=content,
                text=text,
                encoding=encoding,
                elapsed=elapsed,
                redirected=len(response.history) > 0,
                redirect_chain=[r.url for r in response.history],
                success=200 <= response.status_code < 400,
                error=None if 200 <= response.status_code < 400 else f"HTTP {response.status_code}",
            )

            # ── 10. Salva in cache (solo GET success) ──
            if should_cache and method == "GET" and 200 <= response.status_code < 300 and self._cache:
                cached_resp = CachedResponse(
                    status_code=response.status_code,
                    headers=dict(response.headers),
                    content=content,
                    encoding=encoding,
                    elapsed=elapsed,
                    cached_at=datetime.utcnow().isoformat(),
                )
                self._cache.set(method, url, params, data, cached_resp)

            return WebResult(success=True, data=web_resp, duration=elapsed)

        except requests.exceptions.Timeout:
            return WebResult(
                success=False, error=f"Timeout dopo {time.time() - start:.1f}s",
                error_type="TIMEOUT", duration=time.time() - start,
            )
        except requests.exceptions.ConnectionError as e:
            return WebResult(
                success=False, error=f"Errore connessione: {e}",
                error_type="CONNECTION", duration=time.time() - start,
            )
        except requests.exceptions.TooManyRedirects:
            return WebResult(
                success=False, error=f"Troppi redirect (> {self.config.max_redirects})",
                error_type="REDIRECT", duration=time.time() - start,
            )
        except requests.exceptions.RequestException as e:
            return WebResult(
                success=False, error=f"Errore richiesta: {e}",
                error_type="REQUEST", duration=time.time() - start,
            )
        except Exception as e:
            log.exception(f"Errore inaspettato per {url}")
            return WebResult(
                success=False, error=f"Errore inaspettato: {e}",
                error_type="UNKNOWN", duration=time.time() - start,
            )

    def close(self):
        """Chiude la sessione e la cache."""
        if self._session:
            self._session.close()
        if self._cache:
            self._cache.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════

def create_cli() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="web_access",
        description="[WEB] WebAccess -- Accesso Internet Robusto e Sicuro per Oracle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  python tools/web_access.py get https://example.com
  python tools/web_access.py get "https://api.example.com/data" --json
  python tools/web_access.py post "https://httpbin.org/post" --data '{"key":"value"}' --json-body
  python tools/web_access.py download "https://example.com/file.pdf" --output ./downloads/
  python tools/web_access.py scrape "https://example.com" --selector "h1, h2" --extract text
  python tools/web_access.py scrape-links "https://example.com"
  python tools/web_access.py parallel "https://site1.com" "https://site2.com" "https://site3.com"
  python tools/web_access.py cache --stats
  python tools/web_access.py cache --clear
        """,
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # ── get ──
    p_get = sub.add_parser("get", help="Richiesta GET")
    p_get.add_argument("url", help="URL da contattare")
    p_get.add_argument("--params", "-p", help="Query params (JSON, es: '{\"key\":\"val\"}')")
    p_get.add_argument("--headers", "-H", help="Headers extra (JSON)")
    p_get.add_argument("--timeout", "-t", type=float, help="Timeout in secondi")
    p_get.add_argument("--json", "-j", action="store_true", help="Output JSON")
    p_get.add_argument("--pretty", action="store_true", help="JSON formattato")
    p_get.add_argument("--no-cache", action="store_true", help="Ignora cache")
    p_get.add_argument("--raw", action="store_true", help="Mostra solo il corpo (no metadata)")
    p_get.add_argument("--truncate", type=int, default=2000, help="Tronca output a N caratteri (default 2000, 0=no limit)")

    # ── post ──
    p_post = sub.add_parser("post", help="Richiesta POST")
    p_post.add_argument("url", help="URL da contattare")
    p_post.add_argument("--data", "-d", help="Dati form-encoded (stringa)")
    p_post.add_argument("--json-data", "-j", help="Dati JSON (es: '{\"key\":\"val\"}')")
    p_post.add_argument("--headers", "-H", help="Headers extra (JSON)")
    p_post.add_argument("--timeout", "-t", type=float, help="Timeout in secondi")
    p_post.add_argument("--json", action="store_true", help="Output JSON")
    p_post.add_argument("--pretty", action="store_true", help="JSON formattato")

    # ── download ──
    p_dl = sub.add_parser("download", help="Scarica file")
    p_dl.add_argument("url", help="URL del file")
    p_dl.add_argument("--output", "-o", help="Percorso output (default: nome dal URL)")
    p_dl.add_argument("--progress", action="store_true", help="Mostra progresso")

    # ── scrape ──
    p_sc = sub.add_parser("scrape", help="Scraping HTML con selettori CSS")
    p_sc.add_argument("url", help="URL della pagina")
    p_sc.add_argument("--selector", "-s", help="Selettore CSS (default: tutto)")
    p_sc.add_argument("--extract", "-e", default="text",
                      choices=["text", "href", "src", "html", "tag"],
                      help="Cosa estrarre (default: text)")
    p_sc.add_argument("--json", "-j", action="store_true", help="Output JSON")

    # ── scrape-links ──
    p_sl = sub.add_parser("scrape-links", help="Estrae tutti i link da una pagina")
    p_sl.add_argument("url", help="URL della pagina")
    p_sl.add_argument("--json", "-j", action="store_true", help="Output JSON")

    # ── parallel ──
    p_par = sub.add_parser("parallel", help="GET parallele su più URL")
    p_par.add_argument("urls", nargs="+", help="URL da contattare")
    p_par.add_argument("--workers", "-w", type=int, default=5, help="Worker paralleli")
    p_par.add_argument("--json", "-j", action="store_true", help="Output JSON")

    # ── cache ──
    p_cache = sub.add_parser("cache", help="Gestione cache")
    p_cache.add_argument("--stats", action="store_true", help="Statistiche cache")
    p_cache.add_argument("--clear", nargs="?", const="", default=None,
                         help="Pattern URL da rimuovere (default: tutto)")

    return parser


def run_cli():
    """Entry point CLI."""
    parser = create_cli()
    args = parser.parse_args()

    wa = WebAccess()

    try:
        # ── GET ──
        if args.command == "get":
            params = json.loads(args.params) if args.params else None
            headers = json.loads(args.headers) if args.headers else None
            use_cache = not args.no_cache if hasattr(args, 'no_cache') else None
            truncate = getattr(args, 'truncate', 2000)

            result = wa.get(
                args.url,
                params=params,
                headers=headers,
                timeout=args.timeout,
                use_cache=use_cache,
            )

            if not result:
                print(f"[ERR] {result.error}", file=sys.stderr)
                sys.exit(1)

            resp = result.data
            if args.json or args.pretty:
                output = {
                    "success": True,
                    "url": resp.url,
                    "status_code": resp.status_code,
                    "headers": dict(resp.headers),
                    "content_length": len(resp.content),
                    "elapsed": round(resp.elapsed, 3),
                    "cached": resp.cached,
                    "body": resp.text[:truncate] if truncate else resp.text,
                }
                print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
            elif args.raw:
                print(resp.text[:truncate] if truncate else resp.text)
            else:
                print(f"\n[WEB] {resp.url}")
                print(f"   Status: {resp.status_code}  |  {len(resp.content)} bytes  |  {resp.elapsed:.2f}s"
                      f"{'  [CACHE] CACHED' if resp.cached else ''}")
                if resp.redirected:
                    print(f"   Redirect chain: {' -> '.join(resp.redirect_chain)}")
                print(f"   Content-Type: {resp.headers.get('content-type', '?')[:60]}")
                print()
                body = resp.text[:truncate] if truncate else resp.text
                print(body if body else "(nessun contenuto)")
                if truncate and len(resp.text) > truncate:
                    print(f"\n... [troncato a {truncate} caratteri, usa --truncate 0 per tutto]")

        # ── POST ──
        elif args.command == "post":
            data = args.data
            json_data = json.loads(args.json_data) if args.json_data else None
            headers = json.loads(args.headers) if args.headers else None

            result = wa.post(
                args.url,
                data=data,
                json_data=json_data,
                headers=headers,
                timeout=args.timeout,
            )

            if not result:
                print(f"[ERR] {result.error}", file=sys.stderr)
                sys.exit(1)

            resp = result.data
            if args.json or args.pretty:
                output = {
                    "success": True,
                    "url": resp.url,
                    "status_code": resp.status_code,
                    "elapsed": round(resp.elapsed, 3),
                    "body": resp.text[:2000],
                }
                print(json.dumps(output, indent=2 if args.pretty else None, ensure_ascii=False))
            else:
                print(f"\n[WEB] POST -> {resp.url}")
                print(f"   Status: {resp.status_code}  |  {resp.elapsed:.2f}s")
                print()
                print(resp.text[:2000])
                if len(resp.text) > 2000:
                    print("\n... [troncato]")

        # ── DOWNLOAD ──
        elif args.command == "download":
            output = args.output
            if not output:
                fname = urlparse(args.url).path.split("/")[-1] or "download"
                output = f"./downloads/{fname}"

            result = wa.download(
                args.url,
                dest_path=output,
                show_progress=args.progress,
            )

            if not result:
                print(f"[ERR] {result.error}", file=sys.stderr)
                sys.exit(1)

            print(f"[OK] File salvato: {result.data.content}")
            print(f"   Dimensione: {Path(result.data.content).stat().st_size} bytes")

        # ── SCRAPE ──
        elif args.command == "scrape":
            result = wa.scrape(
                args.url,
                selector=args.selector,
                extract=args.extract,
            )

            if not result:
                print(f"[ERR] {result.error}", file=sys.stderr)
                sys.exit(1)

            items = result.data.text.split("\n")
            if args.json:
                print(json.dumps(items, ensure_ascii=False, indent=2))
            else:
                print(f"\n[SEARCH] Scraping da: {args.url}")
                print(f"   Selettore: {args.selector or '(tutto)'}")
                print(f"   Estratto: {args.extract}")
                print(f"   Trovati: {len(items)} elementi\n")
                for i, item in enumerate(items[:50], 1):
                    print(f"  {i:3d}. {item[:200]}")
                if len(items) > 50:
                    print(f"\n   ... e altri {len(items) - 50} elementi")

        # ── SCRAPE-LINKS ──
        elif args.command == "scrape-links":
            result = wa.scrape_links(args.url)

            if not result:
                print(f"[ERR] {result.error}", file=sys.stderr)
                sys.exit(1)

            links = result.data.text.split("\n")
            if args.json:
                print(json.dumps(links, ensure_ascii=False, indent=2))
            else:
                print(f"\n[LINK] Link trovati su: {args.url}")
                for i, link in enumerate(links[:50], 1):
                    print(f"  {i:3d}. {link}")
                if len(links) > 50:
                    print(f"\n   ... e altri {len(links) - 50} link")

        # ── PARALLEL ──
        elif args.command == "parallel":
            results = wa.parallel_get(args.urls, max_workers=args.workers)

            if args.json:
                output = []
                for url, result in zip(args.urls, results):
                    entry = {"url": url, "success": result.success}
                    if result.success:
                        entry.update({
                            "status_code": result.data.status_code,
                            "size": len(result.data.content),
                            "elapsed": round(result.data.elapsed, 3),
                        })
                    else:
                        entry["error"] = result.error
                    output.append(entry)
                print(json.dumps(output, indent=2, ensure_ascii=False))
            else:
                print(f"\n[NET] Richieste parallele ({len(results)} URL, {args.workers} workers):\n")
                for url, result in zip(args.urls, results):
                    if result.success:
                        r = result.data
                        print(f"  [OK] {r.status_code}  {r.elapsed:.2f}s  {url[:100]}")
                    else:
                        print(f"  [ERR] {result.error}  {url[:80]}")

        # ── CACHE ──
        elif args.command == "cache":
            if args.stats:
                stats = wa.cache_stats()
                if stats.get("enabled") is False:
                    print("[INFO]️  Cache disabilitata")
                else:
                    print(f"\n[DL] Statistiche Cache:")
                    print(f"   Entrate: {stats['total_entries']}")
                    print(f"   Dimensione: {stats['total_size_bytes'] / 1024:.1f} KB")
                    print(f"   Più vecchia: {stats['oldest']}")
                    print(f"   Più recente: {stats['newest']}")
            elif args.clear is not None:
                pattern = args.clear if args.clear else None
                wa.clear_cache(pattern)
                print(f"[OK] Cache invalidata{'' if pattern is None else f' (pattern: {pattern})'}")
            else:
                print("Usa --stats o --clear [pattern]")

    finally:
        wa.close()


if __name__ == "__main__":
    run_cli()