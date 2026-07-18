"""Caricamento e accesso alla configurazione."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

import yaml
from dotenv import load_dotenv
from loguru import logger
from pydantic import BaseModel, ConfigDict


# ---------------------------------------------------------------------------
# Provider LLM supportati
# ---------------------------------------------------------------------------

# Provider che usano il client OpenAI (API compatibile)
OPENAI_COMPATIBLE_PROVIDERS = {
    "openai":    {"default_base_url": None},
    "deepseek":  {"default_base_url": "https://api.deepseek.com/v1"},
    "ollama":    {"default_base_url": "http://localhost:11434/v1"},
}


# ---------------------------------------------------------------------------
# Modelli di configurazione (Pydantic)
# ---------------------------------------------------------------------------

class MotionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    method: str = "MOG2"
    history: int = 500
    threshold: int = 25
    min_contour_area: int = 500


class DetectorConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model: str = "yolov8n.pt"
    confidence: float = 0.4
    trigger_classes: list[int] = [0, 1, 2, 3, 5, 7]


class DescriberConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    provider: str = "ollama"
    model: str = "moondream:latest"
    api_base: str = "http://localhost:11434/v1"
    api_key: str = ""
    max_tokens: int = 4096              # sufficiente per descrizioni dettagliate
    max_frames_per_minute: int = 20     # ~1 frame ogni 3 secondi (cooldown implicito)


class VisionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    source_type: str = "webcam"
    source_id: int | str = 0
    fps_target: int = 15
    frame_width: int = 640
    frame_height: int = 480
    motion: MotionConfig = MotionConfig()
    detector: DetectorConfig = DetectorConfig()
    describer: DescriberConfig = DescriberConfig()


class TranscriptionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = True
    model: str = "base"
    language: str = "it"
    device: str = "cpu"
    compute_type: str = "int8"


class EventDetectionConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False

    # Feature-based detection thresholds
    scream_f0_threshold: float = 300.0      # Hz — F0 minima per urlo
    scream_rms_threshold: float = 0.05      # RMS minimo per urlo
    gunshot_energy_ratio: float = 5.0       # rapporto max/mean RMS per sparo
    gunshot_rms_threshold: float = 0.1      # RMS minimo per sparo
    silence_rms_threshold: float = 0.003    # RMS massimo per silenzio
    raised_voice_f0_threshold: float = 250.0  # Hz — F0 minima per tono elevato
    raised_voice_rms_threshold: float = 0.02  # RMS minimo per tono elevato


class AudioConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    enabled: bool = False
    source_type: str = "microphone"
    source_id: int | None = None
    sample_rate: int = 16000
    transcription: TranscriptionConfig = TranscriptionConfig()
    event_detection: EventDetectionConfig = EventDetectionConfig()


class AggregationConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    timeline_window_seconds: int = 300
    entity_similarity_threshold: float = 0.7
    dedup_window_seconds: int = 2


class MemoryCollections(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persons: str = "persons"
    places: str = "places"
    events: str = "events"
    patterns: str = "patterns"


class MemoryConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    persist_directory: str = "data/chroma"
    collection_names: MemoryCollections = MemoryCollections()
    embedding_model: str = "all-MiniLM-L6-v2"
    top_k_retrieval: int = 15
    retention_days: int = 365


class ContextBudget(BaseModel):
    model_config = ConfigDict(extra="forbid")

    system_prompt: int = 1500
    timeline_events: int = 2000
    memory_context: int = 1500
    current_observation: int = 500


class ReasoningConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    provider: str = "deepseek"
    model: str = "deepseek-v4-pro"
    api_key: str = ""
    api_base: str | None = None
    temperature: float = 0.2
    max_tokens: int = 8192
    context_budget: ContextBudget = ContextBudget()
    rate_limit_per_minute: int = 10

    def model_post_init(self, __context: Any) -> None:
        """Se il provider è OpenAI-compatible e api_base non è impostato,
        usa il default del provider."""
        if self.api_base is None and self.provider in OPENAI_COMPATIBLE_PROVIDERS:
            self.api_base = OPENAI_COMPATIBLE_PROVIDERS[self.provider]["default_base_url"]


class LoggingConfig(BaseModel):
    model_config = ConfigDict(extra="forbid")

    level: str = "INFO"
    format: str = "json"
    file: str = "data/logs/archimede.log"
    rotation: str = "10 MB"
    retention: str = "30 days"


class Config(BaseModel):
    """Configurazione completa di Archimede."""
    model_config = ConfigDict(extra="forbid")

    perception_vision: VisionConfig | None = None
    perception_audio: AudioConfig | None = None
    aggregation: AggregationConfig = AggregationConfig()
    memory: MemoryConfig = MemoryConfig()
    reasoning: ReasoningConfig = ReasoningConfig()
    ethics: dict[str, Any] | None = None
    logging: LoggingConfig = LoggingConfig()


# ---------------------------------------------------------------------------
# Caricamento configurazione
# ---------------------------------------------------------------------------

_CONFIG: Config | None = None


def load_config(path: str | Path | None = None) -> Config:
    """Carica la configurazione da file YAML.

    Cerca nell'ordine:
    1. path esplicito passato come argomento
    2. variabile d'ambiente ARCHIMEDE_CONFIG
    3. ./config/default.yaml (percorso relativo alla CWD)

    Carica automaticamente il file .env nella root del progetto
    per variabili d'ambiente come ARCHIMEDE_API_KEY.
    """
    global _CONFIG

    # Carica .env dalla root del progetto prima di tutto
    _load_env_file()

    if path is None:
        path = os.environ.get("ARCHIMEDE_CONFIG", "config/default.yaml")

    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(
            f"File di configurazione non trovato: {path}. "
            "Imposta ARCHIMEDE_CONFIG o passa un path valido."
        )

    with open(path, encoding="utf-8") as f:
        raw = yaml.safe_load(f)

    # Appiattisci la struttura annidata per i modelli Pydantic
    flat: dict[str, Any] = {}

    if "perception" in raw:
        if "vision" in raw["perception"]:
            flat["perception_vision"] = raw["perception"]["vision"]
        if "audio" in raw["perception"]:
            flat["perception_audio"] = raw["perception"]["audio"]

    for key in ("aggregation", "memory", "reasoning", "ethics", "logging"):
        if key in raw:
            flat[key] = raw[key]

    _CONFIG = Config(**flat)

    # Override API key da variabile d'ambiente (più sicuro del file YAML)
    env_key = os.environ.get("ARCHIMEDE_API_KEY")
    if env_key and _CONFIG.reasoning:
        _CONFIG.reasoning.api_key = env_key
        # Non loggare la chiave, solo che è stata caricata
        logger.bind(module="config").info(
            "ARCHIMEDE_API_KEY caricata da .env / environment"
        )

    return _CONFIG


def _load_env_file() -> None:
    """Carica il file .env dalla CWD o dalla directory del progetto.

    Strategia:
    1. load_dotenv() cerca .env nella CWD e risale le directory
    2. Se non trova nulla, prova percorsi espliciti
    """
    # Prova prima load_dotenv() senza argomenti (cerca in CWD e parent)
    loaded = load_dotenv()
    if loaded:
        return

    # Fallback: cerca .env in percorsi espliciti
    root_candidates = [
        Path.cwd(),                         # CWD
        Path.cwd().parent,                  # parent della CWD
        Path(__file__).resolve().parent.parent,  # root del progetto (dove sta config.py)
    ]
    for directory in root_candidates:
        env_path = directory / ".env"
        if env_path.is_file():
            load_dotenv(env_path, override=False)
            return

    # Se ancora nulla, prova a leggere il file direttamente
    for directory in root_candidates:
        env_path = directory / ".env"
        if env_path.is_file():
            try:
                with open(env_path, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if line.startswith("#") or not line:
                            continue
                        if "=" in line:
                            key, _, value = line.partition("=")
                            key = key.strip()
                            value = value.strip().strip("\"'")
                            if key and value and not os.environ.get(key):
                                os.environ[key] = value
            except Exception:
                pass
            return


def get_config() -> Config:
    """Restituisce la configurazione corrente (deve essere stata caricata)."""
    if _CONFIG is None:
        raise RuntimeError(
            "Configurazione non caricata. Chiama load_config() prima di get_config()."
        )
    return _CONFIG
