#!/usr/bin/env python3
"""
Oracle RUI Edition — Avvio unificato del sistema.

Un solo comando per avviare l'intero ecosistema Oracle RUI:

  python run.py

Componenti:
  * Oracle Core (agente esecutivo + frontend)  — :8100
  * Penelope (grafo della conoscenza)          — :5000  (opzionale)
  * Archimede (motore dati grafo)              — :8001  (opzionale)
  * Egida (guardrail HSD)                      — integrato in tutti i layer

Usage:
    python run.py                              # Avvia Oracle Core (default)
    python run.py --with-penelope              # Avvia anche Penelope (:5000)
    python run.py --with-archimede             # Avvia anche Archimede (:8001)
    python run.py --all                        # Avvia TUTTO
    python run.py --port 8100                  # Porta personalizzata
    python run.py --status                     # Verifica stato componenti
    python run.py --init                       # Primo avvio: setup guidato

Configurazione:
    1. Copia oracle-rui/.env.example in oracle-rui/.env e configura le API key
    2. Per Penelope: copia penelope/.env.example in penelope/.env
    3. Per Archimede: copia archimede/.env.example in archimede/.env
    4. (Opzionale) docker-compose up -d per avviare MariaDB
"""

from __future__ import annotations

import argparse
import logging
import signal
import subprocess
import sys
import time
from pathlib import Path

# ── Path setup ──────────────────────────────────────────────────
_ROOT = Path(__file__).resolve().parent
_ORACLE_ROOT = _ROOT / "oracle-rui"
_ARCHIMEDE_ROOT = _ROOT / "archimede"
_PENELOPE_ROOT = _ROOT / "penelope"
_EGIDA_ROOT = _ROOT / "egida"

for _p in (str(_ROOT), str(_ORACLE_ROOT), str(_ARCHIMEDE_ROOT),
           str(_PENELOPE_ROOT), str(_EGIDA_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, _p)

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(name)s] %(message)s",
    datefmt="%H:%M:%S",
)
logger = logging.getLogger("oracle-rui")


def wait_for_server(url: str, timeout: int = 15, interval: float = 0.5) -> bool:
    """Aspetta che un server risponda su un URL."""
    import httpx
    start = time.time()
    while time.time() - start < timeout:
        try:
            r = httpx.get(url, timeout=2.0)
            if r.status_code < 500:
                return True
        except (httpx.ConnectError, httpx.TimeoutException, httpx.RequestError):
            pass
        time.sleep(interval)
    return False


