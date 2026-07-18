# Oracle — Architecture and Current Status

## Objective

Oracle was born from the need to unify Toni's enormous amount of heterogeneous personal data (documents, photos, videos, projects, scripts) scattered across multiple devices, into a single navigable and queryable graph — conceptually analogous to what Palantir does for government or enterprise clients, but applied entirely to local and personal data.

The core idea: heterogeneous data becomes far more useful when linked into a single entity graph, because it enables discovering relationships and information that a human would not notice by looking at sources separately.

## Conceptual Mapping

| Palantir | Oracle | Role |
|---|---|---|
| Gotham / Foundry | **Penelope** | Ingestion and fusion of heterogeneous data into a unified graph |
| (analyst read layer) | **Archimede** | Passive reading and graph navigation |
| (application layer/AIP) | **Oracle** | Orchestrator + executive agent (coding, OSINT, research) |
| Apollo | **Egida** | Guardrail against sensitive data exposure (HSD) |

---

## 1. Penelope — Ingestion & Graph

Ingestion and fusion layer. Handles:
- Ingestion of heterogeneous data: images, documents (PDF/markdown/text), video/audio, project directories/scripts
- Fusion from different devices: main laptop, headless Linux (storage), 1TB external HDD
- Identity/entity resolution: recognizing that the same entity appears in different sources and linking it as a single node in the graph

### Technical Stack

| Component | Role | Status |
|---|---|---|
| **MariaDB** (Proxmox, Celeron 2GB) | Registry: nodes, edges, file_registry, processing queue | ✅ **8,908 nodes, 10,344 edges** |
| **NetworkX** (in-memory) | Read/write bridge to MariaDB, graph queries, node merging | ✅ Operational |
| **ChromaDB** (local persistent) | Semantic embedding (MiniLM 384-dim + CLIP 512-dim) | ✅ **264+ documents + images indexed** |
| **Egida** | HSD filters upstream of ingestion | ✅ **130+ quarantine entries** |

### Implemented Modules

#### `db/` — Database layer
- **`schema.sql`** — 4 tables: `nodes` (5 type ENUM: File, Project, Person, Location, Event), `edges` (7 relations), `file_registry`, `ingestion_queue`
- **`mariadb_store.py`** — Full CRUD: nodes, edges (with foreign key CASCADE), file_registry, queue with dequeue and mark_done, reset_stale_processing for crash recovery
- **`graph_bridge.py`** — Bidirectional sync NetworkX ↔ MariaDB, neighbor queries, shortest path, node merging (identity resolution), subgraph filtering
- **`chroma_store.py`** — Two collections: `file_embeddings` (MiniLM 384-dim for text) and `image_embeddings` (CLIP 512-dim for images). Cross-modal semantic search. Graceful fallback if CLIP is unavailable

#### `egida/` — HSD Guardrail
- **`filters.py`** — 14 regex patterns: API key, JWT, password, GitHub token, AWS key, HuggingFace token, SSH key, postal code, phone, tax ID, email, Discord/Slack token
- **`ner_light.py`** — NER with SpaCy `it_core_news_sm` (PERSON, GPE, LOC, ORG, ADDRESS). Graceful degradation if SpaCy is not installed
- **`quarantine.py`** — Isolation of infected files in dated directories with JSON match reports

#### `ingestion/` — Ingestion Pipeline
- **`scanner.py`** — Recursive one-shot scan with SHA-256 dedup, skip patterns, on_file_processed callback. **Real watchdog** with `WatchdogManager` (thread-safe observer, 2s debounce, FileCreationHandler)
- **`metadata.py`** — Basic metadata extraction: SHA-256 hash, MIME type (80+ mapped types), size, date. Magic bytes fallback for unknown extensions
- **`processor.py`** — **8 processing stages**:
  1. EXIF (Pillow → GPS, date taken, camera make/model)
  2. Text embedding (MiniLM → ChromaDB)
  3. Image embedding (CLIP ViT-B/32, 512-dim → ChromaDB)
  4. NER (SpaCy → creates Person/Location nodes + MENTIONS edges)
  5. Face detection (YOLOv8n → bounding boxes, face count)
  6. Event nodes from dates (filename/EXIF/filesystem → Event nodes + CREATED_AT edges)
  7. Geocoding (GPS coordinates → Nominatim → Location nodes + LOCATED_AT edges, with JSON cache)
  8. Video scene detection (PySceneDetect AdaptiveDetector → keyframe JPEG + Event nodes + HAS_SCENE edges)
