# Penelope — Ingestion & Grafo

Strato di ingestione e fusione di dati eterogenei in un grafo unico.
Parte del sistema **Oracle**.

## Fase 1: Scheletro meccanico

- ✅ MariaDB schema (nodi, archi, file_registry, coda)
- ✅ Bridge NetworkX ↔ MariaDB per interrogazioni veloci
- ✅ Scanner filesystem one-shot (con watchdog futuro)
- ✅ Egida: filtri HSD (regex + NER leggero)
- ✅ Quarantena per file infetti
- ✅ Coda lazy per elaborazione pesante differita (Fase 2)
- ✅ Password DB nel keyring di sistema (Windows Credential Manager)

## Setup

### 1. Prerequisiti

- Python 3.10+
- MariaDB su Proxmox (schema già creato via `penelope.db.schema.sql`)
- (Opzionale) SpaCy modello italiano per NER:
  ```bash
  python -m spacy download it_core_news_sm
  ```

### 2. Installazione

```bash
cd Oracle/Penelope
pip install -r requirements.txt
```

### 3. Configurazione

**Mai scrivere la password in chiaro.** Penelope usa il **keyring di sistema**:

```bash
# 1. Copia il file di esempio (modifica solo host/user, NON la password)
cp .env.example .env
# Modifica .env: host, user, path storage — lascia commentata PENELOPE_DB_PASSWORD

# 2. Imposta la password nel Windows Credential Manager (cifrata dal SO)
python -m penelope.cli configure set

# 3. Verifica che la connessione funzioni
python -m penelope.cli configure test
```

La password finisce nel **Windows Credential Manager** (Vault → Credenziali Generiche → `penelope`), cifrata dal sistema operativo. Non giace in chiaro in nessun file.

### 4. Uso

```bash
# Scansiona una directory
python -m penelope.cli scan --device headless /mnt/shared/docs

# Scansiona tutti gli storage configurati
python -m penelope.cli scan:all

# Stato del grafo
python -m penelope.cli graph status

# Processa la coda lazy
python -m penelope.cli queue process

# Elenca file in quarantena
python -m penelope.cli quarantine list

# Svuota quarantena
python -m penelope.cli quarantine clear

# Cambia password
python -m penelope.cli configure set

# Cancella password dal keyring
python -m penelope.cli configure clear
```

### 5. Test

```bash
pytest tests/ -v
```

## Come funziona la risoluzione della password

`settings.get_db_password()` segue quest'ordine:

1. **Keyring** (Windows Credential Manager) ← **primario**
2. **Variabile d'ambiente** `PENELOPE_DB_PASSWORD`
3. **File .env** (con warning: "password in chiaro")
4. Se nessuna trovata → stringa vuota + errore in connessione

## Architettura

```
Penelope/
├── penelope/
│   ├── db/              # MariaDB store + NetworkX bridge
│   ├── egida/           # HSD filters, NER light, quarantine
│   ├── ingestion/       # Scanner, metadata, dispatcher queue
│   ├── config/          # Settings (con keyring integration)
│   └── cli.py           # CLI
├── tests/
└── requirements.txt
```

## Pipeline di ingestion

```
[File] → Egida (HSD?) → Metadata → MariaDB (nodo+edge) → Coda lazy
                          ↓
                    [Fase 2: embedding, face, NER pesante]
```
