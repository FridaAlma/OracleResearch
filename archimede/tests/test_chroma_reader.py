"""
Test per PenelopeChromaReader — reader read-only della ChromaDB.
"""

import tempfile
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


class TestPenelopeChromaReader:
    """Test per il reader read-only di ChromaDB."""

    def test_init_default_path(self):
        """Inizializzazione cerca ChromaDB nella directory Penelope."""
        from archimede.graph.chroma_reader import PenelopeChromaReader

        with patch("pathlib.Path.exists", return_value=False):
            reader = PenelopeChromaReader()
            assert reader._client is None

    def test_init_with_custom_path(self, tmp_path):
        """Inizializzazione con percorso personalizzato."""
        from archimede.graph.chroma_reader import PenelopeChromaReader

        chroma_dir = tmp_path / "chroma_test"
        chroma_dir.mkdir(parents=True)

        reader = PenelopeChromaReader(persist_dir=str(chroma_dir))
        assert reader._client is not None

    def test_get_collections_empty(self, tmp_path):
        """get_collections su ChromaDB vuota restituisce lista."""
        from archimede.graph.chroma_reader import PenelopeChromaReader

        chroma_dir = tmp_path / "chroma_empty"
        chroma_dir.mkdir(parents=True)

        reader = PenelopeChromaReader(persist_dir=str(chroma_dir))
        collections = reader.get_collections()
        assert isinstance(collections, list)

    def test_get_collections_no_client(self):
        """get_collections senza client restituisce []."""
        from archimede.graph.chroma_reader import PenelopeChromaReader

        reader = PenelopeChromaReader()
        reader._client = None
        assert reader.get_collections() == []

    def test_query_images_no_client(self):
        """query_images senza client restituisce []."""
        from archimede.graph.chroma_reader import PenelopeChromaReader

        reader = PenelopeChromaReader()
        reader._client = None
        result = reader.query_images([0.0] * 512)
        assert result == []

    def test_count_images_no_client(self):
        """count_images senza client restituisce 0."""
        from archimede.graph.chroma_reader import PenelopeChromaReader

        reader = PenelopeChromaReader()
        reader._client = None
        assert reader.count_images() == 0

    def test_count_images_no_collection(self, tmp_path):
        """count_images senza collezione restituisce 0."""
        from archimede.graph.chroma_reader import PenelopeChromaReader
        import chromadb
        from chromadb.config import Settings

        chroma_dir = tmp_path / "chroma_test"
        chroma_dir.mkdir(parents=True)

        reader = PenelopeChromaReader(persist_dir=str(chroma_dir))
        assert reader.count_images() == 0  # collezione non esiste ancora

    def test_close_no_client(self):
        """close senza client non deve sollevare eccezioni."""
        from archimede.graph.chroma_reader import PenelopeChromaReader

        reader = PenelopeChromaReader()
        reader._client = None
        reader.close()  # non deve sollevare

    def test_close_with_client(self, tmp_path):
        """close con client deve chiamare clear_system_cache."""
        from archimede.graph.chroma_reader import PenelopeChromaReader

        chroma_dir = tmp_path / "chroma_test"
        chroma_dir.mkdir(parents=True)

        reader = PenelopeChromaReader(persist_dir=str(chroma_dir))
        reader._client = MagicMock()
        reader.close()
        reader._client.clear_system_cache.assert_called_once()

    def test_count_images_with_data(self, tmp_path):
        """count_images su collezione con dati."""
        from archimede.graph.chroma_reader import PenelopeChromaReader
        import chromadb
        from chromadb.config import Settings

        chroma_dir = tmp_path / "chroma_test"
        chroma_dir.mkdir(parents=True)

        # Crea una collezione con dati
        client = chromadb.PersistentClient(
            path=str(chroma_dir),
            settings=Settings(anonymized_telemetry=False),
        )
        coll = client.get_or_create_collection("image_embeddings")
        coll.add(
            ids=["img1"],
            embeddings=[[0.0] * 512],
            metadatas=[{"file_name": "test.jpg"}],
        )

        reader = PenelopeChromaReader(persist_dir=str(chroma_dir))
        assert reader.count_images() == 1
