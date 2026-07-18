"""
🤖 Archimede Agent — Motore dati read-only per il grafo personale di Oracle.

NON contiene un LLM. Tutta l'elaborazione in linguaggio naturale è demandata
all'orchestratore Oracle. Archimede è un puro motore dati che espone
strumenti strutturati per interrogare il grafo Penelope.

Strumenti a disposizione:
  - search_persons: cerca persone nel grafo
  - get_person_photos: trova foto di una persona
  - get_graph_stats: statistiche del grafo
  - semantic_search: ricerca semantica su ChromaDB
  - search_events: cerca eventi nel grafo
  - search_locations: cerca luoghi nel grafo
  - search_nodes: cerca nodi per nome

Uso:
    agent = ArchimedeAgent()
    result = agent.dispatch("search_persons", query="Angela")
    result = agent.search_persons(query="Angela")
"""

from __future__ import annotations

import json
import logging
import re
from pathlib import Path
from typing import Any, Optional

logger = logging.getLogger("archimede.agent")


class ToolResult:
    """Risultato di un tool."""
    def __init__(self, success: bool, data: Any, error: Optional[str] = None):
        self.success = success
        self.data = data
        self.error = error

    def to_text(self) -> str:
        if not self.success:
            return f"ERRORE: {self.error}"
        if isinstance(self.data, str):
            return self.data
        return json.dumps(self.data, indent=2, ensure_ascii=False, default=str)


