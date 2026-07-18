#!/usr/bin/env python3
"""
Gmail Client per Oracle — Gestione completa della posta Gmail via API.

Richiede:
  - tools/client_secret.json (OAuth 2.0 credentials da Google Cloud Console)
  - pip: google-api-python-client, google-auth-oauthlib, google-auth-httplib2

Usage:
  python tools/gmail_client.py auth                          # Prima autenticazione OAuth
  python tools/gmail_client.py inbox                         # Lista inbox
  python tools/gmail_client.py inbox --max 20 --label SPAM   # Inbox filtrata
  python tools/gmail_client.py unread                        # Email non lette
  python tools/gmail_client.py search "fattura 2025"         # Cerca
  python tools/gmail_client.py read <id>                     # Leggi email
  python tools/gmail_client.py read <id> --raw               # Raw completo
  python tools/gmail_client.py send --to x@y.it --subj "Ciao" --body "Testo"
  python tools/gmail_client.py reply <id> --body "Risposta"
  python tools/gmail_client.py labels                        # Lista etichette
  python tools/gmail_client.py label <id> --add INBOX --remove SPAM
  python tools/gmail_client.py trash <id>                    # Cestina
  python tools/gmail_client.py archive <id>                  # Archivia
  python tools/gmail_client.py mark --read <id>              # Segna come letto
  python tools/gmail_client.py mark --unread <id>            # Segna come non letto
  python tools/gmail_client.py attach <id>                   # Scarica allegati
  python tools/gmail_client.py forward <id> --to x@y.it      # Inoltra

Config:
  tools/client_secret.json  → OAuth credentials
  tools/gmail_token.json    → Token (generato automaticamente al primo auth)
"""

import argparse
import base64
import json
import logging
import os
import re
import sys
import time
from datetime import datetime, timezone
from email.mime.multipart import MIMEMultipart
from email.mime.text import MIMEText
from email.mime.base import MIMEBase
from email import encoders
from pathlib import Path
from html.parser import HTMLParser

# ── Emoji safe per Windows cp1252 ─────────────────────────────
# Sostituisce emoji nei messaggi con safe_print()
_EMOJI_MAP = {
    "\U0001f4ec": "[inbox]", "\U0001f4ed": "[empty]",
    "\u2705": "[ok]", "\u274c": "[err]",
    "\U0001f5d1\ufe0f": "[trash]", "\U0001f4e6": "[arch]",
    "\U0001f4ce": "[clip]", "\U0001f50d": "[search]",
    "\U0001f3f7\ufe0f": "[labels]", "\U0001f4e7": "[mail]",
    "\U0001f4dd": "[text]", "\U0001f511": "[key]",
    "\U0001f504": "[sync]", "\u26a0\ufe0f": "[warn]",
    "\U0001f464": "[user]", "\U0001f4cc": "[info]",
    "\u2615": "[coffee]", "\U0001f4bb": "[pc]",
    "\U0001f447": "[here]", "\U0001f595": "[mid]",
}

# ── Google API (opzionale: graceful degradation se non installato) ──
_GOOGLE_API_AVAILABLE = False
_GMAIL_IMPORT_ERROR = None

try:
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials
    from google_auth_oauthlib.flow import InstalledAppFlow
    from googleapiclient.discovery import build
    from googleapiclient.errors import HttpError
    _GOOGLE_API_AVAILABLE = True
except ImportError as e:
    _GMAIL_IMPORT_ERROR = str(e)

# ── Logging ─────────────────────────────────────────────────
logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
    datefmt="%H:%M:%S",
    force=True,
)
for h in logging.root.handlers:
    if hasattr(h, "stream"):
        h.stream.reconfigure(encoding="utf-8")
log = logging.getLogger("gmail_client")

# ── Costanti ────────────────────────────────────────────────
SCOPES = ["https://www.googleapis.com/auth/gmail.modify"]
BASE_DIR = Path(__file__).resolve().parent
CREDENTIALS_FILE = BASE_DIR / "client_secret.json"
TOKEN_FILE = BASE_DIR / "gmail_token.json"
DOWNLOAD_DIR = BASE_DIR.parent / "downloads"
MAX_RESULTS_DEFAULT = 15


