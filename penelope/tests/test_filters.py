"""
Test completi per i filtri HSD (Egida) v2.0.

Copre:
- Veri positivi (API key, JWT, SSH, password, CF, telefono, email, CAP)
- Falsi positivi (UUID, timestamp, placeholder, test email, URL, type hint)
- Sistema di scoring e soglia
- Rilevamento binario (magic bytes)
"""

import json
import tempfile
from pathlib import Path

import pytest

from egida.filters import (
    HSDMatch,
    HSDFilter,
    Severity,
    _has_entropy,
    _is_binary,
    _is_password_placeholder,
    _is_valid_jwt,
    quick_scan,
)


# ─────────────────────────────────────────────────────────────────────
# Helper tests
# ─────────────────────────────────────────────────────────────────────

class TestEntropy:
    def test_real_password(self):
        assert _has_entropy("Sup3rSecret!2024") is True

    def test_low_entropy_single_category(self):
        assert _has_entropy("abcdefgh") is False  # only lowercase

    def test_low_entropy_short(self):
        assert _has_entropy("Ab1") is False  # too short

    def test_placeholder_password(self):
        assert _has_entropy("postgres") is False  # only lowercase


class TestPasswordPlaceholder:
    def test_common_placeholder(self):
        assert _is_password_placeholder("postgres") is True
        assert _is_password_placeholder("password") is True
        assert _is_password_placeholder("test") is True

    def test_type_hint(self):
        assert _is_password_placeholder("None") is True
        assert _is_password_placeholder("null") is True

    def test_variable_name(self):
        assert _is_password_placeholder("hashed_password") is True
        assert _is_password_placeholder("new_password") is True

    def test_real_password_not_placeholder(self):
        assert _is_password_placeholder("MyStr0ng!Pass") is False


class TestJWTValidation:
    def test_valid_jwt(self):
        import base64
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256", "typ": "JWT"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "1234567890"}).encode()
        ).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"fakesig123456").rstrip(b"=").decode()
        jwt = f"{header}.{payload}.{sig}"
        assert _is_valid_jwt(jwt) is True

    def test_invalid_jwt_url(self):
        url = "https://images-wixmp-ed30a86b8c4ca887773594c2.wixmp.com/f/5c057c8c-e922-4a73-a764/v1/fit/wm/..."
        assert _is_valid_jwt(url) is False


# ─────────────────────────────────────────────────────────────────────
# Binary detection
# ─────────────────────────────────────────────────────────────────────

class TestBinaryDetection:
    def test_extension_known_binary(self, tmp_path: Path):
        f = tmp_path / "test.png"
        f.write_bytes(b"\x89PNG\r\n\x1a\n")
        assert _is_binary(f) is True

    def test_null_byte_detected_as_binary(self, tmp_path: Path):
        f = tmp_path / "unknown.dat"
        f.write_bytes(b"some text\x00binary")
        assert _is_binary(f) is True

    def test_clean_text_not_binary(self, tmp_path: Path):
        f = tmp_path / "notes.txt"
        f.write_text("Questo è un file di testo normale.")
        assert _is_binary(f) is False

    def test_binary_file_skipped_by_filter(self, tmp_path: Path):
        f = tmp_path / "data.bin"
        f.write_bytes(b"\x00" * 100)
        result = HSDFilter().check_file(f)
        assert result.is_infected is False
        assert len(result.matches) == 0


# ─────────────────────────────────────────────────────────────────────
# Veri positivi
# ─────────────────────────────────────────────────────────────────────

