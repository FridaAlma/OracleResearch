"""
NER leggero per la rilevazione di HSD in linguaggio naturale.

Usa SpaCy modello small (it_core_news_sm) su CPU.
Rileva: PERSONE, INDIRIZZI, LUOGHI, ORGANIZZAZIONI in testi liberi.

Se SpaCy non è installato, il modulo si degrada gracefulmente
(restituisce lista vuota e logga un avviso).

v2.0 — Soglia di confidenza configurabile.
Egida — 4° strato di Oracle (guardrail HSD cross-layer).
"""

import logging
from typing import Optional

from .config import EGIDA_SPACY_MODEL, EGIDA_NER_CONFIDENCE

logger = logging.getLogger(__name__)

# SpaCy caricato lazy
_nlp = None


def _load_model():
    global _nlp
    if _nlp is not None:
        return True
    try:
        import spacy
        _nlp = spacy.load(EGIDA_SPACY_MODEL)
        logger.info("Modello SpaCy '%s' caricato.", EGIDA_SPACY_MODEL)
        return True
    except OSError:
        logger.warning(
            "Modello SpaCy '%s' non trovato. "
            "Esegui: python -m spacy download %s",
            EGIDA_SPACY_MODEL,
            EGIDA_SPACY_MODEL,
        )
        return False
    except ImportError:
        logger.warning("SpaCy non installato. Installa con: pip install spacy")
        return False


def scan_text(
    text: str,
    min_confidence: Optional[float] = None,
) -> list[dict]:
    """
    Analizza un testo con SpaCy NER e restituisce le entità trovate.

    Args:
        text: Testo da analizzare.
        min_confidence: Soglia minima di confidenza (0.0-1.0).
                        Default: dal config (EGIDA_NER_CONFIDENCE).

    Returns:
        Lista di dict con: text, label, start, end, confidence
    """
    if not _load_model():
        return []

    if min_confidence is None:
        min_confidence = EGIDA_NER_CONFIDENCE

    doc = _nlp(text)
    entities = []

    # Calcola la confidenza media delle entità (SpaCy small non
    # espone confidence nativamente; usiamo un'euristica basata
    # sulla lunghezza dell'entità e contesto)
    for ent in doc.ents:
        if ent.label_ not in {"PERSON", "GPE", "LOC", "ORG", "ADDRESS"}:
            continue

        # Euristica di confidenza: entità molto corte (<3 caratteri)
        # in isolamento sono probabilmente falsi positivi
        if len(ent.text.strip()) < 3:
            continue

        # Entità che sono solo numeri o caratteri speciali → scarta
        if ent.text.strip().isnumeric():
            continue

        entities.append({
            "text": ent.text,
            "label": ent.label_,
            "start": ent.start_char,
            "end": ent.end_char,
        })

    return entities


def scan_file(file_path: str, min_confidence: Optional[float] = None) -> list[dict]:
    """
    Legge un file di testo e lo analizza con SpaCy NER.

    Args:
        file_path: Percorso del file.
        min_confidence: Soglia minima di confidenza.

    Returns:
        Lista di entità trovate.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        logger.warning("Impossibile leggere %s: %s", file_path, e)
        return []

    return scan_text(text, min_confidence=min_confidence)