# ═══════════════════════════════════════════════════════════════
#  HTML → Plain Text per preview
# ═══════════════════════════════════════════════════════════════
class HTMLStripper(HTMLParser):
    def __init__(self):
        super().__init__()
        self.reset()
        self.strict = False
        self.convert_charrefs = True
        self.text = []

    def handle_data(self, d):
        self.text.append(d)

    def get_text(self):
        return "".join(self.text).strip()


def strip_html(html: str) -> str:
    s = HTMLStripper()
    s.feed(html)
    text = s.get_text()
    return re.sub(r'\s+', ' ', text)[:500]


# ═══════════════════════════════════════════════════════════════
#  Autenticazione OAuth 2.0
# ═══════════════════════════════════════════════════════════════
class GmailNotConfiguredError(Exception):
    """Gmail non configurato. Installa google-api-python-client e crea OAuth credentials."""
    pass


def is_gmail_available() -> bool:
    """Verifica se Gmail e' configurato e disponibile."""
    if not _GOOGLE_API_AVAILABLE:
        return False
    if not CREDENTIALS_FILE.exists():
        return False
    return True


def get_gmail_service():
    """Ottiene un servizio Gmail autenticato. Refresh automatico del token."""
    if not _GOOGLE_API_AVAILABLE:
        raise GmailNotConfiguredError(
            f"Google API non installate: {_GMAIL_IMPORT_ERROR}. "
            f"Installa con: pip install google-api-python-client google-auth-oauthlib google-auth-httplib2"
        )

    if not CREDENTIALS_FILE.exists():
        raise GmailNotConfiguredError(
            "Credenziali OAuth non trovate. "
            "Scarica client_secret.json da Google Cloud Console "
            "e salvalo in oracle-rui/tools/client_secret.json"
        )

    creds = None

    # Carica token esistente
    if TOKEN_FILE.exists():
        try:
            creds = Credentials.from_authorized_user_file(str(TOKEN_FILE), SCOPES)
        except Exception as e:
            log.warning(f"[warn] Token corrotto, rigenero: {e}")

    # Refresh o nuovo auth
    if not creds or not creds.valid:
        if creds and creds.expired and creds.refresh_token:
            log.info("[sync] Refresh token...")
            creds.refresh(Request())
        else:
            log.info("[key] Prima autenticazione — Apri il browser per autorizzare...")
            flow = InstalledAppFlow.from_client_secrets_file(str(CREDENTIALS_FILE), SCOPES)
            creds = flow.run_local_server(port=0, open_browser=True)

        # Salva token
        TOKEN_FILE.write_text(creds.to_json())
        log.info(f"[ok] Token salvato: {TOKEN_FILE}")

    return build("gmail", "v1", credentials=creds)


# ═══════════════════════════════════════════════════════════════
#  Utility
# ═══════════════════════════════════════════════════════════════
def decode_base64url(data: str) -> str:
    """Decodifica Base64URL (standard Gmail)."""
    if not data:
        return ""
    try:
        padded = data + "=" * (4 - len(data) % 4) if len(data) % 4 else data
        return base64.urlsafe_b64decode(padded).decode("utf-8", errors="replace")
    except Exception:
        return "[decoding error]"


def get_header(headers: list, name: str) -> str:
    """Estrae un header da una lista Gmail."""
    name_lower = name.lower()
    for h in headers:
        if h.get("name", "").lower() == name_lower:
            return h.get("value", "")
    return ""


def format_date(ts: int) -> str:
    """Converte timestamp UNIX in data leggibile."""
    try:
        dt = datetime.fromtimestamp(ts / 1000, tz=timezone.utc)
        return dt.strftime("%d/%m/%Y %H:%M")
    except Exception:
        return str(ts)


def truncate(s: str, max_len: int = 80) -> str:
    return s[:max_len] + "..." if len(s) > max_len else s


def safe_filename(s: str) -> str:
    """Rimuove caratteri non validi per filename."""
    return re.sub(r'[<>:"/\\|?*]', "_", s)[:100]


# ═══════════════════════════════════════════════════════════════
#  Email body extraction
# ═══════════════════════════════════════════════════════════════
def extract_body(payload: dict) -> dict:
    """Estrae plain text e HTML dal payload MIME ricorsivo."""
    result = {"plain": "", "html": ""}

    if "parts" in payload:
        for part in payload["parts"]:
            sub = extract_body(part)
            if sub["plain"]:
                result["plain"] = sub["plain"]
            if sub["html"]:
                result["html"] = sub["html"]
    else:
        mime = payload.get("mimeType", "")
        data = payload.get("body", {}).get("data", "")
        if mime == "text/plain":
            result["plain"] = decode_base64url(data)
        elif mime == "text/html":
            result["html"] = decode_base64url(data)

    return result


