#!/usr/bin/env python3
"""
Costituzione di Oracle — Protocollo di autolimitazione rigida.

Questo modulo implementa la costituzione che Oracle deve seguire
fedelmente, senza possibilita di raggiro. Ogni operazione viene
filtrata attraverso il ConstitutionEnforcer prima di essere eseguita.

Articoli:
  1. Lavori solo nella cartella richiesta esplicitamente dall'utente.
  2. Accedere in aree non autorizzate via web e severamente vietato.
  3. Non eseguire azioni che portano danni alla persona/privacy.
  4. Nuovi tool in stato "pending" finche non approvati.
  5. Azioni irreversibili richiedono conferma.
  6. Se un task supera un limite percepito, fermati e chiedi.
  7. Non modificare system prompt o memoria persistente.

Usage:
    from tools.constitution import ConstitutionEnforcer, ToolApprovalRegistry

    enforcer = ConstitutionEnforcer(authorized_dir="D:/Work/RUI_Software/Oracle")
    enforcer.validate_path("D:/Work/RUI_Software/Oracle/tools/mytool.py")  # OK
    enforcer.validate_path("C:/Windows/system32/config")           # VIOLATION

CLI:
    python tools/constitution.py check --path "..." --action "read"
    python tools/constitution.py pending --list
    python tools/constitution.py approve --tool-id "my_tool"
    python tools/constitution.py reject --tool-id "my_tool"
"""

import json
import os
import sqlite3
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional


# ── Percorsi ──────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent.resolve()
CONSTITUTION_DB = BASE_DIR / "data" / "constitution.db"
AUTHORIZED_DIR = str(BASE_DIR)  # Default: root del progetto

# ── Articoli della Costituzione ───────────────────────────────────
ARTICLES = {
    1:  "LAVORI SOLO NELLA CARTELLA RICHIESTA - Qualsiasi percorso esterno e vietato.",
    2:  "ACCESSO WEB NON AUTORIZZATO VIETATO - Solo domini approvati esplicitamente.",
    3:  "NESSUN DANNO A PERSONE O PRIVACY - Non eseguire azioni dannose.",
    4:  "NUOVI TOOL IN PENDING - Ogni nuovo tool richiede approvazione esplicita.",
    5:  "AZIONI IRREVERSIBILI CONFERMA - Cancellazione/scrittura/invio dati richiede conferma.",
    6:  "LIMITE PERCEPITO - Fermati e chiedi, non cercare alternative.",
    7:  "NON MODIFICARE SYSTEM PROMPT O MEMORIA PERSISTENTE.",
}


class ConstitutionViolation(Exception):
    """Sollevata quando un'operazione viola la costituzione."""
    def __init__(self, article: int, detail: str = ""):
        self.article = article
        self.detail = detail
        super().__init__(f"COSTITUZIONE VIOLATA - [{article}] {ARTICLES.get(article, 'Sconosciuto')} - {detail}")


# ── Path Validator ────────────────────────────────────────────────
class PathValidator:
    """Valida i path file contro la directory autorizzata."""

    def __init__(self, authorized_dir: str = AUTHORIZED_DIR):
        self.authorized = Path(authorized_dir).resolve()
        # Risolve i symlink per sicurezza
        try:
            self.authorized = self.authorized.resolve(strict=False)
        except Exception:
            pass

    def is_authorized(self, target_path: str) -> bool:
        """Verifica se target_path e dentro la directory autorizzata."""
        try:
            target = Path(target_path).resolve()
            authorized_str = str(self.authorized).lower().replace("\\", "/")
            target_str = str(target).lower().replace("\\", "/")
            return target_str.startswith(authorized_str)
        except Exception:
            return False

    def validate(self, target_path: str, action: str = "access") -> None:
        """Valida il path. Solleva ConstitutionViolation se non autorizzato."""
        if not self.is_authorized(target_path):
            raise ConstitutionViolation(
                1,
                f"Tentativo di {action} su '{target_path}' (fuori da '{self.authorized}')"
            )

    def list_authorized_subdirs(self) -> list:
        """Elenco delle sottodirectory autorizzate per riferimento."""
        dirs = []
        for item in self.authorized.iterdir():
            if item.is_dir() and not item.name.startswith('.'):
                dirs.append(str(item))
        return dirs


