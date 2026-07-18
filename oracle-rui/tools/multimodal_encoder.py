"""
Multimodal Encoder — Codifica testo, immagini e (in futuro) audio
in uno spazio vettoriale condiviso usando CLIP (OpenAI).

Utilizza "openai/clip-vit-base-patch32" per generare embedding
di 512 dimensioni sia per testo che per immagini.

Usage:
    from tools.multimodal_encoder import MultimodalEncoder
    encoder = MultimodalEncoder()
    vec = encoder.encode_text("un gatto che dorme")
    vec = encoder.encode_image("path/to/foto.jpg")
"""

import logging
import sys
from pathlib import Path
from typing import Optional

import numpy as np

logger = logging.getLogger("multimodal_encoder")

# ── Tentativo di import ────────────────────────────────────────────
try:
    import torch
    import torch.nn.functional as F
    from PIL import Image
    from transformers import CLIPModel, CLIPProcessor
    HAS_DEPS = True
except ImportError as e:
    HAS_DEPS = False
    _import_error = str(e)


class MultimodalEncoder:
    """
    Encoder multimodale basato su CLIP (OpenAI).

    Supporta:
    - .encode_text(testo) → vettore np.ndarray shape (512,)
    - .encode_image(percorso_immagine) → vettore np.ndarray shape (512,)
    - .available → True se il modello è caricato correttamente
    """

    MODEL_NAME = "openai/clip-vit-base-patch32"
    EMBEDDING_DIM = 512

    def __init__(self, device: Optional[str] = None):
        """
        Args:
            device: "cuda", "cpu", o None (auto). Se CUDA non è disponibile,
                    usa CPU.
        """
        self._model = None
        self._processor = None
        self._device = None
        self._load_error: Optional[str] = None

        if not HAS_DEPS:
            self._load_error = (
                f"Dipendenze mancanti: {_import_error}. "
                "Installa: pip install transformers torch Pillow"
            )
            logger.warning(self._load_error)
            return

        # Determina device
        if device is None:
            device = "cuda" if torch.cuda.is_available() else "cpu"
        self._device = torch.device(device)

        try:
            logger.info(f"Caricamento {self.MODEL_NAME} su {self._device}...")
            self._model = CLIPModel.from_pretrained(self.MODEL_NAME)
            self._processor = CLIPProcessor.from_pretrained(self.MODEL_NAME)
            self._model = self._model.to(self._device)
            self._model.eval()
            logger.info("Modello CLIP caricato con successo.")
        except Exception as e:
            self._load_error = f"Errore caricamento CLIP: {e}"
            logger.error(self._load_error)
            self._model = None
            self._processor = None

    # ── Proprietà ──────────────────────────────────────────────────

    @property
    def available(self) -> bool:
        """True se il modello è caricato e pronto all'uso."""
        return self._model is not None and self._processor is not None

    @property
    def device(self) -> str:
        return str(self._device) if self._device else "N/A"

    @property
    def load_error(self) -> Optional[str]:
        return self._load_error

    # ── Encoding ───────────────────────────────────────────────────

    def encode_text(self, text: str) -> Optional[np.ndarray]:
        """
        Codifica un testo in un vettore di 512 dimensioni.

        Args:
            text: Testo da codificare.

        Returns:
            np.ndarray shape (512,) o None se errore.
        """
        if not self.available:
            logger.error("Encoder non disponibile: %s", self._load_error)
            return None
        if not text or not text.strip():
            logger.error("Testo vuoto o None.")
            return None

        try:
            inputs = self._processor(
                text=[text],
                return_tensors="pt",
                padding=True,
                truncation=True,
            ).to(self._device)

            with torch.no_grad():
                output = self._model.get_text_features(**inputs)
                if hasattr(output, 'pooler_output'):
                    embeddings = output.pooler_output
                else:
                    embeddings = output

            # Normalizza e converti in numpy
            embeddings = F.normalize(embeddings, p=2, dim=-1)
            return embeddings.cpu().numpy().flatten().astype(np.float32)
        except Exception as e:
            logger.error(f"Errore encoding testo: {e}")
            return None

    def encode_image(self, image_path: str) -> Optional[np.ndarray]:
        """
        Codifica un'immagine in un vettore di 512 dimensioni.

        Args:
            image_path: Percorso del file immagine.

        Returns:
            np.ndarray shape (512,) o None se errore.
        """
        if not self.available:
            logger.error("Encoder non disponibile: %s", self._load_error)
            return None

        path = Path(image_path)
        if not path.exists():
            logger.error(f"File immagine non trovato: {image_path}")
            return None
        if not path.is_file():
            logger.error(f"Non è un file: {image_path}")
            return None

        try:
            image = Image.open(path).convert("RGB")
        except Exception as e:
            logger.error(f"Impossibile aprire l'immagine {image_path}: {e}")
            return None

        try:
            inputs = self._processor(
                images=image,
                return_tensors="pt",
            ).to(self._device)

            with torch.no_grad():
                output = self._model.get_image_features(**inputs)
                if hasattr(output, 'pooler_output'):
                    embeddings = output.pooler_output
                else:
                    embeddings = output

            # Normalizza e converti in numpy
            embeddings = F.normalize(embeddings, p=2, dim=-1)
            return embeddings.cpu().numpy().flatten().astype(np.float32)
        except Exception as e:
            logger.error(f"Errore encoding immagine {image_path}: {e}")
            return None

    # ── Placeholder audio ──────────────────────────────────────────

    def encode_audio(self, audio_path: str) -> Optional[np.ndarray]:
        """
        Placeholder per encoding audio.
        Restituisce un vettore zero, in attesa di integrazione CLAP/ImageBind.

        Args:
            audio_path: Percorso del file audio.

        Returns:
            np.ndarray shape (512,) di zeri, o None se il file non esiste.
        """
        path = Path(audio_path)
        if not path.exists():
            logger.error(f"File audio non trovato: {audio_path}")
            return None
        logger.warning(
            "encode_audio non ancora implementato. Restituisco vettore zero."
        )
        return np.zeros(self.EMBEDDING_DIM, dtype=np.float32)

    def __repr__(self) -> str:
        status = "disponibile" if self.available else "non disponibile"
        return (
            f"<MultimodalEncoder {self.MODEL_NAME} "
            f"device={self.device} status={status}>"
        )


# ═══════════════════════════════════════════════════════════════════
#  CLI di test rapido
# ═══════════════════════════════════════════════════════════════════

if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO, format="%(levelname)s: %(message)s")

    encoder = MultimodalEncoder()

    if not encoder.available:
        print(f"[ERRORE] {encoder.load_error}", file=sys.stderr)
        sys.exit(1)

    print(f"Encoder: {encoder}")

    # Test encoding testo
    text = "un gatto che dorme su un divano"
    vec = encoder.encode_text(text)
    if vec is not None:
        print(f"Testo: '{text}' → embedding shape {vec.shape}, "
              f"norma={np.linalg.norm(vec):.4f}")

    # Test encoding immagine (se passata come arg)
    if len(sys.argv) > 1:
        img_path = sys.argv[1]
        vec = encoder.encode_image(img_path)
        if vec is not None:
            print(f"Immagine: {img_path} → embedding shape {vec.shape}, "
                  f"norma={np.linalg.norm(vec):.4f}")