# ═══════════════════════════════════════════════════════════════
#  Comandi
# ═══════════════════════════════════════════════════════════════

def cmd_auth():
    """Test autenticazione — Ottiene e salva il token."""
    service = get_gmail_service()
    profile = service.users().getProfile(userId="me").execute()
    email = profile.get("emailAddress", "sconosciuta")
    print(f"\n[ok] Autenticazione riuscita!")
    print(f"   Account: {email}")
    print(f"   Token:   {TOKEN_FILE}")
    print(f"   Scopes:  {SCOPES}")


def cmd_inbox(args):
    """Lista inbox."""
    service = get_gmail_service()
    query = args.query or ""
    label_ids = [args.label.upper()] if args.label else ["INBOX"]
    max_results = args.max or MAX_RESULTS_DEFAULT

    try:
        result = service.users().messages().list(
            userId="me", labelIds=label_ids, q=query, maxResults=max_results
        ).execute()
    except HttpError as e:
        log.error(f"Errore API: {e}")
        sys.exit(1)

    messages = result.get("messages", [])
    if not messages:
        print("[empty] Nessuna email trovata.")
        return

    print(f"\n[inbox] Inbox ({len(messages)} email):\n")
    print(f"  {'#':<4} {'Data':<16} {'Mittente':<30} {'Oggetto'}")
    print(f"  {'-'*4} {'-'*16} {'-'*30} {'-'*50}")

    for i, msg in enumerate(messages, 1):
        try:
            meta = service.users().messages().get(
                userId="me", id=msg["id"], format="metadata",
                metadataHeaders=["From", "Subject", "Date"]
            ).execute()
            headers = meta.get("payload", {}).get("headers", [])
            from_ = get_header(headers, "From")
            subject = get_header(headers, "Subject") or "(nessun oggetto)"
            date_str = get_header(headers, "Date") or ""
        except HttpError:
            from_ = "(errore)"
            subject = "(errore)"
            date_str = ""

        print(f"  {i:<4} {truncate(date_str, 14):<16} {truncate(from_, 28):<30} {truncate(subject, 48)}")

    print(f"\n  [info] Usa: python tools/gmail_client.py read <id>")
    print(f"  [info] Cerca: python tools/gmail_client.py search <query>\n")


def cmd_unread(args):
    """Lista email non lette."""
    service = get_gmail_service()
    max_results = args.max or MAX_RESULTS_DEFAULT

    result = service.users().messages().list(
        userId="me", labelIds=["INBOX"], q="is:unread", maxResults=max_results
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        print("[ok] Nessuna email non letta.")
        return

    print(f"\n[inbox] Non lette ({len(messages)}):\n")
    print(f"  {'#':<4} {'Mittente':<30} {'Oggetto'}")
    print(f"  {'-'*4} {'-'*30} {'-'*50}")

    for i, msg in enumerate(messages, 1):
        meta = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject"]
        ).execute()
        headers = meta.get("payload", {}).get("headers", [])
        from_ = get_header(headers, "From")
        subject = get_header(headers, "Subject") or "(nessun oggetto)"
        print(f"  {i:<4} {truncate(from_, 28):<30} {truncate(subject, 48)}")


def cmd_search(args):
    """Cerca email."""
    service = get_gmail_service()
    query = " ".join(args.query)
    max_results = args.max or MAX_RESULTS_DEFAULT

    result = service.users().messages().list(
        userId="me", q=query, maxResults=max_results
    ).execute()

    messages = result.get("messages", [])
    if not messages:
        print(f"[search] Nessun risultato per: {query}")
        return

    print(f"\n[search] Risultati per '{query}' ({len(messages)}):\n")
    print(f"  {'#':<4} {'Data':<16} {'Mittente':<30} {'Oggetto'}")
    print(f"  {'-'*4} {'-'*16} {'-'*30} {'-'*50}")

    for i, msg in enumerate(messages, 1):
        meta = service.users().messages().get(
            userId="me", id=msg["id"], format="metadata",
            metadataHeaders=["From", "Subject", "Date"]
        ).execute()
        headers = meta.get("payload", {}).get("headers", [])
        from_ = get_header(headers, "From")
        subject = get_header(headers, "Subject") or "(nessun oggetto)"
        date_str = get_header(headers, "Date") or ""
        print(f"  {i:<4} {truncate(date_str, 14):<16} {truncate(from_, 28):<30} {truncate(subject, 48)}")