class ArchimedeAgent:
    """Motore dati read-only per il grafo personale.

    **Non contiene un LLM.** Le richieste in linguaggio naturale devono
    essere processate dall'orchestratore Oracle, che usa questo
    motore per eseguire operazioni strutturate sul grafo.

    I metodi pubblici (tool_*) sono chiamabili singolarmente, oppure
    si usa dispatch(action, **params) per dispatch dinamico.
    """

    def __init__(self):
        self._reader = None
        self._chroma = None
        logger.info("ArchimedeAgent: motore dati inizializzato (senza LLM)")

    # ── Lazy initialization dei reader ──────────────────────────

    def _ensure_reader(self):
        if self._reader is not None:
            return self._reader
        try:
            from archimede.graph.reader import PenelopeGraphReader
            self._reader = PenelopeGraphReader()
            if self._reader.connected:
                logger.info("ArchimedeAgent: connesso al grafo Penelope (MariaDB)")
                return self._reader
            else:
                logger.warning("ArchimedeAgent: reader non connesso")
                self._reader = None
                return None
        except Exception as e:
            logger.warning("ArchimedeAgent: reader error: %s", e)
            return None

    def _ensure_chroma(self):
        if self._chroma is not None:
            return self._chroma
        try:
            from archimede.graph.chroma_reader import PenelopeChromaReader
            self._chroma = PenelopeChromaReader()
            return self._chroma
        except Exception as e:
            logger.warning("ArchimedeAgent: chroma error: %s", e)
            return None

    # ── Strumenti ───────────────────────────────────────────────

    def tool_search_persons(self, query: str) -> ToolResult:
        reader = self._ensure_reader()
        if not reader:
            return ToolResult(False, "", "Grafo non disponibile")
        try:
            persons = reader.get_person_nodes()
            query_lower = query.lower()
            results = [
                p for p in persons
                if query_lower in (p.get("label", "") or "").lower()
            ]
            return ToolResult(True, {
                "count": len(results),
                "persons": [{"id": p["id"], "label": p["label"]} for p in results[:20]],
            })
        except Exception as e:
            return ToolResult(False, "", str(e))

    def tool_get_person_photos(self, person_name: str, limit: int = 20) -> ToolResult:
        reader = self._ensure_reader()
        if not reader:
            return ToolResult(False, "", "Grafo non disponibile")
        try:
            persons = reader.get_person_nodes()
            query_lower = person_name.lower()
            matched = [
                p for p in persons
                if query_lower in (p.get("label", "") or "").lower()
            ]
            if not matched:
                return ToolResult(True, {
                    "count": 0,
                    "person": person_name,
                    "photos": [],
                    "message": f"Nessuna persona trovata con nome '{person_name}'",
                })

            all_photos = reader.get_all_photos(limit=200)
            person_ids = {p["id"] for p in matched}
            photo_results = []
            for photo in all_photos:
                node_id = photo.get("node_id", "")
                if not node_id:
                    continue
                persons_in_photo = reader.get_persons_in_photo(node_id)
                if any(pid in person_ids for pid in {pp["id"] for pp in persons_in_photo}):
                    photo_results.append({
                        "node_id": node_id,
                        "path": photo.get("path", ""),
                        "file_name": photo.get("label", ""),
                    })
                if len(photo_results) >= limit:
                    break

            return ToolResult(True, {
                "count": len(photo_results),
                "person": person_name,
                "matched_persons": [p["label"] for p in matched],
                "photos": photo_results,
            })
        except Exception as e:
            return ToolResult(False, "", str(e))

    def tool_get_graph_stats(self) -> ToolResult:
        reader = self._ensure_reader()
        if not reader:
            return ToolResult(False, "", "Grafo non disponibile")
        try:
            total_photos = reader.count_photos()
            persons = reader.get_person_nodes()
            photos_with_faces = reader.get_photos_with_face_count()
            node_types = reader._query(
                "SELECT type, COUNT(*) as cnt FROM nodes GROUP BY type ORDER BY cnt DESC"
            )
            edge_count = reader._query("SELECT COUNT(*) as cnt FROM edges")
            file_count = reader._query("SELECT COUNT(*) as cnt FROM file_registry")

            return ToolResult(True, {
                "total_photos": total_photos,
                "total_persons": len(persons),
                "photos_with_faces": len(photos_with_faces),
                "nodes_by_type": {r["type"]: r["cnt"] for r in node_types},
                "total_edges": edge_count[0]["cnt"] if edge_count else 0,
                "total_files": file_count[0]["cnt"] if file_count else 0,
            })
        except Exception as e:
            return ToolResult(False, "", str(e))

    def tool_semantic_search(self, query: str, top_k: int = 10) -> ToolResult:
        chroma = self._ensure_chroma()
        if not chroma:
            return ToolResult(False, "", "ChromaDB non disponibile")
        try:
            collections = chroma.get_collections()
            results = []

            try:
                from sentence_transformers import SentenceTransformer
                import chromadb
                from chromadb.config import Settings
                from pathlib import Path

                penelope_dir = (
                    Path(__file__).resolve().parent.parent.parent
                    / "Penelope" / "data" / "chroma"
                )
                if penelope_dir.exists():
                    client = chromadb.PersistentClient(
                        path=str(penelope_dir),
                        settings=Settings(anonymized_telemetry=False),
                    )
                    embedder = SentenceTransformer("all-MiniLM-L6-v2")
                    q_emb = embedder.encode(query).tolist()

                    for coll_name in ["file_embeddings", "image_embeddings"]:
                        try:
                            coll = client.get_collection(coll_name)
                            resp = coll.query(
                                query_embeddings=[q_emb],
                                n_results=top_k,
                            )
                            for i in range(len(resp["ids"][0])):
                                meta = resp["metadatas"][0][i] if resp["metadatas"] else {}
                                results.append({
                                    "node_id": resp["ids"][0][i],
                                    "file_name": meta.get("file_name", ""),
                                    "collection": coll_name,
                                    "distance": resp["distances"][0][i] if resp["distances"] else 0,
                                })
                        except Exception:
                            pass
            except ImportError:
                return ToolResult(True, {
                    "message": "Sentence-transformers non disponibile per ricerca semantica",
                    "results": [],
                })

            results.sort(key=lambda x: x["distance"])
            return ToolResult(True, {"count": len(results), "results": results[:top_k]})
        except Exception as e:
            return ToolResult(False, "", str(e))

    def tool_search_events(self, limit: int = 20) -> ToolResult:
        reader = self._ensure_reader()
        if not reader:
            return ToolResult(False, "", "Grafo non disponibile")
        try:
            events = reader._query(
                "SELECT id, label, metadata, created_at FROM nodes "
                "WHERE type = 'Event' ORDER BY created_at DESC LIMIT %s",
                (limit,),
            )
            return ToolResult(True, {"count": len(events), "events": events})
        except Exception as e:
            return ToolResult(False, "", str(e))

    def tool_search_locations(self, limit: int = 20) -> ToolResult:
        reader = self._ensure_reader()
        if not reader:
            return ToolResult(False, "", "Grafo non disponibile")
        try:
            locations = reader._query(
                "SELECT id, label, metadata, created_at FROM nodes "
                "WHERE type = 'Location' ORDER BY label ASC LIMIT %s",
                (limit,),
            )
            return ToolResult(True, {"count": len(locations), "locations": locations})
        except Exception as e:
            return ToolResult(False, "", str(e))

    def tool_search_nodes(self, query: str, limit: int = 20) -> ToolResult:
        reader = self._ensure_reader()
        if not reader:
            return ToolResult(False, "", "Grafo non disponibile")
        try:
            nodes = reader._query(
                "SELECT id, type, label, metadata FROM nodes "
                "WHERE label LIKE %s LIMIT %s",
                (f"%{query}%", limit),
            )
            return ToolResult(True, {"count": len(nodes), "nodes": nodes})
        except Exception as e:
            return ToolResult(False, "", str(e))

    # ── Dispatch dinamico ────────────────────────────────────────

    def dispatch(self, action: str, **params) -> ToolResult:
        """Dispatches to the appropriate tool method by name.

        Args:
            action: Nome dell'azione (es. 'search_persons', 'get_graph_stats')
            **params: Parametri da passare al metodo

        Returns:
            ToolResult con i dati strutturati
        """
        method_name = f"tool_{action}"
        method = getattr(self, method_name, None)
        if method is None:
            return ToolResult(False, "", f"Azione sconosciuta: '{action}'")
        try:
            logger.info("Archimede dispatch: %s(%s)", action, params)
            return method(**params)
        except TypeError as e:
            return ToolResult(False, "", f"Parametri errati per '{action}': {e}")
        except Exception as e:
            logger.exception("Errore durante %s: %s", action, e)
            return ToolResult(False, "", str(e))

    # ── Chat leggera (per API Archimede, senza LLM) ─────────────

    def chat(self, message: str) -> str:
        """Risponde a un messaggio in linguaggio naturale con pattern matching.

        Metodo leggero per l'endpoint /archimede/chat.
        NON usa un LLM: si basa su keyword matching per casi comuni.
        Per funzionalità complete, usare l'orchestratore Oracle.
        """
        msg_lower = message.lower()

        # Mappa keyword → azione
        if any(k in msg_lower for k in ["stat", "conteggio", "quante", "quanti", "quante foto"]):
            result = self.tool_get_graph_stats()
            if result.success:
                d = result.data
                return (
                    f"Ecco le statistiche del grafo:\n"
                    f"- 📸 Foto totali: {d.get('total_photos', 'N/A')}\n"
                    f"- 👤 Persone: {d.get('total_persons', 'N/A')}\n"
                    f"- 📁 Nodi: {sum(d.get('nodes_by_type', {}).values(), 0)}\n"
                    f"- 🔗 Archi: {d.get('total_edges', 'N/A')}\n"
                    f"- 📄 File: {d.get('total_files', 'N/A')}"
                )
            return "Non riesco a recuperare le statistiche del grafo."

        if any(k in msg_lower for k in ["person", "persone", "gente", "chi"]):
            # Cerca persone per nome
            # Estrai il nome dopo "cerca" o "trova"
            name_match = re.search(r'(?:cerca|trova|dove\s+è|chi\s+è)\s+(.+)$', message, re.IGNORECASE)
            query = name_match.group(1).strip() if name_match else ""
            if query:
                result = self.tool_search_persons(query=query)
                if result.success and result.data.get("count", 0) > 0:
                    names = [p["label"] for p in result.data["persons"]]
                    return f"Ho trovato {len(names)} persone: " + ", ".join(names[:10])
                return f"Non ho trovato persone con nome '{query}'."

        if any(k in msg_lower for k in ["foto", "photo", "immagine"]):
            # Cerca foto di una persona
            name_match = re.search(r'(?:foto|photo|immagine)\s+(?:di|con|del|della|delle?|dei?)\s+(.+)$', message, re.IGNORECASE)
            if name_match:
                person_name = name_match.group(1).strip()
                result = self.tool_get_person_photos(person_name=person_name, limit=5)
                if result.success and result.data.get("count", 0) > 0:
                    files = [f.get("file_name", p.get("path", "?")) for p in result.data["photos"][:5]]
                    return f"Ho trovato {result.data['count']} foto di {person_name}:\n" + "\n".join(f"  - {f}" for f in files)
                return f"Non ho trovato foto di '{person_name}'."

        if any(k in msg_lower for k in ["event", "quando", "data"]):
            result = self.tool_search_events(limit=10)
            if result.success and result.data.get("count", 0) > 0:
                events = [f"{e['label']}" for e in result.data["events"][:10]]
                return f"Ultimi {len(events)} eventi:\n" + "\n".join(f"  - {e}" for e in events)
            return "Non ho trovato eventi nel grafo."

        if any(k in msg_lower for k in ["luogo", "location", "dove", "posto"]):
            result = self.tool_search_locations(limit=10)
            if result.success and result.data.get("count", 0) > 0:
                locs = [l["label"] for l in result.data["locations"][:10]]
                return f"Luoghi registrati ({len(locs)}):\n" + "\n".join(f"  - {l}" for l in locs)
            return "Non ho trovato luoghi nel grafo."

        # Fallback: cerca nodi generici
        result = self.tool_search_nodes(query=message, limit=5)
        if result.success and result.data.get("count", 0) > 0:
            nodes = [f"{n['type']}: {n['label']}" for n in result.data["nodes"][:5]]
            return f"Ho trovato questi nodi nel grafo:\n" + "\n".join(f"  - {n}" for n in nodes)

        return (
            "Non ho capito la richiesta. Prova con:\n"
            "- 'quante foto ci sono?'\n"
            "- 'cerca persone'\n"
            "- 'foto di Angela'\n"
            "- 'eventi recenti'\n"
            "- 'luoghi'\n"
            "- 'statistiche del grafo'"
        )

    def close(self):
        """Chiude le connessioni."""
        if self._reader:
            try:
                self._reader.close()
            except Exception:
                pass
