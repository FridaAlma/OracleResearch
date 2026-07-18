#!/usr/bin/env python3
"""Archimede Query — CLI per la ricerca nel grafo Penelope.

Entry point principale di Archimede in Oracle.
Legge, naviga e presenta dati dal grafo — non scrive mai.

Usage:
    python -m archimede.query find-parents --ref-dir ref_faces/
    python -m archimede.query find-parents --ref-dir ref_faces/ --limit 200
    python -m archimede.query find-parents --interactive
    python -m archimede.query stats
"""

from __future__ import annotations

import argparse
import logging
import sys
from pathlib import Path

# Il path Oracle/ e Archimede/ vengono aggiunti da archimede/__init__.py
# Mantenuto qui per retrocompatibilita' con esecuzione diretta del modulo
_ORACLE_ROOT_DIR = Path(__file__).resolve().parent.parent.parent
_ARCHIMEDE_ROOT = _ORACLE_ROOT_DIR / "archimede"
for _p in (str(_ORACLE_ROOT_DIR), str(_ARCHIMEDE_ROOT)):
    if _p not in sys.path:
        sys.path.insert(0, str(_p))

from archimede.graph.reader import PenelopeGraphReader
from archimede.models import Photo
from archimede.identity.matcher import load_reference_faces, search_couple_photos
from archimede.presentation.report import generate_report

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(message)s",
)
logger = logging.getLogger("archimede.query")


def cmd_stats(args: argparse.Namespace) -> None:
    """Mostra statistiche del grafo Penelope."""
    print("\n[STATS] GRAFO PENELOPE\n")
    reader = PenelopeGraphReader()
    try:
        total = reader.count_photos()
        print(f"  Foto totali indicizzate:    {total}")

        # Foto per directory
        rows = reader._query(
            "SELECT path FROM file_registry WHERE path LIKE %s OR path LIKE %s OR path LIKE %s",
            ("%.jpg", "%.jpeg", "%.png"),
        )
        roots = {}
        for r in rows:
            parts = r["path"].split("\\")
            root = "\\".join(parts[:3]) if len(parts) >= 3 else r["path"]
            roots[root] = roots.get(root, 0) + 1

        print(f"\n  Foto per directory:")
        for root, cnt in sorted(roots.items(), key=lambda x: -x[1])[:10]:
            print(f"    {root}: {cnt}")

        # Nodi Person
        persons = reader.get_person_nodes()
        insightface = [p for p in persons if "insightface" in (p.get("metadata") or "")]
        yolo = [p for p in persons if "face_detection" in (p.get("metadata") or "")]
        print(f"\n  Nodi Person totali:       {len(persons)}")
        print(f"    con InsightFace:        {len(insightface)}")
        print(f"    con YOLO:               {len(yolo)}")

        # File con face_count
        photos_with_faces = reader.get_photos_with_face_count()
        print(f"\n  Foto con volti rilevati:  {len(photos_with_faces)}")

    finally:
        reader.close()