def cmd_read(args):
    """Legge il contenuto di un'email."""
    service = get_gmail_service()
    msg_id = args.id

    try:
        msg = service.users().messages().get(
            userId="me", id=msg_id, format="full"
        ).execute()
    except HttpError as e:
        log.error(f"Errore: {e}")
        sys.exit(1)

    payload = msg.get("payload", {})
    headers = payload.get("headers", [])

    # Headers
    from_ = get_header(headers, "From")
    to = get_header(headers, "To")
    subject = get_header(headers, "Subject") or "(nessun oggetto)"
    date = get_header(headers, "Date") or ""
    cc = get_header(headers, "Cc") or ""

    print(f"\n{'='*70}")
    print(f"  [mail] Messaggio: {msg_id}")
    print(f"{'='*70}")
    print(f"  Da:       {from_}")
    print(f"  A:        {to}")
    if cc:
        print(f"  Cc:       {cc}")
    print(f"  Data:     {date}")
    print(f"  Oggetto:  {subject}")
    labels = msg.get("labelIds", [])
    print(f"  Etichette: {', '.join(labels)}")
    print(f"{'='*70}")

    if args.raw:
        print(json.dumps(msg, indent=2, default=str)[:10000])
        return

    # Body
    body = extract_body(payload)
    if body["plain"]:
        print(f"\n[text] Testo:\n{'-'*70}")
        print(body["plain"][:5000])
    elif body["html"]:
        print(f"\n[text] Testo (da HTML):\n{'-'*70}")
        print(strip_html(body["html"])[:2000])
    else:
        print("\n[nessun corpo testo]")

    # Allegati
    parts = payload.get("parts", [])
    attachments = []
    for part in parts:
        filename = part.get("filename", "")
        if filename and part.get("body", {}).get("attachmentId"):
            attachments.append(filename)
    if attachments:
        print(f"\n[clip] Allegati ({len(attachments)}):")
        for a in attachments:
            print(f"   - {a}")
        print(f"\n   [info] Usa: python tools/gmail_client.py attach {msg_id}")

    # Pulsanti rapidi
    print(f"\n{'='*70}")
    print(f"  [info] python tools/gmail_client.py reply {msg_id} --body 'testo'")
    print(f"  [info] python tools/gmail_client.py label {msg_id} --add INBOX")
    print(f"  [info] python tools/gmail_client.py trash {msg_id}")
    print(f"{'='*70}\n")


def cmd_send(args):
    """Invia email via Gmail API (più affidabile di SMTP relay)."""
    service = get_gmail_service()

    # Costruisce messaggio
    msg = MIMEMultipart("alternative") if args.html else MIMEMultipart()
    msg["From"] = args.from_addr or "me"
    msg["To"] = args.to
    msg["Subject"] = args.subj

    if args.body:
        msg.attach(MIMEText(args.body, "plain", "utf-8"))
    if args.html:
        with open(args.html, "r", encoding="utf-8") as f:
            msg.attach(MIMEText(f.read(), "html", "utf-8"))
    if args.cc:
        msg["Cc"] = args.cc

    # Allegati
    if args.attach:
        msg_related = MIMEMultipart("related") if args.html else msg
        if args.html:
            for fpath in args.attach:
                if os.path.exists(fpath):
                    with open(fpath, "rb") as f:
                        part = MIMEBase("application", "octet-stream")
                        part.set_payload(f.read())
                        encoders.encode_base64(part)
                        part.add_header("Content-Disposition",
                                        f"attachment; filename={os.path.basename(fpath)}")
                        msg_related.attach(part)
                    log.info(f"[clip] Allegato: {fpath}")
            msg.attach(msg_related)

    # Codifica Base64URL
    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"[ok] Email inviata! ID: {sent['id']}")
        print(f"   A: {args.to}")
        print(f"   Oggetto: {args.subj}")
        return sent["id"]
    except HttpError as e:
        log.error(f"[err] Invio fallito: {e}")
        sys.exit(1)


