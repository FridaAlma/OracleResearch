"""
Test per Archimede FaceEngine — InsightFace detection e matching.
"""

import os
import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest


@pytest.fixture
def temp_image():
    """Crea un'immagine finta."""
    img_dir = Path(tempfile.mkdtemp())
    img_path = img_dir / "test_face.jpg"
    import cv2
    dummy_img = np.zeros((100, 100, 3), dtype=np.uint8)
    cv2.imwrite(str(img_path), dummy_img)
    yield str(img_path)
    import shutil
    shutil.rmtree(str(img_dir), ignore_errors=True)


class TestFaceEngine:
    """Test per il motore InsightFace di Archimede."""

    def test_get_analyzer_no_insightface(self):
        """get_analyzer deve restituire None se insightface non installato."""
        from archimede.identity.face_engine import get_analyzer

        with patch.dict("sys.modules", {"insightface": None, "insightface.app": None}):
            result = get_analyzer()
            assert result is None

    def test_detect_faces_file_not_found(self):
        """detect_faces su file inesistente → []."""
        from archimede.identity.face_engine import detect_faces

        result = detect_faces("/nonexistent/path.jpg")
        assert result == []

    def test_detect_faces_no_analyzer(self, temp_image):
        """detect_faces senza analyzer → []."""
        from archimede.identity.face_engine import detect_faces

        with patch("archimede.identity.face_engine.get_analyzer", return_value=None):
            result = detect_faces(temp_image)
            assert result == []

    def test_cosine_similarity_identical(self):
        """Due embedding identici → 1.0."""
        from archimede.identity.face_engine import cosine_similarity

        emb = [1.0, 0.0, 0.0, 0.0]
        sim = cosine_similarity(emb, emb)
        assert abs(sim - 1.0) < 0.001

    def test_cosine_similarity_orthogonal(self):
        """Due embedding ortogonali → ~0.0."""
        from archimede.identity.face_engine import cosine_similarity

        emb_a = [1.0, 0.0, 0.0, 0.0]
        emb_b = [0.0, 1.0, 0.0, 0.0]
        sim = cosine_similarity(emb_a, emb_b)
        assert abs(sim) < 0.001

    def test_cosine_similarity_numpy(self):
        """cosine_similarity accetta np.array."""
        from archimede.identity.face_engine import cosine_similarity

        a = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        b = np.array([1.0, 0.0, 0.0, 0.0], dtype=np.float32)
        sim = cosine_similarity(a, b)
        assert abs(sim - 1.0) < 0.001

    def test_verify_match(self):
        """verify deve riconoscere embedding identici."""
        from archimede.identity.face_engine import verify

        emb = [1.0 / np.sqrt(4)] * 4  # normalizzato
        result = verify(emb, emb, threshold=0.35)
        assert result["is_match"]
        assert result["similarity"] > 0.99

    def test_verify_no_match(self):
        """verify deve rifiutare embedding diversi."""
        from archimede.identity.face_engine import verify

        emb1 = [1.0, 0.0, 0.0, 0.0]
        emb2 = [0.0, 1.0, 0.0, 0.0]
        result = verify(emb1, emb2, threshold=0.9)
        assert not result["is_match"]

    def test_verify_output_format(self):
        """verify deve restituire dict con tutti i campi."""
        from archimede.identity.face_engine import verify

        emb = [1.0, 0.0, 0.0, 0.0]
        result = verify(emb, emb, threshold=0.5)
        assert "is_match" in result
        assert "similarity" in result
        assert "distance" in result
        assert "threshold" in result