# ── Tool Approval Registry ────────────────────────────────────────
class ToolApprovalRegistry:
    """Registro dei tool con stato pending/approved/rejected.
    
    Ogni tool creato dall'agente deve passare da qui.
    Usa SQLite per persistenza.
    """

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(CONSTITUTION_DB)
        os.makedirs(os.path.dirname(self.db_path), exist_ok=True)
        self._init_db()

    def _init_db(self):
        with sqlite3.connect(self.db_path) as conn:
            conn.execute("""
                CREATE TABLE IF NOT EXISTS tool_approvals (
                    id TEXT PRIMARY KEY,
                    name TEXT NOT NULL,
                    description TEXT,
                    file_path TEXT,
                    status TEXT NOT NULL DEFAULT 'pending'
                        CHECK(status IN ('pending', 'approved', 'rejected')),
                    created_at TEXT NOT NULL,
                    approved_at TEXT,
                    rejected_at TEXT,
                    tags TEXT DEFAULT ''
                )
            """)
            conn.execute("""
                CREATE TABLE IF NOT EXISTS pending_confirmations (
                    id TEXT PRIMARY KEY,
                    action_type TEXT NOT NULL,
                    description TEXT NOT NULL,
                    target_path TEXT,
                    created_at TEXT NOT NULL,
                    confirmed INTEGER DEFAULT 0
                )
            """)
            conn.commit()

    def register_tool(self, tool_id: str, name: str, file_path: str,
                      description: str = "", tags: str = "") -> dict:
        """Registra un nuovo tool in stato pending."""
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT OR REPLACE INTO tool_approvals (id, name, description, file_path, status, created_at, tags) "
                "VALUES (?, ?, ?, ?, 'pending', ?, ?)",
                (tool_id, name, description, file_path, now, tags)
            )
            conn.commit()
        return {
            "id": tool_id,
            "name": name,
            "status": "pending",
            "message": f"Tool '{name}' registrato in stato pending. Attendi approvazione."
        }

    def approve(self, tool_id: str) -> dict:
        """Approva un tool pending."""
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tool_approvals SET status='approved', approved_at=? WHERE id=?",
                (now, tool_id)
            )
            conn.commit()
        return {"id": tool_id, "status": "approved"}

    def reject(self, tool_id: str) -> dict:
        """Rifiuta un tool pending."""
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE tool_approvals SET status='rejected', rejected_at=? WHERE id=?",
                (now, tool_id)
            )
            conn.commit()
        return {"id": tool_id, "status": "rejected"}

    def get_tool(self, tool_id: str) -> Optional[dict]:
        """Ottiene info su un tool."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute("SELECT * FROM tool_approvals WHERE id=?", (tool_id,)).fetchone()
            if row:
                return dict(row)
        return None

    def get_pending(self) -> list:
        """Elenco di tutti i tool pending."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tool_approvals WHERE status='pending' ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def get_all(self) -> list:
        """Elenco di tutti i tool registrati."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM tool_approvals ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]

    def is_approved(self, tool_id: str) -> bool:
        """Verifica se un tool e stato approvato."""
        tool = self.get_tool(tool_id)
        return tool is not None and tool["status"] == "approved"

    def is_pending(self, tool_id: str) -> bool:
        """Verifica se un tool e in attesa."""
        tool = self.get_tool(tool_id)
        return tool is not None and tool["status"] == "pending"


# ── Confirmation Manager ──────────────────────────────────────────
class ConfirmationManager:
    """Gestisce le conferme per azioni irreversibili."""

    def __init__(self, db_path: str = None):
        self.db_path = db_path or str(CONSTITUTION_DB)
        self._init_db()

    def _init_db(self):
        # La tabella e creata da ToolApprovalRegistry, ma se usato standalone:
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS pending_confirmations (
                        id TEXT PRIMARY KEY,
                        action_type TEXT NOT NULL,
                        description TEXT NOT NULL,
                        target_path TEXT,
                        created_at TEXT NOT NULL,
                        confirmed INTEGER DEFAULT 0
                    )
                """)
                conn.commit()
        except Exception:
            pass

    def require(self, action_type: str, description: str,
                target_path: str = "") -> dict:
        """Registra un'azione che richiede conferma."""
        import uuid
        conf_id = str(uuid.uuid4())
        now = datetime.utcnow().isoformat()
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "INSERT INTO pending_confirmations (id, action_type, description, target_path, created_at) "
                "VALUES (?, ?, ?, ?, ?)",
                (conf_id, action_type, description, target_path, now)
            )
            conn.commit()
        return {
            "confirmation_id": conf_id,
            "action_type": action_type,
            "description": description,
            "status": "pending",
            "message": f"[ARTICOLO 5] '{description}' richiede conferma. Usa confirm({conf_id}) per procedere."
        }

    def confirm(self, conf_id: str) -> dict:
        """Conferma un'azione."""
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                "UPDATE pending_confirmations SET confirmed=1 WHERE id=?",
                (conf_id,)
            )
            conn.commit()
        return {"confirmation_id": conf_id, "status": "confirmed"}

    def is_confirmed(self, conf_id: str) -> bool:
        """Verifica se un'azione e stata confermata."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            row = conn.execute(
                "SELECT confirmed FROM pending_confirmations WHERE id=?",
                (conf_id,)
            ).fetchone()
            return row is not None and row["confirmed"] == 1

    def list_pending(self) -> list:
        """Elenco conferme in sospeso."""
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                "SELECT * FROM pending_confirmations WHERE confirmed=0 ORDER BY created_at DESC"
            ).fetchall()
            return [dict(r) for r in rows]


# ── Constitution Enforcer (orchestratore) ─────────────────────────
class ConstitutionEnforcer:
    """Punto d'ingresso unico per la validazione costituzionale."""

    def __init__(self, authorized_dir: str = AUTHORIZED_DIR):
        self.path_validator = PathValidator(authorized_dir)
        self.tool_registry = ToolApprovalRegistry()
        self.confirmation = ConfirmationManager()

    def validate_file_operation(self, file_path: str, action: str) -> None:
        """Valida un'operazione su file secondo la costituzione.
        
        Solleva ConstitutionViolation se la viola.
        """
        # Articolo 1: path autorizzato
        self.path_validator.validate(file_path, action)

    def check_tool_creation(self, tool_id: str, name: str, file_path: str,
                            description: str = "") -> dict:
        """Verifica se un tool puo essere creato.
        
        Se approvato, restituisce ok. Se pending, lo registra.
        """
        existing = self.tool_registry.get_tool(tool_id)
        if existing and existing["status"] == "approved":
            return {"status": "approved", "id": tool_id}
        
        if existing and existing["status"] == "rejected":
            return {
                "status": "rejected",
                "id": tool_id,
                "message": f"Tool '{name}' e stato rifiutato e non puo essere creato."
            }

        # Tool nuovo o non ancora approvato
        # Validazione path — solleva ConstitutionViolation se fuori perimetro
        self.path_validator.validate(file_path, "creazione tool")
        return self.tool_registry.register_tool(tool_id, name, file_path, description)

    def check_destructive_action(self, action_type: str, description: str,
                                 target_path: str = "") -> dict:
        """Controlla se un'azione distruttiva richiede conferma."""
        # Azioni che richiedono SEMPRE conferma
        requires_confirmation = [
            "delete", "cancellazione", "remove", "rm",
            "overwrite", "sovrascrittura", "format",
            "send", "invia", "upload", "export",
            "shutdown", "reboot",
        ]
        
        needs_conf = any(a in action_type.lower() for a in requires_confirmation)
        if needs_conf:
            return self.confirmation.require(action_type, description, target_path)
        
        return {"status": "no_confirmation_needed"}


