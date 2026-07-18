# Oracle — Architettura e Stato Attuale

## Obiettivo

Oracle nasce dal bisogno di unificare l'enorme mole di dati personali eterogenei di Toni (documenti, foto, video, progetti, script) sparsi su più dispositivi, in un unico grafo navigabile e interrogabile — analogo concettualmente a quello che Palantir fa per clienti governativi o enterprise, ma applicato interamente a dati locali e personali.

L'idea centrale: dati eterogenei diventano molto più utili quando collegati in un unico grafo di entità, perché permettono di scoprire relazioni e informazioni che un umano non noterebbe guardando le fonti separatamente.

## Mapping concettuale

| Palantir | Oracle | Ruolo |
|---|---|---|
| Gotham / Foundry | **Penelope** | Ingestione e fusione di dati eterogenei in un grafo unico |
| (layer di lettura analista) | **Archimede** | Lettura passiva e navigazione del grafo |
| (layer applicativo/AIP) | **Oracle** | Orchestratore + agente esecutivo (coding, OSINT, ricerca) |
| Apollo | **Egida** | Guardrail contro l'esposizione di dati sensibili (HSD) |

---

## 1. Penelope — Ingestion & Grafo

Strato di ingestione e fusione. Si occupa di:
- Ingestione di dati eterogenei: immagini, documenti (PDF/markdown/testo), video/audio, directory/script di progetto
- Fusione da dispositivi diversi: laptop main, headless Linux (storage), HDD esterno 1TB
- Identity/entity resolution: riconoscere che la stessa entità appare in fonti diverse e collegarla come unico nodo nel grafo

### Stack tecnico

| Componente | Ruolo | Stato |
|---|---|---|
| **MariaDB** (Proxmox, Celeron 2GB) | Anagrafica: nodi, archi, file_registry, coda di elaborazione | ✅ **8.908 nodi, 10.344 archi** |
| **NetworkX** (in-memory) | Bridge lettura/scrittura con MariaDB, query grafo, merge nodi | ✅ Operativo |
| **ChromaDB** (persistente locale) | Embedding semantico (MiniLM 384-dim + CLIP 512-dim) | ✅ **264+ documenti + immagini indicizzati** |
| **Egida** | Filtri HSD a monte dell'ingestione | ✅ **130+ entry in quarantena** |

### Moduli implementati

#### `db/` — Database layer
- **`schema.sql`** — 4 tabelle: `nodes` (5 type ENUM: File, Project, Person, Location, Event), `edges` (7 relazioni), `file_registry`, `ingestion_queue`
- **`mariadb_store.py`** — CRUD completo: nodi, archi (con foreign key CASCADE), file_registry, coda con dequeue e mark_done, reset_stale_processing per crash recovery
- **`graph_bridge.py`** — Sincronizzazione bidirezionale NetworkX ↔ MariaDB, query vicini, cammino minimo, merge nodi (identity resolution), subgraph filtering
- **`chroma_store.py`** — Due collezioni: `file_embeddings` (MiniLM 384-dim per testo) e `image_embeddings` (CLIP 512-dim per immagini). Ricerca semantica cross-modale. Fallback graceful se CLIP non disponibile

#### `egida/` — HSD Guardrail
- **`filters.py`** — 14 pattern regex: API key, JWT, password, GitHub token, AWS key, HuggingFace token, SSH key, CAP, telefono, CF, email, Discord/Slack token
- **`ner_light.py`** — NER con SpaCy `it_core_news_sm` (PERSON, GPE, LOC, ORG, ADDRESS). Degrado graceful se SpaCy non installato
- **`quarantine.py`** — Isolamento file infetti in directory datate con report JSON dei match