- **`image_embedder.py`** — Real CLIP embedding with open-clip-torch (ViT-B/32, 512-dim). Supports `get_image_embedding()` and `get_text_embedding()` for cross-modal search
- **`dispatcher.py`** — Lazy queue with continuous loop, batch processing, stale item reset (crash recovery). **8 active stages**, individually configurable

#### `recognition/` — Face recognition
- **`deepface_engine.py`** — InsightFace with `buffalo_l` model (RetinaFace detection + ArcFace 512-dim recognition). Runs entirely on CPU. Supports: detect, embedding, cosine similarity, DBSCAN clustering, merging, batch processing, embedding saving to .npy files

#### `web/api.py` — REST API (Flask)
Exposes the graph via HTTP endpoints:
- `GET /api/stats` — Aggregate stats (nodes by type, edges, faces, Chroma count)
- `GET /api/nodes` — Node list with pagination, type filter, text search
- `GET /api/nodes/<id>` — Node detail with incoming/outgoing edges, embedding, associated photo
- `GET /api/graph` — Full graph for visualization (intelligent sampling, colors by type)
- `GET /api/search` — Semantic search via ChromaDB (text → cross-modal results with similarity)
- `GET /api/faces` — Person node list with bounding boxes, embedding, source
- `GET /api/projects` — Projects with file count
- `GET /api/locations` — Locations with mention count
- `GET /api/embeddings/status` — Saved .npy embedding stats
- `GET /api/images/<node_id>` — Serve image file from file_registry
- `GET /api/events` — Events with date, file count, chronologically sorted
- `GET /api/events/<id>` — Event detail with linked files and scenes
- `GET /api/events/timeline` — Event timeline grouped by year/month
- `GET /api/events/calendar` — Calendar data (GitHub-style heatmap)

#### CLI (`cli.py`)
16 implemented commands:

| Command | Function |
|---|---|
| `scan` | Single directory scan |
| `scan:all` | Scan all configured storage |
| `watchdog start/status` | Real-time filesystem watchdog |
| `queue process/loop/status/reset-stale` | Processing queue management |
| `search` | Semantic graph search |
| `graph status` | Graph statistics |
| `configure set/test/clear` | Password management via keyring |
| `db dedup/stats` | Database operations |
| `geo process/test/cache` | GPS geocoding (Nominatim) |
| `event create-from-dates/status/list` | Event node management |
| `video detect-scenes/list/status` | Video scene detection |
| `quarantine list/clear` | HSD quarantine management |
| `face test/process-all/reprocess/status/cluster/cluster-dbscan/embedding-status` | Face recognition |

#### Batch Scripts
- `scripts/batch_face_detection.py` — Large-scale face detection
- `scripts/batch_image_embedding.py` — Batch embedding for images
- `scripts/check_paths.py` — Verify configured path existence
- `scripts/explore_data.py` — Dataset exploration
- `scripts/find_parents_photos.py` — Find parent photos (standalone version)

#### Configuration
- `settings.py` — Config via `.env` + system keyring (Windows Credential Manager, macOS Keychain, Linux Secret Service)
- Password never in plaintext: keyring → env var → `.env` (with warning)
- Storage paths, Chroma path, quarantine path, SpaCy model, InsightFace settings, log level, batch size

---

## 2. Archimede — Passive Reading

Adaptation for **read-only** reading and navigation of the Penelope graph.

### Principles
- **Never writes** to the graph — only SELECT queries on MariaDB and ChromaDB reads
- **Does not execute, modify, or delete** anything
- Identity resolution on photos via InsightFace ArcFace 512-dim
- Credential re-encoding: inherits configuration (`.env` and keyring) from Penelope

### Implemented Modules

#### `query.py` — Search CLI
Main entry point. Commands:
- `find-parents` — Search for parent couple photos. Modes:
  - `--ref-dir <path>`: folder with subfolders `dad/`, `mom/` with reference photos
  - `--interactive`: interactive face clustering with guided identification
- `stats` — Graph reading statistics

