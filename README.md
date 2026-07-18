# Oracle RUI Edition

**Ricerca · Unione · Intelligenza**

Framework OSINT modulare a 4 strati per ricercatori: agente autonomo con memoria, grafo della conoscenza, identity resolution e guardrail etico.

---

## Architettura

```
┌─────────────────────────────────────────────────────────┐
│                   ORACLE CORE (:8100)                    │
│            Agente esecutivo + Frontend web               │
│   50+ provider LLM · MCTS · Sandbox · Vector Memory      │
│             RUI Framework (24 tool OSINT)                │
└──────┬───────────────────────────────────────────────────┘
       │
   ┌───┴───────────────┬──────────────────────┐
   │                   │                      │
┌──┴──────────┐  ┌────┴──────────┐  ┌───────┴──────────┐
│  PENELOPE   │  │  ARCHIMEDE    │  │     EGIDA         │
│  (:5000)    │  │  (:8001)      │  │  (integrato)      │
│  Ingestione │  │  Graph Reader │  │  Guardrail HSD    │
│  + Grafo    │  │  + Face Match │  │  · NER detection  │
│             │  │               │  │  · Quarantena     │
└─────────────┘  └───────────────┘  └────────────────────┘
```

| Strato | Ruolo | Porta | Avvio |
|--------|-------|-------|-------|
| **Oracle Core** | Agente esecutivo, UI, tool OSINT | :8100 | Sempre |
| **Penelope** | Ingestione dati, grafo conoscenza (MariaDB/ChromaDB) | :5000 | `--with-penelope` |
| **Archimede** | Navigazione read-only grafo, face recognition | :8001 | `--with-archimede` |
| **Egida** | Guardrail HSD integrato in tutti i layer | — | Automatico |

---

## Quick Start (5 minuti)

### Prerequisiti
- Python 3.11+
- Git
- (Opzionale) Docker — per MariaDB

### 1. Clona e installa

```bash
git clone <repo-url> oracle-rui-edition
cd oracle-rui-edition

# Crea ambiente virtuale
python -m venv venv
venv\Scripts\activate      # Windows
# source venv/bin/activate  # Linux/macOS

# Installa dipendenze
pip install -r oracle-rui/requirements.txt
```

### 2. Setup guidato

```bash
python run.py --init
```

Questo copia i template `.env.example` nei rispettivi `.env`. Modifica i file creati:

- `oracle-rui/.env` → inserisci la tua API key LLM
- `penelope/.env` → configura storage paths (opzionale)
- `archimede/.env` → configura API key e path Penelope (opzionale)

### 3. Avvia

```bash
# Solo Oracle Core (agente + frontend)
python run.py

# Oracle + Penelope + Archimede (sistema completo)
python run.py --all
```

Apri [http://localhost:8100](http://localhost:8100) nel browser.

---

## Configurazione

### Oracle Core

Modifica `oracle-rui/.env`:

```ini
# API key LLM (almeno una)
OPENAI_API_KEY=sk-...
DEEPSEEK_API_KEY=sk-...
ANTHROPIC_API_KEY=sk-...

# Provider default
ORACLE_DEFAULT_PROVIDER=deepseek
ORACLE_DEFAULT_MODEL=deepseek-chat
```

### Penelope (Grafo della conoscenza)

Il grafo richiede un database SQL. Due opzioni:

**Opzione A — Docker MariaDB (consigliata)**

```bash
docker-compose up -d
```

**Opzione B — SQLite (zero setup)**

Modifica `penelope/.env`:
```ini
PENELOPE_DB_BACKEND=sqlite
```

Poi configura gli storage path da scandire:
```ini
PENELOPE_STORAGE_1=C:/Users/tuono/Documenti
PENELOPE_STORAGE_2=D:/ArchivioFoto
```

### Archimede (Face Recognition)

Richiede Penelope attivo. Modifica `archimede/.env`:
```ini
ARCHIMEDE_API_KEY=sk-...
ARCHIMEDE_PENELOPE_PATH=../penelope
```

---

## Comandi

```bash
# Avvio
python run.py                        # Solo Oracle
python run.py --with-penelope        # + Penelope
python run.py --with-archimede       # + Archimede
python run.py --all                  # Tutto
python run.py --port 9000            # Porta personalizzata

# Diagnostica
python run.py --status               # Stato componenti

# Setup
python run.py --init                 # Setup guidato

# Penelope CLI
python -m penelope.cli scan:all      # Scansiona storage
python -m penelope.cli queue loop    # Elabora coda

# Archimede CLI
python -m archimede.query stats      # Statistiche grafo
python -m archimede.query find-parents --ref-dir ref_faces/
```

---

## Tool OSINT (RUI Framework)

24 tool OSINT integrati nell'agente Oracle Core. Accessibili via chat o API:

| Categoria | Tool |
|-----------|------|
| **Web** | web_search, web_scrape, download_file |
| **Email** | email_lookup, domain_reputation, haveibeenpwned |
| **Network** | dns_lookup, whois, ssl_cert, port_scan |
| **Social** | social_profile_search, username_search |
| **Geo** | geolocate_ip, reverse_geocode |
| **Doc** | pdf_metadata, exif_extract, doc_analyze |
| **Crypto** | wallet_lookup, tx_lookup |
| **Graph** | penelope_search, penelope_stats, semantic_search |

---

## Personalizzazione

Tutto è configurabile via `.env`. Nessun dato personale, nessun hardcode.

- **LLM**: 50+ provider supportati (OpenAI, DeepSeek, Anthropic, Ollama, Groq, Together...)
- **Database**: MariaDB o SQLite
- **Storage**: Fino a 5 dispositivi/path
- **Face Recognition**: InsightFace (ArcFace 512-dim, CPU)
- **NER**: SpaCy (italiano predefinito, configurabile)
- **Guardrail**: Soglie HSD configurabili in `egida/config.py`

---

## Requisiti di sistema

| Risorsa | Minimo | Consigliato |
|---------|--------|-------------|
| CPU | 4 core | 8+ core |
| RAM | 8 GB | 16+ GB |
| Disco | 2 GB (codice) + spazio per dati | SSD |
| GPU | Non richiesta | Opzionale (per face rec) |
| Rete | Internet per API LLM | — |

---

## Licenza

Oracle RUI Edition e' rilasciato per uso di ricerca. Ogni ricercatore configura il proprio
ambiente e i propri dati. Il software non contiene, raccoglie o trasmette dati personali.

---

## Riferimenti

- [Oracle_Architettura.md](Oracle_Architettura.md) — Documento di architettura completo
- [SETUP.md](SETUP.md) — Guida dettagliata di setup
- [CONSTITUTION.md](oracle-rui/CONSTITUTION.md) — Costituzione Oracle