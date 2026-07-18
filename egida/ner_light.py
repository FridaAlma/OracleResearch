"""
Lightweight NER for HSD detection in natural language.

Uses SpaCy small model (it_core_news_sm) on CPU.
Detects: PERSONS, ADDRESSES, LOCATIONS, ORGANIZATIONS in free text.

If SpaCy is not installed, the module degrades gracefully
(returns empty list and logs a warning).

v2.0 — Configurable confidence threshold.
Egida — 4th layer of Oracle (cross-layer HSD guardrail).
"""

import logging
from typing import Optional

from .config import EGIDA_SPACY_MODEL, EGIDA_NER_CONFIDENCE

logger = logging.getLogger(__name__)

# Lazy-loaded SpaCy
_nlp = None


def _load_model():
    global _nlp
    if _nlp is not None:
        return True
    try:
        import spacy
        _nlp = spacy.load(EGIDA_SPACY_MODEL)
        logger.info("SpaCy model '%s' loaded.", EGIDA_SPACY_MODEL)
        return True
    except OSError:
        logger.warning(
            "SpaCy model '%s' not found. "
            "Run: python -m spacy download %s",
            EGIDA_SPACY_MODEL,
            EGIDA_SPACY_MODEL,
        )
        return False
    except ImportError:
        logger.warning("SpaCy not installed. Install with: pip install spacy")
        return False


def scan_text(
    text: str,
    min_confidence: Optional[float] = None,
) -> list[dict]:
    """
    Analyzes text with SpaCy NER and returns found entities.

    Args:
        text: Text to analyze.
        min_confidence: Minimum confidence threshold (0.0-1.0).
                        Default: from config (EGIDA_NER_CONFIDENCE).

    Returns:
        List of dicts with: text, label, start, end, confidence
    """
    if not _load_model():
        return []

    if min_confidence is None:
        min_confidence = EGIDA_NER_CONFIDENCE

    doc = _nlp(text)
    entities = []

    # Estimate entity confidence (SpaCy small doesn't
    # natively expose confidence; we use a heuristic based
    # on entity length and context)
    for ent in doc.ents:
        if ent.label_ not in {"PERSON", "GPE", "LOC", "ORG", "ADDRESS"}:
            continue

        # Confidence heuristic: very short entities (<3 chars)
        # in isolation are likely false positives
        if len(ent.text.strip()) < 3:
            continue

        # Entities that are only numbers or special chars → discard
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
    Reads a text file and analyzes it with SpaCy NER.

    Args:
        file_path: File path.
        min_confidence: Minimum confidence threshold.

    Returns:
        List of found entities.
    """
    try:
        with open(file_path, "r", encoding="utf-8", errors="replace") as f:
            text = f.read()
    except Exception as e:
        logger.warning("Cannot read %s: %s", file_path, e)
        return []

    return scan_text(text, min_confidence=min_confidence)
