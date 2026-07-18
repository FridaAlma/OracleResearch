"""
Independent configuration for Egida (4th layer of Oracle).

All variables are read from environment variables with fallback
to default values. No dependency on Penelope.
"""

import os


# ─── HSD Threshold ─────────────────────────────────────────────────
# Minimum score to trigger quarantine (default: 90)
# CRITICAL=100, HIGH=90, MEDIUM=50, LOW=25, INFO=10
EGIDA_QUARANTINE_THRESHOLD = int(os.getenv("EGIDA_THRESHOLD", "90"))

# ─── NER (SpaCy) ──────────────────────────────────────────────────
# SpaCy model for NER (default: Italian small)
EGIDA_SPACY_MODEL = os.getenv("EGIDA_SPACY_MODEL", "it_core_news_sm")

# Minimum confidence threshold for NER (default: 0.5)
EGIDA_NER_CONFIDENCE = float(os.getenv("EGIDA_NER_CONFIDENCE", "0.5"))

# ─── Quarantine ───────────────────────────────────────────────────
# Directory to isolate HSD files (default: quarantine/)
EGIDA_QUARANTINE_DIR = os.getenv("EGIDA_QUARANTINE_DIR", "quarantine")
