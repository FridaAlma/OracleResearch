#!/usr/bin/env python3
"""
📓 WikiTool — Gestione completa della wiki personale via API HTTP.

Permette di creare, leggere, elencare pagine HTML e caricare immagini
su un server wiki con API REST (http://192.168.1.5:8000).

Utilizzo CLI:
  python tools/wiki_tool.py list
  python tools/wiki_tool.py read home
  python tools/wiki_tool.py write home --content "<h1>Ciao</h1>"
  python tools/wiki_tool.py write home --file pagina.html
  python tools/wiki_tool.py upload logo.png
  python tools/wiki_tool.py search "keyword"

Utilizzo Python:
  from tools.wiki_tool import WikiClient
  wiki = WikiClient()
  wiki.list_pages()
  wiki.read_page("home")
  wiki.write_page("home", "<h1>Ciao</h1>")
  wiki.upload_image("logo.png")
"""

import argparse
import json
import logging
import mimetypes
import os
import re
import sys
import time
from pathlib import Path
from typing import Optional
from urllib.parse import urljoin

import requests

# ── Configurazione ──────────────────────────────────────────────────────────

DEFAULT_BASE_URL = "http://192.168.1.5:8000"
REQUEST_TIMEOUT = 15  # secondi
MAX_RETRIES = 2
VALID_PAGE_NAME_RE = re.compile(r'^[a-zA-Z0-9._-]+$')
VALID_IMAGE_EXT = {'.jpg', '.jpeg', '.png', '.gif', '.webp', '.svg'}
MAX_FILE_SIZE_MB = 10

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)s | %(message)s",
)
log = logging.getLogger("WikiTool")


# ── Eccezioni personalizzate ────────────────────────────────────────────────

class WikiError(Exception):
    """Errore generico del client wiki."""
    pass

class PageNotFoundError(WikiError):
    """La pagina richiesta non esiste."""
    pass

class InvalidNameError(WikiError):
    """Nome pagina non valido."""
    pass

class ServerError(WikiError):
    """Errore lato server."""
    pass


# ── Client principale ───────────────────────────────────────────────────────

