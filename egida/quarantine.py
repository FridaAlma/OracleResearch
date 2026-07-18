"""
Gestione della quarantena per file HSD.

Quando un file viene identificato come contenente HSD con score
superiore alla soglia, viene copiato in quarantena con un report
dei match e NON viene registrato nel grafo.

Il report include score totale, soglia e dettaglio severity.
Egida — 4° strato di Prometeo (guardrail HSD cross-layer).
"""

import json
import logging
import shutil
from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import EGIDA_QUARANTINE_DIR, EGIDA_QUARANTINE_THRESHOLD
from .filters import HSDMatch

logger = logging.getLogger(__name__)


class Quarantine:
    """
    Isola file HSD in una directory separata, fuori dal grafo.
    Solo i file con score >= soglia vengono effettivamente isolati.
    """

    def __init__(self, base_dir: str | Path | None = None):
        self.base_dir = Path(base_dir) if base_dir else Path(EGIDA_QUARANTINE_DIR)

    def isolate(
        self,
        match: HSDMatch,
        source_path: Optional[str | Path] = None,
    ) -> Optional[Path]:
        """
        Copia un file infetto in quarantena e genera un report JSON.

        Se lo score è sotto soglia, il file NON viene isolato
        (restituisce None).

        Args:
            match: risultato HSDMatch del file analizzato
            source_path: percorso originale (default: match.file_path)

        Returns:
            Path della directory di quarantena, o None se sotto soglia.
        """
        if not match.is_infected:
            logger.debug(
                "File sotto soglia (%d < %d): %s",
                match.score, EGIDA_QUARANTINE_THRESHOLD, match.file_path,
            )
            return None

        src = Path(source_path or match.file_path)
        if not src.exists():
            logger.error("File non trovato: %s", src)
            raise FileNotFoundError(f"File non trovato: {src}")

        # Crea directory di quarantena datata
        date_prefix = datetime.now().strftime("%Y%m%d_%H%M%S")
        dest_dir = self.base_dir / date_prefix
        dest_dir.mkdir(parents=True, exist_ok=True)

        # Copia il file
        dest_file = dest_dir / src.name
        try:
            shutil.copy2(src, dest_file)
            logger.info("Copiato in quarantena: %s → %s", src, dest_file)
        except Exception as e:
            logger.error("Errore copia quarantena %s: %s", src, e)
            raise

        # Genera report JSON arricchito
        report = {
            "original_path": str(src.absolute()),
            "quarantine_path": str(dest_file.absolute()),
            "timestamp": datetime.now().isoformat(),
            "match_count": len(match.matches),
            "score": match.score,
            "threshold": EGIDA_QUARANTINE_THRESHOLD,
            "matches": match.matches,
        }
        report_path = dest_dir / "report.json"
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        logger.info("Report quarantena: %s", report_path)

        return dest_dir

    def list_quarantine(self) -> list[dict]:
        """Elenca tutti i file in quarantena con i loro report."""
        entries = []
        if not self.base_dir.exists():
            return entries

        for entry in sorted(self.base_dir.iterdir()):
            if entry.is_dir():
                report_path = entry / "report.json"
                if report_path.exists():
                    try:
                        report = json.loads(report_path.read_text("utf-8"))
                        entries.append(report)
                    except Exception:
                        entries.append({
                            "quarantine_path": str(entry),
                            "timestamp": entry.name,
                            "error": "report non leggibile",
                        })
                else:
                    entries.append({
                        "quarantine_path": str(entry),
                        "timestamp": entry.name,
                        "match_count": 0,
                        "matches": [],
                    })
        return entries

    def clear(self) -> int:
        """Svuota la quarantena. Restituisce il numero di entry rimosse."""
        count = 0
        if self.base_dir.exists():
            for entry in self.base_dir.iterdir():
                if entry.is_dir():
                    shutil.rmtree(entry)
                    count += 1
            logger.info("Quarantena svuotata: %d entry rimosse", count)
        return count
