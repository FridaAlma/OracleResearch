-- =============================================================
-- Penelope — Schema MariaDB (Proxmox)
-- =============================================================
-- Eseguito su server Uninet/Proxmox (Celeron, 2GB RAM).
-- Database: penelope

CREATE DATABASE IF NOT EXISTS penelope
    CHARACTER SET utf8mb4
    COLLATE utf8mb4_unicode_ci;

USE penelope;

-- ─── NODI ───────────────────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS nodes (
    id          VARCHAR(64) PRIMARY KEY COMMENT 'UUID v4',
    type        ENUM('File','Project','Person','Location','Event') NOT NULL,
    label       VARCHAR(255) DEFAULT NULL COMMENT 'Nome leggibile',
    metadata    JSON DEFAULT NULL COMMENT 'Attributi variabili per tipo',
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    INDEX idx_type (type),
    INDEX idx_label (label)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ─── ARCHI (relazioni) ──────────────────────────────────────────────
CREATE TABLE IF NOT EXISTS edges (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    source_id   VARCHAR(64) NOT NULL,
    target_id   VARCHAR(64) NOT NULL,
    relation    VARCHAR(50) NOT NULL COMMENT 'MEMBER_OF, APPEARS_IN, MENTIONS, CREATED_AT, SIMILAR_TO',
    weight      FLOAT DEFAULT 1.0,
    metadata    JSON DEFAULT NULL,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (source_id) REFERENCES nodes(id) ON DELETE CASCADE,
    FOREIGN KEY (target_id) REFERENCES nodes(id) ON DELETE CASCADE,
    INDEX idx_relation (relation),
    INDEX idx_source (source_id),
    INDEX idx_target (target_id)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ─── FILE REGISTRY (path fisici su dispositivi) ────────────────────
CREATE TABLE IF NOT EXISTS file_registry (
    id              INT AUTO_INCREMENT PRIMARY KEY,
    node_id         VARCHAR(64) NOT NULL,
    device          VARCHAR(50) NOT NULL COMMENT 'laptop-main, headless, hdd-ext, smartphone',
    path            TEXT NOT NULL,
    size_bytes      BIGINT DEFAULT NULL,
    sha256          CHAR(64) DEFAULT NULL,
    mime_type       VARCHAR(100) DEFAULT NULL,
    last_seen       DATETIME DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    INDEX idx_device (device),
    INDEX idx_sha256 (sha256)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;

-- ─── CODA DI ELABORAZIONE (lazy processing) ────────────────────────
CREATE TABLE IF NOT EXISTS ingestion_queue (
    id          INT AUTO_INCREMENT PRIMARY KEY,
    node_id     VARCHAR(64) NOT NULL,
    status      ENUM('pending','processing','done','failed') DEFAULT 'pending',
    priority    INT DEFAULT 0,
    created_at  DATETIME DEFAULT CURRENT_TIMESTAMP,
    updated_at  DATETIME DEFAULT CURRENT_TIMESTAMP ON UPDATE CURRENT_TIMESTAMP,
    error_msg   TEXT DEFAULT NULL,
    FOREIGN KEY (node_id) REFERENCES nodes(id) ON DELETE CASCADE,
    INDEX idx_status (status),
    INDEX idx_priority (priority)
) ENGINE=InnoDB DEFAULT CHARSET=utf8mb4;