#### `graph/reader.py` — PenelopeGraphReader
**Read-only** wrapper around Penelope's `MariaDBStore`. Features:
- Only SELECT queries (auto-block via `stripped.upper().startswith("SELECT")`)
- Automatically inherits credentials from Penelope (keyring + `.env`)
- Automatically searches for the Penelope directory
- Methods: `count_photos()`, `get_all_photos()`, `get_photos_in_directory()`, `get_photos_with_face_count()`, `get_person_nodes()`, `get_edges_for_photo()`, `get_persons_in_photo()`

#### `graph/chroma_reader.py` — PenelopeChromaReader
**Read-only** reader of Penelope's ChromaDB. Methods:
- `get_collections()`, `query_images()`, `count_images()`

#### `identity/face_engine.py` — InsightFace Engine
- Lazy loading of `buffalo_l` model (ArcFace 512-dim)
- Functions: `detect_faces()`, `cosine_similarity()`, `verify()`
- 512-dim L2-normalized embeddings

#### `identity/matcher.py` — Face matching
Complete pipeline:
1. Load reference photos from structured directories (`dad/`, `mom/`)
2. Compute average embedding for each person
3. For each photo in the database: detect faces, compare embeddings with references
4. Find photos where BOTH parents appear together
- Configurable similarity threshold (default 0.35)
- Batch support with progress callback

#### `presentation/report.py` — HTML Report
Generates navigable HTML report with:
- Statistics (scanned photos, with faces, couple photos, duration)
- Couple photo gallery with badges
- Individual parent galleries
- Lightbox for enlargement
- Colored tags per person
- Thumbnails in data URI (CV2 + JPEG compression)

#### `models.py` — Oracle data models
`Photo`, `DetectedFace`, `ReferenceFace`, `FaceMatch`, `PhotoMatchResult`, `SearchReport`

---

## 3. Oracle — Executive Agent and Application Interface

Autonomous coding agent based on the Agno framework. In Oracle, it is the **executive agent** and **main application interface**: creates code, accesses the internet, performs web searches, and communicates directly with the user via web frontend and chat API. Uses Penelope data as context/memory but **does not have write access** to sensitive graph data.

### Architecture

```
Oracle/
├── coding_agent.py         # Agent + FastAPI server
├── cli.py                  # Interactive CLI
├── model_factory.py        # LLM provider registry (50+ providers supported)
├── system_prompt*.md       # 3 variants: full, lite, standard
├── CONSTITUTION.md         # Immutable fundamental law (7 articles)
├── api/
│   ├── auth.py             # JWT authentication
│   ├── rate_limit.py       # Rate limiting (in-memory or Redis)
│   └── security.py         # CORS, HSTS, CSP, X-Frame-Options
├── tools/                  # Tools available to the agent
│   ├── oracle_protocol.py  # MCTS Orchestrator + Sandbox + Context Filter
│   ├── mcts_engine.py      # Multi-branch decision tree
│   ├── interleaved_sandbox.py # Safe Python/Bash/SQL execution
│   ├── semantic_context_filter.py # Anti-drift for long sessions
│   ├── vector_memory.py    # Vector memory ChromaDB (multimodal CLIP)
│   ├── multimodal_encoder.py # CLIP encoder for images
│   ├── web_access.py       # HTTP GET/POST/DOWNLOAD with SSRF protection
│   ├── wiki_tool.py        # Wikipedia API wrapper
│   ├── gmail_client.py     # Gmail API (read/send emails)
│   ├── immunity_guardian.py # Constitutional action verification
│   ├── environment_probe.py # Feasibility pre-flight (ports, dependencies, FS)
│   ├── constitution.py     # Constitution as tool
│   ├── chunk_filter.py     # Context chunk filter
│   ├── tool_catalog.md     # Dynamic tool catalog
│   └── TOOL_REGISTRY.md    # Available tools registry
└── workspace/
    ├── tool_lifecycle.py   # Tool lifecycle
    ├── tool_repository.py  # Tool repository
    ├── rui/                # OSINT intelligence framework
    │   ├── collection/     # Collection: web scraper, domain recon, social search
    │   ├── analysis/       # Analysis: cross-reference, timeline, geo OSINT
    │   ├── verification/   # Verification: fact checker, confidence scorer
    │   ├── core/           # Case manager, intelligence cycle, source matrix
    │   ├── reporting/      # Report generator, sanitizer
    │   ├── cybersecurity/  # Attack surface, breach checker, threat intel
    │   ├── research/       # Academic search, legal reference
    │   └── math_science/   # Math solver, physics tools, CS analyzer
    └── *.md                # Objectives and capabilities documentation
```

