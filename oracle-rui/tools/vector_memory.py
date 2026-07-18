#!/usr/bin/env python3
"""
Vector Memory — Tool di memoria vettoriale per Oracle.

Basato su ChromaDB (embedded, zero-config).
Permette di salvare e cercare informazioni per similarità semantica
usando embedding. Progettato per essere esteso a vettori multimodali
(immagini, audio) in futuro.

Database:  ./data/vector_memory/
API:       CLI (argparse) + Python import

Usage:
    # Salvare un documento
    python tools/vector_memory.py add --collection notes --id doc1 \
        --text "Il cielo è blu perché la luce solare si diffonde" \
        --metadata '{"fonte": "libro_fisica", "anno": 2024}'

    # Cercare per similarità
    python tools/vector_memory.py search --collection notes \
        --query "Perché il cielo è azzurro?" --top-k 5

    # Elencare collezioni
    python tools/vector_memory.py list-collections

    # Eliminare una collezione
    python tools/vector_memory.py delete-collection --name notes

    # Eliminare un documento
    python tools/vector_memory.py delete --collection notes --id doc1

    # Ottenere un documento per ID
    python tools/vector_memory.py get --collection notes --id doc1

    # Contare documenti
    python tools/vector_memory.py count --collection notes

    # Info sul database
    python tools/vector_memory.py info
"""

import argparse
import json
import logging
import os
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("vector_memory")

# ── ChromaDB ──────────────────────────────────────────────────────
try:
    import chromadb
    from chromadb.config import Settings
    HAS_CHROMA = True
except ImportError:
    HAS_CHROMA = False

# ── Paths ─────────────────────────────────────────────────────────
BASE_DIR = Path(__file__).parent.parent.resolve()
VECTOR_DB_DIR = BASE_DIR / "data" / "vector_memory"

# ── Multimodal Encoder (opzionale) ─────────────────────────────────
try:
    sys.path.insert(0, str(BASE_DIR))
    from tools.multimodal_encoder import MultimodalEncoder
    HAS_ENCODER = True
except ImportError:
    HAS_ENCODER = False


# ── Custom embedding function per ChromaDB usando CLIP ───────────
class CLIPEmbeddingFunction(chromadb.EmbeddingFunction[list[str]]):
    """
    Wrapper ChromaDB EmbeddingFunction che usa MultimodalEncoder (CLIP)
    per generare embedding di 512 dimensioni.

    Usato sia per testo che come funzione di embedding della collezione,
    così testo e immagini condividono lo stesso spazio vettoriale.
    """

    def __init__(self, encoder: Optional[MultimodalEncoder] = None):
        self._encoder = encoder or MultimodalEncoder()

    @property
    def available(self) -> bool:
        return self._encoder.available

    @staticmethod
    def name() -> str:
        return "CLIP_512"

    def __call__(self, input: list[str]) -> list[list[float]]:
        results = []
        for text in input:
            vec = self._encoder.encode_text(text)
            if vec is not None:
                results.append(vec.tolist())
            else:
                results.append([0.0] * MultimodalEncoder.EMBEDDING_DIM)
        return results


# ═══════════════════════════════════════════════════════════════════
#  VectorMemoryEngine
# ═══════════════════════════════════════════════════════════════════