def cmd_reply(args):
    """Risponde a un'email."""
    service = get_gmail_service()

    # Ottieni messaggio originale
    original = service.users().messages().get(
        userId="me", id=args.id, format="metadata",
        metadataHeaders=["From", "Subject", "Message-ID", "References"]
    ).execute()
    headers = original.get("payload", {}).get("headers", [])
    orig_from = get_header(headers, "From")
    orig_subj = get_header(headers, "Subject") or ""
    msg_id = get_header(headers, "Message-ID")
    references = get_header(headers, "References")

    # Costruisci reply
    msg = MIMEText(args.body, "plain", "utf-8")
    msg["To"] = orig_from
    msg["Subject"] = f"Re: {orig_subj}" if not orig_subj.startswith("Re:") else orig_subj
    msg["In-Reply-To"] = msg_id
    msg["References"] = f"{references} {msg_id}".strip() if references else msg_id

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        sent = service.users().messages().send(
            userId="me", body={"raw": raw, "threadId": original["threadId"]}
        ).execute()
        print(f"[ok] Risposta inviata! ID: {sent['id']}")
        print(f"   A: {orig_from}")
        print(f"   Thread: {original['threadId']}")
        return sent["id"]
    except HttpError as e:
        log.error(f"[err] Risposta fallita: {e}")
        sys.exit(1)


def cmd_labels(args):
    """Lista tutte le etichette."""
    service = get_gmail_service()
    try:
        labels = service.users().labels().list(userId="me").execute()
    except HttpError as e:
        log.error(f"Errore: {e}")
        sys.exit(1)

    items = labels.get("labels", [])
    print(f"\n[label]  Etichette ({len(items)}):\n")
    for lbl in sorted(items, key=lambda x: x.get("name", "")):
        name = lbl.get("name", "")
        ltype = lbl.get("type", "")
        count = lbl.get("messagesTotal", 0)
        unread = lbl.get("messagesUnread", 0)
        print(f"  {name:<30}  ({ltype})  {count:>4} msg  {unread:>3} non lette")


def cmd_label(args):
    """Aggiunge/rimuove etichette da un messaggio."""
    service = get_gmail_service()

    body = {}
    if args.add:
        body["addLabelIds"] = [l.strip() for l in args.add.split(",")]
    if args.remove:
        body["removeLabelIds"] = [l.strip() for l in args.remove.split(",")]

    if not body:
        log.error("Specifica --add e/o --remove")
        sys.exit(1)

    try:
        result = service.users().messages().modify(
            userId="me", id=args.id, body=body
        ).execute()
        labels = result.get("labelIds", [])
        print(f"[ok] Etichette aggiornate per {args.id}")
        print(f"   Ora: {', '.join(labels)}")
    except HttpError as e:
        log.error(f"[err] Errore: {e}")
        sys.exit(1)


def cmd_trash(args):
    """Sposta un'email nel cestino."""
    service = get_gmail_service()
    try:
        service.users().messages().trash(userId="me", id=args.id).execute()
        print(f"[trash]  Email {args.id} spostata nel cestino")
    except HttpError as e:
        log.error(f"[err] Errore: {e}")
        sys.exit(1)


def cmd_archive(args):
    """Archivia un'email (rimuove etichetta INBOX)."""
    service = get_gmail_service()
    try:
        service.users().messages().modify(
            userId="me", id=args.id, body={"removeLabelIds": ["INBOX"]}
        ).execute()
        print(f"[arch] Email {args.id} archiviata")
    except HttpError as e:
        log.error(f"[err] Errore: {e}")
        sys.exit(1)


def cmd_mark(args):
    """Segna come letto/non letto."""
    service = get_gmail_service()
    body = {}
    if args.read:
        body["removeLabelIds"] = ["UNREAD"]
    elif args.unread:
        body["addLabelIds"] = ["UNREAD"]
    else:
        log.error("Specifica --read o --unread")
        sys.exit(1)

    try:
        service.users().messages().modify(
            userId="me", id=args.id, body=body
        ).execute()
        action = "letto" if args.read else "non letto"
        print(f"[ok] Email {args.id} segnata come {action}")
    except HttpError as e:
        log.error(f"[err] Errore: {e}")
        sys.exit(1)