#### `ingestion/` — Pipeline di ingestion
- **`scanner.py`** — Scansione one-shot ricorsiva con dedup SHA-256, skip pattern, callback on_file_processed. **Watchdog reale** con `WatchdogManager` (observer thread-safe, debounce 2s, FileCreationHandler)
- **`metadata.py`** — Estrazione metadati base: hash SHA-256, MIME type (80+ tipi mappati), size, date. Magic bytes fallback per estensione sconosciuta
- **`processor.py`** — **8 stage di elaborazione**:
  1. EXIF (Pillow → GPS, data scatto, camera make/model)
  2. Embedding testo (MiniLM → ChromaDB)
  3. Embedding immagini (CLIP ViT-B/32, 512-dim → ChromaDB)
  4. NER (SpaCy → crea nodi Person/Location + edge MENTIONS)
  5. Face detection (YOLOv8n → bounding box, conteggio volti)
  6. Event nodes da data (filename/EXIF/filesystem → nodi Event + edge CREATED_AT)
  7. Geocoding (coordinate GPS → Nominatim → nodi Location + edge LOCATED_AT, con cache JSON)
  8. Scene detection video (PySceneDetect AdaptiveDetector → keyframe JPEG + nodi Event + edge HAS_SCENE)
- **`image_embedder.py`** — CLIP embedding reale con open-clip-torch (ViT-B/32, 512-dim). Supporta `get_image_embedding()` e `get_text_embedding()` per ricerca cross-modale
- **`dispatcher.py`** — Coda lazy con loop continuo, batch processing, reset stale items (crash recovery). **8 stage attivi** configurabili singolarmente

#### `recognition/` — Face recognition
- **`deepface_engine.py`** — InsightFace con modello `buffalo_l` (RetinaFace detection + ArcFace 512-dim recognition). Funziona interamente su CPU. Supporta: detect, embedding, cosine similarity, clustering DBSCAN, merging, batch processing, salvataggio embedding su file .npy

#### `web/api.py` — REST API (Flask)
Espone il grafo via endpoint HTTP:
- `GET /api/stats` — Statistiche aggregate (nodi per tipo, archi, volti, Chroma count)
- `GET /api/nodes` — Lista nodi con paginazione, filtro per tipo, ricerca testuale
- `GET /api/nodes/<id>` — Dettaglio nodo con archi entranti/uscenti, embedding, foto associata
- `GET /api/graph` — Grafo completo per visualizzazione (campionamento intelligente, colori per tipo)
- `GET /api/search` — Ricerca semantica via ChromaDB (testuale → risultati cross-modali con similarità)
- `GET /api/faces` — Lista nodi Person con bounding box, embedding, sorgente
- `GET /api/projects` — Progetti con conteggio file
- `GET /api/locations` — Location con conteggio mentions
- `GET /api/embeddings/status` — Statistiche embedding .npy salvati
- `GET /api/images/<node_id>` — Servi file immagine dal file_registry
- `GET /api/events` — Eventi con data, conteggio file, ordinati cronologicamente
- `GET /api/events/<id>` — Dettaglio evento con file collegati e scene
- `GET /api/events/timeline` — Timeline eventi raggruppata per anno/mese
- `GET /api/events/calendar` — Dati per calendario (heatmap stile GitHub)

#### CLI (`cli.py`)
16 comandi implementati:

| Comando | Funzione |
|---|---|
| `scan` | Scansione singola directory |
| `scan:all` | Scansione tutti storage configurati |
| `watchdog start/status` | Watchdog filesystem in tempo reale |
| `queue process/loop/status/reset-stale` | Gestione coda di elaborazione |
| `search` | Ricerca semantica nel grafo |
| `graph status` | Statistiche del grafo |
| `configure set/test/clear` | Gestione password via keyring |
| `db dedup/stats` | Operazioni sul database |
| `geo process/test/cache` | Geocoding GPS (Nominatim) |
| `event create-from-dates/status/list` | Gestione nodi Event |
| `video detect-scenes/list/status` | Scene detection per video |
| `quarantine list/clear` | Gestione quarantena HSD |
| `face test/process-all/reprocess/status/cluster/cluster-dbscan/embedding-status` | Face recognition |

#### Scripts batch
- `scripts/batch_face_detection.py` — Face detection su larga scala
- `scripts/batch_image_embedding.py` — Embedding batch per immagini
- `scripts/check_paths.py` — Verifica esistenza path configurati
- `scripts/explore_data.py` — Esplorazione dataset
- `scripts/find_parents_photos.py` — Trova foto genitori (versione standalone)

#### Configurazione
- `settings.py` — Config via `.env` + keyring di sistema (Windows Credential Manager, macOS Keychain, Linux Secret Service)
- Password mai in chiaro: keyring → env var → `.env` (con warning)
- Storage paths, Chroma path, quarantine path, SpaCy model, InsightFace settings, log level, batch size