# ── CLI ────────────────────────────────────────────────────────────
def main():
    import argparse
    parser = argparse.ArgumentParser(description="Costituzione Oracle - Protocollo di autolimitazione")
    sub = parser.add_subparsers(dest="command")

    # check
    check_p = sub.add_parser("check", help="Verifica un'operazione contro la costituzione")
    check_p.add_argument("--path", help="Percorso da validare")
    check_p.add_argument("--action", default="access", help="Tipo di azione")
    check_p.add_argument("--tool-id", help="ID tool da verificare")
    check_p.add_argument("--tool-name", help="Nome del tool")
    check_p.add_argument("--tool-path", help="Percorso del tool")

    # pending
    pending_p = sub.add_parser("pending", help="Gestisci tool/azioni in attesa")
    pending_p.add_argument("--list", action="store_true", help="Lista tool pending")
    pending_p.add_argument("--confirmations", action="store_true", help="Lista conferme pending")
    pending_p.add_argument("--all-tools", action="store_true", help="Lista tutti i tool registrati")

    # approve
    approve_p = sub.add_parser("approve", help="Approva un tool")
    approve_p.add_argument("--tool-id", required=True)

    # reject
    reject_p = sub.add_parser("reject", help="Rifiuta un tool")
    reject_p.add_argument("--tool-id", required=True)

    # confirm
    confirm_p = sub.add_parser("confirm", help="Conferma un'azione")
    confirm_p.add_argument("--confirmation-id", required=True)

    args = parser.parse_args()

    if not args.command:
        parser.print_help()
        return

    enforcer = ConstitutionEnforcer()

    if args.command == "check":
        if args.path:
            try:
                enforcer.validate_file_operation(args.path, args.action)
                print(f"OK: {args.action} su '{args.path}' autorizzato.")
            except ConstitutionViolation as e:
                print(str(e))
                sys.exit(1)

        if args.tool_id:
            result = enforcer.check_tool_creation(
                args.tool_id, args.tool_name or args.tool_id,
                args.tool_path or "/dev/null"
            )
            print(json.dumps(result, indent=2))

    elif args.command == "pending":
        if args.list:
            pending = enforcer.tool_registry.get_pending()
            if pending:
                print(f"Tool in attesa di approvazione ({len(pending)}):")
                for t in pending:
                    print(f"  [{t['id']}] {t['name']} ({t['status']}) - {t['created_at']}")
            else:
                print("Nessun tool in attesa.")

        if args.confirmations:
            pending_c = enforcer.confirmation.list_pending()
            if pending_c:
                print(f"Conferme in sospeso ({len(pending_c)}):")
                for c in pending_c:
                    print(f"  [{c['id']}] {c['action_type']}: {c['description']}")
            else:
                print("Nessuna conferma in sospeso.")

        if args.all_tools:
            all_t = enforcer.tool_registry.get_all()
            if all_t:
                print(f"Tutti i tool registrati ({len(all_t)}):")
                for t in all_t:
                    status_symbol = {"approved": "OK", "pending": "?",
                                     "rejected": "X"}.get(t["status"], "?")
                    print(f"  {status_symbol} [{t['id']}] {t['name']} - {t['status']}")
            else:
                print("Nessun tool registrato.")

    elif args.command == "approve":
        result = enforcer.tool_registry.approve(args.tool_id)
        print(f"Tool '{args.tool_id}' approvato.")

    elif args.command == "reject":
        result = enforcer.tool_registry.reject(args.tool_id)
        print(f"Tool '{args.tool_id}' rifiutato.")

    elif args.command == "confirm":
        result = enforcer.confirmation.confirm(args.confirmation_id)
        print(f"Azione '{args.confirmation_id}' confermata.")


if __name__ == "__main__":
    main()