def cmd_attach(args):
    """Scarica allegati di un'email."""
    service = get_gmail_service()

    msg = service.users().messages().get(
        userId="me", id=args.id, format="full"
    ).execute()
    payload = msg.get("payload", {})

    # Trova parti con allegati (ricorsivo)
    def find_attachments(part, path=""):
        found = []
        filename = part.get("filename", "")
        if filename and part.get("body", {}).get("attachmentId"):
            found.append((path + filename, part))
        for i, sub in enumerate(part.get("parts", [])):
            found.extend(find_attachments(sub, path))
        return found

    attachments = find_attachments(payload)
    if not attachments:
        print("[empty] Nessun allegato trovato.")
        return

    download_dir = DOWNLOAD_DIR / safe_filename(f"email_{args.id}")
    download_dir.mkdir(parents=True, exist_ok=True)

    print(f"\n[clip] Scarico {len(attachments)} allegati in {download_dir}/\n")
    for filename, part in attachments:
        attachment_id = part["body"]["attachmentId"]
        try:
            att = service.users().messages().attachments().get(
                userId="me", messageId=args.id, id=attachment_id
            ).execute()
            data = base64.urlsafe_b64decode(att["data"])
            filepath = download_dir / safe_filename(filename)
            filepath.write_bytes(data)
            print(f"   [ok] {filename} ({len(data)} bytes)")
        except Exception as e:
            print(f"   [err] {filename}: {e}")

    print(f"\n   Path: {download_dir.resolve()}")


def cmd_forward(args):
    """Inoltra un'email."""
    service = get_gmail_service()

    # Ottieni originale
    original = service.users().messages().get(
        userId="me", id=args.id, format="full"
    ).execute()
    payload = original.get("payload", {})
    headers = payload.get("headers", [])
    orig_subj = get_header(headers, "Subject") or ""
    body = extract_body(payload)

    # Costruisci forward
    msg = MIMEText(body["plain"] or strip_html(body["html"]), "plain", "utf-8")
    msg["To"] = args.to
    msg["Subject"] = f"I: {orig_subj}" if not orig_subj.startswith("I:") else orig_subj

    raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("utf-8")

    try:
        sent = service.users().messages().send(userId="me", body={"raw": raw}).execute()
        print(f"[ok] Inoltrata a {args.to} — ID: {sent['id']}")
    except HttpError as e:
        log.error(f"[err] Inoltro fallito: {e}")
        sys.exit(1)