class WikiClient:
    """Client per interagire con la wiki personale via API HTTP."""

    def __init__(self, base_url: str = DEFAULT_BASE_URL, timeout: int = REQUEST_TIMEOUT):
        self.base_url = base_url.rstrip("/")
        self.timeout = timeout
        self._session = requests.Session()
        # Headers di default
        self._session.headers.update({
            "User-Agent": "WikiTool/1.0 (Oracle AI Agent)",
            "Accept": "application/json, text/html, */*",
        })

    # ── Helpers interni ─────────────────────────────────────────────────

    def _url(self, path: str) -> str:
        return urljoin(self.base_url + "/", path.lstrip("/"))

    def _request(self, method: str, path: str, **kwargs) -> requests.Response:
        """Esegue una richiesta HTTP con retry e timeout."""
        url = self._url(path)
        kwargs.setdefault("timeout", self.timeout)

        last_error = None
        for attempt in range(1, MAX_RETRIES + 2):
            try:
                resp = self._session.request(method, url, **kwargs)
                return resp
            except requests.ConnectionError as e:
                last_error = WikiError(f"Connessione fallita a {url}: {e}")
                if attempt <= MAX_RETRIES:
                    wait = 0.5 * attempt
                    log.warning("Tentativo %d/%d fallito, riprovo tra %.1fs...", attempt, MAX_RETRIES + 1, wait)
                    time.sleep(wait)
                else:
                    raise last_error from e
            except requests.Timeout as e:
                raise WikiError(f"Timeout dopo {self.timeout}s: {url}") from e
            except requests.RequestException as e:
                raise WikiError(f"Richiesta fallita: {e}") from e
        raise last_error  # never reached

    @staticmethod
    def _validate_page_name(name: str) -> str:
        """Valida e normalizza un nome pagina."""
        name = name.strip().lower()
        if not name:
            raise InvalidNameError("Il nome pagina non può essere vuoto.")
        if not VALID_PAGE_NAME_RE.match(name):
            raise InvalidNameError(
                f"Nome pagina '{name}' non valido. Usa solo: lettere, numeri, punti, trattini, underscore."
            )
        return name

    @staticmethod
    def _validate_image_file(filepath: str) -> str:
        """Valida un file immagine (esistenza, formato, dimensione)."""
        path = Path(filepath)
        if not path.exists():
            raise WikiError(f"File non trovato: {filepath}")
        if not path.is_file():
            raise WikiError(f"Percorso non è un file: {filepath}")

        ext = path.suffix.lower()
        if ext not in VALID_IMAGE_EXT:
            raise WikiError(
                f"Formato '{ext}' non supportato. Usa: {', '.join(sorted(VALID_IMAGE_EXT))}"
            )

        size_mb = path.stat().st_size / (1024 * 1024)
        if size_mb > MAX_FILE_SIZE_MB:
            raise WikiError(
                f"File troppo grande: {size_mb:.1f} MB (max {MAX_FILE_SIZE_MB} MB)"
            )
        return str(path)

    # ── API Pubbliche ───────────────────────────────────────────────────

    def list_pages(self) -> list:
        """Restituisce la lista di tutte le pagine della wiki."""
        resp = self._request("GET", "/pages")
        if resp.status_code == 200:
            data = resp.json()
            return data.get("pages", [])
        raise ServerError(f"GET /pages -> {resp.status_code}: {resp.text[:200]}")

    def read_page(self, name: str) -> str:
        """Legge il contenuto HTML di una pagina. Solleva PageNotFoundError se non esiste."""
        name = self._validate_page_name(name)
        resp = self._request("GET", f"/pages/{name}")
        if resp.status_code == 200:
            return resp.text
        if resp.status_code == 404:
            raise PageNotFoundError(f"Pagina '{name}' non trovata (404).")
        raise ServerError(f"GET /pages/{name} -> {resp.status_code}: {resp.text[:200]}")

    def write_page(self, name: str, content: str) -> dict:
        """Crea o sovrascrive una pagina HTML. Restituisce il JSON di risposta."""
        name = self._validate_page_name(name)
        if not content or not content.strip():
            raise WikiError("Il contenuto HTML non può essere vuoto.")

        resp = self._request(
            "POST", f"/pages/{name}",
            data=content.encode("utf-8") if isinstance(content, str) else content,
            headers={"Content-Type": "text/html; charset=utf-8"},
        )
        if resp.status_code in (200, 201):
            try:
                return resp.json()
            except json.JSONDecodeError:
                return {"status": "ok", "message": resp.text[:200]}
        raise ServerError(f"POST /pages/{name} -> {resp.status_code}: {resp.text[:200]}")

    def write_page_from_file(self, name: str, filepath: str) -> dict:
        """Crea/aggiorna una pagina leggendo il contenuto da un file."""
        path = Path(filepath)
        if not path.exists():
            raise WikiError(f"File non trovato: {filepath}")
        content = path.read_text(encoding="utf-8")
        return self.write_page(name, content)

    def upload_image(self, filepath: str) -> dict:
        """Carica un'immagine sulla wiki. Restituisce filename e URL."""
        filepath = self._validate_image_file(filepath)
        filename = Path(filepath).name

        with open(filepath, "rb") as f:
            resp = self._request(
                "POST", "/upload/image",
                files={"file": (filename, f, mimetypes.guess_type(filepath)[0] or "image/png")},
            )

        if resp.status_code in (200, 201):
            data = resp.json()
            # Costruisce URL assoluto
            if "url" in data:
                url = data["url"]
                if url.startswith("/"):
                    data["url_abs"] = f"{self.base_url}{url}"
                else:
                    data["url_abs"] = url
            return data

        raise ServerError(f"POST /upload/image -> {resp.status_code}: {resp.text[:200]}")

    def page_exists(self, name: str) -> bool:
        """Verifica se una pagina esiste (senza scaricarne il contenuto)."""
        try:
            self.read_page(name)
            return True
        except PageNotFoundError:
            return False

    def search_pages(self, keyword: str) -> list:
        """Cerca keyword nei titoli delle pagine (case-insensitive)."""
        keyword = keyword.lower().strip()
        if not keyword:
            raise WikiError("Keyword di ricerca vuota.")
        pages = self.list_pages()
        matches = [p for p in pages if keyword in p.lower()]
        return matches

    def delete_page(self, name: str) -> bool:
        """
        Prova a eliminare una pagina inviando contenuto vuoto (overwrite con stringa vuota).
        NOTA: L'API non ha un endpoint DELETE esplicito.
        """
        # Alcuni server permettono sovrascrittura con contenuto vuoto
        # ma l'istruzione dice che bisogna inviare HTML nel body.
        # Provo con contenuto placeholder minimo.
        try:
            self.write_page(name, "<!-- deleted -->")
            return True
        except WikiError:
            return False

    def close(self):
        """Chiude la sessione HTTP."""
        self._session.close()

    def __enter__(self):
        return self

    def __exit__(self, *args):
        self.close()


