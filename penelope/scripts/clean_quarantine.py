#!/usr/bin/env python
"""
Pulisce la directory di quarantena dai falsi positivi usando Egida v2.0.

Per ogni entry in quarantena:
  1. Riscansiona il file con il nuovo HSDFilter (scoring v2)
  2. Se score >= soglia → aggiorna report.json (nuovo formato)
  3. Se score < soglia  → elimina la directory (falso positivo)

Usa --dry-run per vedere cosa verrebbe fatto senza modificare nulla.
"""

import argparse
import json
import logging
import os
import shutil
import sys
from datetime import datetime
from pathlib import Path
from typing import Optional

# Aggiungi i path di Penelope e Oracle (per egida)
PENELOPE_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PENELOPE_ROOT))
PROMETEO_ROOT = PENELOPE_ROOT.parent
if str(PROMETEO_ROOT) not in sys.path:
    sys.path.insert(0, str(PROMETEO_ROOT))

from egida.filters import HSDFilter, QUARANTINE_THRESHOLD, Severity

logging.basicConfig(
    level=logging.INFO,
    format="%(levelname)-7s %(message)s",
)
log = logging.getLogger("egida.cleanup")


def find_quarantined_file(entry_dir: Path) -> Optional[Path]:
    """
    Trova il file originale in quarantena (non report.json).

    La directory di quarantena contiene:
      - report.json
      - <nome file originale>  (il file messo in quarantena)
      - eventuali altri file
    """
    for f in sorted(entry_dir.iterdir()):
        if f.is_file() and f.name != "report.json":
            return f
    return None


