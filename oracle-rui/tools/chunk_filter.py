#!/usr/bin/env python3
"""
ChunkFilter — Modello-figlio per filtraggio chunk contesto.

Sistema di filtraggio che valuta la rilevanza di ogni chunk recuperato
rispetto alla query utente, prima di inviarlo al modello GLM.

Architettura:
  ┌─────────┐     ┌──────────────┐     ┌─────────────┐     ┌──────┐
  │ Query   │────▶│ ContextRetri │────▶│ ChunkFilter │────▶│ GLM  │
  │ (user)  │     │ (ChromaDB)   │     │ (RF model)  │     │ API  │
  └─────────┘     └──────────────┘     └─────────────┘     └──────┘
                                            │
                                            ▼
                                    ┌──────────────┐
                                    │  17 feature  │
                                    │  TF-IDF +    │
                                    │  lexical     │
                                    └──────────────┘

Integrazione in coding_agent.py:
  1. Prima di agent.run()/agent.arun() → recupera chunk da ChromaDB
  2. ChunkFilter valuta ogni chunk (query, chunk) → probabilità
  3. Chunk con prob < threshold vengono scartati
  4. Chunk rimanenti vengono iniettati nel messaggio come contesto
  5. Fallback: se modello non disponibile → tutti i chunk passano

Configurazione (.env):
  CHUNK_FILTER_ENABLED=true
  CHUNK_FILTER_THRESHOLD=0.20
  CHUNK_FILTER_MAX_CHUNKS=5
  CHUNK_FILTER_TOP_K=10
"""

import json
import logging
import math
import os
import pickle
import re
import sys
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any, Optional

import numpy as np

logger = logging.getLogger("chunk_filter")

BASE_DIR = Path(__file__).parent.parent.resolve()
DATA_DIR = BASE_DIR / "workspace" / "chunk_filter_data"

# ── Feature Engineering (identico a FASE 2a/2b) ──────────────────

def _tokenize(text: str) -> list[str]:
    return re.findall(r'\w+', text.lower().strip())


def _compute_features(query: str, passage: str, idf_weights: dict = None) -> list[float]:
    """Compute 17 relevance features between query and passage."""
    q_tokens = _tokenize(query)
    p_tokens = _tokenize(passage)

    q_set = set(q_tokens)
    p_set = set(p_tokens)

    q_len = max(len(q_tokens), 1)
    p_len = max(len(p_tokens), 1)

    overlap = len(q_set & p_set)
    coverage = overlap / q_len
    union = len(q_set | p_set)
    jaccard = overlap / max(union, 1)
    exact_match = 1.0 if query.lower().strip() in passage.lower() else 0.0
    len_ratio = q_len / p_len

    p_counter = Counter(p_tokens)
    tf_sum = sum(p_counter.get(t, 0) for t in q_set)
    tf_avg = tf_sum / q_len
    tf_norm = tf_sum / p_len

    k1, b = 1.5, 0.75
    avg_p_len = 200
    bm25 = 0.0
    for term in q_set:
        tf = p_counter.get(term, 0)
        if tf == 0:
            continue
        idf = idf_weights.get(term, math.log(10000 / 2)) if idf_weights else 1.0
        numerator = tf * (k1 + 1)
        denominator = tf + k1 * (1 - b + b * p_len / avg_p_len)
        bm25 += idf * numerator / denominator

    first_pos = 1.0
    for i, t in enumerate(p_tokens):
        if t in q_set:
            first_pos = i / p_len
            break

    if idf_weights:
        idf_cov = sum(idf_weights.get(t, 0) for t in q_set if t in p_set) / \
                   max(sum(idf_weights.get(t, 0) for t in q_set), 1e-8)
    else:
        idf_cov = coverage

    first_50 = p_tokens[:50]
    density_50 = sum(1 for t in first_50 if t in q_set) / max(len(first_50), 1)

    q_chars = set(query.lower())
    p_chars = set(passage.lower())
    char_overlap = len(q_chars & p_chars) / max(len(q_chars | p_chars), 1)

    lcs = 0
    for i in range(len(q_tokens)):
        for j in range(len(p_tokens)):
            k = 0
            while i + k < len(q_tokens) and j + k < len(p_tokens) and q_tokens[i + k] == p_tokens[j + k]:
                k += 1
            lcs = max(lcs, k)
    lcs_ratio = lcs / q_len

    return [
        overlap, coverage, jaccard, exact_match,
        q_len, p_len, len_ratio, tf_sum, tf_avg, tf_norm,
        bm25, first_pos, idf_cov, density_50, char_overlap, lcs_ratio,
    ]


