"""Reader read-only della ChromaDB di Penelope.

Legge gli embedding delle immagini già processati da Penelope.
Nessuna operazione di scrittura.
"""

from __future__ import annotations

import logging
from pathlib import Path
from typing import Any, Optional

import chromadb
from chromadb.config import Settings

logger = logging.getLogger(__name__)


class PenelopeChromaReader:
    """Connessione read-only alla ChromaDB di Penelope.

    Legge le collezioni di embedding (immagini, testo) senza mai scrivere.
    """

    def __init__(self, persist_dir: Optional[str | Path] = None) -> None:
        if persist_dir:
            self._persist_dir = Path(persist_dir)
        else:
            # Default: cerca nella directory di Penelope
            default = (
                Path(__file__).resolve().parent.parent.parent.parent
                / "Penelope" / "data" / "chroma"
            )
            self._persist_dir = default

        if not self._persist_dir.exists():
            logger.warning("ChromaDB Penelope non trovata: %s", self._persist_dir)
            self._client = None
        else:
            self._client = chromadb.PersistentClient(
                path=str(self._persist_dir),
                settings=Settings(anonymized_telemetry=False),
            )
            logger.info("ChromaDB Penelope caricata: %s", self._persist_dir)

    def get_collections(self) -> list[str]:
        """Lista delle collezioni disponibili."""
        if self._client is None:
            return []
        return [c.name for c in self._client.list_collections()]

    def query_images(
        self,
        query_embedding: list[float],
        collection_name: str = "image_embeddings",
        top_k: int = 20,
    ) -> list[dict[str, Any]]:
        """Cerca immagini per similarità di embedding.

        Args:
            query_embedding: Embedding 512-dim da confrontare.
            collection_name: Collezione ChromaDB.
            top_k: Numero risultati.

        Returns:
            Lista di dict con: id, metadata, distance.
        """
        if self._client is None:
            return []
        try:
            collection = self._client.get_collection(collection_name)
        except Exception:
            logger.warning("Collezione '%s' non trovata", collection_name)
            return []

        try:
            results = collection.query(
                query_embeddings=[query_embedding],
                n_results=top_k,
            )
        except Exception as e:
            logger.error("Errore query ChromaDB: %s", e)
            return []

        output = []
        for i in range(len(results["ids"][0])):
            output.append({
                "id": results["ids"][0][i],
                "metadata": results["metadatas"][0][i] if results["metadatas"] else {},
                "distance": results["distances"][0][i] if results["distances"] else 0.0,
            })
        return output

    def count_images(self, collection_name: str = "image_embeddings") -> int:
        """Numero di immagini indicizzate in ChromaDB."""
        if self._client is None:
            return 0
        try:
            collection = self._client.get_collection(collection_name)
            return collection.count()
        except Exception:
            return 0

    def close(self) -> None:
        if self._client:
            try:
                self._client.clear_system_cache()
            except Exception:
                pass
