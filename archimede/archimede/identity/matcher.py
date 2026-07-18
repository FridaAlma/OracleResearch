"""Face matching: confronta volti in foto con referenze dei genitori.

Flusso:
  1. Carica foto di referenza per ogni genitore (es. papa/, mamma/)
  2. Calcola embedding medio per ogni genitore
  3. Per ogni foto nel database:
     a. Rileva volti con InsightFace
     b. Per ogni volto, confronta embedding con ogni referenza
     c. Se similarità > soglia → match
  4. Trova foto dove ENTRAMBI i genitori appaiono insieme
"""

from __future__ import annotations

import logging
import os
import time
from pathlib import Path
from typing import Optional

import numpy as np

from archimede.identity.face_engine import detect_faces, cosine_similarity
from archimede.models import (
    DetectedFace,
    FaceMatch,
    Photo,
    PhotoMatchResult,
    ReferenceFace,
    SearchReport,
)

logger = logging.getLogger(__name__)

# Soglia default per ArcFace 512-dim (più bassa = più falsi positivi ma meno falsi negativi)
DEFAULT_THRESHOLD = 0.35


def load_reference_faces(ref_dir: str | Path) -> list[ReferenceFace]:
    """Carica foto di referenza da una directory strutturata.

    Struttura attesa:
        ref_dir/
            papa/
                foto1.jpg
                foto2.jpg  (opzionale, più foto = embedding medio più robusto)
            mamma/
                foto1.jpg
                ...

    Returns:
        Lista di ReferenceFace, una per sottocartella.
    """
    ref_dir = Path(ref_dir)
    if not ref_dir.exists():
        logger.error("Directory referenza non trovata: %s", ref_dir)
        return []

    references = []
    for person_dir in sorted(ref_dir.iterdir()):
        if not person_dir.is_dir():
            continue
        person_name = person_dir.name
        embeddings = []
        source_photos = []

        for img_file in sorted(person_dir.iterdir()):
            if img_file.suffix.lower() not in (".jpg", ".jpeg", ".png", ".webp"):
                continue
            faces = detect_faces(str(img_file))
            if not faces:
                logger.warning("Nessun volto in %s", img_file)
                continue
            emb = faces[0].get("embedding")
            if emb:
                embeddings.append(np.array(emb, dtype=np.float32))
                source_photos.append(str(img_file))
                logger.info("  %s: volto da %s", person_name, img_file.name)

        if not embeddings:
            logger.warning("Nessun volto valido per %s, saltato", person_name)
            continue

        # Media degli embedding per robustezza
        avg_emb = np.mean(embeddings, axis=0)
        avg_emb = avg_emb / (np.linalg.norm(avg_emb) + 1e-10)

        references.append(ReferenceFace(
            name=person_name,
            embedding=avg_emb.tolist(),
            source_photos=source_photos,
        ))
        logger.info("  -> %s: %d volti, embedding creato", person_name, len(embeddings))

    return references


def match_photo(
    photo: Photo,
    references: list[ReferenceFace],
    threshold: float = DEFAULT_THRESHOLD,
) -> PhotoMatchResult:
    """Analizza una singola foto e la confronta con le referenze.

    Args:
        photo: Foto da analizzare.
        references: Referenze dei genitori.
        threshold: Soglia similarità coseno.

    Returns:
        PhotoMatchResult con matches e dettagli.
    """
    # Rileva volti
    raw_faces = detect_faces(photo.file_path)
    faces = []
    for rf in raw_faces:
        faces.append(DetectedFace(
            photo_path=photo.file_path,
            photo_node_id=photo.node_id,
            bbox=rf.get("bbox", [0, 0, 0, 0]),
            confidence=rf.get("det_score", 0.0),
            embedding=rf.get("embedding"),
            gender=rf.get("gender"),
            age=rf.get("age"),
        ))

    photo.face_count = len(faces)

    # Per ogni referenza, controlla se matcha almeno un volto
    matches: dict[str, bool] = {ref.name: False for ref in references}
    match_details: list[FaceMatch] = []

    for face in faces:
        if face.embedding is None:
            continue

        for ref in references:
            if matches[ref.name]:
                continue  # già trovato per questa referenza

            sim = cosine_similarity(face.embedding, ref.embedding)
            is_match = sim > threshold

            match_details.append(FaceMatch(
                reference_name=ref.name,
                similarity=round(sim, 4),
                is_match=is_match,
                photo_path=photo.file_path,
                bbox=face.bbox,
            ))

            if is_match:
                matches[ref.name] = True
                logger.debug("Match %s in %s (sim=%.3f)", ref.name, photo.file_name, sim)

    is_couple = all(matches.values()) if matches else False

    return PhotoMatchResult(
        photo=photo,
        faces=faces,
        matches=matches,
        match_details=match_details,
        is_couple=is_couple,
    )


def search_couple_photos(
    photos: list[Photo],
    references: list[ReferenceFace],
    threshold: float = DEFAULT_THRESHOLD,
    batch_callback=None,
) -> SearchReport:
    """Cerca foto di coppia in una lista di foto.

    Args:
        photos: Lista di foto da scandire.
        references: Referenze dei genitori.
        threshold: Soglia similarità.
        batch_callback: Callable(batch_index, total, partial_report) per progressi.

    Returns:
        SearchReport con tutti i risultati.
    """
    t_start = time.time()
    ref_names = [ref.name for ref in references]

    all_results: list[PhotoMatchResult] = []
    total = len(photos)

    for i, photo in enumerate(photos, 1):
        result = match_photo(photo, references, threshold)
        all_results.append(result)

        if batch_callback and (i % 50 == 0 or i == total):
            couple_count = sum(1 for r in all_results if r.is_couple)
            batch_callback(i, total, couple_count)

    couple_photos = [r for r in all_results if r.is_couple]

    # Raggruppa per singolo genitore
    single_parent: dict[str, list[PhotoMatchResult]] = {}
    for name in ref_names:
        single_parent[name] = [
            r for r in all_results
            if r.matches.get(name) and not r.is_couple
        ]

    report = SearchReport(
        query_name="Ricerca foto genitori",
        reference_names=ref_names,
        similarity_threshold=threshold,
        photos_scanned=total,
        photos_with_faces=sum(1 for r in all_results if r.photo.face_count > 0),
        couple_photos=couple_photos,
        single_parent_photos=single_parent,
        all_results=all_results,
        duration_seconds=round(time.time() - t_start, 1),
        generated_at=time.strftime("%Y-%m-%d %H:%M:%S"),
    )

    logger.info("=" * 50)
    logger.info("RICERCA COMPLETATA")
    logger.info("  Foto scansionate:     %d", total)
    logger.info("  Foto con volti:       %d", report.photos_with_faces)
    logger.info("  Foto di coppia:       %d", report.couple_count)
    logger.info("  Durata:               %.1fs", report.duration_seconds)
    logger.info("=" * 50)

    return report