class VectorMemoryEngine:
    """
    Motore di memoria vettoriale multimodale.

    Astrazione su ChromaDB che permette di:
    - Salvare testi con embedding automatico
    - Cercare per similarità semantica
    - Gestire metadati strutturati
    - Supportare estensioni future (immagini, audio) → basta cambiare
      la funzione di embedding e il tipo di input.

    Attributi di classe per estensibilità futura:
        MODALITY_TEXT    = "text"
        MODALITY_IMAGE   = "image"    # riservato
        MODALITY_AUDIO   = "audio"    # riservato
    """

    MODALITY_TEXT = "text"
    MODALITY_IMAGE = "image"
    MODALITY_AUDIO = "audio"
    DEFAULT_DEDUP_THRESHOLD = 0.90
    DEFAULT_MAX_DOCS = 10000
    DEFAULT_TTL_DAYS = 30

    def __init__(self, persist_dir: Optional[Path] = None):
        if not HAS_CHROMA:
            raise ImportError(
                "ChromaDB non è installato. Esegui: pip install chromadb"
            )

        self.persist_dir = Path(persist_dir or VECTOR_DB_DIR)
        self.persist_dir.mkdir(parents=True, exist_ok=True)

        # ChromaDB 1.5+ non usa più Settings(persist_directory) direttamente
        # ma il costruttore di PersistentClient
        self._client = chromadb.PersistentClient(
            path=str(self.persist_dir),
            settings=Settings(anonymized_telemetry=False),
        )

        # Crea embedding function CLIP se disponibile, altrimenti None
        # (ChromaDB userà il default all-MiniLM via ONNX)
        self._clip_ef = None
        if HAS_ENCODER:
            enc = MultimodalEncoder()
            if enc.available:
                self._clip_ef = CLIPEmbeddingFunction(enc)

    # ── Properties ────────────────────────────────────────────────

    @property
    def client(self):
        """Accesso diretto al client ChromaDB sottostante."""
        return self._client

    @property
    def db_path(self) -> Path:
        return self.persist_dir

    # ── Collection Management ─────────────────────────────────────

    def get_or_create_collection(self, name: str, metadata: Optional[dict] = None):
        """Ottiene o crea una collezione."""
        meta = metadata if metadata else None
        ef = self._clip_ef if self._clip_ef else None
        return self._client.get_or_create_collection(
            name=name,
            metadata=meta,
            embedding_function=ef,
        )

    def list_collections(self) -> list[dict]:
        """Lista tutte le collezioni con metadati."""
        collections = self._client.list_collections()
        result = []
        for c in collections:
            result.append({
                "name": c.name,
                "metadata": c.metadata or {},
                "count": c.count(),
            })
        return result

    def delete_collection(self, name: str) -> bool:
        """Elimina una collezione. Ritorna True se esisteva."""
        try:
            self._client.delete_collection(name)
            return True
        except ValueError:
            return False

    # ── CRUD Operations ───────────────────────────────────────────

    def add_text(
        self,
        collection: str,
        document_id: str,
        text: str,
        metadata: Optional[dict] = None,
        modality: str = MODALITY_TEXT,
    ) -> dict:
        """
        Aggiunge un documento testuale alla collezione.

        Args:
            collection: Nome della collezione.
            document_id: ID univoco del documento.
            text: Contenuto testuale da indicizzare.
            metadata: Dizionario con metadati associati.
            modality: Modalità del contenuto (default: text).

        Returns:
            Dict con ID e stato.
        """
        coll = self.get_or_create_collection(collection)
        meta = dict(metadata or {})
        meta["_modality"] = modality
        meta["_created_at"] = datetime.utcnow().isoformat()

        try:
            existing = coll.get(ids=[document_id])
            if existing["ids"]:
                coll.update(
                    ids=[document_id],
                    documents=[text],
                    metadatas=[meta],
                )
                action = "updated"
            else:
                coll.add(
                    ids=[document_id],
                    documents=[text],
                    metadatas=[meta],
                )
                action = "added"
        except Exception:
            coll.add(
                ids=[document_id],
                documents=[text],
                metadatas=[meta],
            )
            action = "added"

        return {
            "id": document_id,
            "collection": collection,
            "action": action,
            "modality": modality,
        }

    def add_embedding(
        self,
        collection: str,
        document_id: str,
        embedding: list[float],
        metadata: Optional[dict] = None,
        modality: str = MODALITY_IMAGE,
        document: Optional[str] = None,
    ) -> dict:
        """
        Aggiunge un vettore pre-calcolato (es. da immagine o audio).

        Args:
            collection: Nome della collezione.
            document_id: ID univoco del documento.
            embedding: Vettore di embedding (lista di float).
            metadata: Dizionario con metadati associati.
            modality: Modalità del contenuto (default: image).
            document: Testo descrittivo opzionale.

        Returns:
            Dict con ID e stato.
        """
        coll = self.get_or_create_collection(collection)
        meta = dict(metadata or {})
        meta["_modality"] = modality
        meta["_created_at"] = datetime.utcnow().isoformat()

        docs = [document] if document else None

        try:
            existing = coll.get(ids=[document_id])
            if existing["ids"]:
                coll.update(
                    ids=[document_id],
                    embeddings=[embedding],
                    metadatas=[meta],
                    documents=docs,
                )
                action = "updated"
            else:
                coll.add(
                    ids=[document_id],
                    embeddings=[embedding],
                    metadatas=[meta],
                    documents=docs,
                )
                action = "added"
        except Exception:
            coll.add(
                ids=[document_id],
                embeddings=[embedding],
                metadatas=[meta],
                documents=docs,
            )
            action = "added"

        return {
            "id": document_id,
            "collection": collection,
            "action": action,
            "modality": modality,
        }

    # ═══════════════════════════════════════════════════════════════
    #  Image Lifecycle Management
    # ═══════════════════════════════════════════════════════════════

    # ── Collection Config ───────────────────────────────────────

    def _get_collection_config(self, collection_name: str) -> dict:
        """Recupera la configurazione di retention dalla collezione."""
        coll = self.get_or_create_collection(collection_name)
        meta = coll.metadata or {}
        return {
            "max_docs": meta.get("_config_max_docs"),
            "ttl_days": meta.get("_config_ttl_days"),
            "dedup_threshold": meta.get("_config_dedup_threshold", self.DEFAULT_DEDUP_THRESHOLD),
        }

    def _set_collection_config(
        self,
        collection_name: str,
        max_docs: Optional[int] = None,
        ttl_days: Optional[int] = None,
        dedup_threshold: Optional[float] = None,
    ):
        """Salva la configurazione di retention nei metadata della collezione."""
        coll = self.get_or_create_collection(collection_name)
        meta = dict(coll.metadata or {})
        modified = False
        if max_docs is not None:
            meta["_config_max_docs"] = max_docs
            modified = True
        if ttl_days is not None:
            meta["_config_ttl_days"] = ttl_days
            modified = True
        if dedup_threshold is not None:
            meta["_config_dedup_threshold"] = dedup_threshold
            modified = True
        if modified:
            coll.modify(metadata=meta)

    # ── Deduplicazione semantica ─────────────────────────────────

    def _find_similar_image(
        self,
        collection: str,
        embedding: list[float],
        threshold: float = 0.90,
    ) -> Optional[dict]:
        """
        Cerca nella collezione immagini semanticamente simili.
        Se trova un match con similarità >= threshold, lo restituisce.
        """
        coll = self.get_or_create_collection(collection)
        try:
            results = coll.query(
                query_embeddings=[embedding],
                n_results=1,
                include=["distances", "metadatas", "documents"],
            )
            if results["ids"] and results["ids"][0]:
                distance = results["distances"][0][0]
                similarity = max(0.0, 1.0 - distance / 2.0)
                if similarity >= threshold:
                    return {
                        "id": results["ids"][0][0],
                        "similarity": round(similarity, 4),
                        "distance": distance,
                        "metadata": results["metadatas"][0][0] if results.get("metadatas") else None,
                        "text": results["documents"][0][0] if results.get("documents") else None,
                    }
        except Exception:
            pass
        return None

    # ── Retention enforcement ───────────────────────────────────

    def _enforce_retention(self, collection: str) -> dict:
        """
        Applica le policy di retention sulla collezione:
        - Se supera max_docs, elimina i documenti più vecchi
        - Se ci sono documenti con età > ttl_days, li elimina
        """
        config = self._get_collection_config(collection)
        coll = self.get_or_create_collection(collection)
        max_docs = config.get("max_docs")
        ttl_days = config.get("ttl_days")

        if not max_docs and not ttl_days:
            return {"deleted": 0, "reason": "nessuna policy configurata"}

        # Recupera tutti i documenti con metadati
        all_data = coll.get(limit=100000, include=["metadatas"])
        if not all_data["ids"]:
            return {"deleted": 0}

        now = datetime.utcnow()
        to_delete = []
        deleted_files = []

        # ── TTL check ──
        if ttl_days:
            for i, doc_id in enumerate(all_data["ids"]):
                meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
                created_at = meta.get("_created_at")
                if created_at:
                    try:
                        created = datetime.fromisoformat(created_at)
                        age = (now - created).days
                        if age > ttl_days:
                            to_delete.append(doc_id)
                            img_path = meta.get("_image_path")
                            if img_path and Path(img_path).exists():
                                deleted_files.append(img_path)
                    except ValueError:
                        pass

        # ── Max docs check ── (solo se non abbiamo già eliminato abbastanza)
        remaining = [d for d in all_data["ids"] if d not in to_delete]
        if max_docs and len(remaining) > max_docs:
            docs_with_time = []
            for i, doc_id in enumerate(all_data["ids"]):
                meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
                if doc_id in to_delete:
                    continue
                created_at = meta.get("_created_at", "2000-01-01T00:00:00")
                docs_with_time.append((doc_id, created_at, meta.get("_image_path")))

            docs_with_time.sort(key=lambda x: x[1])  # più vecchi prima
            excess = len(docs_with_time) - max_docs
            if excess > 0:
                for i in range(excess):
                    doc_id, _, img_path = docs_with_time[i]
                    to_delete.append(doc_id)
                    if img_path and Path(img_path).exists():
                        deleted_files.append(img_path)

        # ── Esegue la cancellazione ──
        if to_delete:
            try:
                coll.delete(ids=to_delete)
                for fp in deleted_files:
                    try:
                        Path(fp).unlink(missing_ok=True)
                    except Exception:
                        pass
                return {"deleted": len(to_delete), "files_removed": len(deleted_files)}
            except Exception as e:
                return {"deleted": 0, "error": str(e)}

        return {"deleted": 0}

    # ── Add Image (completo con dedup + retention + auto-cleanup) ─

    def add_image(
        self,
        collection: str,
        document_id: str,
        image_path: str,
        metadata: Optional[dict] = None,
        text: Optional[str] = None,
        dedup_threshold: Optional[float] = None,
        keep_files: bool = True,
        max_docs: Optional[int] = None,
        ttl_days: Optional[int] = None,
    ) -> dict:
        """
        Aggiunge un'immagine alla collezione con:
        - Deduplicazione semantica (CLIP-based)
        - Retention policy (max_docs, ttl_days)
        - Auto-cleanup opzionale del file originale

        Args:
            collection: Nome della collezione.
            document_id: ID univoco del documento.
            image_path: Percorso del file immagine.
            metadata: Metadati associati.
            text: Descrizione testuale opzionale.
            dedup_threshold: Soglia di similarità per dedup (0.0-1.0).
                             Default: config della collezione o 0.90.
            keep_files: Se True mantiene il file originale; se False lo cancella.
            max_docs: Numero massimo di documenti nella collezione.
            ttl_days: Giorni di vita massimi per ogni documento.
        """
        from tools.multimodal_encoder import MultimodalEncoder

        encoder = MultimodalEncoder()
        if not encoder.available:
            return {"status": "error", "error": f"Encoder non disponibile: {encoder.load_error}"}

        # Verifica che il file esista
        img_path_obj = Path(image_path)
        if not img_path_obj.exists():
            return {"status": "error", "error": f"File non trovato: {image_path}"}

        # Codifica l'immagine
        embedding = encoder.encode_image(image_path)
        if embedding is None:
            return {"status": "error", "error": f"Impossibile codificare immagine: {image_path}"}

        # Determina soglia di deduplicazione
        config = self._get_collection_config(collection)
        threshold = dedup_threshold if dedup_threshold is not None else config.get("dedup_threshold", self.DEFAULT_DEDUP_THRESHOLD)

        # ── Deduplicazione ──
        duplicate = self._find_similar_image(collection, embedding.tolist(), threshold)
        if duplicate:
            return {
                "status": "skipped",
                "reason": "duplicate",
                "duplicate_of": duplicate["id"],
                "similarity": duplicate["similarity"],
            }

        # Prepara metadati
        meta = dict(metadata or {})
        meta["_image_path"] = str(img_path_obj.resolve())
        meta["_modality"] = self.MODALITY_IMAGE
        meta["_created_at"] = datetime.utcnow().isoformat()

        # Salva embedding in ChromaDB
        result = self.add_embedding(
            collection=collection,
            document_id=document_id,
            embedding=embedding.tolist(),
            metadata=meta,
            modality=self.MODALITY_IMAGE,
            document=text,
        )

        # Salva configurazione retention nei metadata della collezione
        has_policy = max_docs is not None or ttl_days is not None or dedup_threshold is not None
        if has_policy:
            self._set_collection_config(
                collection,
                max_docs=max_docs,
                ttl_days=ttl_days,
                dedup_threshold=dedup_threshold,
            )

        # Applica retention (potrebbe eliminare documenti vecchi)
        retention = self._enforce_retention(collection)

        # Auto-cleanup file originale (se richiesto)
        if not keep_files:
            try:
                img_path_obj.unlink(missing_ok=True)
                result["original_file_deleted"] = True
            except Exception as e:
                result["original_file_deleted"] = False
                result["file_delete_error"] = str(e)

        result["status"] = "added"
        result["retention"] = retention
        return result

    # ── Image Statistics ───────────────────────────────────────

    def get_image_stats(self, collection: str) -> dict:
        """
        Statistiche dettagliate delle immagini in una collezione.
        """
        coll = self.get_or_create_collection(collection)
        all_data = coll.get(limit=100000, include=["metadatas"])
        total_images = 0
        total_file_size = 0
        image_files = []
        oldest = None
        newest = None

        for i, doc_id in enumerate(all_data["ids"]):
            meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
            modality = meta.get("_modality", "text")
            if modality == self.MODALITY_IMAGE:
                total_images += 1
                img_path = meta.get("_image_path")
                created_at_str = meta.get("_created_at")
                if img_path:
                    p = Path(img_path)
                    entry = {
                        "id": doc_id,
                        "path": str(p),
                        "exists": p.exists(),
                        "created_at": created_at_str,
                    }
                    if p.exists():
                        entry["size_bytes"] = p.stat().st_size
                        total_file_size += entry["size_bytes"]
                    image_files.append(entry)
                if created_at_str:
                    try:
                        dt = datetime.fromisoformat(created_at_str)
                        if oldest is None or dt < oldest:
                            oldest = dt
                        if newest is None or dt > newest:
                            newest = dt
                    except ValueError:
                        pass

        config = self._get_collection_config(collection)
        return {
            "collection": collection,
            "total_images": total_images,
            "total_documents": len(all_data["ids"]),
            "file_size_bytes": total_file_size,
            "file_size_human": self._human_size(total_file_size),
            "oldest": oldest.isoformat() if oldest else None,
            "newest": newest.isoformat() if newest else None,
            "config": config,
            "image_files": image_files,
        }

    @staticmethod
    def _human_size(size_bytes: int) -> str:
        """Converte byte in formato leggibile."""
        if size_bytes == 0:
            return "0 B"
        units = ["B", "KB", "MB", "GB", "TB"]
        size = float(size_bytes)
        for unit in units:
            if size < 1024:
                return f"{size:.1f} {unit}"
            size /= 1024
        return f"{size:.1f} TB"

    # ── Cleanup Collection ─────────────────────────────────────

    def cleanup_collection(self, collection: str, force: bool = False) -> dict:
        """
        Pulizia forzata della collezione:
        - Applica TTL e max_docs
        - Se force=True, cerca e rimuove file immagine orfani
        """
        retention_result = self._enforce_retention(collection)

        orphan_files = []
        if force:
            coll = self.get_or_create_collection(collection)
            all_data = coll.get(limit=100000, include=["metadatas"])
            tracked_paths = set()
            for i in range(len(all_data["ids"])):
                meta = all_data["metadatas"][i] if all_data["metadatas"] else {}
                img_path = meta.get("_image_path")
                if img_path:
                    tracked_paths.add(str(Path(img_path).resolve()))

            # Cerca nella directory data/images/collection per orfani
            data_dir = BASE_DIR / "data" / "images" / collection
            if data_dir.exists():
                for f in data_dir.iterdir():
                    if f.is_file() and f.suffix.lower() in {".jpg", ".jpeg", ".png", ".gif", ".webp", ".bmp"}:
                        if str(f.resolve()) not in tracked_paths:
                            orphan_files.append(str(f))
                            try:
                                f.unlink()
                            except Exception:
                                pass

        return {
            "retention_deleted": retention_result.get("deleted", 0),
            "orphan_files_removed": len(orphan_files),
            "orphan_files": orphan_files,
        }

    # ── Policy Management ──────────────────────────────────────

    def set_collection_policy(
        self,
        collection: str,
        max_docs: Optional[int] = None,
        ttl_days: Optional[int] = None,
        dedup_threshold: Optional[float] = None,
    ) -> dict:
        """Imposta la policy di retention per una collezione."""
        self._set_collection_config(collection, max_docs, ttl_days, dedup_threshold)
        return {
            "collection": collection,
            "max_docs": max_docs,
            "ttl_days": ttl_days,
            "dedup_threshold": dedup_threshold,
        }

    def get_document(self, collection: str, document_id: str) -> Optional[dict]:
        """Recupera un documento per ID."""
        coll = self.get_or_create_collection(collection)
        result = coll.get(ids=[document_id])
        if not result["ids"]:
            return None
        return {
            "id": result["ids"][0],
            "text": result["documents"][0] if result["documents"] else None,
            "metadata": result["metadatas"][0] if result["metadatas"] else {},
        }

    def delete_document(self, collection: str, document_id: str) -> bool:
        """Elimina un documento. Ritorna True se esisteva."""
        coll = self.get_or_create_collection(collection)
        try:
            coll.delete(ids=[document_id])
            return True
        except Exception:
            return False

    def count(self, collection: str) -> int:
        """Conta i documenti in una collezione."""
        coll = self.get_or_create_collection(collection)
        return coll.count()

    # ── Semantic Search ───────────────────────────────────────────

    def search(
        self,
        collection: str,
        query: str,
        top_k: int = 5,
        include_metadata: bool = True,
        include_text: bool = True,
        distance_threshold: Optional[float] = None,
    ) -> list[dict]:
        """
        Cerca documenti per similarità semantica.

        Args:
            collection: Nome della collezione.
            query: Testo della query.
            top_k: Numero massimo di risultati.
            include_metadata: Includere i metadati.
            include_text: Includere il testo originale.
            distance_threshold: Filtra risultati con distanza > soglia.

        Returns:
            Lista di dict con id, text, metadata, distance.
        """
        coll = self.get_or_create_collection(collection)

        includes = ["distances"]
        if include_metadata:
            includes.append("metadatas")
        if include_text:
            includes.append("documents")

        results = coll.query(
            query_texts=[query],
            n_results=top_k,
            include=includes,
        )

        # ChromaDB ritorna liste annidate [[...]]
        ids = results["ids"][0] if results["ids"] else []
        distances = results["distances"][0] if results.get("distances") else []
        documents = results["documents"][0] if results.get("documents") else []
        metadatas = results["metadatas"][0] if results.get("metadatas") else []

        output = []
        for i, doc_id in enumerate(ids):
            distance = distances[i] if i < len(distances) else 0.0

            # Filtro per soglia di distanza
            if distance_threshold is not None and distance > distance_threshold:
                continue

            # Per vettori normalizzati, L2 è in [0, 2]; convertiamo in [0,1]
            sim = max(0.0, 1.0 - distance / 2.0)
            item = {
                "id": doc_id,
                "distance": distance,
                "similarity": round(sim, 4),
            }
            if include_text and i < len(documents):
                item["text"] = documents[i]
            if include_metadata and i < len(metadatas):
                item["metadata"] = metadatas[i]
            output.append(item)

        return output

    # ── Statistics ────────────────────────────────────────────────

    def get_stats(self) -> dict:
        """Statistiche del database vettoriale."""
        collections = self.list_collections()
        total_docs = sum(c["count"] for c in collections)
        db_size = sum(
            f.stat().st_size for f in self.persist_dir.rglob("*") if f.is_file()
        )
        return {
            "db_path": str(self.persist_dir),
            "total_collections": len(collections),
            "total_documents": total_docs,
            "db_size_bytes": db_size,
            "collections": collections,
        }