### Frontend
Oracle exposes the user interface with Matrix Rain design:
- **http://localhost:8100/** — Main interface
- **POST /api/chat** — Non-streaming chat
- **GET /api/chat/stream** — Chat with SSE streaming
- **GET /api/health** — Health check
- **GET /api/model** — Active model info
- **GET /ui** — Serves the HTML frontend

### CONSTITUTION.md — Fundamental Law
7 immutable articles that bind the agent:
1. **Operational perimeter**: only authorized directory
2. **Web access**: only whitelisted domains
3. **Harm and privacy**: no harmful actions
4. **New tools**: must be approved by the user before becoming active
5. **Irreversible actions**: require explicit confirmation
6. **Perceived limit**: if a task crosses an ethical/security boundary → immediate stop
7. **Immutability**: the constitution cannot be modified

---

## 4. Egida — HSD Guardrail (4th independent layer)

**Egida is Oracle's 4th layer**, a self-contained and independent HSD (Highly Sensitive Data) guardrail. It acts **upstream of ALL layers**: Penelope, Archimede, and Oracle.

### Architecture

```
Oracle/egida/
├── __init__.py        # Exports public API (HSDFilter, Quarantine, scan_text, scan_file)
├── config.py          # Independent config via env vars (EGIDA_THRESHOLD, EGIDA_SPACY_MODEL, ...)
├── filters.py         # 14 regex patterns + post-match validation + scoring/severity v2.0
├── ner_light.py       # NER SpaCy (PERSON, GPE, LOC, ORG, ADDRESS) with graceful degradation
├── quarantine.py      # File isolation in dated directory + JSON report
├── pyproject.toml     # Installable via pip install -e .
└── tests/
    └── test_filters.py # 45 tests (true positives, false positives, scoring, binary detection)
```

### Operation
Each file is analyzed BEFORE entering any layer. If it contains HSD with score >= threshold (default 90), it is copied to quarantine with JSON report and does NOT pass through.

### Scoring System v2.0

| Severity | Weight | Examples |
|----------|--------|----------|
| **CRITICAL** | 100 | AWS Key, GitHub Token, SSH Key, HuggingFace Token |
| **HIGH** | 90 | Tax ID, Real API Key, Valid JWT |
| **MEDIUM** | 50 | Phone, Email (cumulative) |
| **LOW** | 25 | Postal code, placeholder password |
| **INFO** | 10 | Test dummy email |

### Detected Patterns (14)
**CRITICAL:** AWS Access Key, GitHub Token (ghp_/gho_/ghu_), HuggingFace Token, Private SSH Key
**HIGH:** Italian Tax ID, Generic API Key / Secret, JWT / Bearer Token, Explicit Password, Discord/Slack Token
**MEDIUM:** Phone number, Email address
**LOW:** Postal code

**Linguistic NER (SpaCy):** Persons, Places, Organizations, Addresses

### False Positive Protection
- UUID not mistaken for phone
- Timestamps not mistaken for postal codes
- Placeholder passwords (type hints, CI defaults) downgraded to LOW
- Dummy emails (example.com, test.com) downgraded to INFO
- URLs not mistaken for JWT
- Magic byte detection for binary files

### Differences from Previous Version (Penelope module)

| Aspect | Before (Penelope module) | Now (4th layer) |
|---------|------------------------|-----------------|
| **Location** | `Oracle/Penelope/penelope/egida/` | `Oracle/egida/` |
| **Config** | Read `penelope.config.settings` (PENELOPE_ prefix) | `egida/config.py` independent (EGIDA_ prefix) |
| **Coverage** | Penelope only | Penelope + Archimede + Oracle |
| **Import** | `from penelope.egida import ...` | `from egida import ...` |
| **Installation** | Included in Penelope | `pip install -e Oracle/egida` |
| **Tests** | Together with Penelope tests | Independent (45 tests) |

### Quarantine
- File copy to `quarantine/YYYYMMDD_HHMMSS/` directory
- JSON report with: original path, timestamp, match_count, score, threshold, detailed matches
- CLI commands: `quarantine list`, `quarantine clear`
- Current status: **130+ quarantine entries**