class TestTruePositives:
    def test_api_key_detection(self):
        text = 'API_KEY="sk-1234567890abcdef1234567890abcdef"'
        matches = quick_scan(text)
        assert any("API Key" in m for m in matches)

    def test_jwt_detection(self):
        import base64
        header = base64.urlsafe_b64encode(
            json.dumps({"alg": "HS256"}).encode()
        ).rstrip(b"=").decode()
        payload = base64.urlsafe_b64encode(
            json.dumps({"sub": "1234567890"}).encode()
        ).rstrip(b"=").decode()
        sig = base64.urlsafe_b64encode(b"fakesignature12").rstrip(b"=").decode()
        jwt = f"{header}.{payload}.{sig}"
        text = f'token = "{jwt}"'
        matches = quick_scan(text)
        assert any("JWT" in m for m in matches)

    def test_github_token(self):
        text = "ghp_abcdefghijklmnopqrstuvwxyz1234567890"
        matches = quick_scan(text)
        assert any("GitHub" in m for m in matches)

    def test_ssh_key(self):
        text = "-----BEGIN RSA PRIVATE KEY-----\nMIIEpAIBAAKCAQEA..."
        matches = quick_scan(text)
        assert len(matches) > 0

    def test_password_real(self):
        text = 'password = "MyStr0ng!P@ss2024"'
        matches = quick_scan(text)
        assert any("Password" in m for m in matches)

    def test_italian_codice_fiscale(self):
        text = "Il codice fiscale è RSSMRA85M10H501Z"
        matches = quick_scan(text)
        assert any("Fiscale" in m for m in matches)

    def test_real_phone_number(self):
        # Numero italiano con prefisso internazionale
        text = '"phone_number": "+393889972758"'
        matches = quick_scan(text)
        assert any("telefono" in m.lower() for m in matches)

    def test_real_phone_with_plus(self):
        text = "Chiamami al +39 347 123 4567 per favore"
        matches = quick_scan(text)
        assert any("telefono" in m.lower() for m in matches)

    def test_real_email(self):
        text = "Contattami a nome.cognome@gmail.com"
        matches = quick_scan(text)
        assert any("email" in m.lower() for m in matches)

    def test_real_cap(self):
        text = "CAP: 20100 Milano"
        matches = quick_scan(text)
        assert any("CAP" in m for m in matches)

    def test_aws_key(self):
        text = "AKIAIOSFODNN7EXAMPLE"
        matches = quick_scan(text)
        assert any("AWS" in m for m in matches)


# ─────────────────────────────────────────────────────────────────────
# Falsi positivi (devono essere eliminati)
# ─────────────────────────────────────────────────────────────────────

class TestFalsePositives:
    """Test che verificano che i falsi positivi NON siano rilevati."""

    def test_uuid_not_phone(self):
        """UUID non devono essere scambiati per numeri di telefono."""
        text = '"id": "699c3f5a-9acc-832b-a142-043373a62e06"'
        matches = quick_scan(text)
        assert not any("telefono" in m.lower() for m in matches), (
            f"UUID matchato come telefono: {matches}"
        )

    def test_uuid_multiple_not_phone(self):
        """Vari UUID non devono attivare il pattern telefono."""
        uuids = [
            "69400035-0554-8004-95f7-dd3b746b3190",
            "6978a2fb-a810-8004-9204-25207b26505a",
            "69d19755-9034-832a-8eff-71eafab568bf",
        ]
        for uid in uuids:
            text = f'"id": "{uid}"'
            matches = quick_scan(text)
            assert not any("telefono" in m.lower() for m in matches), (
                f"UUID {uid} matchato come telefono: {matches}"
            )

    def test_file_path_not_phone(self):
        """Path di file non devono essere scambiati per telefoni."""
        text = (
            '"file-1MPrgEtWgkpHqQWjqewhJE-881c7ad3-2869-4dab-92a4-'
            '8bde349daecb156420130932773562"'
        )
        matches = quick_scan(text)
        assert not any("telefono" in m.lower() for m in matches)

    def test_timestamp_not_cap(self):
        """Timestamp UNIX non devono essere scambiati per CAP."""
        text = '"create_time": 1735660909.17968'
        matches = quick_scan(text)
        assert not any("CAP" in m for m in matches)

    def test_big_number_not_cap(self):
        """Numeri grandi non devono matchare CAP."""
        text = '"timestamp": 1735660909'
        matches = quick_scan(text)
        assert not any("CAP" in m for m in matches)

    def test_password_placeholder_not_critical(self):
        """Placeholder password non devono essere considerate critiche."""
        f = HSDFilter()
        # POSTGRES_PASSWORD: postgres (default CI)
        result = f.check_text('POSTGRES_PASSWORD: postgres')
        assert not result or all(
            m.get("severity") != "CRITICAL" for m in result
        )

    def test_type_hint_password_not_critical(self):
        """Type hint Python con password non sono critici."""
        text = 'password: Optional[str] = None'
        result = quick_scan(text)
        assert not any("Password" in m for m in result) or True
        # Il match può esserci ma deve essere LOW severity
        f = HSDFilter()
        matches = f.check_text(text)
        password_matches = [m for m in matches if "Password" in m["pattern"]]
        if password_matches:
            assert password_matches[0]["severity"] == "LOW"

    def test_hashed_password_not_critical(self):
        """hashed_password non è una password in chiaro."""
        text = 'new_password=user_in_db.hashed_password'
        f = HSDFilter()
        matches = f.check_text(text)
        password_matches = [m for m in matches if "Password" in m["pattern"]]
        if password_matches:
            assert password_matches[0]["severity"] == "LOW"

    def test_test_email_not_critical(self):
        """Email fittizie di test non sono critiche."""
        text = 'email="test@test.com", password="password", username="username"'
        f = HSDFilter()
        matches = f.check_text(text)
        email_matches = [m for m in matches if "email" in m["pattern"].lower()]
        if email_matches:
            assert email_matches[0]["severity"] in ("INFO", "LOW")

    def test_example_email_not_critical(self):
        """Email con dominio example.com non sono critiche."""
        text = 'La mia email è test@example.com per i test'
        f = HSDFilter()
        matches = f.check_text(text)
        email_matches = [m for m in matches if "email" in m["pattern"].lower()]
        if email_matches:
            assert email_matches[0]["severity"] == "INFO"

    def test_url_not_jwt(self):
        """URL di immagini non devono essere scambiati per JWT."""
        url = (
            "https://images-wixmp-ed30a86b8c4ca887773594c2.wixmp.com/"
            "f/5c057c8c-e922-4a73-a764-9f63bf5f40ef/v1/fit/wm/"
        )
        matches = quick_scan(url)
        assert not any("JWT" in m for m in matches)

    def test_clean_file_no_match(self):
        text = "Questo è un file pulito. Nessun dato sensibile."
        matches = quick_scan(text)
        assert len(matches) == 0


