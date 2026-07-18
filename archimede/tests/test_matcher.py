"""
Test per FaceMatcher — matching di volti con referenze.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import numpy as np
import pytest

from archimede.models import Photo, ReferenceFace, FaceMatch, DetectedFace


@pytest.fixture
def sample_photo():
    """Crea una Photo di esempio."""
    return Photo(
        node_id="photo-1",
        file_path="/path/foto.jpg",
        file_name="foto.jpg",
        mime_type="image/jpeg",
        device="test",
    )


@pytest.fixture
def sample_references():
    """Crea due ReferenceFace di esempio."""
    emb = np.random.rand(512).astype(np.float32)
    emb = emb / np.linalg.norm(emb)
    emb_list = emb.tolist()

    return [
        ReferenceFace(
            name="papa",
            embedding=emb_list,
            source_photos=["/ref/papa1.jpg"],
        ),
        ReferenceFace(
            name="mamma",
            embedding=[0.0] * 512,  # diverso da papa
            source_photos=["/ref/mamma1.jpg"],
        ),
    ]


class TestMatcher:
    """Test per il modulo di face matching."""

    def test_load_reference_faces_no_dir(self):
        """load_reference_faces su directory inesistente → []."""
        from archimede.identity.matcher import load_reference_faces

        result = load_reference_faces("/nonexistent/dir")
        assert result == []

    def test_load_reference_faces_empty_dir(self, tmp_path):
        """load_reference_faces su directory vuota → []."""
        from archimede.identity.matcher import load_reference_faces

        ref_dir = tmp_path / "ref_faces"
        ref_dir.mkdir()
        (ref_dir / "papa").mkdir()
        (ref_dir / "mamma").mkdir()

        result = load_reference_faces(str(ref_dir))
        assert result == []  # nessuna foto nelle sottocartelle

    @patch("archimede.identity.matcher.detect_faces")
    def test_load_reference_faces_with_photos(self, mock_detect, tmp_path):
        """load_reference_faces carica foto e crea embedding."""
        from archimede.identity.matcher import load_reference_faces

        ref_dir = tmp_path / "ref_faces"
        ref_dir.mkdir()
        (ref_dir / "papa").mkdir()
        (ref_dir / "mamma").mkdir()

        # Crea foto finta per papa
        img_path = ref_dir / "papa" / "foto1.jpg"
        img_path.write_bytes(b"fake-jpeg-data")

        # Mock detect_faces
        mock_detect.return_value = [
            {"embedding": [0.5] * 512, "bbox": [0, 0, 100, 100], "det_score": 0.95}
        ]

        references = load_reference_faces(str(ref_dir))
        assert len(references) == 1  # solo papa ha foto
        assert references[0].name == "papa"

    @patch("archimede.identity.matcher.detect_faces")
    def test_match_photo_no_faces(self, mock_detect, sample_photo, sample_references):
        """match_photo su foto senza volti."""
        from archimede.identity.matcher import match_photo

        mock_detect.return_value = []

        result = match_photo(sample_photo, sample_references, threshold=0.35)
        assert not result.is_couple
        assert len(result.faces) == 0

    @patch("archimede.identity.matcher.detect_faces")
    def test_match_photo_with_faces(self, mock_detect, sample_photo, sample_references):
        """match_photo trova volti e li confronta con referenze."""
        from archimede.identity.matcher import match_photo

        # Simula un volto che matcha "papa"
        mock_detect.return_value = [
            {
                "embedding": sample_references[0].embedding,  # embedding di papa
                "bbox": [10, 20, 100, 120],
                "det_score": 0.95,
                "gender": 1,
                "age": 45.0,
            }
        ]

        result = match_photo(sample_photo, sample_references, threshold=0.35)
        assert result.matches["papa"]  # papa matcha
        assert not result.matches["mamma"]  # mamma no
        assert not result.is_couple  # solo papa, non coppia

    @patch("archimede.identity.matcher.detect_faces")
    def test_match_couple_photo(self, mock_detect, sample_photo, sample_references):
        """match_photo trova una foto di coppia (entrambi i genitori)."""
        from archimede.identity.matcher import match_photo

        # Simula due volti: uno matcha papa, uno matcha mamma
        mock_detect.return_value = [
            {
                "embedding": sample_references[0].embedding,  # papa
                "bbox": [10, 20, 50, 70],
                "det_score": 0.95,
                "gender": 1,
                "age": 45.0,
            },
            {
                "embedding": sample_references[1].embedding,  # mamma (diverso)
                "bbox": [60, 30, 110, 80],
                "det_score": 0.90,
                "gender": 0,
                "age": 42.0,
            },
        ]

        result = match_photo(sample_photo, sample_references, threshold=0.35)
        assert result.matches["papa"]
        assert result.matches["mamma"]
        assert result.is_couple  # coppia trovata!

    @patch("archimede.identity.matcher.detect_faces")
    def test_search_couple_photos(self, mock_detect, sample_references):
        """search_couple_photos processa un batch di foto."""
        from archimede.identity.matcher import search_couple_photos

        # Crea foto di test
        photos = [
            Photo(node_id=f"p{i}", file_path=f"/path/foto{i}.jpg",
                  file_name=f"foto{i}.jpg", mime_type="image/jpeg")
            for i in range(5)
        ]

        # Alterna foto con/senza volti
        def detect_side_effect(path):
            if "foto0" in path or "foto2" in path:
                return [{
                    "embedding": sample_references[0].embedding,  # solo papa
                    "bbox": [0, 0, 50, 50],
                    "det_score": 0.9,
                }]
            return []

        mock_detect.side_effect = detect_side_effect

        report = search_couple_photos(photos, sample_references, threshold=0.35)
        assert report.photos_scanned == 5
        assert report.photos_with_faces == 2
        assert report.couple_count == 0  # nessuna coppia (solo papa)

    @patch("archimede.identity.matcher.detect_faces")
    def test_search_couple_with_callback(self, mock_detect, sample_references):
        """search_couple_photos chiama batch_callback."""
        from archimede.identity.matcher import search_couple_photos

        photos = [
            Photo(node_id=f"p{i}", file_path=f"/path/foto{i}.jpg",
                  file_name=f"foto{i}.jpg", mime_type="image/jpeg")
            for i in range(3)
        ]

        mock_detect.return_value = []
        callback_calls = []

        def callback(i, total, couples):
            callback_calls.append((i, total, couples))

        report = search_couple_photos(photos, sample_references, threshold=0.35,
                                       batch_callback=callback)
        assert len(callback_calls) > 0
        assert callback_calls[-1] == (3, 3, 0)

    def test_reference_face_creation(self):
        """ReferenceFace si crea correttamente."""
        ref = ReferenceFace(
            name="test",
            embedding=[0.5] * 512,
            source_photos=["/path/photo.jpg"],
        )
        assert ref.name == "test"
        assert len(ref.embedding) == 512

    def test_photo_model(self):
        """Photo si crea con campi opzionali."""
        photo = Photo(
            node_id="n1",
            file_path="/path/file.jpg",
            file_name="file.jpg",
        )
        assert photo.face_count == 0
        assert photo.device == ""