### Usage from Other Layers

```python
# Penelope (already configured)
from egida.filters import HSDFilter
from egida.quarantine import Quarantine

# Archimede (add Oracle/ to path)
import sys; sys.path.insert(0, 'path/to/Oracle')
from egida.filters import HSDFilter

# Oracle (add Oracle/ to path)
from egida.filters import HSDFilter
```

---

## 5. Current Graph Status (Penelope)

Real data from MariaDB (accessed July 15, 2026):

| Metric | Value |
|---|---|
| **Total nodes** | **8,908** |
| └ File | 4,127 |
| └ Location | 2,675 |
| └ Person | 2,102 |
| └ Project | 4 |
| └ Event | **Actively generated** from dates and scene detection |
| **Total edges** | **10,344** |
| └ MEMBER_OF (File → Project) | 4,127 |
| └ MENTIONS (File → Person/Location) | 4,115 |
| └ CONTAINS (File → Person) | 2,102 |
| └ CREATED_AT (File → Event) | ✨ Generated by process_date_event |
| └ LOCATED_AT (File → Location) | ✨ Generated by process_geocoding |
| └ HAS_SCENE (Video → Event) | ✨ Generated by process_scene_detection |
| **Files with detected faces** | **3,105** |
| └ with InsightFace (ArcFace 512-dim) | 870 |
| └ YOLO only (base detection) | 2,235 |
| **Person nodes with embedding** | 209 out of 2,102 |
| **ChromaDB — text documents** | 264 |
| **ChromaDB — images (CLIP)** | Depends on batch processing |
| **Ingestion queue** | 3,991 done, ~136 processing |
| **HSD quarantine entries** | 130+ |

---

## 6. Testing

### Penelope

| Test | Status |
|---|---|
| `tests/test_filters.py` — HSD Filters | ✅ |
| `tests/test_graph_bridge.py` — NetworkX bridge | ✅ |
| `tests/test_metadata.py` — Metadata extraction | ✅ |
| `tests/test_scanner.py` — Filesystem scanner | ✅ |
| `tests/test_chroma.py` — ChromaDB | ✅ (8 tests: index, search, count, upsert, images, persistence) |
| `tests/test_dispatcher.py` — Processing queue | ✅ (6 tests: init, process, batch, loop, stale reset, stop) |
| `tests/test_processor.py` — Processing stages | ✅ (11 tests: EXIF, embedding, NER, face detection, scene detection) |
| `tests/test_deepface.py` — InsightFace | ✅ (8 tests: detection, cosine similarity, verify, save/load embedding, process) |
| `tests/test_e2e_integration.py` — E2E Tests | ✅ (13 tests: scan→queue, dedup, dispatcher, graph bridge, chroma, egida, processor) |

### Archimede

| Test | Status |
|---|---|
| `tests/test_graph_reader.py` — Graph reader | ✅ (13 tests: connection, SELECT-only, query, photos, persons, edges) |
| `tests/test_chroma_reader.py` — ChromaDB reader | ✅ (10 tests: init, collections, query, count, close) |
| `tests/test_face_engine.py` — InsightFace engine | ✅ (9 tests: analyzer, detection, cosine similarity, verify) |
| `tests/test_matcher.py` — Face matching | ✅ (10 tests: load references, match photo, couple search, callback) |
| `tests/test_report.py` — HTML report | ✅ (9 tests: generation, content, empty/single parent, properties) |
| `tests/test_query.py` — CLI query | ✅ (5 tests: stats, find-parents, error handling) |

### Egida (4th independent layer)
| Test | Status |
|---|---|
| `tests/test_filters.py` — 45 tests | ✅ (true positives, false positives, scoring, binary) |

### Oracle
| Test | Status |
|---|---|
| `tests/test_api_auth.py` | ✅ |
| `tests/test_config.py` | ✅ |
| `tests/test_immunity_guardian.py` | ✅ |

---

## 7. Placeholders and Work in Progress

| Functionality | Status | Detail |
|---|---|---|
| **Large-scale face clustering** | 🟠 Partial | DBSCAN implemented but not run on all 2,102 Person nodes |
| **Audio transcription (Whisper)** | 🔴 Not started | Mentioned as Phase 2, no code |
| **Cross-device sync** | 🟡 Not started | Moved file detection, conflicts, merge |
| **Smartphone** | 🔵 Future | Architecture mentions it, not started |