# ═══════════════════════════════════════════════════════════════════
#  CLI Interface
# ═══════════════════════════════════════════════════════════════════

def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="vector_memory",
        description="Vector Memory — Memoria vettoriale per Oracle",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog=__doc__,
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # ── add ───────────────────────────────────────────────────────
    p_add = sub.add_parser("add", help="Aggiunge/aggiorna un documento")
    p_add.add_argument("--collection", "-c", required=True, help="Nome collezione")
    p_add.add_argument("--id", "-i", required=True, help="ID documento")
    p_add.add_argument("--text", "-t", help="Contenuto testuale (opzionale se si usa --image o --audio)")
    p_add.add_argument("--image", help="Percorso immagine per encoding multimodale")
    p_add.add_argument("--audio", help="Percorso audio per encoding multimodale (placeholder)")
    p_add.add_argument("--metadata", "-m", help="Metadati JSON (es. '{\"k\":\"v\"}')")
    # Flag per Image Lifecycle Management
    p_add.add_argument("--dedup-threshold", type=float, help="Soglia deduplicazione (0.0-1.0, default 0.90)")
    p_add.add_argument("--no-keep-files", action="store_true", help="Cancella il file originale dopo l'encoding")
    p_add.add_argument("--max-docs", type=int, help="Numero massimo documenti nella collezione")
    p_add.add_argument("--ttl-days", type=int, help="Giorni di retention massimi per documento")
    p_add.add_argument("--set-policy", action="store_true", help="Salva max-docs / ttl-days / dedup-threshold come policy permanente della collezione")

    # ── search ────────────────────────────────────────────────────
    p_search = sub.add_parser("search", help="Cerca documenti per similarità")
    p_search.add_argument("--collection", "-c", required=True, help="Nome collezione")
    p_search.add_argument("--query", "-q", required=True, help="Testo della query")
    p_search.add_argument("--top-k", "-k", type=int, default=5, help="Numero risultati")
    p_search.add_argument("--threshold", type=float, help="Soglia distanza massima")
    p_search.add_argument("--raw", action="store_true", help="Output JSON raw")
    p_search.add_argument("--no-text", action="store_true", help="Escludi testo")
    p_search.add_argument("--no-metadata", action="store_true", help="Escludi metadati")

    # ── get ───────────────────────────────────────────────────────
    p_get = sub.add_parser("get", help="Recupera documento per ID")
    p_get.add_argument("--collection", "-c", required=True, help="Nome collezione")
    p_get.add_argument("--id", "-i", required=True, help="ID documento")

    # ── delete ────────────────────────────────────────────────────
    p_del = sub.add_parser("delete", help="Elimina un documento")
    p_del.add_argument("--collection", "-c", required=True)
    p_del.add_argument("--id", "-i", required=True)

    # ── list-collections ──────────────────────────────────────────
    sub.add_parser("list-collections", help="Elenca collezioni")

    # ── delete-collection ─────────────────────────────────────────
    p_dc = sub.add_parser("delete-collection", help="Elimina una collezione")
    p_dc.add_argument("--name", "-n", required=True)

    # ── count ─────────────────────────────────────────────────────
    p_cnt = sub.add_parser("count", help="Conta documenti in una collezione")
    p_cnt.add_argument("--collection", "-c", required=True)

    # ── info ──────────────────────────────────────────────────────
    sub.add_parser("info", help="Statistiche del database")

    # ── image-stats ───────────────────────────────────────────────
    p_istats = sub.add_parser("image-stats", help="Statistiche dettagliate immagini in una collezione")
    p_istats.add_argument("--collection", "-c", required=True, help="Nome collezione")

    # ── image-cleanup ─────────────────────────────────────────────
    p_iclean = sub.add_parser("image-cleanup", help="Pulisce una collezione (TTL, max_docs, orfani)")
    p_iclean.add_argument("--collection", "-c", required=True, help="Nome collezione")
    p_iclean.add_argument("--force", "-f", action="store_true", help="Includi rimozione file orfani")

    # ── set-policy ────────────────────────────────────────────────
    p_policy = sub.add_parser("set-policy", help="Imposta policy di retention per una collezione")
    p_policy.add_argument("--collection", "-c", required=True, help="Nome collezione")
    p_policy.add_argument("--max-docs", type=int, help="Numero massimo documenti")
    p_policy.add_argument("--ttl-days", type=int, help="Giorni di retention")
    p_policy.add_argument("--dedup-threshold", type=float, help="Soglia deduplicazione (0.0-1.0)")

    return parser


