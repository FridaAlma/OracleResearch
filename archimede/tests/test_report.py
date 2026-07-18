"""
Test per ReportGenerator — generazione HTML delle foto trovate.
"""

import tempfile
from pathlib import Path
from unittest.mock import patch

import numpy as np
import pytest

from archimede.models import Photo, PhotoMatchResult, SearchReport


@pytest.fixture
def sample_report():
    """Crea un SearchReport di esempio per test."""
    photos = [
        Photo(node_id=f"p{i}", file_path=f"/path/foto{i}.jpg",
              file_name=f"foto{i}.jpg", mime_type="image/jpeg",
              face_count=1)
        for i in range(3)
    ]

    results = []
    for photo in photos:
        results.append(PhotoMatchResult(
            photo=photo,
            faces=[],
            matches={"papa": True, "mamma": True},
            match_details=[],
            is_couple=True,
        ))

    return SearchReport(
        query_name="Test ricerca",
        reference_names=["papa", "mamma"],
        similarity_threshold=0.35,
        photos_scanned=3,
        photos_with_faces=3,
        couple_photos=results,
        single_parent_photos={"papa": [], "mamma": []},
        all_results=results,
        duration_seconds=12.5,
        generated_at="2026-07-15 14:30:00",
    )


class TestReport:
    """Test per la generazione del report HTML."""

    def test_generate_report_creates_file(self, sample_report, tmp_path):
        """generate_report crea un file HTML."""
        from archimede.presentation.report import generate_report

        output = tmp_path / "report.html"
        result = generate_report(sample_report, str(output))

        assert output.exists()
        assert result == str(output.resolve())

    def test_generate_report_content(self, sample_report, tmp_path):
        """Il file HTML contiene elementi chiave."""
        from archimede.presentation.report import generate_report

        output = tmp_path / "report.html"
        generate_report(sample_report, str(output))

        html = output.read_text(encoding="utf-8")
        assert "PROMETEO" in html
        assert "Foto di coppia" in html or "coppia" in html
        assert "3" in html  # numero foto scansionate
        assert "papa" in html
        assert "mamma" in html

    def test_generate_report_no_couple(self, tmp_path):
        """Report senza foto di coppia."""
        from archimede.presentation.report import generate_report

        report = SearchReport(
            query_name="Test",
            reference_names=["papa", "mamma"],
            similarity_threshold=0.35,
            photos_scanned=10,
            photos_with_faces=0,
            couple_photos=[],
            single_parent_photos={"papa": [], "mamma": []},
            all_results=[],
            duration_seconds=5.0,
            generated_at="2026-07-15",
        )

        output = tmp_path / "empty_report.html"
        generate_report(report, str(output))

        html = output.read_text(encoding="utf-8")
        assert "Nessuna foto di coppia" in html

    def test_generate_report_single_parent(self, tmp_path):
        """Report con foto di un solo genitore."""
        from archimede.presentation.report import generate_report

        photo = Photo(node_id="p1", file_path="/path/foto.jpg",
                      file_name="foto.jpg", mime_type="image/jpeg")

        single_result = PhotoMatchResult(
            photo=photo, faces=[], matches={"papa": True, "mamma": False},
            match_details=[], is_couple=False,
        )

        report = SearchReport(
            query_name="Test",
            reference_names=["papa", "mamma"],
            similarity_threshold=0.35,
            photos_scanned=1,
            photos_with_faces=1,
            couple_photos=[],
            single_parent_photos={"papa": [single_result], "mamma": []},
            all_results=[single_result],
            duration_seconds=1.0,
            generated_at="2026-07-15",
        )

        output = tmp_path / "single_report.html"
        generate_report(report, str(output))

        html = output.read_text(encoding="utf-8")
        assert "papa" in html
        assert "foto.jpg" in html

    def test_img_to_datauri_nonexistent(self):
        """_img_to_datauri su file inesistente → stringa vuota."""
        from archimede.presentation.report import _img_to_datauri

        result = _img_to_datauri("/nonexistent/image.jpg")
        assert result == ""

    def test_report_properties(self, sample_report):
        """Proprietà del report."""
        assert sample_report.couple_count == 3
        assert sample_report.total_faces_detected == 3

    def test_empty_report_properties(self):
        """Proprietà del report vuoto."""
        report = SearchReport()
        assert report.couple_count == 0
        assert report.total_faces_detected == 0
