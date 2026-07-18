"""
Configurazione indipendente di Egida (4° strato di Oracle).

Tutte le variabili sono lette da environment variables con fallback
a valori predefiniti. Nessuna dipendenza da Penelope.
"""

import os


# ─── Threshold HSD ─────────────────────────────────────────────────
# Score minimo per attivare la quarantena (default: 90)
# CRITICAL=100, HIGH=90, MEDIUM=50, LOW=25, INFO=10
EGIDA_QUARANTINE_THRESHOLD = int(os.getenv("EGIDA_THRESHOLD", "90"))

# ─── NER (SpaCy) ──────────────────────────────────────────────────
# Modello SpaCy per NER (default: italiano small)
EGIDA_SPACY_MODEL = os.getenv("EGIDA_SPACY_MODEL", "it_core_news_sm")

# Soglia di confidenza minima per NER (default: 0.5)
EGIDA_NER_CONFIDENCE = float(os.getenv("EGIDA_NER_CONFIDENCE", "0.5"))

# ─── Quarantena ───────────────────────────────────────────────────
# Directory dove isolare i file HSD (default: quarantine/)
EGIDA_QUARANTINE_DIR = os.getenv("EGIDA_QUARANTINE_DIR", "quarantine")