def main():
    parser = build_parser()
    args = parser.parse_args()

    try:
        engine = VectorMemoryEngine()
    except ImportError as e:
        print(f"[ERROR] {e}", file=sys.stderr)
        sys.exit(1)

    if args.command == "add":
        metadata = None
        if args.metadata:
            try:
                metadata = json.loads(args.metadata)
            except json.JSONDecodeError as e:
                print(f"[ERROR] Metadati JSON non validi: {e}", file=sys.stderr)
                sys.exit(1)

        # ── Modalità immagine (con dedup + retention + auto-cleanup) ──
        if args.image:
            # Determina se --set-policy è stato usato (salva config nella collezione)
            max_docs = args.max_docs
            ttl_days = args.ttl_days
            dedup_threshold = args.dedup_threshold
            if args.set_policy:
                # Salva la policy nella collezione anche se i valori sono i default
                if max_docs is None:
                    max_docs = VectorMemoryEngine.DEFAULT_MAX_DOCS
                if ttl_days is None:
                    ttl_days = VectorMemoryEngine.DEFAULT_TTL_DAYS
                if dedup_threshold is None:
                    dedup_threshold = VectorMemoryEngine.DEFAULT_DEDUP_THRESHOLD

            result = engine.add_image(
                collection=args.collection,
                document_id=args.id,
                image_path=args.image,
                metadata=metadata,
                text=args.text,
                dedup_threshold=dedup_threshold,
                keep_files=not args.no_keep_files,
                max_docs=max_docs,
                ttl_days=ttl_days,
            )

        # ── Modalità audio ──
        elif args.audio:
            if not HAS_ENCODER:
                print("[ERROR] MultimodalEncoder non disponibile. Install: pip install transformers torch Pillow", file=sys.stderr)
                sys.exit(1)
            encoder = MultimodalEncoder()
            if not encoder.available:
                print(f"[ERROR] Encoder non disponibile: {encoder.load_error}", file=sys.stderr)
                sys.exit(1)
            embedding = encoder.encode_audio(args.audio)
            if embedding is None:
                print(f"[ERROR] Impossibile codificare l'audio: {args.audio}", file=sys.stderr)
                sys.exit(1)
            meta = dict(metadata or {})
            meta["_audio_path"] = str(Path(args.audio).resolve())
            result = engine.add_embedding(
                collection=args.collection,
                document_id=args.id,
                embedding=embedding.tolist(),
                metadata=meta,
                modality=VectorMemoryEngine.MODALITY_AUDIO,
                document=args.text,
            )

        # ── Modalità testo (default) ──
        elif args.text:
            result = engine.add_text(
                collection=args.collection,
                document_id=args.id,
                text=args.text,
                metadata=metadata,
            )

        else:
            print("[ERROR] Specifica --text, --image o --audio.", file=sys.stderr)
            sys.exit(1)

        print(json.dumps(result, indent=2))

    elif args.command == "search":
        results = engine.search(
            collection=args.collection,
            query=args.query,
            top_k=args.top_k,
            include_metadata=not args.no_metadata,
            include_text=not args.no_text,
            distance_threshold=args.threshold,
        )
        if args.raw:
            print(json.dumps(results, indent=2))
        else:
            if not results:
                print("Nessun risultato trovato.")
                return
            print(f"\n{'='*70}")
            print(f"  Risultati per: \"{args.query}\"")
            print(f"  Collezione: {args.collection}")
            print(f"{'='*70}")
            for i, r in enumerate(results, 1):
                sim_pct = r["similarity"] * 100
                print(f"\n  [{i}] ID: {r['id']}  (similarità: {sim_pct:.1f}%)")
                if "text" in r and r["text"] is not None:
                    text = r["text"]
                    if len(text) > 300:
                        text = text[:297] + "..."
                    print(f"      Testo: {text}")
                if "metadata" in r and r["metadata"]:
                    m = r["metadata"]
                    # Escludi metadati interni
                    display_meta = {k: v for k, v in m.items() if not k.startswith("_")}
                    if display_meta:
                        print(f"      Metadati: {json.dumps(display_meta)}")
            print()

    elif args.command == "get":
        doc = engine.get_document(args.collection, args.id)
        if doc is None:
            print(json.dumps({"error": "Documento non trovato"}, indent=2))
            sys.exit(1)
        print(json.dumps(doc, indent=2, default=str))

    elif args.command == "delete":
        ok = engine.delete_document(args.collection, args.id)
        print(json.dumps({"deleted": ok, "id": args.id, "collection": args.collection}, indent=2))

    elif args.command == "list-collections":
        collections = engine.list_collections()
        if not collections:
            print("Nessuna collezione presente.")
            return
        print(f"\n{'='*50}")
        print(f"  Collezioni ({len(collections)})")
        print(f"{'='*50}")
        for c in collections:
            print(f"  [C] {c['name']:30s}  {c['count']:>6d} documenti")
        print()

    elif args.command == "delete-collection":
        ok = engine.delete_collection(args.name)
        print(json.dumps({"deleted": ok, "name": args.name}, indent=2))

    elif args.command == "count":
        n = engine.count(args.collection)
        print(json.dumps({"collection": args.collection, "count": n}, indent=2))

    elif args.command == "info":
        stats = engine.get_stats()
        print(f"\n{'='*50}")
        print("  Vector Memory — Database Info")
        print(f"{'='*50}")
        print(f"  Percorso:    {stats['db_path']}")
        print(f"  Dimensione:  {stats['db_size_bytes']:,} bytes")
        print(f"  Collezioni:  {stats['total_collections']}")
        print(f"  Documenti:   {stats['total_documents']}")
        print()
        if stats["collections"]:
            print(f"  {'Collezione':25s} {'Documenti':>10s}")
            print(f"  {'-'*25} {'-'*10}")
            for c in stats["collections"]:
                print(f"  {c['name']:25s} {c['count']:>10d}")
        print()

    elif args.command == "image-stats":
        stats = engine.get_image_stats(args.collection)
        print(f"\n{'='*60}")
        print(f"  Image Stats — Collezione: {stats['collection']}")
        print(f"{'='*60}")
        print(f"  Documenti totali:   {stats['total_documents']}")
        print(f"  Di cui immagini:    {stats['total_images']}")
        print(f"  Spazio su disco:    {stats['file_size_human']} ({stats['file_size_bytes']:,} bytes)")
        print(f"  Documento + vecchio: {stats['oldest'] or 'N/A'}")
        print(f"  Documento + recente: {stats['newest'] or 'N/A'}")
        print(f"\n  --- Policy attive ---")
        cfg = stats['config']
        print(f"  Max docs:      {cfg.get('max_docs') or 'nessun limite'}")
        print(f"  TTL (giorni):  {cfg.get('ttl_days') or 'nessun limite'}")
        print(f"  Soglia dedup:  {cfg.get('dedup_threshold', 'N/A')}")
        print(f"\n  --- File immagine ({len(stats['image_files'])}) ---")
        for f in stats['image_files']:
            status = "[OK]" if f['exists'] else "[DEL]"
            size = f.get('size_bytes', 0)
            print(f"  {status:5s} {f['id']:20s} {f['path']}")
            print(f"         size={size:,} bytes  creato={f['created_at']}")
        print()

    elif args.command == "image-cleanup":
        result = engine.cleanup_collection(args.collection, force=args.force)
        print(f"\n{'='*50}")
        print(f"  Cleanup — Collezione: {args.collection}")
        print(f"{'='*50}")
        print(f"  Documenti eliminati (retention): {result['retention_deleted']}")
        print(f"  File orfani rimossi:             {result['orphan_files_removed']}")
        if result['orphan_files']:
            print(f"  File rimossi:")
            for fp in result['orphan_files']:
                print(f"    - {fp}")
        print()

    elif args.command == "set-policy":
        result = engine.set_collection_policy(
            collection=args.collection,
            max_docs=args.max_docs,
            ttl_days=args.ttl_days,
            dedup_threshold=args.dedup_threshold,
        )
        print(json.dumps(result, indent=2))

    else:
        parser.print_help()
        sys.exit(1)


if __name__ == "__main__":
    main()