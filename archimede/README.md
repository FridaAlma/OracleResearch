# Archimede — Agente Passivo di Lettura, Navigazione e Identity Resolution per Penelope

**Archimede** è l'agente di interrogazione passiva del grafo **Penelope** all'interno del sistema **Oracle**. Legge, naviga e collega dati senza mai scrivere, modificare o eliminare nulla. Specializzato in **identity resolution** su grandi volumi di foto, utilizza InsightFace (ArcFace 512-dim) per il riconoscimento facciale interamente su CPU.

> **Ruolo in Oracle:** Agente passivo, read-only, multimodale. Legge il grafo di conoscenza di Penelope (MariaDB + ChromaDB), esegue face matching su foto, e presenta i risultati all'utente su richiesta. Non esegue, non modifica, non elimina — solo SELECT.

---

## Indice

1. [Panoramica](#panoramica)
2. [Architettura](#architettura)
3. [Componenti](#componenti)
4. [Identity Resolution](#identity-resolution)
5. [Utilizzo](#utilizzo)
6. [Struttura del Progetto](#struttura-del-progetto)
7. [Modelli Dati](#modelli-dati)
8. [Installazione](#installazione)
9. [Configurazione](#configurazione)
10. [Test](#test)

---

## Panoramica

Archimede è un agente **read-only** che opera sul grafo **Penelope**, il database di memoria di Oracle. Le sue capacità principali:

- **Leggere** il grafo Penelope (MariaDB) — solo query SELECT
- **Interrogare** la ChromaDB di Penelope per embedding immagini
- **Riconoscere volti** con InsightFace ArcFace 512-dim su CPU
- **Trovare foto di coppia** dei genitori in grandi raccolte fotografiche
- **Generare report HTML** navigabili con galleria foto e statistiche
- **Fare clustering interattivo** di volti per identificare persone sconosciute
- **Non scrivere mai** — nessuna operazione di modifica, cancellazione o inserimento

### Principio Fondamentale

```
ARCHIMEDE NON SCRIVE MAI
```

Tutte le query sono strettamente `SELECT` o `WITH`. Qualsiasi tentativo di scrittura viene bloccato a livello di architettura.

---

## Architettura

```
┌──────────────────────────────────────────────────────────────────────┐
│                        ARCHIMEDE QUERY (CLI)                        │
│                     (archimede/query.py)                             │
│   Entry point: python -m archimede.query <comando> [opzioni]        │
└──────┬───────────────────────────────────────────────────────────────┘
       │
       ├── Comando: stats
       │   → Statistiche del grafo Penelope
       │
       └── Comando: find-parents
           → Ricerca foto di coppia dei genitori
              │
              ├── Modalità --ref-dir    (carica referenze da cartella)
              └── Modalità --interactive (clustering interattivo volti)
                    │
                    ▼
       ┌─────────────────────────────────────────────────────┐
       │                  ARCHIMEDE MODULES                   │
       │                                                     │
       │  ┌─────────────────────────────────────────────┐    │
       │  │           GRAPH READER (read-only)           │    │
       │  │  PenelopeGraphReader → MariaDB (SELECT)     │    │
       │  │  PenelopeChromaReader → ChromaDB (query)    │    │
       │  └─────────────────────────────────────────────┘    │
       │                                                     │
       │  ┌─────────────────────────────────────────────┐    │
       │  │           IDENTITY ENGINE                     │    │
       │  │  face_engine.py → InsightFace ArcFace        │    │
       │  │  matcher.py → face matching + couple search  │    │
       │  └─────────────────────────────────────────────┘    │
       │                                                     │
       │  ┌─────────────────────────────────────────────┐    │
       │  │           PRESENTATION LAYER                 │    │
       │  │  report.py → HTML report con galleria foto  │    │
       │  └─────────────────────────────────────────────┘    │
       └─────────────────────────────────────────────────────┘
                              │
                              ▼
       ┌─────────────────────────────────────────────────────┐
       │              PENELOPE (sistema esterno)              │
       │                                                     │
       │  ┌──────────────┐          ┌──────────────────┐     │
       │  │   MariaDB    │          │    ChromaDB       │     │
       │  │ (graph data) │          │ (image embeddings)│     │
       │  └──────────────┘          └──────────────────┘     │
       └─────────────────────────────────────────────────────┘
```

### Flusso Operativo

#### Comando `stats`

```
1. PenelopeGraphReader.connect() → MariaDB
2. Query: COUNT(*) foto indicizzate
3. Query: nodi Person (con/senza InsightFace, YOLO)
4. Query: foto con face_count
5. Stampa statistiche aggregate
```

#### Comando `find-parents` (con `--ref-dir`)

```
1. Carica foto referenza da ref_faces/papa/, ref_faces/mamma/
2. Per ogni referenza: rileva volti con InsightFace → embedding medio
3. Legge foto dal grafo Penelope (tutte o per directory)
4. Per ogni foto:
   a. Rileva volti con InsightFace
   b. Confronta embedding con referenze (similarità coseno)
   c. Se soglia > 0.35 → match
5. Trova foto dove ENTRAMBI i genitori appaiono insieme
6. Genera report HTML con galleria foto
```

#### Comando `find-parents` (con `--interactive`)

```
1. Legge foto con face_count da Penelope
2. Rileva volti e embedding per le prime 200 foto
3. Clustering greedy per similarità coseno (soglia 0.4)
4. Mostra cluster all'utente e chiede: "Chi è? (papa/mamma/salta)"
5. Una volta identificati entrambi → procede con la ricerca
```

---

## Componenti

### Graph Reader (`graph/`)

#### PenelopeGraphReader

Wrapper read-only attorno a `MariaDBStore` di Penelope. Ottiene le credenziali via keyring di sistema (come fa Penelope).

- **Solo SELECT** — qualsiasi query non-SELECT solleva RuntimeError
- **Connessione automatica** — cerca la directory Penelope, carica il suo `.env`, importa `MariaDBStore`
- **Query pubbliche:**
  - `count_photos()` — totale foto indicizzate
  - `get_all_photos(limit, offset)` — tutte le foto con metadati
  - `get_photos_in_directory(directory)` — foto in una directory specifica
  - `get_photos_with_face_count()` — foto con metadati face_count
  - `get_person_nodes(source)` — nodi Person, opzionalmente filtrati per source
  - `get_edges_for_photo(photo_node_id)` — archi entità per una foto
  - `get_persons_in_photo(photo_node_id)` — persone collegate a una foto

#### PenelopeChromaReader

Reader read-only della ChromaDB di Penelope per query su embedding immagini.

- **Solo query** — nessuna scrittura su ChromaDB
- **Cerca automaticamente** la persistenza ChromaDB di Penelope
- **Metodi:**
  - `get_collections()` — collezioni disponibili
  - `query_images(query_embedding, top_k)` — ricerca per similarità
  - `count_images()` — immagini indicizzate

### Identity Engine (`identity/`)

#### Face Engine (`face_engine.py`)

Motore di riconoscimento facciale basato su **InsightFace** (modello `buffalo_l`).

- **ArcFace 512-dim** — embeddings facciali 512-dimensionali
- **CPU-only** — funziona interamente su CPU (i3 10th gen testato)
- **Modello leggero** — buffalo_l: 30MB (detection + recognition + age/gender)
- **Lazy loading** — il modello si carica al primo uso

**Funzioni:**
- `detect_faces(image_path)` → lista volti con bbox, embedding 512-dim, det_score, gender, age, landmark
- `cosine_similarity(a, b)` → similarità coseno tra due embedding
- `verify(emb1, emb2, threshold)` → confronto con soglia (default 0.35)

#### Matcher (`matcher.py`)

Sistema di face matching per cercare foto di persone specifiche in grandi raccolte.

- **Caricamento referenze** da directory strutturate (`papa/`, `mamma/`)
- **Embedding medio** per robustezza (più foto = embedding più stabile)
- **Ricerca foto di coppia** — trova foto con ENTRAMBI i genitori
- **Progress callback** — callback per monitorare l'avanzamento su grandi volumi

### Presentation Layer (`presentation/`)

#### Report Generator (`report.py`)

Genera pagine HTML navigabili con:
- **Statistiche** in cards colorate (foto scansionate, volti, coppie)
- **Galleria foto di coppia** con badge 💑
- **Sezioni per singolo genitore** con foto rimanenti
- **Lightbox** per visualizzazione ingrandita
- **Thumbnail** via OpenCV con data URI (no file temporanei)
- **Design dark** responsivo con gradienti

### Models (`models.py`)

Modelli dati Pydantic-style (dataclass) per Archimede in Oracle:

| Modello | Descrizione |
|---------|-------------|
| **Photo** | Foto indicizzata nel grafo Penelope (node_id, file_path, face_count, metadata) |
| **DetectedFace** | Volto rilevato (bbox, embedding 512-dim, confidence, gender, age) |
| **ReferenceFace** | Volto di referenza (nome, embedding medio, source_photos) |
| **FaceMatch** | Risultato matching (reference_name, similarity, is_match, bbox) |
| **PhotoMatchResult** | Risultato per una foto (faces, matches, is_couple) |
| **SearchReport** | Report completo (couple_photos, single_parent_photos, duration) |

---

## Identity Resolution

### InsightFace ArcFace 512-dim

Archimede utilizza **InsightFace** con il modello `buffalo_l` per il riconoscimento facciale:

| Caratteristica | Valore |
|----------------|--------|
| **Modello** | buffalo_l (30MB) |
| **Embedding** | 512-dimensionale normalizzato |
| **Detection** | RetinaFace-based |
| **Recognition** | ArcFace |
| **Extra** | Age estimation, Gender estimation, Landmarks |
| **Esecuzione** | CPU (CPUExecutionProvider) |
| **Threshold default** | 0.35 (similarità coseno) |

### Soglia di Similarità

| Soglia | Comportamento |
|--------|---------------|
| **0.30** | Più tollerante (più falsi positivi, meno falsi negativi) |
| **0.35** | Default — bilanciato |
| **0.40** | Più severo (meno falsi positivi, più falsi negativi) |
| **0.50** | Molto severo — solo match molto forti |

### Strategia di Matching

1. **Embedding medio** — per ogni persona, si calcola la media degli embedding di tutte le foto di referenza, normalizzata a norma unitaria
2. **Similarità coseno** — confronto tra embedding del volto rilevato e embedding medio della referenza
3. **Match se soglia superata** — se similarità > threshold, la foto contiene quella persona
4. **Foto di coppia** — se TUTTE le referenze hanno almeno un match nella stessa foto

---

## Utilizzo

### Query Engine (CLI principale)

```bash
# Statistiche del grafo Penelope
python -m archimede.query stats

# Ricerca foto di coppia con referenze
python -m archimede.query find-parents --ref-dir ref_faces/

# Con limite di foto
python -m archimede.query find-parents --ref-dir ref_faces/ --limit 200

# In una directory specifica
python -m archimede.query find-parents --ref-dir ref_faces/ --directory "MyPhotos"

# Con soglia personalizzata
python -m archimede.query find-parents --ref-dir ref_faces/ --threshold 0.40

# Modalità interattiva (clustering volti)
python -m archimede.query find-parents --interactive

# Output HTML personalizzato
python -m archimede.query find-parents --ref-dir ref_faces/ --output "results/miei_genitori.html"
```

### Struttura Directory Referenze

```
ref_faces/
├── papa/
│   ├── foto1.jpg
│   ├── foto2.jpg       (opzionale, più foto = embedding più robusto)
│   └── ...
└── mamma/
    ├── foto1.jpg
    ├── foto2.jpg
    └── ...
```

### Modalità Interattiva

Se non hai foto di referenza pronte, Archimede può:
1. Scansionare le prime 200 foto con volti rilevati
2. Clusterizzare i volti simili (greedy clustering, soglia 0.4)
3. Mostrarti i cluster e chiederti "Chi è? (papa/mamma/salta)"
4. Una volta identificati entrambi i genitori → procedere con la ricerca

---

## Struttura del Progetto

```
Oracle/Archimede/
├── pyproject.toml                 # Package Python
├── README.md                      # Questo file
├── .env                           # Configurazione
├── .env.example                   # Template configurazione
├── yolov8n.pt                     # Modello YOLOv8 (legacy)
│
├── archimede/                     # Package principale
│   ├── __init__.py                # Versione 0.2.0 + docstring ruolo
│   ├── query.py                   # CLI entry point + orchestrazione
│   ├── config.py                  # Configurazione
│   ├── models.py                  # Modelli dati (Photo, FaceMatch, etc.)
│   ├── log_setup.py               # Logging strutturato
│   │
│   ├── graph/                     # Lettura grafo Penelope
│   │   ├── reader.py              #   PenelopeGraphReader (MariaDB, SELECT-only)
│   │   └── chroma_reader.py       #   PenelopeChromaReader (ChromaDB, read-only)
│   │
│   ├── identity/                  # Identity resolution
│   │   ├── face_engine.py         #   InsightFace (ArcFace 512-dim, CPU)
│   │   └── matcher.py             #   Face matching + couple search
│   │
│   └── presentation/              # Presentazione risultati
│       └── report.py              #   Generazione report HTML con galleria
│
├── tests/                         # Test suite
│   ├── test_chroma_reader.py      #   Test ChromaDB reader
│   ├── test_face_engine.py        #   Test face detection
│   ├── test_graph_reader.py       #   Test MariaDB reader
│   ├── test_matcher.py            #   Test face matching
│   ├── test_query.py              #   Test CLI query
│   └── test_report.py             #   Test report generation
│
└── data/                          # Dati di runtime
    ├── chroma/                    #   ChromaDB locale
    ├── logs/                      #   Log files
    └── results/                   #   Report HTML generati
```

---

## Modelli Dati

Tutti i dati sono modellati con `dataclass` in `archimede/models.py`:

### Photo
```python
@dataclass
class Photo:
    node_id: str                  # ID nodo nel grafo Penelope
    file_path: str                # Path assoluto nel filesystem
    file_name: str                # Nome file
    mime_type: str                # "image/jpeg", etc.
    size_bytes: int               # Dimensione in byte
    sha256: str                   # Hash SHA-256
    device: str                   # Dispositivo di origine
    date_taken: str               # Data EXIF
    face_count: int               # Numero volti rilevati
    metadata: dict                # Metadati aggiuntivi
```

### DetectedFace
```python
@dataclass
class DetectedFace:
    photo_path: str               # Path foto originale
    photo_node_id: str            # ID nodo foto
    bbox: list[int]               # [x1, y1, x2, y2]
    confidence: float             # Confidence score
    embedding: list[float] | None # 512-dim ArcFace
    gender: int | None            # 0=F, 1=M
    age: float | None             # Età stimata
    person_node_id: str | None    # Nodo Person in Penelope (se esiste)
```

### SearchReport
```python
@dataclass
class SearchReport:
    query_name: str               # Nome della ricerca
    reference_names: list[str]    # ["papa", "mamma"]
    similarity_threshold: float   # Soglia usata (default 0.35)
    photos_scanned: int           # Foto totali scansionate
    photos_with_faces: int        # Foto con almeno un volto
    couple_photos: list           # Foto con entrambi i genitori
    single_parent_photos: dict    # Foto per singolo genitore
    all_results: list             # Tutti i risultati
    duration_seconds: float       # Durata totale
    generated_at: str             # Timestamp generazione
```

---

## Installazione

### 1. Clona il repository

```bash
git clone <url-del-repository>
cd Oracle/Archimede
```

### 2. Crea un ambiente virtuale (consigliato)

```bash
python -m venv .venv
.venv\Scripts\activate   # Windows
source .venv/bin/activate  # Linux/Mac
```

### 3. Installa il package

```bash
# Core (leggero)
pip install -e .

# Con face recognition
pip install -e ".[vision]"

# Con tutte le dipendenze
pip install -e ".[dev]"
```

### 4. Configura l'ambiente

```bash
copy .env.example .env    # Windows
cp .env.example .env      # Linux/Mac
```

Assicurati che il sistema **Penelope** sia configurato e accessibile (Archimede legge il suo `.env` per le credenziali MariaDB).

---

## Configurazione

Archimede legge la configurazione da file `.env`:

```ini
# ── Penelope ──
# Archimede ottiene le credenziali MariaDB dal .env di Penelope
# (cercato automaticamente in ../Penelope/.env o Penelope/.env)

# ── Face Recognition ──
# InsightFace buffalo_l viene scaricato automaticamente al primo uso
# (richiede connessione Internet per il download iniziale)
```

Archimede **non ha una propria configurazione MariaDB** — usa le credenziali di Penelope via keyring di sistema.

---

## Test

```bash
# Esegui tutti i test
pytest tests/ -v

# Test specifici
pytest tests/test_face_engine.py -v
pytest tests/test_matcher.py -v
pytest tests/test_graph_reader.py -v
pytest tests/test_query.py -v
pytest tests/test_report.py -v

# Con output dettagliato
pytest tests/ -v --tb=short
```

---

## Requisiti

### Core
- Python 3.10+
- rich
- python-dotenv

### Vision (face recognition)
- insightface >= 0.7.0
- opencv-python >= 4.8.0
- numpy >= 1.24.0

### ChromaDB (optional)
- chromadb >= 0.4.0

### Dev
- pytest >= 7.0.0

---

## Note Tecniche

- **Hardware testato**: i3 10th gen, 8GB RAM, GPU integrata — InsightFace gira interamente su CPU
- **InsightFace buffalo_l**: ~30MB, si scarica automaticamente al primo `detect_faces()`
- **Threshold default 0.35**: bilanciato per ArcFace 512-dim su foto reali
- **Penelope**: Archimede presuppone che Penelope sia già configurato e abbia scansito le foto
- **Read-only**: garantito dal PenelopeGraphReader che blocca qualsiasi query non-SELECT
- **Connessione**: le credenziali MariaDB vengono lette dal `.env` di Penelope via keyring

---

## Note sulla Serie

A differenza del sistema Samaritan in *Person of Interest* (un'ASI di sorveglianza e controllo), Archimede:
- ✅ È **read-only** — non esegue azioni, non manipola, non elimina
- ✅ Ha l'**etica operativa di The Machine** — protezione, non controllo
- ✅ È **trasparente** — ogni operazione è loggata e ispezionabile
- ✅ È **locale** — opera solo sui dati già presenti in Penelope
- ✅ È **passivo** — risponde a richieste, non agisce autonomamente

*"Non esegue, non modifica, non elimina nulla. Solo lettura e presentazione all'utente su richiesta."*

---

*Archimede v0.2.0 — Agente Passivo di Identity Resolution per Oracle*
