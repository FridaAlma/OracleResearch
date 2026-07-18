# Penelope — Ingestion & Graph

Layer for ingesting and fusing heterogeneous data into a unified graph.
Part of the **Oracle** system.

## Phase 1: Mechanical skeleton

- ✅ MariaDB schema (nodes, edges, file_registry, queue)
- ✅ NetworkX ↔ MariaDB bridge for fast queries
- ✅ One-shot filesystem scanner (with future watchdog)
- ✅ Egida: HSD filters (regex + lightweight NER)
- ✅ Quarantine for infected files
- ✅ Lazy queue for deferred heavy processing (Phase 2)
- ✅ DB password in system keyring (Windows Credential Manager)

## Setup

### 1. Prerequisites

- Python 3.10+
- MariaDB on Proxmox (schema already created via `penelope.db.schema.sql`)
- (Optional) SpaCy Italian model for NER:
  ```bash
  python -m spacy download it_core_news_sm
  ```

### 2. Installation

```bash
cd Oracle/Penelope
pip install -r requirements.txt
```

### 3. Configuration

**Never write the password in plaintext.** Penelope uses the **system keyring**:

```bash
# 1. Copy the example file (edit only host/user, NOT the password)
cp .env.example .env
# Edit .env: host, user, storage path — leave PENELOPE_DB_PASSWORD commented

# 2. Set the password in Windows Credential Manager (OS-encrypted)
python -m penelope.cli configure set

# 3. Verify the connection works
python -m penelope.cli configure test
```

The password ends up in **Windows Credential Manager** (Vault → Generic Credentials → `penelope`), encrypted by the operating system. It is never stored in plaintext in any file.

### 4. Usage

```bash
# Scan a directory
python -m penelope.cli scan --device headless /mnt/shared/docs

# Scan all configured storage
python -m penelope.cli scan:all

# Graph status
python -m penelope.cli graph status

# Process the lazy queue
python -m penelope.cli queue process

# List quarantined files
python -m penelope.cli quarantine list

# Clear quarantine
python -m penelope.cli quarantine clear

# Change password
python -m penelope.cli configure set

# Delete password from keyring
python -m penelope.cli configure clear
```

### 5. Tests

```bash
pytest tests/ -v
```

## How password resolution works

`settings.get_db_password()` follows this order:

1. **Keyring** (Windows Credential Manager) ← **primary**
2. **Environment variable** `PENELOPE_DB_PASSWORD`
3. **.env file** (with warning: "password in plaintext")
4. If none found → empty string + connection error

## Architecture

```
Penelope/
├── penelope/
│   ├── db/              # MariaDB store + NetworkX bridge
│   ├── egida/           # HSD filters, NER light, quarantine
│   ├── ingestion/       # Scanner, metadata, dispatcher queue
│   ├── config/          # Settings (with keyring integration)
│   └── cli.py           # CLI
├── tests/
└── requirements.txt
```

## Ingestion pipeline

```
[File] → Egida (HSD?) → Metadata → MariaDB (node+edge) → Lazy queue
                          ↓
                    [Phase 2: embedding, face, heavy NER]
```