def cmd_find_parents(args: argparse.Namespace) -> None:
    """Trova foto di coppia dei genitori."""
    print("\n" + "=" * 60)
    print("  [PROMETEO] Ricerca foto di coppia dei genitori")
    print("  " + "=" * 56)
    print()

    # Fase 1: Carica referenze
    if args.interactive:
        references = _interactive_choose_references()
    elif args.ref_dir:
        ref_dir = Path(args.ref_dir)
        if not ref_dir.exists():
            print(f"[ERR] Directory referenza non trovata: {ref_dir}")
            return
        print(f"[REF] Carico foto di referenza da: {ref_dir}")
        references = load_reference_faces(str(ref_dir))
    else:
        print("[ERR] Specifica --ref-dir o --interactive")
        return

    if not references:
        print("\n[ERR] Nessuna referenza valida caricata.")
        print("   Prepara una cartella con:")
        print("     ref_faces/papa/   (foto del papa)")
        print("     ref_faces/mamma/  (foto della mamma)")
        return

    ref_names = [r.name for r in references]
    print(f"\n[OK] Referenze caricate: {', '.join(ref_names)}")
    for r in references:
        print(f"   {r.name}: {len(r.source_photos)} foto, embedding 512-dim")

    threshold = args.threshold
    print(f"   Soglia similarita: {threshold}")

    # Fase 2: Carica foto dal grafo
    print(f"\n[READ] Leggo il grafo Penelope...")
    reader = PenelopeGraphReader()
    try:
        if args.directory:
            raw_photos = reader.get_photos_in_directory(args.directory)
        else:
            raw_photos = reader.get_all_photos(limit=args.limit)

        if not raw_photos:
            print("[ERR] Nessuna foto trovata nel database.")
            print("   Esegui prima: python -m penelope.cli scan <path>")
            return

        print(f"   Trovate {len(raw_photos)} foto")

        # Converti in oggetti Photo
        photos = []
        for r in raw_photos:
            meta = r.get("node_metadata") or {}
            if isinstance(meta, str):
                import json
                try:
                    meta = json.loads(meta)
                except Exception:
                    meta = {}

            photos.append(Photo(
                node_id=r.get("node_id", ""),
                file_path=r.get("path", ""),
                file_name=Path(r.get("path", "")).name,
                mime_type=r.get("mime_type", ""),
                size_bytes=r.get("size_bytes", 0) or 0,
                sha256=r.get("sha256", ""),
                device=r.get("device", ""),
                face_count=meta.get("face_count", 0) if isinstance(meta, dict) else 0,
                metadata=meta if isinstance(meta, dict) else {},
            ))

    finally:
        reader.close()

    # Fase 3: Esegui matching
    print(f"\n[MATCH] Avvio face matching su {len(photos)} foto...")
    print(f"   (questo potrebbe richiedere alcuni minuti)\n")

    def progress(i, total, couple_count):
        bar_len = 30
        filled = int(bar_len * i / total)
        bar = "#" * filled + "." * (bar_len - filled)
        print(f"  [{bar}] {i}/{total}  coppie: {couple_count}", end="\r")
        if i == total:
            print()

    report = search_couple_photos(
        photos, references,
        threshold=threshold,
        batch_callback=progress,
    )

    # Fase 4: Genera report
    safe_time = report.generated_at.replace(":", "-").replace(" ", "_")
    output_file = args.output or f"data/results/parents_photos_{safe_time}.html"
    html_path = generate_report(report, output_file)

    # Fase 5: Mostra riepilogo
    print()
    print("=" * 60)
    print(f"  [RISULTATI]")
    print(f"  {'=' * 56}")
    print(f"  Foto scansionate:          {report.photos_scanned}")
    print(f"  Foto con volti:            {report.photos_with_faces}")
    for name in ref_names:
        count = sum(1 for r in report.all_results if r.matches.get(name))
        print(f"  Foto con {name}:            {count}")
    print(f"  Foto di coppia:            {report.couple_count}")
    print(f"  Tempo:                     {report.duration_seconds:.1f}s")
    print(f"  {'=' * 56}")

    if report.couple_count > 0:
        print(f"\n  [FOUND] Buon anniversario! Trovate {report.couple_count} foto di coppia!\n")
        for r in report.couple_photos[:10]:
            print(f"    [PHOTO] {r.photo.file_name}")

    print(f"\n  [REPORT] HTML: {html_path}")
    print()