def re_evaluate_entry(entry_dir: Path, hsd: HSDFilter, dry_run: bool) -> dict:
    """
    Rivaluta una singola entry di quarantena col nuovo filtro.

    Returns:
        dict con: action, entry_name, original_path, old_score, new_score, reason
    """
    entry_name = entry_dir.name
    report_path = entry_dir / "report.json"
    quarantined_file = find_quarantined_file(entry_dir)

    # Carica vecchio report
    old_matches = 0
    old_original = "?"
    try:
        if report_path.exists():
            with open(report_path, encoding="utf-8", errors="replace") as f:
                old = json.load(f)
            old_matches = len(old.get("matches", []))
            old_original = old.get("original_path", "?")
    except Exception:
        pass

    result = {
        "entry": entry_name,
        "original_path": old_original,
        "old_matches": old_matches,
        "new_matches": 0,
        "new_score": 0,
        "action": "ERROR",
        "reason": "",
        "severity_breakdown": {},
    }

    # Caso 1: Nessun file in quarantena
    if quarantined_file is None:
        result["action"] = "ORPHAN"
        result["reason"] = "nessun file in quarantena (solo report.json?)"
        return result

    # Caso 2: File non più esistente
    if not quarantined_file.exists():
        result["action"] = "MISSING"
        result["reason"] = f"file non trovato: {quarantined_file}"
        return result

    # Caso 3: File binario
    try:
        with open(quarantined_file, "rb") as f:
            chunk = f.read(1024)
        if b"\x00" in chunk:
            result["action"] = "BINARY"
            result["reason"] = "file binario (null byte) — non riscansionabile"
            return result
    except Exception:
        result["action"] = "UNREADABLE"
        result["reason"] = "impossibile leggere il file"
        return result

    # Caso 4: Riscansiona con Egida v2
    # Skip file enormi (> 2MB): sono quasi sicuramente dump di dati reali
    file_size = quarantined_file.stat().st_size
    if file_size > 2_000_000:
        result["action"] = "KEEP_LARGE"
        result["reason"] = f"file grande ({file_size/1e6:.1f}MB) — presunto HSD reale"
        if not dry_run:
            # Aggiorna comunque il report
            match = hsd.check_file(quarantined_file)
            result["new_matches"] = len(match.matches)
            result["new_score"] = match.score
            for m in match.matches:
                sev = m.get("severity", "?")
                result["severity_breakdown"][sev] = \
                    result["severity_breakdown"].get(sev, 0) + 1
        return result

    match = hsd.check_file(quarantined_file)
    result["new_matches"] = len(match.matches)
    result["new_score"] = match.score

    # Raccogli breakdown severity
    for m in match.matches:
        sev = m.get("severity", "?")
        result["severity_breakdown"][sev] = \
            result["severity_breakdown"].get(sev, 0) + 1

    if not match.is_infected:
        result["action"] = "CLEAN"  # Falso positivo → da rimuovere
        result["reason"] = f"score={match.score} < soglia={QUARANTINE_THRESHOLD}"
    else:
        result["action"] = "KEEP"  # Ancora infetto → mantieni
        result["reason"] = f"score={match.score} >= soglia={QUARANTINE_THRESHOLD}"

    # Aggiorna report se KEEP
    if match.is_infected and not dry_run:
        new_report = {
            "original_path": old_original,
            "quarantine_path": str(quarantined_file.absolute()),
            "timestamp": datetime.now().isoformat(),
            "rescan_timestamp": datetime.now().isoformat(),
            "old_match_count": old_matches,
            "new_match_count": result["new_matches"],
            "score": match.score,
            "threshold": QUARANTINE_THRESHOLD,
            "egida_version": "2.0",
            "matches": match.matches,
        }
        report_path.write_text(
            json.dumps(new_report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    # Elimina se CLEAN, ORPHAN, MISSING, BINARY, UNREADABLE
    if result["action"] in ("CLEAN", "ORPHAN", "MISSING", "BINARY"):
        if not dry_run:
            shutil.rmtree(entry_dir)
            log.info("RIMOSSA:  %s (%s)", entry_name, result["reason"])
        else:
            log.info("[DRY-RUN] RIMUOVEREBBE: %s (%s)", entry_name, result["reason"])

    elif result["action"] == "KEEP":
        if not dry_run:
            log.info("MANTENUTA: %s (%s)", entry_name, result["reason"])
        else:
            sev_str = ", ".join(
                f"{s}:{c}" for s, c in result["severity_breakdown"].items()
            )
            log.info(
                "[DRY-RUN] MANTERREBBE: %s (score=%d, %s)",
                entry_name, result["new_score"], sev_str,
            )

    elif result["action"] == "KEEP_LARGE":
        log.info("SALTATA:  %s (%s)", entry_name, result["reason"])

    elif result["action"] == "UNREADABLE":
        log.warning("SALTATA: %s (%s)", entry_name, result["reason"])

    return result


def main():
    parser = argparse.ArgumentParser(description="Pulisci quarantena Egida v2.0")
    parser.add_argument(
        "--quarantine-dir",
        default=str(PENELOPE_ROOT / "quarantine"),
        help="Directory di quarantena (default: ./quarantine)",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Mostra cosa verrebbe fatto senza modificare nulla",
    )
    parser.add_argument(
        "--threshold",
        type=int,
        default=QUARANTINE_THRESHOLD,
        help=f"Soglia di quarantena (default: {QUARANTINE_THRESHOLD})",
    )
    parser.add_argument(
        "--fast",
        action="store_true",
        help="Salta la riscansione dei file testo grandi (verifica solo binari/orfani)",
    )
    args = parser.parse_args()

    qd = Path(args.quarantine_dir)
    if not qd.exists():
        log.error("Directory quarantena non trovata: %s", qd)
        sys.exit(1)

    entries = sorted(
        d for d in qd.iterdir() if d.is_dir()
    )

    if not entries:
        log.info("Quarantena vuota. Niente da pulire.")
        return

    log.info("=" * 60)
    log.info("Egida v2.0 — Pulizia quarantena")
    log.info("Directory: %s", qd)
    log.info("Entry totali: %d", len(entries))
    log.info("Soglia: %d", args.threshold)
    log.info("Modalità: %s", "DRY-RUN" if args.dry_run else "ESECUZIONE REALE")
    log.info("=" * 60)

    hsd = HSDFilter()

    stats = {
        "total": len(entries),
        "clean": 0,
        "keep": 0,
        "keep_large": 0,
        "orphan": 0,
        "missing": 0,
        "binary": 0,
        "unreadable": 0,
        "old_matches_total": 0,
        "new_matches_total": 0,
    }

    for entry_dir in entries:
        result = re_evaluate_entry(entry_dir, hsd, args.dry_run)
        action = result["action"].lower()
        if action == "keep_large":
            stats["keep_large"] += 1
        elif action in stats:
            stats[action] += 1
        stats["old_matches_total"] += result["old_matches"]
        stats["new_matches_total"] += result["new_matches"]

    # ─── Riepilogo ───
    log.info("=" * 60)
    log.info("RIEPILOGO:")
    log.info("  Totali:                %d", stats["total"])
    log.info("  Da MANTENERE (HSD):    %d", stats["keep"])
    log.info("  Da MANTENERE (large):  %d", stats["keep_large"])
    log.info("  Da RIMUOVERE (FP):     %d", stats["clean"])
    log.info("  Orfani (no file):      %d", stats["orphan"])
    log.info("  File mancanti:         %d", stats["missing"])
    log.info("  Binari (skip):         %d", stats["binary"])
    log.info("  Illeggibili:           %d", stats["unreadable"])
    log.info("  ---")
    log.info(
        "  Match OLD (v1):        %d → NEW (v2): %d (riduzione %.1f%%)",
        stats["old_matches_total"],
        stats["new_matches_total"],
        (1 - stats["new_matches_total"] / max(stats["old_matches_total"], 1)) * 100,
    )

    if args.dry_run:
        log.info("\n⚠️  DRY-RUN: nessuna modifica effettiva. Rimuovi --dry-run per eseguire.")


if __name__ == "__main__":
    main()