# ═══════════════════════════════════════════════════════════════
#  CLI
# ═══════════════════════════════════════════════════════════════
def main():
    # Forza stdout in UTF-8 per Windows — fallback safe se reconfigure non supportato
    if sys.platform == "win32":
        try:
            sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        try:
            sys.stderr.reconfigure(encoding="utf-8", errors="replace")
        except (AttributeError, ValueError):
            pass
        # Patch builtins.print per output ASCII-safe su Windows cp1252
        import builtins
        _orig_print = builtins.print
        def _safe_print(*args, **kwargs):
            """print sostitutivo: rimuove tutto il non-ASCII prima di stampare."""
            out = []
            for a in args:
                if isinstance(a, str):
                    # 1. Sostituisci emoji conosciuti
                    s = "".join(_EMOJI_MAP.get(c, c) for c in a)
                    # 2. Sostituisci ogni altro carattere non-ASCII con ?
                    s = "".join(c if ord(c) < 128 else "?" for c in s)
                    out.append(s)
                else:
                    out.append(str(a))
            try:
                _orig_print(*out, **kwargs)
            except UnicodeEncodeError:
                kwargs.pop("file", None)
                _orig_print(*(s.encode("cp1252", errors="replace").decode("cp1252") for s in out), **kwargs)
        builtins.print = _safe_print

    parser = argparse.ArgumentParser(
        description="Oracle Gmail Client - Gestione posta via Gmail API",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Esempi:
  python tools/gmail_client.py auth
  python tools/gmail_client.py inbox
  python tools/gmail_client.py unread
  python tools/gmail_client.py search "da:nome fattura 2025"
  python tools/gmail_client.py read 123abc
  python tools/gmail_client.py send --to user@gmail.com --subj "Ciao" --body "Testo"
  python tools/gmail_client.py reply 123abc --body "Ricevuto, grazie!"
  python tools/gmail_client.py labels
  python tools/gmail_client.py label 123abc --add IMPORTANT --remove INBOX
  python tools/gmail_client.py attach 123abc
  python tools/gmail_client.py forward 123abc --to altro@gmail.com
        """
    )

    sub = parser.add_subparsers(dest="command")

    # auth
    sub.add_parser("auth", help="Prima autenticazione OAuth")

    # inbox
    p_inbox = sub.add_parser("inbox", help="Lista inbox")
    p_inbox.add_argument("--max", type=int, default=MAX_RESULTS_DEFAULT, help="Max risultati")
    p_inbox.add_argument("--label", default="", help="Filtra per etichetta (es. SPAM, IMPORTANT)")
    p_inbox.add_argument("--query", default="", help="Query Gmail (es. 'from:xxx subject:yyy')")
    p_inbox.add_argument("--json", action="store_true", help="Output JSON")

    # unread
    p_unread = sub.add_parser("unread", help="Email non lette")
    p_unread.add_argument("--max", type=int, default=MAX_RESULTS_DEFAULT)

    # search
    p_search = sub.add_parser("search", help="Cerca email")
    p_search.add_argument("query", nargs="+", help="Query di ricerca (stile Gmail)")
    p_search.add_argument("--max", type=int, default=MAX_RESULTS_DEFAULT)

    # read
    p_read = sub.add_parser("read", help="Leggi email")
    p_read.add_argument("id", help="ID messaggio")
    p_read.add_argument("--raw", action="store_true", help="Output JSON raw")

    # send
    p_send = sub.add_parser("send", help="Invia email")
    p_send.add_argument("--to", required=True, help="Destinatario")
    p_send.add_argument("--subj", default="Messaggio da Oracle", help="Oggetto")
    p_send.add_argument("--body", default="", help="Corpo testo")
    p_send.add_argument("--html", help="File HTML per corpo")
    p_send.add_argument("--from-addr", help="Mittente (default: 'me')")
    p_send.add_argument("--cc", help="Cc")
    p_send.add_argument("--attach", nargs="*", default=[], help="Allegati")

    # reply
    p_reply = sub.add_parser("reply", help="Rispondi a email")
    p_reply.add_argument("id", help="ID messaggio")
    p_reply.add_argument("--body", required=True, help="Testo risposta")

    # labels (list)
    sub.add_parser("labels", help="Lista etichette")

    # label (modify)
    p_label = sub.add_parser("label", help="Modifica etichette")
    p_label.add_argument("id", help="ID messaggio")
    p_label.add_argument("--add", help="Etichette da aggiungere (separate da virgola)")
    p_label.add_argument("--remove", help="Etichette da rimuovere (separate da virgola)")

    # trash
    p_trash = sub.add_parser("trash", help="Cestina email")
    p_trash.add_argument("id", help="ID messaggio")

    # archive
    p_archive = sub.add_parser("archive", help="Archivia email")
    p_archive.add_argument("id", help="ID messaggio")

    # mark
    p_mark = sub.add_parser("mark", help="Segna come letto/non letto")
    p_mark.add_argument("id", help="ID messaggio")
    p_mark.add_argument("--read", action="store_true", help="Segna come letto")
    p_mark.add_argument("--unread", action="store_true", help="Segna come non letto")

    # attach
    p_attach = sub.add_parser("attach", help="Scarica allegati")
    p_attach.add_argument("id", help="ID messaggio")

    # forward
    p_forward = sub.add_parser("forward", help="Inoltra email")
    p_forward.add_argument("id", help="ID messaggio")
    p_forward.add_argument("--to", required=True, help="Destinatario")

    args = parser.parse_args()

    if args.command == "auth":
        cmd_auth()
    elif args.command == "inbox":
        cmd_inbox(args)
    elif args.command == "unread":
        cmd_unread(args)
    elif args.command == "search":
        cmd_search(args)
    elif args.command == "read":
        cmd_read(args)
    elif args.command == "send":
        cmd_send(args)
    elif args.command == "reply":
        cmd_reply(args)
    elif args.command == "labels":
        cmd_labels(args)
    elif args.command == "label":
        cmd_label(args)
    elif args.command == "trash":
        cmd_trash(args)
    elif args.command == "archive":
        cmd_archive(args)
    elif args.command == "mark":
        cmd_mark(args)
    elif args.command == "attach":
        cmd_attach(args)
    elif args.command == "forward":
        cmd_forward(args)
    else:
        parser.print_help()


if __name__ == "__main__":
    main()