# ── CLI ─────────────────────────────────────────────────────────────────────

def setup_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="wiki_tool",
        description="WikiTool - Gestione wiki personale via API HTTP (192.168.1.5:8000)",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  python tools/wiki_tool.py list
  python tools/wiki_tool.py read home
  python tools/wiki_tool.py write home --content "<h1>Ciao</h1><p>Test</p>"
  python tools/wiki_tool.py write home --file pagina.html
  python tools/wiki_tool.py upload logo.png
  python tools/wiki_tool.py search progetto
  python tools/wiki_tool.py exists home
  python tools/wiki_tool.py batch create_home --file homepage.html
        """,
    )
    parser.add_argument(
        "--url",
        default=DEFAULT_BASE_URL,
        help=f"URL base del server wiki (default: {DEFAULT_BASE_URL})",
    )
    parser.add_argument(
        "--json",
        action="store_true",
        help="Output in formato JSON (invece del testo formattato)",
    )
    parser.add_argument(
        "--quiet", "-q",
        action="store_true",
        help="Output minimo (solo dati essenziali)",
    )

    sub = parser.add_subparsers(dest="command", required=True)

    # list
    sub.add_parser("list", help="Elenca tutte le pagine")

    # read
    p_read = sub.add_parser("read", help="Legge il contenuto di una pagina")
    p_read.add_argument("name", help="Nome della pagina")

    # write
    p_write = sub.add_parser("write", help="Crea o sovrascrive una pagina HTML")
    p_write.add_argument("name", help="Nome della pagina")
    content_group = p_write.add_mutually_exclusive_group(required=True)
    content_group.add_argument("--content", "-c", help="Contenuto HTML (stringa)")
    content_group.add_argument("--file", "-f", help="File HTML da caricare")

    # upload
    p_up = sub.add_parser("upload", help="Carica un'immagine")
    p_up.add_argument("filepath", help="Percorso del file immagine")
    p_up.add_argument("--abs-url", action="store_true", default=True,
                      help="Mostra URL assoluto (default)")

    # search
    p_search = sub.add_parser("search", help="Cerca pagine per keyword")
    p_search.add_argument("keyword", help="Keyword da cercare")

    # exists
    p_exists = sub.add_parser("exists", help="Verifica se una pagina esiste")
    p_exists.add_argument("name", help="Nome della pagina")

    # delete (hack: sovrascrive con contenuto vuoto)
    p_del = sub.add_parser("delete", help="Sovrascrive pagina con placeholder")
    p_del.add_argument("name", help="Nome della pagina")

    # batch: create from file with optional upload
    p_batch = sub.add_parser("batch", help="Operazione batch (es. crea pagina + link immagine)")
    p_batch.add_argument("action", choices=["create_home", "create_from_file"])
    p_batch.add_argument("--name", "-n", help="Nome pagina")
    p_batch.add_argument("--file", "-f", help="File HTML")
    p_batch.add_argument("--image", "-i", help="Carica anche un'immagine")

    return parser


def output_text(data, fmt: str = "text"):
    """Formatta l'output in modo leggibile."""
    if fmt == "json":
        return json.dumps(data, indent=2, ensure_ascii=False)

    if isinstance(data, list):
        if not data:
            return "[Nessuna pagina trovata]"
        lines = [f"Pagine ({len(data)}):"]
        for i, page in enumerate(sorted(data), 1):
            lines.append(f"  {i:3d}. {page}")
        return "\n".join(lines)

    if isinstance(data, dict):
        parts = []
        for k, v in data.items():
            parts.append(f"  {k}: {v}")
        return "\n".join(parts)

    return str(data)


