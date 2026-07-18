"""
Egida — HSD (Highly Sensitive Data) Guardrail — 4th layer of Oracle.

Acts upstream of ALL layers (Penelope, Archimede, Oracle):
if a file contains HSD with score >= threshold (default 90), it is
isolated in quarantine and does NOT enter the graph.

Features v2.0:
  - Scoring/severity system (CRITICAL=100, HIGH=90, MEDIUM=50, LOW=25, INFO=10)
  - Magic byte detection for binary files (not just extension)
  - JWT validation (decodable JSON header)
  - Dummy email domain whitelist (example.com, test.com, ...)
  - Placeholder detection for passwords (type hints, variable names, CI defaults)
  - Postal code with Italian validation range (00100-98199) and anti-decimal
  - Phone with UUID exclusion and mandatory separators
  - Contextual exclusions for lines with UUID/file-id
  - Configurable quarantine threshold via EGIDA_THRESHOLD
  - NER with configurable confidence threshold via EGIDA_NER_CONFIDENCE
  - Independent from Penelope — importable from any layer
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