def _build_feature_matrix(query: str, chunks: list[str], tfidf_vec, idf_weights: dict) -> np.ndarray:
    """Build feature matrix for a single query vs multiple chunks."""
    from sklearn.feature_extraction.text import TfidfVectorizer
    from sklearn.metrics.pairwise import cosine_similarity

    queries = [query] * len(chunks)
    passages = chunks

    q_tfidf = tfidf_vec.transform(queries)
    p_tfidf = tfidf_vec.transform(passages)

    q_norm = np.sqrt(np.asarray(q_tfidf.multiply(q_tfidf).sum(axis=1)).flatten() + 1e-8)
    p_norm = np.sqrt(np.asarray(p_tfidf.multiply(p_tfidf).sum(axis=1)).flatten() + 1e-8)
    dot = np.asarray(q_tfidf.multiply(p_tfidf).sum(axis=1)).flatten()
    cosine = dot / (q_norm * p_norm + 1e-8)

    lexical = []
    for p in passages:
        lexical.append(_compute_features(query, p, idf_weights))
    lexical_arr = np.array(lexical)

    X = np.column_stack([cosine, lexical_arr])
    return X


# ── ChunkFilter Class ────────────────────────────────────────────

class ChunkFilter:
    """
    Modello-figlio per filtraggio chunk contesto.

    Usage:
        cf = ChunkFilter()
        cf.load()

        filtered = cf.filter(
            query="come funziona l'algoritmo di Instagram?",
            chunks=[
                {"text": "L'algoritmo di Instagram usa...", "id": "c1"},
                {"text": "Ricetta della carbonara...", "id": "c2"},
            ]
        )
        # filtered = [{"text": "...", "id": "c1", "score": 0.85}]
    """

    MODEL_FILE = "calibrated_chunk_filter.pkl"
    TFIDF_FILE = "tfidf_vectorizer_v3.pkl"
    IDF_FILE = "idf_weights_v3.pkl"

    def __init__(
        self,
        threshold: float = None,
        max_chunks: int = None,
        top_k: int = None,
        enabled: bool = None,
    ):
        self.threshold = threshold or float(os.getenv("CHUNK_FILTER_THRESHOLD", "0.20"))
        self.max_chunks = max_chunks or int(os.getenv("CHUNK_FILTER_MAX_CHUNKS", "5"))
        self.top_k = top_k or int(os.getenv("CHUNK_FILTER_TOP_K", "10"))
        self.enabled = enabled if enabled is not None else os.getenv("CHUNK_FILTER_ENABLED", "true").lower() == "true"

        self.model = None
        self.tfidf_vec = None
        self.idf_weights = None
        self.model_name = "unknown"
        self._loaded = False

    def load(self) -> bool:
        """Carica modello, TF-IDF vectorizer e IDF weights."""
        model_path = DATA_DIR / self.MODEL_FILE
        tfidf_path = DATA_DIR / self.TFIDF_FILE
        idf_path = DATA_DIR / self.IDF_FILE

        if not model_path.exists():
            logger.warning(f"ChunkFilter: modello non trovato ({model_path})")
            return False

        try:
            with open(model_path, "rb") as f:
                data = pickle.load(f)
            self.model = data["model"]
            self.model_name = data.get("model_name", "unknown")
            self.threshold = data.get("threshold", self.threshold)

            with open(tfidf_path, "rb") as f:
                self.tfidf_vec = pickle.load(f)

            with open(idf_path, "rb") as f:
                self.idf_weights = pickle.load(f)

            self._loaded = True
            logger.info(f"ChunkFilter caricato: {self.model_name} (threshold={self.threshold:.2f})")
            return True
        except Exception as e:
            logger.error(f"ChunkFilter load error: {e}")
            return False

    def is_ready(self) -> bool:
        """Verifica se il filtro è pronto all'uso."""
        return self.enabled and self._loaded and self.model is not None

    def score_chunks(self, query: str, chunks: list[dict]) -> list[dict]:
        """
        Assegna un punteggio di rilevanza a ogni chunk.

        Args:
            query: La query utente
            chunks: Lista di dict con chiave "text"

        Returns:
            Lista di dict con chiavi: text, id, score, kept
        """
        if not self.is_ready():
            # Fallback: tutti i chunk passano con score 1.0
            for c in chunks:
                c["score"] = 1.0
                c["kept"] = True
            return chunks

        if not chunks:
            return []

        chunk_texts = [c.get("text", "") for c in chunks]

        try:
            X = _build_feature_matrix(query, chunk_texts, self.tfidf_vec, self.idf_weights)

            if hasattr(self.model, "predict_proba"):
                probs = self.model.predict_proba(X)[:, 1]
            elif hasattr(self.model, "decision_function"):
                scores = self.model.decision_function(X)
                probs = 1 / (1 + np.exp(-scores))
            else:
                probs = self.model.predict(X).astype(float)

            for i, c in enumerate(chunks):
                c["score"] = float(probs[i])
                c["kept"] = probs[i] >= self.threshold

            return chunks

        except Exception as e:
            logger.error(f"ChunkFilter score error: {e}")
            # Fallback: tutti passano
            for c in chunks:
                c["score"] = 1.0
                c["kept"] = True
            return chunks

    def filter(self, query: str, chunks: list[dict]) -> list[dict]:
        """
        Filtra chunk in base alla rilevanza con la query.

        Args:
            query: La query utente
            chunks: Lista di dict con chiave "text" (e opzionalmente "id")

        Returns:
            Lista filtrata di dict con chiavi: text, id, score
        """
        if not self.is_ready():
            logger.debug("ChunkFilter non pronto, skip filtering")
            return chunks

        if not chunks:
            return []

        # Score tutti i chunk
        scored = self.score_chunks(query, chunks)

        # Filtra: mantieni solo chunk con score >= threshold
        kept = [c for c in scored if c.get("kept", True)]

        # Safety: se nessun chunk passa, mantieni il top-1 (meglio avere contesto che niente)
        if not kept and scored:
            best = max(scored, key=lambda x: x.get("score", 0))
            best["kept"] = True
            kept = [best]
            logger.debug(f"ChunkFilter: nessun chunk sopra threshold, mantenuto top-1 (score={best['score']:.3f})")

        # Safety: se solo 1 chunk passa, mantienilo
        if len(kept) == 1:
            logger.debug(f"ChunkFilter: 1 chunk mantenuto (score={kept[0]['score']:.3f})")

        # Ordina per score decrescente
        kept.sort(key=lambda x: x.get("score", 0), reverse=True)

        # Limita numero di chunk
        kept = kept[:self.max_chunks]

        # Log
        total = len(chunks)
        filtered_out = total - len(kept)
        avg_score = np.mean([c.get("score", 0) for c in kept]) if kept else 0
        logger.info(
            f"ChunkFilter: {total} → {len(kept)} chunk "
            f"(filtered: {filtered_out}, avg_score: {avg_score:.3f}, threshold: {self.threshold:.2f})"
        )

        return kept

    def build_context_string(self, filtered_chunks: list[dict]) -> str:
        """
        Costruisce stringa di contesto dai chunk filtrati.

        Args:
            filtered_chunks: Lista di dict con chiave "text"

        Returns:
            Stringa formattata per iniezione nel prompt
        """
        if not filtered_chunks:
            return ""

        lines = ["<context>"]
        for i, c in enumerate(filtered_chunks):
            text = c.get("text", "")
            score = c.get("score", 0)
            source = c.get("collection", c.get("id", "unknown"))
            lines.append(f"<context_item id=\"{i+1}\" relevance=\"{score:.2f}\" source=\"{source}\">")
            lines.append(text[:2000])  # limita lunghezza
            lines.append("</context_item>")

        lines.append("</context>")
        return "\n".join(lines)

    def get_stats(self) -> dict:
        """Ritorna statistiche del filtro."""
        return {
            "enabled": self.enabled,
            "loaded": self._loaded,
            "model": self.model_name,
            "threshold": self.threshold,
            "max_chunks": self.max_chunks,
            "top_k": self.top_k,
        }