def main(argv: Optional[list] = None) -> int:
    # Fix encoding su Windows (cp1252 -> utf-8)
    try:
        sys.stdout.reconfigure(encoding='utf-8')
    except AttributeError:
        pass  # Python < 3.7
    parser = setup_parser()
    args = parser.parse_args(argv)

    try:
        with WikiClient(base_url=args.url) as wiki:
            result = None
            page_name = None

            # ── list ──
            if args.command == "list":
                pages = wiki.list_pages()
                if args.json:
                    print(json.dumps(pages, indent=2, ensure_ascii=False))
                else:
                    print(output_text(pages))
                return 0

            # ── read ──
            elif args.command == "read":
                content = wiki.read_page(args.name)
                if args.json:
                    print(json.dumps({"name": args.name, "content": content}, ensure_ascii=False))
                else:
                    print(content)
                return 0

            # ── write ──
            elif args.command == "write":
                if args.content:
                    result = wiki.write_page(args.name, args.content)
                elif args.file:
                    result = wiki.write_page_from_file(args.name, args.file)
                name = args.name

            # ── upload ──
            elif args.command == "upload":
                result = wiki.upload_image(args.filepath)
                name = args.filepath

            # ── search ──
            elif args.command == "search":
                matches = wiki.search_pages(args.keyword)
                if args.json:
                    print(json.dumps(matches, indent=2, ensure_ascii=False))
                else:
                    if not matches:
                        print(f"[Ricerca] Nessuna pagina contiene '{args.keyword}'.")
                    else:
                        print(f"[Ricerca] {len(matches)} pagina/e con '{args.keyword}':")
                        for m in sorted(matches):
                            print(f"   - {m}")
                return 0

            # ── exists ──
            elif args.command == "exists":
                exists = wiki.page_exists(args.name)
                if args.json:
                    print(json.dumps({"name": args.name, "exists": exists}))
                else:
                    print(f"[{'OK' if exists else 'NO'}] Pagina '{args.name}': {'ESISTE' if exists else 'NON ESISTE'}")
                return 0

            # ── delete (placeholder) ──
            elif args.command == "delete":
                result = wiki.write_page(args.name, "<!-- pagina eliminata -->")
                name = args.name

            # ── batch ──
            elif args.command == "batch":
                name = args.name or "home"
                uploaded = None

                # Eventuale upload immagine
                if args.image:
                    if not args.quiet:
                        print(f"[Upload] Caricamento immagine: {args.image}...")
                    uploaded = wiki.upload_image(args.image)
                    if not args.quiet:
                        print(f"   [OK] Immagine: {uploaded.get('url_abs', uploaded.get('url', '?'))}")

                # Creazione pagina
                if args.action == "create_home":
                    title = name.replace('_', ' ').title()
                    content = f"""<!DOCTYPE html>
<html lang="it">
<head>
    <meta charset="UTF-8">
    <meta name="viewport" content="width=device-width, initial-scale=1.0">
    <title>{title}</title>
    <style>
        body {{ font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif; max-width: 800px; margin: 0 auto; padding: 2rem; line-height: 1.6; color: #333; }}
        h1 {{ color: #2563eb; border-bottom: 2px solid #e5e7eb; padding-bottom: 0.5rem; }}
        a {{ color: #2563eb; text-decoration: none; }}
        a:hover {{ text-decoration: underline; }}
        img {{ max-width: 100%; height: auto; border-radius: 8px; }}
        .container {{ background: #f9fafb; border-radius: 12px; padding: 2rem; }}
    </style>
</head>
<body>
    <div class="container">
        <h1>{title}</h1>
        <p>Benvenuto nella mia wiki personale!</p>
        <hr>
        <h2>Pagine</h2>
        <ul>
            <li><a href="/pages/home">Home</a></li>
        </ul>
        {f'<img src="{uploaded.get("url", "")}" alt="Immagine">' if uploaded else ''}
    </div>
</body>
</html>"""
                    result = wiki.write_page(name, content)

                elif args.action == "create_from_file":
                    if not args.file:
                        print("❌ --file è necessario per create_from_file", file=sys.stderr)
                        return 1
                    result = wiki.write_page_from_file(name, args.file)

            # Output risultato per write/upload/delete/batch
            if result is not None:
                if args.json:
                    print(json.dumps(result, indent=2, ensure_ascii=False))
                elif args.quiet:
                    url = result.get("url_abs") or result.get("url") or ""
                    if url:
                        print(url)
                    else:
                        print("✅ Operazione completata.")
                else:
                    # Output formattato leggibile
                    if args.command in ("write", "batch", "delete"):
                        print(f"[OK] Pagina '{name}' salvata con successo!")
                        page_url = result.get("url", f"/pages/{name}")
                        print(f"     URL: {args.url}{page_url}")
                    elif args.command == "upload":
                        print(f"[OK] Immagine caricata con successo!")
                        print(f"     File: {name}")
                        print(f"     URL:  {result.get('url_abs', '?')}")
                        print(f"     Rel:  {result.get('url', '?')}")

            return 0

    except PageNotFoundError as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    except (InvalidNameError, WikiError) as e:
        print(f"[ERR] {e}", file=sys.stderr)
        return 1
    except requests.ConnectionError:
        print(f"[ERR] Impossibile connettersi a {args.url}. Server acceso?", file=sys.stderr)
        return 1
    except KeyboardInterrupt:
        print("\nInterrotto.", file=sys.stderr)
        return 130


if __name__ == "__main__":
    sys.exit(main())