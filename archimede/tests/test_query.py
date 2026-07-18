"""
Test per il CLI query di Archimede.
"""

from unittest.mock import MagicMock, patch

import pytest


class TestArchimedeQuery:
    """Test per i comandi CLI di Archimede."""

    @patch("archimede.query.PenelopeGraphReader")
    def test_cmd_stats(self, MockReader):
        """Comando stats deve mostrare statistiche del grafo."""
        from archimede.query import cmd_stats

        mock_reader = MagicMock()
        MockReader.return_value = mock_reader

        # Configura mock
        mock_reader.count_photos.return_value = 100
        mock_reader._query.side_effect = [
            # Path dei file
            [{"path": "C:\\path1\\foto.jpg"}, {"path": "C:\\path1\\foto2.jpg"}],
            # Node Person
            [{"id": "p1", "metadata": '{"source":"insightface"}'},
             {"id": "p2", "metadata": '{"source":"face_detection"}'}],
            # Photos with faces
            [{"node_id": "n1"}, {"node_id": "n2"}],
        ]
        mock_reader.get_person_nodes.return_value = [
            {"id": "p1", "metadata": '{"source":"insightface"}'},
            {"id": "p2", "metadata": '{"source":"face_detection"}'},
        ]
        mock_reader.get_photos_with_face_count.return_value = [
            {"node_id": "n1"},
        ]

        args = MagicMock()
        cmd_stats(args)

        # Verifica che siano state chiamate le query
        assert mock_reader.count_photos.called
        assert mock_reader.get_person_nodes.called
        assert mock_reader.get_photos_with_face_count.called
        mock_reader.close.assert_called_once()

    @patch("archimede.query.PenelopeGraphReader")
    @patch("archimede.query.load_reference_faces")
    @patch("archimede.query.search_couple_photos")
    @patch("archimede.query.generate_report")
    def test_cmd_find_parents(self, mock_report, mock_search,
                              mock_load, MockReader, tmp_path):
        """Comando find-parents esegue matching."""
        from archimede.query import cmd_find_parents

        # Configura mock
        mock_reader = MagicMock()
        MockReader.return_value = mock_reader
        mock_reader.get_all_photos.return_value = [
            {"node_id": "n1", "path": "/path/foto.jpg",
             "mime_type": "image/jpeg", "size_bytes": 1000,
             "sha256": "abc", "device": "test",
             "node_metadata": '{"face_count": 1}'},
        ]

        mock_load.return_value = [
            MagicMock(name="papa", source_photos=["/ref/p1.jpg"]),
            MagicMock(name="mamma", source_photos=["/ref/m1.jpg"]),
        ]
        mock_search.return_value = MagicMock(
            photos_scanned=1,
            photos_with_faces=1,
            couple_count=0,
            duration_seconds=5.0,
            generated_at="2026-07-15_14-30-00",
            couple_photos=[],
            all_results=[],
        )
        mock_report.return_value = str(tmp_path / "report.html")

        args = MagicMock(
            ref_dir="/test/ref",
            interactive=False,
            directory=None,
            limit=100,
            threshold=0.35,
            output=None,
        )

        cmd_find_parents(args)

        mock_load.assert_called_once()
        mock_search.assert_called_once()
        mock_report.assert_called_once()
        mock_reader.close.assert_called_once()

    @patch("archimede.query.PenelopeGraphReader")
    @patch("archimede.query.load_reference_faces")
    def test_cmd_find_parents_no_ref(self, mock_load, MockReader):
        """find-parents senza referenze deve mostrare errore."""
        from archimede.query import cmd_find_parents

        mock_load.return_value = []

        args = MagicMock(
            ref_dir="/test/ref",
            interactive=False,
        )

        cmd_find_parents(args)
        mock_load.assert_called_once()

    @patch("archimede.query.PenelopeGraphReader")
    def test_cmd_find_parents_no_ref_dir(self, MockReader, tmp_path):
        """find-parents senza --ref-dir e senza --interactive deve mostrare errore."""
        from archimede.query import cmd_find_parents

        args = MagicMock(ref_dir=None, interactive=False)
        cmd_find_parents(args)

    @patch("archimede.query.PenelopeGraphReader")
    def test_cmd_stats_reader_error(self, MockReader):
        """Comando stats deve gestire errori di connessione."""
        from archimede.query import cmd_stats

        mock_reader = MagicMock()
        MockReader.return_value = mock_reader
        mock_reader.count_photos.side_effect = Exception("DB error")

        args = MagicMock()
        # Non deve sollevare eccezioni
        try:
            cmd_stats(args)
        except Exception:
            pytest.fail("cmd_stats non deve sollevare eccezioni")