---

## 2. Archimede — Lettura Passiva

Riadattamento per la lettura e navigazione **read-only** del grafo Penelope.

### Principi
- **Non scrive mai** sul grafo — solo query SELECT su MariaDB e letture ChromaDB
- **Non esegue, non modifica, non elimina** nulla
- Identity resolution su foto tramite InsightFace ArcFace 512-dim
- Re-encoding delle credenziali: eredita la configurazione (`.env` e keyring) da Penelope

### Moduli implementati

#### `query.py` — CLI di ricerca
Entry point principale. Comandi:
- `find-parents` — Cerca foto di coppia dei genitori. Modalità:
  - `--ref-dir <path>`: cartella con sottocartelle `papa/`, `mamma/` con foto di referenza
  - `--interactive`: clustering interattivo dei volti con identificazione guidata
- `stats` — Statistiche lettura del grafo

#### `graph/reader.py` — PenelopeGraphReader
Wrapper **read-only** attorno a `MariaDBStore` di Penelope. Caratteristiche:
- Solo query SELECT (blocco automatico via `stripped.upper().startswith("SELECT")`)
- Eredita automaticamente credenziali da Penelope (keyring + `.env`)
- Cerca automaticamente la directory Penelope
- Metodi: `count_photos()`, `get_all_photos()`, `get_photos_in_directory()`, `get_photos_with_face_count()`, `get_person_nodes()`, `get_edges_for_photo()`, `get_persons_in_photo()`

#### `graph/chroma_reader.py` — PenelopeChromaReader
Reader **read-only** della ChromaDB di Penelope. Metodi:
- `get_collections()`, `query_images()`, `count_images()`

#### `identity/face_engine.py` — Motore InsightFace
- Caricamento lazy del modello `buffalo_l` (ArcFace 512-dim)
- Funzioni: `detect_faces()`, `cosine_similarity()`, `verify()`
- Embedding 512-dim normalizzati L2

#### `identity/matcher.py` — Face matching
Pipeline completa:
1. Carica foto referenza da directory strutturate (`papa/`, `mamma/`)
2. Calcola embedding medio per ogni persona
3. Per ogni foto nel database: rileva volti, confronta embedding con referenze
4. Trova foto dove ENTRAMBI i genitori appaiono insieme
- Soglia similarità configurabile (default 0.35)
- Supporto batch con callback di progresso

#### `presentation/report.py` — Report HTML
Genera report HTML navigabile con:
- Statistiche (foto scansionate, con volti, di coppia, durata)
- Galleria foto di coppia con badge
- Gallerie per singolo genitore
- Lightbox per ingrandimento
- Tags colorati per persona
- Thumbnail in data URI (CV2 + JPEG compression)

#### `models.py` — Modelli dati Oracle
`Photo`, `DetectedFace`, `ReferenceFace`, `FaceMatch`, `PhotoMatchResult`, `SearchReport`

---

## 3. Oracle — Agente Esecutivo e Interfaccia Applicativa

Agente di coding autonomo basato su framework Agno. In Oracle è **agente esecutivo** e **interfaccia applicativa principale**: crea codice, accede a internet, fa ricerche web, e comunica direttamente con l'utente tramite frontend web e API chat. Usa i dati di Penelope come contesto/memoria ma **non ha accesso di scrittura** ai dati sensibili del grafo.

### Architettura