# ── Context Retriever ─────────────────────────────────────────────

class ContextRetriever:
    """
    Recupera chunk rilevanti da ChromaDB per la query corrente.

    Accede direttamente al SQLite di ChromaDB (senza libreria chromadb)
    e usa TF-IDF similarity per la ricerca.
    """

    def __init__(self, top_k: int = None):
        self.top_k = top_k or int(os.getenv("CHUNK_FILTER_TOP_K", "10"))
        self._available = False
        self._chunks_cache = None  # cache di tutti i chunk

        chroma_path = BASE_DIR / "data" / "vector_memory" / "chroma.sqlite3"
        if chroma_path.exists():
            self._chroma_path = str(chroma_path)
            self._available = True
            logger.info(f"ContextRetriever: ChromaDB trovato ({chroma_path})")
        else:
            self._chroma_path = None
            logger.warning("ContextRetriever: ChromaDB non trovato")

    def is_available(self) -> bool:
        return self._available

    def _load_all_chunks(self) -> list[dict]:
        """Carica tutti i chunk da ChromaDB (con cache)."""
        if self._chunks_cache is not None:
            return self._chunks_cache

        import sqlite3

        chunks = []
        try:
            conn = sqlite3.connect(self._chroma_path)
            cur = conn.cursor()

            # Mappa segment_id -> collection name
            cur.execute("""
                SELECT s.id, c.name FROM segments s 
                JOIN collections c ON s.collection = c.id 
                WHERE s.scope = 'METADATA'
            """)
            seg_to_coll = {r[0]: r[1] for r in cur.fetchall()}

            # Mappa embedding id -> segment
            cur.execute("SELECT id, segment_id, embedding_id FROM embeddings")
            emb_info = {r[0]: {"segment": r[1], "eid": r[2]} for r in cur.fetchall()}

            # Estrai testi
            cur.execute("SELECT id, c0 FROM embedding_fulltext_search_content")
            texts = cur.fetchall()

            for doc_id, text in texts:
                if not text or len(text.strip()) < 5:
                    continue
                info = emb_info.get(doc_id, {})
                coll = seg_to_coll.get(info.get("segment", ""), "unknown")
                eid = info.get("eid", f"doc_{doc_id}")
                chunks.append({
                    "text": text.strip(),
                    "id": eid,
                    "collection": coll,
                    "doc_id": doc_id,
                })

            conn.close()
        except Exception as e:
            logger.error(f"ContextRetriever: errore lettura ChromaDB: {e}")

        self._chunks_cache = chunks
        logger.info(f"ContextRetriever: {len(chunks)} chunk caricati da ChromaDB")
        return chunks

    def retrieve(self, query: str, collections: list[str] = None) -> list[dict]:
        """
        Recupera chunk rilevanti da ChromaDB usando TF-IDF similarity.

        Args:
            query: La query utente
            collections: Lista di collezioni da cercare (None = tutte)

        Returns:
            Lista di dict con chiavi: text, id, collection, score
        """
        if not self._available:
            return []

        all_chunks = self._load_all_chunks()
        if not all_chunks:
            return []

        # Filtra per collezione se specificato
        if collections:
            all_chunks = [c for c in all_chunks if c.get("collection") in collections]

        if not all_chunks:
            return []

        # TF-IDF similarity
        try:
            from sklearn.feature_extraction.text import TfidfVectorizer
            from sklearn.metrics.pairwise import cosine_similarity

            chunk_texts = [c["text"] for c in all_chunks]
            tfidf = TfidfVectorizer(
                max_features=10000,
                ngram_range=(1, 2),
                sublinear_tf=True,
                stop_words="english",
            )
            matrix = tfidf.fit_transform(chunk_texts + [query])
            q_vec = matrix[-1:]
            c_vecs = matrix[:-1]
            sims = cosine_similarity(q_vec, c_vecs).flatten()

            # Top-k per similarity
            top_indices = np.argsort(sims)[::-1][:self.top_k]

            results = []
            for idx in top_indices:
                if sims[idx] > 0.01:  # threshold minimo
                    c = all_chunks[idx].copy()
                    c["vector_score"] = float(sims[idx])
                    results.append(c)

            return results

        except Exception as e:
            logger.error(f"ContextRetriever: errore similarity: {e}")
            return []


