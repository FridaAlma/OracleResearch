# Oracle RUI Edition — Guida di Setup

Questa guida ti accompagna nell'installazione e configurazione completa di Oracle RUI Edition.

---

## 1. Prerequisiti

### Software richiesto

| Software | Versione minima | Note |
|----------|----------------|------|
| Python | 3.11+ | [python.org](https://python.org) |
| Git | qualsiasi | [git-scm.com](https://git-scm.com) |
| Docker | 24+ | Opzionale, per MariaDB |

### Python packages

I requirements sono divisi per layer:

```bash
# Oracle Core (sempre necessario)
pip install -r oracle-rui/requirements.txt

# Penelope (se usi il grafo)
pip install -r penelope/requirements.txt

# Archimede (se usi face recognition)
pip install -r archimede/requirements.txt
```

### Modelli NLP (scaricati automaticamente al primo uso)

- SpaCy: `it_core_news_sm` (~15 MB)
- Sentence Transformers: `all-MiniLM-L6-v2` (~90 MB)
- InsightFace: `buffalo_l` (~300 MB) — per face recognition
- YOLOv8n: `yolov8n.pt` (~6 MB) — gia' incluso

---

## 2. Installazione

### 2.1 Clona il repository

```bash
git clone <repo-url> oracle-rui-edition
cd oracle-rui-edition
```

### 2.2 Crea ambiente virtuale

**Windows:**
```bash
python -m venv venv
venv\Scripts\activate
```

**Linux/macOS:**
```bash
python -m venv venv
source venv/bin/activate
```

### 2.3 Installa dipendenze

```bash
pip install -r oracle-rui/requirements.txt
```

### 2.4 Setup guidato

```bash
python run.py --init
```

Questo comando:
- Crea i file `.env` dai template `.env.example`
- Crea le directory necessarie (`logs/`, `data/`)

---

## 3. Configurazione Oracle Core

Modifica `oracle-rui/.env`:

```ini
# ─── LLM API Key ────────────────────────────────────────────
# Almeno UNA chiave e' necessaria. Scegli il tuo provider:

# OpenAI
OPENAI_API_KEY=sk-...

# DeepSeek (consigliato per rapporto qualita'/prezzo)
DEEPSEEK_API_KEY=sk-...

# Anthropic (Claude)
ANTHROPIC_API_KEY=sk-...

# Ollama (locale, gratuito)
# Non serve API key, avvia ollama localmente

# ─── Provider default ───────────────────────────────────────
ORACLE_DEFAULT_PROVIDER=deepseek
ORACLE_DEFAULT_MODEL=deepseek-chat

# ─── Sicurezza ──────────────────────────────────────────────
JWT_SECRET_KEY=una-stringa-casuale-lunga-almeno-32-caratteri
```

---

## 4. Configurazione Penelope (Grafo della conoscenza)

Penelope richiede un database SQL per memorizzare il grafo. Hai due opzioni.

### Opzione A: Docker MariaDB (consigliata)

```bash
# Avvia MariaDB in container
docker-compose up -d

# Verifica che funzioni
docker exec oracle-rui-mariadb mariadb-admin ping -h localhost
```

Il database e' gia' inizializzato con lo schema corretto (`schema.sql` viene eseguito automaticamente all'avvio).

### Opzione B: SQLite (zero setup, single-user)

Modifica `penelope/.env`:
```ini
PENELOPE_DB_BACKEND=sqlite
PENELOPE_SQLITE_PATH=data/penelope.db
```

Nessun server richiesto. Il database viene creato automaticamente nella directory `data/`.

### Configura storage paths

Modifica `penelope/.env` per indicare quali directory scansionare:

```ini
# Fino a 5 dispositivi o cartelle
PENELOPE_STORAGE_1=C:/Users/tuono/Documenti
PENELOPE_STORAGE_2=D:/Archivio
PENELOPE_STORAGE_3=E:/Foto
PENELOPE_STORAGE_4=
PENELOPE_STORAGE_5=
```

Lascia vuoti i dispositivi che non usi.

---

## 5. Configurazione Archimede (Face Recognition)

Archimede legge il grafo di Penelope e aggiunge capacita' di face recognition.

### Prerequisiti

```bash
pip install -r archimede/requirements.txt
```

Il modello InsightFace (`buffalo_l`) viene scaricato automaticamente al primo utilizzo (~300 MB).

### Configurazione

Modifica `archimede/.env`:

```ini
# API key per il reasoning core (LLM)
ARCHIMEDE_API_KEY=sk-...

# Path di Penelope (default: ../penelope)
ARCHIMEDE_PENELOPE_PATH=../penelope

# ChromaDB path
ARCHIMEDE_CHROMA_PATH=data/chroma
```

---

## 6. Avvio

### Sistema completo

```bash
python run.py --all
```

Questo avvia:
- **Oracle Core** su [http://localhost:8100](http://localhost:8100)
- **Penelope** su [http://localhost:5000](http://localhost:5000)
- **Archimede** su [http://localhost:8001](http://localhost:8001)

### Solo Oracle Core

```bash
python run.py
```

Utile se non hai ancora configurato Penelope.

### Combinazioni

```bash
python run.py --with-penelope          # Oracle + Penelope
python run.py --with-archimede         # Oracle + Archimede
python run.py --port 9000              # Porta personalizzata
```

---

## 7. Primo utilizzo

### 7.1 Scansione storage (Penelope)

```bash
# Scansiona tutti i path configurati
python -m penelope.cli scan:all

# Avvia elaborazione lazy (in background)
python -m penelope.cli queue loop
```

Il processor esegue automaticamente:
1. Estrazione metadati (EXIF, data, dimensione)
2. Embedding semantico (testo → ChromaDB)
3. NER (riconoscimento entita' nominate)
4. Face detection (YOLOv8n)
5. Scene detection (video → keyframe)

### 7.2 Esplora il grafo

```bash
# Statistiche
python -m archimede.query stats

# Accedi alla web UI di Penelope
# http://localhost:5000
```

### 7.3 Face recognition

1. Crea una directory con foto di referenza:
   ```
   ref_faces/
       persona_1/
           foto1.jpg
           foto2.jpg
       persona_2/
           foto1.jpg
   ```

2. Esegui la ricerca:
   ```bash
   python -m archimede.query find-parents --ref-dir ref_faces/
   ```

3. Oppure in modalita' interattiva (scopre automaticamente cluster di volti):
   ```bash
   python -m archimede.query find-parents --interactive
   ```

---

## 8. Verifica stato

```bash
python run.py --status
```

Output di esempio:
```
+----------------------------------------------------+
|  Oracle RUI Edition — Diagnostica                  |
+----------------------------------------------------+
|  Oracle       [OK]  :8100 (v0.5.0)
|  Penelope     [OK]  :5000 (1247 nodi)
|  Archimede    [OK]  :8001 (grafo read-only)
+----------------------------------------------------+
```

---

## 9. Troubleshooting

### "Impossibile connettersi a MariaDB"

- Verifica che Docker sia in esecuzione: `docker ps`
- Se usi Docker: `docker-compose up -d`
- Se usi SQLite: verifica `PENELOPE_DB_BACKEND=sqlite` in `penelope/.env`

### "Modello SpaCy non trovato"

```bash
python -m spacy download it_core_news_sm
```

### "ERRORE: API key non configurata"

Verifica che `oracle-rui/.env` contenga almeno una API key valida.

### "face_engine: impossibile caricare InsightFace"

Al primo avvio InsightFace scarica automaticamente il modello `buffalo_l`.
Assicurati di avere connessione internet e almeno 300 MB di spazio libero.

---

## 10. Disinstallazione

```bash
# Arresta Docker
docker-compose down -v

# Rimuovi ambiente virtuale
deactivate
rm -rf venv/

# Rimuovi dati generati
rm -rf oracle-rui/data/
rm -rf penelope/data/
rm -rf archimede/data/
rm -rf logs/
```

---

## Supporto

Per problemi o domande, apri una issue sul repository del progetto.