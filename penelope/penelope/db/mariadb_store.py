"""
Layer di accesso a MariaDB su Proxmox.
CRUD base per nodi, archi, file_registry e coda di ingestion.
"""

import json
import logging
from typing import Any, Optional
from uuid import uuid4

import pymysql
import pymysql.cursors
from penelope.config import settings

logger = logging.getLogger(__name__)


class MariaDBStore:
    """Connessione thread-safe a MariaDB con context manager."""

    def __init__(self):
        self._conn: Optional[pymysql.Connection] = None

    # ─── Connessione ─────────────────────────────────────────────

    def connect(self) -> pymysql.Connection:
        if self._conn is None or not self._conn.open:
            self._conn = pymysql.connect(
                host=settings.MARIADB_HOST,
                port=settings.MARIADB_PORT,
                user=settings.MARIADB_USER,
                password=settings.get_db_password(),
                database=settings.MARIADB_DATABASE,
                charset="utf8mb4",
                cursorclass=pymysql.cursors.DictCursor,
                autocommit=False,
            )
        return self._conn

    def close(self) -> None:
        if self._conn and self._conn.open:
            self._conn.close()
        self._conn = None

    def __enter__(self) -> "MariaDBStore":
        self.connect()
        return self

    def __exit__(self, *args) -> None:
        if self._conn and self._conn.open:
            self._conn.commit()
        self.close()

    # ─── NODI ────────────────────────────────────────────────────

    def create_node(
        self,
        node_type: str,
        label: Optional[str] = None,
        metadata: Optional[dict] = None,
        node_id: Optional[str] = None,
    ) -> str:
        """Crea un nodo e restituisce il suo ID."""
        node_id = node_id or str(uuid4())
        sql = """INSERT INTO nodes (id, type, label, metadata)
                 VALUES (%s, %s, %s, %s)"""
        self._execute(sql, (node_id, node_type, label, json.dumps(metadata) if metadata else None))
        return node_id

    def get_node(self, node_id: str) -> Optional[dict]:
        """Restituisce un nodo per ID."""
        sql = "SELECT * FROM nodes WHERE id = %s"
        rows = self._query(sql, (node_id,))
        return rows[0] if rows else None

    def get_nodes_by_type(self, node_type: str) -> list[dict]:
        """Restituisce tutti i nodi di un tipo."""
        sql = "SELECT * FROM nodes WHERE type = %s ORDER BY label"
        return self._query(sql, (node_type,))

    def update_node(self, node_id: str, **fields) -> bool:
        """Aggiorna campi di un nodo. Usa solo i campi passati come kwargs."""
        if not fields:
            return False
        sets = ", ".join(f"{k} = %s" for k in fields)
        vals = list(fields.values()) + [node_id]
        sql = f"UPDATE nodes SET {sets} WHERE id = %s"
        return self._execute(sql, tuple(vals)) > 0

    def delete_node(self, node_id: str) -> bool:
        """Cancella un nodo (cascade elimina anche archi e registry)."""
        sql = "DELETE FROM nodes WHERE id = %s"
        return self._execute(sql, (node_id,)) > 0

    # ─── ARCHI ───────────────────────────────────────────────────

    def create_edge(
        self,
        source_id: str,
        target_id: str,
        relation: str,
        weight: float = 1.0,
        metadata: Optional[dict] = None,
    ) -> int:
        """Crea un arco e restituisce il suo ID."""
        sql = """INSERT INTO edges (source_id, target_id, relation, weight, metadata)
                 VALUES (%s, %s, %s, %s, %s)"""
        self._execute(
            sql,
            (source_id, target_id, relation, weight, json.dumps(metadata) if metadata else None),
        )
        return int(self._conn.insert_id())

    def get_edges(self, node_id: Optional[str] = None, relation: Optional[str] = None) -> list[dict]:
        """Restituisce archi, opzionalmente filtrati per nodo e/o relazione."""
        conditions = []
        params = []
        if node_id:
            conditions.append("(source_id = %s OR target_id = %s)")
            params.extend([node_id, node_id])
        if relation:
            conditions.append("relation = %s")
            params.append(relation)
        where = "WHERE " + " AND ".join(conditions) if conditions else ""
        sql = f"SELECT * FROM edges {where} ORDER BY created_at"
        return self._query(sql, tuple(params))

    def delete_edge(self, edge_id: int) -> bool:
        sql = "DELETE FROM edges WHERE id = %s"
        return self._execute(sql, (edge_id,)) > 0

    # ─── FILE REGISTRY ──────────────────────────────────────────

    def register_file(
        self,
        node_id: str,
        device: str,
        path: str,
        size_bytes: Optional[int] = None,
        sha256: Optional[str] = None,
        mime_type: Optional[str] = None,
    ) -> int:
        sql = """INSERT INTO file_registry (node_id, device, path, size_bytes, sha256, mime_type)
                 VALUES (%s, %s, %s, %s, %s, %s)"""
        self._execute(sql, (node_id, device, path, size_bytes, sha256, mime_type))
        return int(self._conn.insert_id())

    def get_files_by_device(self, device: str) -> list[dict]:
        sql = """SELECT * FROM file_registry WHERE device = %s ORDER BY path"""
        return self._query(sql, (device,))

    def get_file_by_sha256(self, sha256: str) -> Optional[dict]:
        sql = "SELECT * FROM file_registry WHERE sha256 = %s LIMIT 1"
        rows = self._query(sql, (sha256,))
        return rows[0] if rows else None

    # ─── CODA DI INGESTIONE ─────────────────────────────────────

    def enqueue(self, node_id: str, priority: int = 0) -> int:
        sql = """INSERT INTO ingestion_queue (node_id, status, priority)
                 VALUES (%s, 'pending', %s)"""
        self._execute(sql, (node_id, priority))
        return int(self._conn.insert_id())

    def dequeue(self, limit: int = 1) -> list[dict]:
        """Preleva i prossimi elementi pending (più prioritari prima)."""
        sql = """SELECT * FROM ingestion_queue
                 WHERE status = 'pending'
                 ORDER BY priority DESC, created_at ASC
                 LIMIT %s FOR UPDATE"""
        rows = self._query(sql, (limit,))
        for row in rows:
            self._execute("UPDATE ingestion_queue SET status = 'processing' WHERE id = %s", (row["id"],))
        return rows

    def mark_done(self, queue_id: int, error: Optional[str] = None) -> bool:
        status = "failed" if error else "done"
        sql = "UPDATE ingestion_queue SET status = %s, error_msg = %s WHERE id = %s"
        return self._execute(sql, (status, error, queue_id)) > 0

    def reset_stale_processing(self, max_age_minutes: int = 30) -> int:
        """Resetta elementi bloccati in 'processing' da più di N minuti.

        Quando un dispatcher crasha, gli item rimangono in 'processing'.
        Questo metodo li riporta a 'pending' per essere rielaborati.

        Args:
            max_age_minutes: età massima in minuti per considerare un item 'stale'

        Returns:
            Numero di elementi resettati.
        """
        sql = """UPDATE ingestion_queue
                 SET status = 'pending', error_msg = CONCAT_WS('; ', error_msg, 'reset_stale')
                 WHERE status = 'processing'
                   AND updated_at < NOW() - INTERVAL %s MINUTE
                 """
        affected = self._execute(sql, (max_age_minutes,))
        if affected:
            logger.warning("Reset %d elementi stale dalla coda (processing > %d min)",
                          affected, max_age_minutes)
        return affected

    # ─── INTERNI ────────────────────────────────────────────────

    def _execute(self, sql: str, params: tuple = ()) -> int:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            conn.commit()
            return cur.rowcount

    def _query(self, sql: str, params: tuple = ()) -> list[dict]:
        conn = self.connect()
        with conn.cursor() as cur:
            cur.execute(sql, params)
            # Normalizza le chiavi a lowercase (MariaDB su Linux restituisce maiuscolo)
            return [
                {k.lower(): v for k, v in row.items()}
                for row in cur.fetchall()
            ]