# ── Pipeline Integration ─────────────────────────────────────────

class ChunkFilterPipeline:
    """
    Pipeline completa: retrieve → filter → context string.

    Usage in coding_agent.py:
        from tools.chunk_filter import ChunkFilterPipeline

        pipeline = ChunkFilterPipeline()
        pipeline.load()

        # Prima di agent.run():
        context = pipeline.process(query=message)
        if context:
            message = context + "\\n\\n" + message
    """

    def __init__(self):
        self.filter = ChunkFilter()
        self.retriever = ContextRetriever(top_k=self.filter.top_k)
        self._initialized = False

    def load(self) -> bool:
        """Inizializza la pipeline."""
        filter_ok = self.filter.load()
        retriever_ok = self.retriever.is_available()

        self._initialized = filter_ok or retriever_ok

        if not filter_ok:
            logger.warning("ChunkFilterPipeline: modello non caricato, fallback a no-filter")
        if not retriever_ok:
            logger.warning("ChunkFilterPipeline: retriever non disponibile, skip context retrieval")

        return self._initialized

    def is_ready(self) -> bool:
        return self._initialized and self.filter.is_ready()

    def process(self, query: str, existing_chunks: list[dict] = None) -> str:
        """
        Processa la query: recupera chunk, filtra, ritorna contesto.

        Args:
            query: La query utente
            existing_chunks: Chunk già disponibili (se None, recupera da ChromaDB)

        Returns:
            Stringa di contesto formattata (vuota se nessun chunk)
        """
        if not self.filter.enabled:
            return ""

        # 1. Retrieve chunks (se non forniti)
        if existing_chunks is None:
            if not self.retriever.is_available():
                return ""
            chunks = self.retriever.retrieve(query)
        else:
            chunks = existing_chunks

        if not chunks:
            return ""

        # 2. Filter chunks
        filtered = self.filter.filter(query, chunks)

        if not filtered:
            return ""

        # 3. Build context string
        return self.filter.build_context_string(filtered)

    def get_stats(self) -> dict:
        return {
            "filter": self.filter.get_stats(),
            "retriever_available": self.retriever.is_available(),
            "pipeline_ready": self.is_ready(),
        }