def start_penelope() -> subprocess.Popen | None:
    """Avvia Penelope Web API (:5000) come sottoprocesso."""
    script = _PENELOPE_ROOT / "web" / "api.py"
    if not script.exists():
        logger.warning("[WARN] Penelope API non trovata: %s", script)
        return None

    log_file = _ROOT / "logs" / "penelope.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Avvio Penelope su :5000...")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script)],
            cwd=str(_PENELOPE_ROOT),
            stdout=open(log_file, "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        )
        if wait_for_server("http://127.0.0.1:5000/api/stats", timeout=30):
            logger.info("[OK] Penelope pronto su http://localhost:5000")
        else:
            logger.warning("[WARN] Penelope avviato ma non risponde (log: %s)", log_file)
        return proc
    except Exception as e:
        logger.error("[ERR] Errore avvio Penelope: %s", e)
        return None


def start_archimede() -> subprocess.Popen | None:
    """Avvia Archimede API (:8001) come sottoprocesso."""
    script = _ARCHIMEDE_ROOT / "archimede" / "api.py"
    if not script.exists():
        logger.warning("[WARN] Archimede API non trovata: %s", script)
        return None

    log_file = _ROOT / "logs" / "archimede.log"
    log_file.parent.mkdir(parents=True, exist_ok=True)

    logger.info("Avvio Archimede su :8001...")
    try:
        proc = subprocess.Popen(
            [sys.executable, str(script), "--port", "8001"],
            cwd=str(_ARCHIMEDE_ROOT),
            stdout=open(log_file, "w", encoding="utf-8"),
            stderr=subprocess.STDOUT,
        )
        if wait_for_server("http://127.0.0.1:8001/archimede/health", timeout=20):
            logger.info("[OK] Archimede pronto su :8001")
        else:
            logger.warning("[WARN] Archimede avviato ma non risponde (log: %s)", log_file)
        return proc
    except Exception as e:
        logger.error("[ERR] Errore avvio Archimede: %s", e)
        return None


def print_banner(args: argparse.Namespace):
    """Stampa il banner di avvio."""
    print()
    print("  +--------------------------------------------------+")
    print("  |         O R A C L E   R U I   E d i t i o n      |")
    print("  |     Ricerca * Unione * Intelligenza               |")
    print("  +--------------------------------------------------+")
    print(f"  |  Porta:            {args.port:<31} |")
    print(f"  |  Oracle Core:      http://localhost:{args.port:<5}               |")
    print(f"  |  Penelope:         {'attivo su :5000' if args.with_penelope else 'non avviato':<31} |")
    print(f"  |  Archimede:        {'attivo su :8001' if args.with_archimede else 'non avviato':<31} |")
    print(f"  |  Egida:            sempre attivo (guardrail HSD)  |")
    print("  +--------------------------------------------------+")
    print()


def check_status():
    """Verifica lo stato di tutti i componenti."""
    import httpx

    print()
    W = 52
    sep = "  +-" + "-" * W + "+"
    print(sep)
    print("  |  " + "Oracle RUI Edition — Diagnostica".center(W) + "  |")
    print(sep)

    def line(label, ok, text=""):
        status = "[OK]" if ok else "[OFF]"
        content = f"  |  {label:<12} {status}"
        if text:
            content += "  " + text
        content = content.ljust(W + 6) + "|"
        print(content)

    # Oracle :8100
    try:
        r = httpx.get("http://127.0.0.1:8100/api/health", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            line("Oracle", True, f":8100 (v{data.get('version', '?')})")
        else:
            line("Oracle", False, f":8100 status={r.status_code}")
    except Exception:
        line("Oracle", False, ":8100 — NON in esecuzione")

    # Penelope :5000
    try:
        r = httpx.get("http://127.0.0.1:5000/api/stats", timeout=3.0)
        if r.status_code == 200:
            data = r.json()
            line("Penelope", True, f":5000 ({data.get('total_nodes', '?')} nodi)")
        else:
            line("Penelope", False, f":5000 status={r.status_code}")
    except Exception:
        line("Penelope", False, ":5000 — NON in esecuzione")

    # Archimede :8001
    try:
        r = httpx.get("http://127.0.0.1:8001/archimede/health", timeout=2.0)
        if r.status_code == 200:
            line("Archimede", True, ":8001 (grafo read-only)")
        else:
            line("Archimede", False, f":8001 status={r.status_code}")
    except Exception:
        line("Archimede", False, ":8001 — NON in esecuzione")

    print(sep)
    print()
    print("Suggerimenti:")
    print("  * Avvio base:                 python run.py")
    print("  * Con Penelope:               python run.py --with-penelope")
    print("  * Con tutto:                  python run.py --all")
    print("  * Stato dettagliato:          curl http://localhost:8100/api/health")
    print()


def cmd_init():
    """Setup guidato primo avvio."""
    print()
    print("=" * 60)
    print("Oracle RUI Edition — Setup guidato")
    print("=" * 60)
    print()

    # 1. Oracle .env
    oracle_env = _ORACLE_ROOT / ".env"
    oracle_env_example = _ORACLE_ROOT / ".env.example"
    if not oracle_env.exists() and oracle_env_example.exists():
        import shutil
        shutil.copy2(oracle_env_example, oracle_env)
        print("[OK] Creato oracle-rui/.env da template")
        print("     Modificalo per inserire la tua API key LLM:")
        print(f"     {oracle_env}")
    elif oracle_env.exists():
        print("[OK] oracle-rui/.env gia' presente")

    # 2. Penelope .env
    penelope_env = _PENELOPE_ROOT / ".env"
    penelope_env_example = _PENELOPE_ROOT / ".env.example"
    if not penelope_env.exists() and penelope_env_example.exists():
        import shutil
        shutil.copy2(penelope_env_example, penelope_env)
        print("[OK] Creato penelope/.env da template")
    elif penelope_env.exists():
        print("[OK] penelope/.env gia' presente")

    # 3. Archimede .env
    archimede_env = _ARCHIMEDE_ROOT / ".env"
    archimede_env_example = _ARCHIMEDE_ROOT / ".env.example"
    if not archimede_env.exists() and archimede_env_example.exists():
        import shutil
        shutil.copy2(archimede_env_example, archimede_env)
        print("[OK] Creato archimede/.env da template")
    elif archimede_env.exists():
        print("[OK] archimede/.env gia' presente")

    # 4. Logs directory
    (_ROOT / "logs").mkdir(parents=True, exist_ok=True)
    print("[OK] Directory logs/ creata")

    print()
    print("Setup completato! Ora configura i file .env e poi avvia:")
    print("  python run.py --all")
    print()


def main():
    parser = argparse.ArgumentParser(
        description="Oracle RUI Edition — Avvio unificato"
    )
    parser.add_argument("--port", type=int, default=8100,
                        help="Porta per Oracle Core (default: 8100)")
    parser.add_argument("--host", type=str, default="0.0.0.0",
                        help="Host per Oracle Core")
    parser.add_argument("--with-penelope", action="store_true",
                        help="Avvia anche Penelope API (:5000)")
    parser.add_argument("--with-archimede", action="store_true",
                        help="Avvia anche Archimede API (:8001)")
    parser.add_argument("--all", action="store_true",
                        help="Avvia TUTTI i componenti")
    parser.add_argument("--status", "--check", action="store_true",
                        help="Verifica stato componenti (senza avviare)")
    parser.add_argument("--init", action="store_true",
                        help="Setup guidato primo avvio")
    args = parser.parse_args()

    # ── Setup guidato ─────────────────────────────────────────
    if args.init:
        cmd_init()
        return

    # ── Diagnostica ───────────────────────────────────────────
    if args.status:
        check_status()
        return

    # ── --all attiva tutto ────────────────────────────────────
    if args.all:
        args.with_penelope = True
        args.with_archimede = True

    penelope_proc: subprocess.Popen | None = None
    archimede_proc: subprocess.Popen | None = None

    # ── 1. Penelope (opzionale) ──────────────────────────────
    if args.with_penelope:
        penelope_proc = start_penelope()

    # ── 2. Archimede (opzionale) ─────────────────────────────
    if args.with_archimede:
        archimede_proc = start_archimede()

    # ── 3. Avvia Oracle Core ─────────────────────────────────
    print_banner(args)

    try:
        import os
        os.environ["ORACLE_PORT"] = str(args.port)
        os.environ["ORACLE_HOST"] = args.host

        from coding_agent import app
        import uvicorn

        uvicorn.run(app, host=args.host, port=args.port, log_level="info")
    except KeyboardInterrupt:
        print("\n[Oracle RUI] Arresto in corso...")
    finally:
        # Cleanup Penelope
        if penelope_proc:
            logger.info("Arresto Penelope (PID: %d)...", penelope_proc.pid)
            if sys.platform == "win32":
                penelope_proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                penelope_proc.terminate()
            try:
                penelope_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                penelope_proc.kill()
            logger.info("Penelope arrestato.")

        # Cleanup Archimede
        if archimede_proc:
            logger.info("Arresto Archimede (PID: %d)...", archimede_proc.pid)
            if sys.platform == "win32":
                archimede_proc.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                archimede_proc.terminate()
            try:
                archimede_proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                archimede_proc.kill()
            logger.info("Archimede arrestato.")

    print("[Oracle RUI] Arrivederci.")


if __name__ == "__main__":
    main()