def _interactive_choose_references() -> list:
    """Modalita interattiva: mostra cluster di volti e chiede di identificarli."""
    from archimede.graph.reader import PenelopeGraphReader
    from archimede.identity.face_engine import detect_faces, cosine_similarity
    import numpy as np

    print("\n[CLUSTER] Modalita interattiva: trovo cluster di volti simili...")

    reader = PenelopeGraphReader()
    try:
        # Prendi foto con face_count
        photos = reader.get_photos_with_face_count()
        print(f"   Trovate {len(photos)} foto con volti rilevati")

        if not photos:
            print("[ERR] Nessuna foto con volti. Processa prima le foto con InsightFace.")
            return []

        # Per ogni foto, rileva volti e embedding
        all_faces = []  # [(embedding, photo_path, bbox)]
        for i, p in enumerate(photos[:200]):  # max 200 per velocita
            path = p.get("path", "")
            if not path:
                continue
            faces = detect_faces(path)
            for f in faces:
                if f.get("embedding"):
                    all_faces.append((
                        np.array(f["embedding"], dtype=np.float32),
                        path,
                        f.get("bbox"),
                    ))
            if (i + 1) % 50 == 0:
                print(f"   Processate {i+1}/{min(len(photos), 200)} foto...")

    finally:
        reader.close()

    if not all_faces:
        print("[ERR] Nessun embedding facciale trovato.")
        return []

    print(f"\n   Rilevati {len(all_faces)} volti totali")

    # Clustering greedy semplice
    threshold = 0.4
    clusters = []
    assigned = set()

    for i, (emb_i, path_i, bbox_i) in enumerate(all_faces):
        if i in assigned:
            continue
        cluster = [i]
        assigned.add(i)
        for j in range(i + 1, len(all_faces)):
            if j in assigned:
                continue
            emb_j = all_faces[j][0]
            sim = cosine_similarity(emb_i, emb_j)
            if sim > threshold:
                cluster.append(j)
                assigned.add(j)
        if len(cluster) >= 2:  # almeno 2 occorrenze
            clusters.append(cluster)

    # Ordina per dimensione decrescente
    clusters.sort(key=len, reverse=True)

    print(f"   Trovati {len(clusters)} cluster di volti simili\n")

    references = []
    for idx, cluster in enumerate(clusters[:10]):  # max 10 cluster
        sample_paths = []
        for ci in cluster[:3]:
            path = all_faces[ci][1]
            sample_paths.append(path)

        print(f"  [Cluster {idx+1}] ({len(cluster)} occorrenze)")
        for sp in sample_paths:
            print(f"       {sp}")

        # Media embedding del cluster
        embs = [all_faces[ci][0] for ci in cluster]
        avg_emb = np.mean(embs, axis=0)
        avg_emb = avg_emb / (np.linalg.norm(avg_emb) + 1e-10)

        ans = input(f"  Chi e? (papa/mamma/salta) [invio=salta]: ").strip().lower()
        if ans in ("papa", "papa", "padre"):
            references.append(type('Ref', (), {'name': 'papa', 'embedding': avg_emb.tolist(), 'source_photos': sample_paths})())
            print("  [OK] Salvato come PAPA\n")
        elif ans in ("mamma", "madre"):
            references.append(type('Ref', (), {'name': 'mamma', 'embedding': avg_emb.tolist(), 'source_photos': sample_paths})())
            print("  [OK] Salvato come MAMMA\n")
        else:
            print("  [SKIP] Saltato\n")

        if len(references) == 2:
            print("[OK] Ho entrambi i genitori! Procedo con la ricerca...")
            break

    return references


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Archimede — Query engine per il grafo Penelope"
    )
    sub = parser.add_subparsers(dest="command", required=True)

    # Comando: stats
    p_stats = sub.add_parser("stats", help="Statistiche del grafo")

    # Comando: find-parents
    p_find = sub.add_parser("find-parents", help="Trova foto di coppia dei genitori")
    p_find.add_argument("--ref-dir", type=str, default=None,
                        help="Directory con foto di referenza (sottocartelle papa/, mamma/)")
    p_find.add_argument("--interactive", action="store_true",
                        help="Modalita interattiva: identifica i genitori tra cluster di volti")
    p_find.add_argument("--limit", type=int, default=0,
                        help="Numero massimo foto da scandire (0=tutte)")
    p_find.add_argument("--directory", type=str, default=None,
                        help="Filtra per directory (es. 'my_photos')")
    p_find.add_argument("--threshold", type=float, default=0.35,
                        help="Soglia similarita coseno (0.3-0.5, default: 0.35)")
    p_find.add_argument("--output", type=str, default=None,
                        help="Path file HTML output")

    args = parser.parse_args()

    if args.command == "stats":
        cmd_stats(args)
    elif args.command == "find-parents":
        cmd_find_parents(args)


if __name__ == "__main__":
    main()