```
Oracle/
├── coding_agent.py         # Agente + server FastAPI
├── cli.py                  # CLI interattiva
├── model_factory.py        # Registry provider LLM (50+ provider supportati)
├── system_prompt*.md       # 3 varianti: full, lite, standard
├── CONSTITUTION.md         # Legge fondamentale immutabile (7 articoli)
├── api/
│   ├── auth.py             # JWT authentication
│   ├── rate_limit.py       # Rate limiting (in-memory o Redis)
│   └── security.py         # CORS, HSTS, CSP, X-Frame-Options
├── tools/                  # Tool a disposizione dell'agente
│   ├── oracle_protocol.py  # Orchestratore MCTS + Sandbox + Context Filter
│   ├── mcts_engine.py      # Albero decisionale multi-ramo
│   ├── interleaved_sandbox.py # Esecuzione Python/Bash/SQL sicura
│   ├── semantic_context_filter.py # Anti-drift per sessioni lunghe
│   ├── vector_memory.py    # Memoria vettoriale ChromaDB (multimodale CLIP)
│   ├── multimodal_encoder.py # CLIP encoder per immagini
│   ├── web_access.py       # HTTP GET/POST/DOWNLOAD con SSRF protection
│   ├── wiki_tool.py        # Wikipedia API wrapper
│   ├── gmail_client.py     # Gmail API (lettura/invio email)
│   ├── immunity_guardian.py # Verifica costituzionale delle azioni
│   ├── environment_probe.py # Feasibility pre-flight (porte, dipendenze, FS)
│   ├── constitution.py     # Costituzione in formato tool
│   ├── chunk_filter.py     # Filtro chunk per contesto
│   ├── tool_catalog.md     # Catalogo dinamico dei tool
│   └── TOOL_REGISTRY.md    # Registro dei tool disponibili
└── workspace/
    ├── tool_lifecycle.py   # Ciclo di vita dei tool
    ├── tool_repository.py  # Repository dei tool
    ├── rui/                # Framework di intelligence OSINT
    │   ├── collection/     # Raccolta: web scraper, domain recon, social search
    │   ├── analysis/       # Analisi: cross-reference, timeline, geo OSINT
    │   ├── verification/   # Verifica: fact checker, confidence scorer
    │   ├── core/           # Case manager, intelligence cycle, source matrix
    │   ├── reporting/      # Report generator, sanitizer
    │   ├── cybersecurity/  # Attack surface, breach checker, threat intel
    │   ├── research/       # Academic search, legal reference
    │   └── math_science/   # Math solver, physics tools, CS analyzer
    └── *.md                # Documentazione obiettivi e capacità
```

### Frontend
Oracle espone l'interfaccia utente con design Matrix Rain:
- **http://localhost:8100/** — Interfaccia principale
- **POST /api/chat** — Chat non-streaming
- **GET /api/chat/stream** — Chat con SSE streaming
- **GET /api/health** — Health check
- **GET /api/model** — Info modello attivo
- **GET /ui** — Serve il frontend HTML

### CONSTITUTION.md — Legge fondamentale
7 articoli immutabili che vincolano l'agente:
1. **Perimetro operativo**: solo directory autorizzata
2. **Accesso web**: solo domini whitelistati
3. **Danno e privacy**: nessuna azione dannosa
4. **Nuovi tool**: devono essere approvati dall'utente prima di essere attivi
5. **Azioni irreversibili**: richiedono conferma esplicita
6. **Perceived limit**: se un task supera un confine etico/di sicurezza → stop immediato
7. **Immutabilità**: la costituzione non può essere modificata

---

## 4. Egida — Guardrail HSD (4° strato indipendente)

**Egida è il 4° strato di Oracle**, un guardrail HSD (Highly Sensitive Data) auto-contenuto e indipendente. Agisce **a monte di TUTTI gli strati**: Penelope, Archimede e Oracle.

### Architettura

```
Oracle/egida/
├── __init__.py        # Esporta API pubblica (HSDFilter, Quarantine, scan_text, scan_file)
├── config.py          # Configurazione indipendente via env var (EGIDA_THRESHOLD, EGIDA_SPACY_MODEL, ...)
├── filters.py         # 14 pattern regex + validazioni post-match + scoring/severity v2.0
├── ner_light.py       # NER SpaCy (PERSON, GPE, LOC, ORG, ADDRESS) con degrado graceful
├── quarantine.py      # Isolamento file in directory datata + report JSON
├── pyproject.toml     # Installabile via pip install -e .
└── tests/
    └── test_filters.py # 45 test (veri positivi, falsi positivi, scoring, binary detection)
```

### Funzionamento
Ogni file viene analizzato PRIMA di entrare in qualsiasi strato. Se contiene HSD con score >= soglia (default 90), viene copiato in quarantena con report JSON e NON passa.

### Sistema di Scoring v2.0