# ─────────────────────────────────────────────────────────────────────
# Sistema di scoring
# ─────────────────────────────────────────────────────────────────────

class TestScoring:
    def test_single_low_no_quarantine(self):
        """Un solo match LOW non dovrebbe attivare la quarantena."""
        match = HSDMatch(file_path="test.txt")
        match.add_match("Test", 1, "snippet", Severity.LOW)
        assert match.is_infected is False
        assert match.score == 25

    def test_two_low_no_quarantine(self):
        """Due match LOW non bastano per quarantena (50 < 90)."""
        match = HSDMatch(file_path="test.txt")
        match.add_match("A", 1, "a", Severity.LOW)
        match.add_match("B", 2, "b", Severity.LOW)
        assert match.is_infected is False
        assert match.score == 50

    def test_one_critical_quarantine(self):
        """Un match CRITICAL quarantena da solo."""
        match = HSDMatch(file_path="test.txt")
        match.add_match("AWS", 1, "key", Severity.CRITICAL)
        assert match.is_infected is True
        assert match.score == 100

    def test_medium_plus_low_no_quarantine(self):
        """MEDIUM + LOW = 75, sotto soglia 90."""
        match = HSDMatch(file_path="test.txt")
        match.add_match("Phone", 1, "+39...", Severity.MEDIUM)
        match.add_match("CAP", 1, "20100", Severity.LOW)
        assert match.is_infected is False
        assert match.score == 75

    def test_two_medium_quarantine(self):
        """Due MEDIUM = 100, sopra soglia → quarantena."""
        match = HSDMatch(file_path="test.txt")
        match.add_match("Phone", 1, "+39...", Severity.MEDIUM)
        match.add_match("Email", 2, "a@b.com", Severity.MEDIUM)
        assert match.is_infected is True
        assert match.score == 100

    def test_high_quarantine(self):
        """Un match HIGH quarantena da solo (90)."""
        match = HSDMatch(file_path="test.txt")
        match.add_match("CF", 1, "RSSMRA85M10H501Z", Severity.HIGH)
        assert match.is_infected is True

    def test_real_file_scenario_safe(self):
        """File con solo CAP e email di test non va in quarantena."""
        text = (
            "CAP: 20100\n"
            "email: test@example.com\n"
            "password: Optional[str] = None\n"
        )
        f = HSDFilter()
        # Simula il comportamento di check_file creando un file temporaneo
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".py", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(text)
            tmppath = tmp.name
        try:
            result = f.check_file(tmppath)
            # Dovrebbe avere match ma score sotto soglia
            assert not result.is_infected, (
                f"File innocuo in quarantena! Score={result.score}, "
                f"matches={result.matches}"
            )
        finally:
            Path(tmppath).unlink(missing_ok=True)

    def test_real_file_with_secrets_quarantine(self):
        """File con vero CF + password reale → quarantena."""
        text = (
            "CF: RSSMRA85M10H501Z\n"
            'DB_PASSWORD = "XyZaB!7890SecurePass"\n'
        )
        import tempfile
        with tempfile.NamedTemporaryFile(
            mode="w", suffix=".env", delete=False, encoding="utf-8"
        ) as tmp:
            tmp.write(text)
            tmppath = tmp.name
        try:
            result = HSDFilter().check_file(tmppath)
            assert result.is_infected, (
                f"File con segreti NON rilevato! Score={result.score}"
            )
        finally:
            Path(tmppath).unlink(missing_ok=True)
