"""
Test per PenelopeGraphReader — reader read-only del grafo MariaDB.
"""

from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def mock_reader():
    """Crea un PenelopeGraphReader con MariaDBStore mock."""
    with patch("archimede.graph.reader.MariaDBStore") as MockStore:
        mock_store = MagicMock()
        MockStore.return_value = mock_store
        mock_store.connect.return_value = MagicMock()
        mock_store._query.return_value = []

        # Import dopo il patch
        from archimede.graph.reader import PenelopeGraphReader
        reader = PenelopeGraphReader()
        reader._store = mock_store
        yield reader


class TestPenelopeGraphReader:
    """Test per il reader read-only del grafo."""

    def test_connected_property(self, mock_reader):
        """connected deve essere True quando store è presente."""
        assert mock_reader.connected

    def test_connected_false_when_no_store(self):
        """connected deve essere False quando store è None."""
        from archimede.graph.reader import PenelopeGraphReader
        reader = PenelopeGraphReader()
        reader._store = None
        assert not reader.connected

    def test_query_only_select(self, mock_reader):
        """Solo query SELECT sono permesse."""
        with pytest.raises(RuntimeError, match="operazione non consentita"):
            mock_reader._query("DELETE FROM nodes")

    def test_query_with_select_allowed(self, mock_reader):
        """Query SELECT devono funzionare."""
        mock_reader._store._query.return_value = [{"cnt": 10}]
        result = mock_reader._query("SELECT COUNT(*) as cnt FROM nodes")
        assert result == [{"cnt": 10}]

    def test_query_with_cte_allowed(self, mock_reader):
        """WITH (CTE) deve essere permesso."""
        mock_reader._store._query.return_value = []
        result = mock_reader._query("WITH cte AS (SELECT 1) SELECT * FROM cte")
        assert result == []

    def test_count_photos(self, mock_reader):
        """count_photos deve contare file immagine."""
        mock_reader._store._query.return_value = [{"cnt": 42}]
        count = mock_reader.count_photos()
        assert count == 42

    def test_count_photos_empty(self, mock_reader):
        """count_photos con 0 risultati."""
        mock_reader._store._query.return_value = [{"cnt": 0}]
        assert mock_reader.count_photos() == 0

    def test_get_all_photos(self, mock_reader):
        """get_all_photos restituisce foto con estensioni immagine."""
        mock_reader._store._query.return_value = [
            {"node_id": "1", "label": "foto.jpg", "path": "/path/foto.jpg"},
            {"node_id": "2", "label": "foto.png", "path": "/path/foto.png"},
        ]
        photos = mock_reader.get_all_photos()
        assert len(photos) == 2

    def test_get_all_photos_with_limit(self, mock_reader):
        """get_all_photos con limite."""
        mock_reader._store._query.return_value = [{"node_id": "1", "label": "foto.jpg", "path": "/path/foto.jpg"}]
        photos = mock_reader.get_all_photos(limit=1)
        assert len(photos) == 1

    def test_get_photos_in_directory(self, mock_reader):
        """get_photos_in_directory filtra per directory."""
        mock_reader._store._query.return_value = [
            {"node_id": "1", "label": "foto.jpg", "path": "/MyPhotos/foto.jpg"},
        ]
        photos = mock_reader.get_photos_in_directory("MyPhotos")
        assert len(photos) == 1

    def test_get_photos_in_directory_empty(self, mock_reader):
        """get_photos_in_directory restituisce [] se nessuna foto."""
        mock_reader._store._query.return_value = []
        assert mock_reader.get_photos_in_directory("Inesistente") == []

    def test_get_photos_with_face_count(self, mock_reader):
        """get_photos_with_face_count filtra per metadati face_count."""
        mock_reader._store._query.return_value = [
            {"node_id": "1", "path": "/path/with_face.jpg"},
        ]
        photos = mock_reader.get_photos_with_face_count()
        assert len(photos) == 1

    def test_get_person_nodes(self, mock_reader):
        """get_person_nodes restituisce nodi Person."""
        mock_reader._store._query.return_value = [
            {"id": "p1", "label": "Mario", "metadata": '{"source":"insightface"}'},
        ]
        persons = mock_reader.get_person_nodes()
        assert len(persons) == 1
        assert persons[0]["label"] == "Mario"

    def test_get_person_nodes_filtered(self, mock_reader):
        """get_person_nodes con filtro source."""
        mock_reader._store._query.return_value = [
            {"id": "p1", "label": "Mario", "metadata": '{"source":"insightface"}'},
        ]
        persons = mock_reader.get_person_nodes(source="insightface")
        assert len(persons) == 1

    def test_get_edges_for_photo(self, mock_reader):
        """get_edges_for_photo restituisce archi di una foto."""
        mock_reader._store._query.return_value = [
            {"id": 1, "source_id": "photo1", "target_id": "person1", "relation": "CONTAINS"},
        ]
        edges = mock_reader.get_edges_for_photo("photo1")
        assert len(edges) == 1
        assert edges[0]["relation"] == "CONTAINS"

    def test_get_persons_in_photo(self, mock_reader):
        """get_persons_in_photo restituisce persone collegate a una foto."""
        mock_reader._store._query.return_value = [
            {"id": "p1", "label": "Mario", "metadata": '{}'},
        ]
        persons = mock_reader.get_persons_in_photo("photo1")
        assert len(persons) == 1

    def test_context_manager(self, mock_reader):
        """Context manager deve funzionare."""
        with mock_reader:
            assert mock_reader.connected

    def test_close(self, mock_reader):
        """close deve chiamare store.close()."""
        mock_reader.close()
        mock_reader._store.close.assert_called_once()

    def test_raises_when_not_connected(self):
        """Query deve sollevare errore se non connesso."""
        from archimede.graph.reader import PenelopeGraphReader
        reader = PenelopeGraphReader()
        reader._store = None
        with pytest.raises(RuntimeError, match="non connesso"):
            reader._query("SELECT 1")

    def test_find_penelope_dir(self, mock_reader):
        """_find_penelope_dir deve cercare Penelope/."""
        # Test con percorso non esistente
        from archimede.graph.reader import PenelopeGraphReader
        path = mock_reader._find_penelope_dir()
        # Non deve sollevare eccezioni
        assert path is None or path.exists()