| Severity | Peso | Esempi |
|----------|------|--------|
| **CRITICAL** | 100 | AWS Key, GitHub Token, SSH Key, HuggingFace Token |
| **HIGH** | 90 | Codice Fiscale, API Key vera, JWT valido
| **MEDIUM** | 50 | Telefono, Email (cumulativo) |
| **LOW** | 25 | CAP, placeholder password |
| **INFO** | 10 | Email fittizia di test |

### Pattern rilevati (14)
**CRITICAL:** AWS Access Key, Token GitHub (ghp_/gho_/ghu_), Token HuggingFace, Chiave SSH privata
**HIGH:** Codice Fiscale italiano, API Key / Secret generico, Token JWT / Bearer, Password esplicita, Token Discord/Slack
**MEDIUM:** Numero di telefono, Indirizzo email
**LOW:** CAP

**NER linguistico (SpaCy):** Persone, Luoghi, Organizzazioni, Indirizzi

### Protezione anti-falso positivo
- UUID non scambiati per telefono
- Timestamp non scambiati per CAP
- Placeholder password (type hint, CI defaults) declassati a LOW
- Email fittizie (example.com, test.com) declassate a INFO
- URL non scambiati per JWT
- Magic byte detection per file binari

### Differenze dalla versione precedente (modulo di Penelope)

| Aspetto | Prima (modulo Penelope) | Ora (4° strato) |
|---------|------------------------|-----------------|
| **Posizione** | `Oracle/Penelope/penelope/egida/` | `Oracle/egida/` |
| **Config** | Leggeva `penelope.config.settings` (prefisso PENELOPE_) | `egida/config.py` indipendente (prefisso EGIDA_) |
| **Copertura** | Solo Penelope | Penelope + Archimede + Oracle |
| **Import** | `from penelope.egida import ...` | `from egida import ...` |
| **Installazione** | Inclusa in Penelope | `pip install -e Oracle/egida` |
| **Test** | Insieme ai test di Penelope | Indipendenti (45 test) |

### Quarantena
- Copia del file in directory `quarantine/YYYYMMDD_HHMMSS/`
- Report JSON con: path originale, timestamp, match_count, score, soglia, match dettagliati
- Comandi CLI: `quarantine list`, `quarantine clear`
- Stato attuale: **130+ entry in quarantena**

### Uso da altri strati

```python
# Penelope (gia' configurato)
from egida.filters import HSDFilter
from egida.quarantine import Quarantine

# Archimede (aggiungere Oracle/ al path)
import sys; sys.path.insert(0, 'path/to/Oracle')
from egida.filters import HSDFilter

# Oracle (aggiungere Oracle/ al path)
from egida.filters import HSDFilter
```

---

## 5. Stato attuale del grafo (Penelope)

Dati reali dal MariaDB (accesso al 15 Luglio 2026):

| Metrica | Valore |
|---|---|
| **Nodi totali** | **8.908** |
| └ File | 4.127 |
| └ Location | 2.675 |
| └ Person | 2.102 |
| └ Project | 4 |
| └ Event | **Generati attivamente** da date e scene detection |
| **Archi totali** | **10.344** |
| └ MEMBER_OF (File → Project) | 4.127 |
| └ MENTIONS (File → Person/Location) | 4.115 |
| └ CONTAINS (File → Person) | 2.102 |
| └ CREATED_AT (File → Event) | ✨ Generati da process_date_event |
| └ LOCATED_AT (File → Location) | ✨ Generati da process_geocoding |
| └ HAS_SCENE (Video → Event) | ✨ Generati da process_scene_detection |
| **File con volti rilevati** | **3.105** |
| └ con InsightFace (ArcFace 512-dim) | 870 |
| └ solo YOLO (detection base) | 2.235 |
| **Nodi Person con embedding** | 209 su 2.102 |
| **ChromaDB — documenti testo** | 264 |
| **ChromaDB — immagini (CLIP)** | Dipende dal batch processing |
| **Coda ingestion** | 3.991 fatti, ~136 in elaborazione |
| **Entry in quarantena HSD** | 130+ |

---

## 6. Testing

### Penelope