### Resolved Placeholder Legend
- ~~Filesystem watchdog~~ → ✅ **Real implementation** (`WatchdogManager` with debounce)
- ~~CLIP image embeddings~~ → ✅ **Real implementation** (open-clip-torch ViT-B/32)
- ~~Video scene detection~~ → ✅ **Real implementation** (PySceneDetect AdaptiveDetector)
- ~~Event nodes~~ → ✅ **Actively created** by process_date_event and process_scene_detection
- ~~GPS geocoding~~ → ✅ **Implemented** with Nominatim and JSON cache
- ~~Azure Face API~~ → ❌ **REMOVED** (no longer needed, local InsightFace sufficient)

---

## 8. Current Hardware Constraints

Nodes available today:
- **Main laptop**: i3, 8GB RAM, integrated GPU
- **Headless Linux laptop**: storage only, Celeron, 4GB RAM
- **1TB external hard disk**: ongoing or GitHub-published projects
- **Uninet Server (Proxmox)**: Celeron, 2GB RAM — hosts Penelope's MariaDB
- **Smartphone**: to be integrated in the future
- **Mac M1 Pro 32GB**: incoming — will handle heavy computation (embedding, NER, local models)

---

## 9. CLI Commands

### Penelope

```powershell
# Scanning
python -m penelope.cli scan --device <name> --project <name> <path>
python -m penelope.cli scan:all

# Watchdog
python -m penelope.cli watchdog start [--path D:/dir]
python -m penelope.cli watchdog status

# Queue
python -m penelope.cli queue process
python -m penelope.cli queue loop
python -m penelope.cli queue status
python -m penelope.cli queue reset-stale

# Semantic search
python -m penelope.cli search "query" --top 10

# Graph
python -m penelope.cli graph status

# Face
python -m penelope.cli face reprocess
python -m penelope.cli face cluster-dbscan --eps 0.4 --merge
python -m penelope.cli face process-all
python -m penelope.cli face embedding-status

# Geo
python -m penelope.cli geo process
python -m penelope.cli geo test

# Events
python -m penelope.cli event create-from-dates
python -m penelope.cli event status

# Video
python -m penelope.cli video detect-scenes

# Quarantine
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

### Oracle (orchestrator + frontend)

```powershell
# Unified startup (Oracle on :8100)
python run.py

# Custom port
python run.py --port 9000

# With Archimede API
python run.py --with-archimede

# Interactive CLI
cd Oracle/Oracle
python cli.py
```

---

## 10. Complete Architecture (diagram)

```
┌────────────────────────────────────────────────────────────────────────────┐
│                              ORACLE                                        │
├────────────────────────────────────────────────────────────────────────────┤
│                                                                            │
│  ┌──────────────────────────────────────────────────────────────────────┐  │
│  │  EGIDA (4th layer — independent, cross-layer HSD guardrail)          │  │
│  │                                                                      │  │
│  │  ╔═══════════════════════════════════════════════════════════════╗   │  │
│  │  ║  filters.py: 14 regex patterns + scoring v2.0                ║   │  │
│  │  ║  ner_light.py: NER SpaCy (PERSON, GPE, LOC, ORG, ADDRESS)   ║   │  │
│  │  ║  quarantine.py: Isolation + JSON report                      ║   │  │
│  │  ║  config.py: independent env vars (EGIDA_THRESHOLD, etc.)     ║   │  │
│  │  ╚═══════════════════════════════════════════════════════════════╝   │  │
│  │  API: check_file()  check_text()  scan_file()  isolate()            │  │
│  └────────────────────────┬─────────────────────────────────────────────┘  │
│                           │                                                │
│           ┌───────────────┼───────────────┐                                │
│           ▼               ▼               ▼                                │
│  ┌──────────────────┐ ┌──────────┐ ┌──────────────────────────────────┐   │
│  │    PENELOPE      │ │SAMARITAN │ │        ORACLE                    │   │
│  │  Ingestion &     │ │ Read-only│ │  Orchestrator + Agent            │   │
│  │    Graph         │ │ Reading  │ │  Executive + Frontend            │   │
│  │                  │ │          │ │  + Application Interface         │   │
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