# ── Singleton ────────────────────────────────────────────────────

_pipeline_instance = None

def get_pipeline() -> ChunkFilterPipeline:
    """Restituisce l'istanza singleton della pipeline."""
    global _pipeline_instance
    if _pipeline_instance is None:
        _pipeline_instance = ChunkFilterPipeline()
        _pipeline_instance.load()
    return _pipeline_instance


# ── CLI ──────────────────────────────────────────────────────────

def main():
    import argparse
    parser = argparse.ArgumentParser(description="ChunkFilter — modello-figlio per filtraggio contesto")
    parser.add_argument("--query", "-q", type=str, help="Query da testare")
    parser.add_argument("--stats", action="store_true", help="Mostra statistiche")
    parser.add_argument("--test", action="store_true", help="Test con query di esempio")
    args = parser.parse_args()

    logging.basicConfig(level=logging.INFO, format="%(name)s: %(message)s")

    pipeline = get_pipeline()

    if args.stats:
        stats = pipeline.get_stats()
        print(json.dumps(stats, indent=2))
        return

    if args.test:
        test_queries = [
            "come funziona l'algoritmo di Instagram?",
            "crea uno script Python per analizzare dati CSV",
            "quali sono le ultime vulnerabilità CVE?",
        ]
        for q in test_queries:
            print(f"\n{'='*60}")
            print(f"Query: {q}")
            context = pipeline.process(q)
            if context:
                print(f"Context ({len(context)} chars):")
                print(context[:500])
            else:
                print("Nessun contesto recuperato")
        return

    if args.query:
        context = pipeline.process(args.query)
        if context:
            print(context)
        else:
            print("Nessun contesto recuperato")
        return

    parser.print_help()


if __name__ == "__main__":
    main()