"""Identity resolution — riconoscimento facciale e matching.

Usa InsightFace (ArcFace 512-dim) per:
- Rilevamento volti in foto (RetinaFace)
- Calcolo embedding
- Matching tra volti (stessa persona?)
- Clustering di volti simili
"""

from archimede.identity.face_engine import detect_faces, cosine_similarity, verify
from archimede.identity.matcher import load_reference_faces, match_photo, search_couple_photos

__all__ = [
    "detect_faces",
    "cosine_similarity",
    "verify",
    "load_reference_faces",
    "match_photo",
    "search_couple_photos",
]
