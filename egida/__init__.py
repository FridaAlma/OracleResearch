"""
Egida — Guardrail HSD (Highly Sensitive Data) — 4° strato di Oracle.

Agisce a monte di TUTTI gli strati (Penelope, Archimede, Oracle):
se un file contiene HSD con score >= soglia (default 90), viene
isolato in quarantena e NON entra nel grafo.

Caratteristiche v2.0:
  - Sistema di scoring/severity (CRITICAL=100, HIGH=90, MEDIUM=50, LOW=25, INFO=10)
  - Magic byte detection per file binari (non solo estensione)
  - Validazione JWT (header JSON decodificabile)
  - Whitelist domini email fittizi (example.com, test.com, ...)
  - Placeholder detection per password (type hint, nomi variabile, CI defaults)
  - CAP con range validazione italiana (00100-98199) e anti-decimale
  - Telefono con esclusione UUID e separatori obbligatori
  - Esclusioni contestuali per righe con UUID/file-id
  - Soglia quarantena configurabile via EGIDA_THRESHOLD
  - NER con soglia di confidenza configurabile via EGIDA_NER_CONFIDENCE
  - Indipendente da Penelope — importabile da qualsiasi strato
"""

from .filters import HSDMatch, HSDFilter, Severity, quick_scan
from .ner_light import scan_text, scan_file
from .quarantine import Quarantine

__all__ = [
    "HSDMatch",
    "HSDFilter",
    "Severity",
    "quick_scan",
    "scan_text",
    "scan_file",
    "Quarantine",
]

__version__ = "2.0.0"
