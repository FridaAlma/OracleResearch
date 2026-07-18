"""
Bridge NetworkX ↔ MariaDB.

Carica nodi e archi da MariaDB in un MultiDiGraph NetworkX.
Tutte le letture avvengono sul grafo in-memory (veloce).
Le scritture sono propagate immediatamente su MariaDB (source of truth).
"""

import json
import logging
from typing import Any, Optional

import networkx as nx

from penelope.db.mariadb_store import MariaDBStore

logger = logging.getLogger(__name__)


class GraphBridge:
    """
    Interfaccia unica per interrogare e modificare il grafo.
    NetworkX in-memory per letture, MariaDB come storage persistente.
    """

    def __init__(self, db: Optional[MariaDBStore] = None):
        self.db = db or MariaDBStore()
        self.graph = nx.MultiDiGraph()

    # ─── Sincronizzazione ───────────────────────────────────────

    def load_from_db(self) -> None:
        """Carica l'intero grafo da MariaDB in NetworkX."""
        self.graph.clear()

        with self.db as store:
            # Nodi
            rows = store._query("SELECT * FROM nodes")
            for row in rows:
                meta = json.loads(row["metadata"]) if row.get("metadata") else {}
                self.graph.add_node(
                    row["id"],
                    type=row["type"],
                    label=row["label"],
                    created_at=str(row["created_at"]),
                    **meta,
                )

            # Archi
            rows = store._query("SELECT * FROM edges")
            for row in rows:
                meta = json.loads(row["metadata"]) if row.get("metadata") else {}
                self.graph.add_edge(
                    row["source_id"],
                    row["target_id"],
                    key=row["id"],
                    relation=row["relation"],
                    weight=row["weight"],
                    created_at=str(row["created_at"]),
                    **meta,
                )

        logger.info("Grafo caricato: %d nodi, %d archi", self.graph.number_of_nodes(), self.graph.number_of_edges())

    def sync_node_to_db(self, node_id: str) -> None:
        """Propaga un nodo modificato localmente su MariaDB."""
        if node_id not in self.graph:
            return
        data = self.graph.nodes[node_id]
        with self.db as store:
            existing = store.get_node(node_id)
            if existing:
                store.update_node(node_id, label=data.get("label"), metadata=data.get("metadata"))
            else:
                store.create_node(
                    node_id=node_id,
                    node_type=data.get("type", "File"),
                    label=data.get("label"),
                    metadata=data.get("metadata"),
                )

    def sync_edge_to_db(self, u: str, v: str, key: Any) -> None:
        """Propaga un arco modificato localmente su MariaDB."""
        if not self.graph.has_edge(u, v, key=key):
            return
        data = self.graph.edges[u, v, key]
        # Se l'arco non ha un id numerico, è nuovo e va creato
        if not isinstance(key, int):
            new_id = self.db.create_edge(
                source_id=u,
                target_id=v,
                relation=data.get("relation", "UNKNOWN"),
                weight=data.get("weight", 1.0),
                metadata=data.get("metadata"),
            )
            # Sostituisce la chiave nell'edge
            self.graph.add_edge(u, v, key=new_id, **data)
        else:
            # Arco già esistente, aggiornamento futuro se serve
            pass

    # ─── Query helpers ──────────────────────────────────────────

    def get_neighbors(
        self, node_id: str, relation: Optional[str] = None
    ) -> list[dict]:
        """Restituisce i vicini di un nodo, opzionalmente filtrati per relazione."""
        neighbors = []
        for u, v, k, data in self.graph.edges(node_id, keys=True, data=True):
            if relation and data.get("relation") != relation:
                continue
            other = v if u == node_id else u
            neighbors.append({
                "node_id": other,
                "node_data": dict(self.graph.nodes[other]),
                "relation": data.get("relation"),
                "weight": data.get("weight", 1.0),
                "edge_id": k,
            })
        return neighbors

    def shortest_path(self, source: str, target: str) -> Optional[list[str]]:
        """Cammino minimo tra due nodi (senza considerare direzione)."""
        try:
            return nx.shortest_path(self.graph.to_undirected(), source=source, target=target)
        except (nx.NetworkXNoPath, nx.NodeNotFound):
            return None

    def get_subgraph(self, node_type: Optional[str] = None) -> nx.MultiDiGraph:
        """Restituisce un sottografo filtrato per tipo di nodo."""
        if node_type is None:
            return self.graph.copy()
        nodes = [n for n, d in self.graph.nodes(data=True) if d.get("type") == node_type]
        return self.graph.subgraph(nodes).copy()

    def merge_nodes(self, keep_id: str, merge_id: str) -> None:
        """
        Fonde due nodi (identity resolution).
        Tutti gli archi di merge_id vengono riassegnati a keep_id.
        merge_id viene eliminato.
        """
        if keep_id == merge_id:
            return

        # Riassegna archi entranti
        for u, v, k, data in list(self.graph.in_edges(merge_id, keys=True, data=True)):
            self.graph.add_edge(u, keep_id, key=k, **data)
            self.graph.remove_edge(u, v, key=k)

        # Riassegna archi uscenti
        for u, v, k, data in list(self.graph.out_edges(merge_id, keys=True, data=True)):
            self.graph.add_edge(keep_id, v, key=k, **data)
            self.graph.remove_edge(u, v, key=k)

        # Propaga su MariaDB
        with self.db as store:
            # Riassegna edges su DB
            store._execute("UPDATE edges SET source_id = %s WHERE source_id = %s", (keep_id, merge_id))
            store._execute("UPDATE edges SET target_id = %s WHERE target_id = %s", (keep_id, merge_id))
            # Riassegna file_registry
            store._execute("UPDATE file_registry SET node_id = %s WHERE node_id = %s", (keep_id, merge_id))
            # Elimina il nodo fuso
            store.delete_node(merge_id)

        # Rimuove il nodo dal grafo
        self.graph.remove_node(merge_id)
        logger.info("Fusi nodi: %s → %s", merge_id, keep_id)

    def count(self) -> dict:
        return {
            "nodes": self.graph.number_of_nodes(),
            "edges": self.graph.number_of_edges(),
        }