| Test | Stato |
|---|---|
| `tests/test_filters.py` — Filtri HSD | ✅ |
| `tests/test_graph_bridge.py` — NetworkX bridge | ✅ |
| `tests/test_metadata.py` — Estrazione metadati | ✅ |
| `tests/test_scanner.py` — Scanner filesystem | ✅ |
| `tests/test_chroma.py` — ChromaDB | ✅ (8 test: index, search, count, upsert, immagini, persistenza) |
| `tests/test_dispatcher.py` — Coda elaborazione | ✅ (6 test: init, process, batch, loop, stale reset, stop) |
| `tests/test_processor.py` — Stage elaborazione | ✅ (11 test: EXIF, embedding, NER, face detection, scene detection) |
| `tests/test_deepface.py` — InsightFace | ✅ (8 test: detection, cosine similarity, verify, save/load embedding, process) |
| `tests/test_e2e_integration.py` — Test E2E | ✅ (13 test: scan→queue, dedup, dispatcher, graph bridge, chroma, egida, processor) |

### Archimede

| Test | Stato |
|---|---|
| `tests/test_graph_reader.py` — Reader grafo | ✅ (13 test: connessione, SELECT-only, query, photos, persons, edges) |
| `tests/test_chroma_reader.py` — Reader ChromaDB | ✅ (10 test: init, collections, query, count, close) |
| `tests/test_face_engine.py` — Motore InsightFace | ✅ (9 test: analyzer, detection, cosine similarity, verify) |
| `tests/test_matcher.py` — Face matching | ✅ (10 test: load references, match photo, couple search, callback) |
| `tests/test_report.py` — Report HTML | ✅ (9 test: generation, content, empty/single parent, properties) |
| `tests/test_query.py` — CLI query | ✅ (5 test: stats, find-parents, error handling) |

### Egida (4° strato indipendente)
| Test | Stato |
|---|---|
| `tests/test_filters.py` — 45 test | ✅ (veri positivi, falsi positivi, scoring, binary) |

### Oracle
| Test | Stato |
|---|---|
| `tests/test_api_auth.py` | ✅ |
| `tests/test_config.py` | ✅ |
| `tests/test_immunity_guardian.py` | ✅ |

---

## 7. Placeholder e lavori in corso

| Funzionalità | Stato | Dettaglio |
|---|---|---|
| **Face clustering su larga scala** | 🟠 Parziale | DBSCAN implementato ma non eseguito su tutti i 2.102 nodi Person |
| **Trascrizione audio (Whisper)** | 🔴 Non iniziato | Menzionato come Fase 2, nessun codice |
| **Sync cross-device** | 🟡 Non iniziato | Rilevamento file spostati, conflitti, merge |
| **Smartphone** | 🔵 Futuro | Architettura lo menziona, non iniziato |

### Legenda placeholder risolti
- ~~Watchdog filesystem~~ → ✅ **Implementato reale** (`WatchdogManager` con debounce)
- ~~Embedding immagini CLIP~~ → ✅ **Implementato reale** (open-clip-torch ViT-B/32)
- ~~Scene detection video~~ → ✅ **Implementato reale** (PySceneDetect AdaptiveDetector)
- ~~Nodi Event~~ → ✅ **Creati attivamente** da process_date_event e process_scene_detection
- ~~Geocoding GPS~~ → ✅ **Implementato** con Nominatim e cache JSON
- ~~Azure Face API~~ → ❌ **RIMOSSA** (non più necessaria, InsightFace locale sufficiente)

---

## 8. Vincoli hardware attuali

Nodi disponibili oggi:
- **Laptop main**: i3, 8GB RAM, GPU integrata
- **Laptop Linux headless**: solo storage, Celeron, 4GB RAM
- **Hard disk esterno 1TB**: progetti in corso o pubblicati su GitHub
- **Server Uninet (Proxmox)**: Celeron, 2GB RAM — ospita il MariaDB per Penelope
- **Smartphone**: da integrare in futuro
- **Mac M1 Pro 32GB**: in arrivo — riserverà calcolo pesante (embedding, NER, modelli locali)

---

## 9. Comandi CLI

### Penelope

