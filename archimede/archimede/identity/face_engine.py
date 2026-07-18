"""Motore di riconoscimento facciale basato su InsightFace (ArcFace 512-dim).

Funziona interamente su CPU. Modello: buffalo_l (30MB, detection + recognition + age/gender).
"""

from __future__ import annotations

import logging
import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

logger = logging.getLogger(__name__)

# Cache del modello (lazy loading)
_face_analyzer = None


def get_analyzer():
    """Carica InsightFace lazy (buffalo_l, CPU)."""
    global _face_analyzer
    if _face_analyzer is not None:
        return _face_analyzer
    try:
        from insightface.app import FaceAnalysis

        app = FaceAnalysis(
            name="buffalo_l",
            providers=["CPUExecutionProvider"],
        )
        app.prepare(ctx_id=0, det_size=(320, 320))
        _face_analyzer = app
        logger.info("InsightFace caricato (buffalo_l, CPU)")
        return app
    except Exception as e:
        logger.error("Errore caricamento InsightFace: %s", e)
        return None


def detect_faces(image_path: str) -> list[dict]:
    """Rileva volti in un'immagine.

    Args:
        image_path: Path dell'immagine.

    Returns:
        Lista di dict, uno per volto:
            - bbox: [x1, y1, x2, y2]
            - det_score: float
            - embedding: list[float] 512-dim | None
            - gender: int (0=F, 1=M) | None
            - age: float | None
            - landmark: list | None
    """
    app = get_analyzer()
    if app is None:
        return []

    if not os.path.isfile(image_path):
        logger.debug("File non trovato: %s", image_path)
        return []

    img = cv2.imread(image_path)
    if img is None:
        logger.debug("Impossibile leggere: %s", image_path)
        return []

    img_rgb = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)

    try:
        faces = app.get(img_rgb)
    except Exception as e:
        logger.warning("Errore detection su %s: %s", image_path, e)
        return []

    results = []
    for face in faces:
        bbox = face.bbox.astype(int).tolist() if hasattr(face, "bbox") else None
        det_score = float(face.det_score) if hasattr(face, "det_score") else 1.0
        gender = int(face.gender) if hasattr(face, "gender") else None
        age = float(face.age) if hasattr(face, "age") else None
        landmark = face.landmark.tolist() if hasattr(face, "landmark") and face.landmark is not None else None
        embedding = face.normed_embedding.tolist() if hasattr(face, "normed_embedding") and face.normed_embedding is not None else None

        results.append({
            "bbox": bbox,
            "det_score": det_score,
            "gender": gender,
            "age": age,
            "landmark": landmark,
            "embedding": embedding,
        })

    return results


def cosine_similarity(a: list[float] | np.ndarray, b: list[float] | np.ndarray) -> float:
    """Similarità coseno tra due embedding."""
    a = np.array(a, dtype=np.float32).flatten()
    b = np.array(b, dtype=np.float32).flatten()
    return float(np.dot(a, b) / (np.linalg.norm(a) * np.linalg.norm(b) + 1e-10))


def verify(emb1: list[float], emb2: list[float], threshold: float = 0.35) -> dict:
    """Confronta due embedding.

    Soglia tipica per ArcFace 512-dim: 0.35 (più bassa = più tollerante).

    Returns:
        {"is_match": bool, "similarity": float, "distance": float, "threshold": float}
    """
    sim = cosine_similarity(emb1, emb2)
    return {
        "is_match": sim > threshold,
        "similarity": round(sim, 4),
        "distance": round(1.0 - sim, 4),
        "threshold": threshold,
    }