```powershell
# Scansione
python -m penelope.cli scan --device <nome> --project <nome> <path>
python -m penelope.cli scan:all

# Watchdog
python -m penelope.cli watchdog start [--path D:/dir]
python -m penelope.cli watchdog status

# Coda
python -m penelope.cli queue process
python -m penelope.cli queue loop
python -m penelope.cli queue status
python -m penelope.cli queue reset-stale

# Ricerca semantica
python -m penelope.cli search "query" --top 10

# Grafo
python -m penelope.cli graph status

# Face
python -m penelope.cli face reprocess
python -m penelope.cli face cluster-dbscan --eps 0.4 --merge
python -m penelope.cli face process-all
python -m penelope.cli face embedding-status

# Geo
python -m penelope.cli geo process
python -m penelope.cli geo test

# Eventi
python -m penelope.cli event create-from-dates
python -m penelope.cli event status

# Video
python -m penelope.cli video detect-scenes

# Quarantena
python -m penelope.cli quarantine list
python -m penelope.cli quarantine clear

# Config
python -m penelope.cli configure set
python -m penelope.cli configure test

# Web API
python web/api.py
```

### Archimede

```powershell
python -m archimede.query find-parents --ref-dir ref_faces/
python -m archimede.query find-parents --interactive
python -m archimede.query stats
```

### Oracle (orchestratore + frontend)

```powershell
# Avvio unificato (Oracle su :8100)
python run.py

# Con porta personalizzata
python run.py --port 9000

# Con Archimede API
python run.py --with-archimede

# CLI interattiva
cd Oracle/Oracle
python cli.py
```

---

## 10. Architettura completa (diagramma)

```
┌────────────────────────────────────────────────────────────────────────────┐
│                              ORACLE                                        │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  EGIDA (4° strato — guardrail HSD indipendente e cross-layer)       │  │
│  │                                                                      │  │
│  │  ╔═══════════════════════════════════════════════════════════════╗   │  │
│  │  ║  filters.py: 14 pattern regex + scoring v2.0                 ║   │  │
│  │  ║  ner_light.py: NER SpaCy (PERSON, GPE, LOC, ORG, ADDRESS)   ║   │  │
│  │  ║  quarantine.py: Isolamento + report JSON                     ║   │  │
│  │  ║  config.py: env vars indipendenti (EGIDA_THRESHOLD, ecc.)    ║   │  │
│  │  ╚═══════════════════════════════════════════════════════════════╝   │  │
│  │  API: check_file()  check_text()  scan_file()  isolate()            │  │
│  └────────────────────────┬─────────────────────────────────────────────┘  │
│                           │                                                │
│           ┌───────────────┼───────────────┐                                │
│           ▼               ▼               ▼                                │
│  ┌──────────────────┐ ┌──────────┐ ┌──────────────────────────────────┐   │
│  │    PENELOPE      │ │SAMARITAN │ │        ORACLE                    │   │
│  │  Ingestione &    │ │ Lettura  │ │  Orchestratore + Agente          │   │
│  │    Grafo         │ │ passiva  │ │  Esecutivo + Frontend            │   │
│  │                  │ │read-only │ │  + Interfaccia Applicativa       │   │
│  │ [Scanner]        │ │[Graph    │ │                                  │   │
│  │ [Watchdog]       │ │ Reader]  │ │ [Matrix Rain UI]                 │   │
│  │ [Dispatcher]     │ │[Chroma   │ │ [MCTS Engine]                    │   │
│  │ [MariaDB]        │ │ Reader]  │ │ [Sandbox]                        │   │
│  │ [NetworkX]       │ │[Face     │ │ [SCF]                            │   │
│  │ [ChromaDB]       │ │ Engine]  │ │ [Vector Memory]                  │   │
│  │ [Web API Flask]  │ │[Report]  │ │ [Web Access + Wiki]              │   │
│  │ [Recognition]    │ │          │ │ [Gmail]                          │   │
│  │                  │ │          │ │ [Immunity Guardian]              │   │
│  │                  │ │          │ │ [Constitution]                   │   │
│  │                  │ │          │ │ [RUI OSINT Framework]            │   │
│  └──────────────────┘ └──────────┘ └──────────────────────────────────┘   │
│                                                                            │
│  Frontend: http://localhost:8100  |  API Chat: POST /api/chat             │
│                                                                            │
└────────────────────────────────────────────────────────────────────────────┘
```
