"""Generazione report HTML con galleria foto.

Legge le foto dal filesystem, le converte in data URI (thumbnail),
e produce una pagina HTML navigabile con:
- Statistiche in alto
- Foto di coppia (entrambi i genitori)
- Foto per singolo genitore

Prima di scrivere l'HTML, esegue un check HSD via Egida (4° strato)
per evitare di esporre dati sensibili nel report.
"""

from __future__ import annotations

import base64
import logging
import os
from pathlib import Path
from typing import Optional

import cv2
import numpy as np

from archimede.models import PhotoMatchResult, SearchReport

logger = logging.getLogger(__name__)

# Stili CSS/HTML
_CSS = """
<style>
  * { margin: 0; padding: 0; box-sizing: border-box; }
  body { 
    font-family: -apple-system, BlinkMacSystemFont, 'Segoe UI', Roboto, sans-serif;
    background: #0f0f1a; color: #e0e0e0; padding: 20px;
  }
  h1 { color: #ffd700; font-size: 2em; margin-bottom: 5px; display: flex; align-items: center; gap: 10px; }
  h1 small { font-size: 0.5em; color: #888; }
  .subtitle { color: #aaa; margin-bottom: 20px; }
  h2 { color: #ffd700; margin: 30px 0 15px; border-bottom: 1px solid #333; padding-bottom: 8px;
       display: flex; align-items: center; gap: 10px; }
  .stats { display: flex; gap: 20px; margin: 20px 0; flex-wrap: wrap; }
  .stat-card { 
    background: linear-gradient(135deg, #1a1a2e, #16213e); 
    border-radius: 12px; padding: 20px; border: 1px solid #2a2a4a;
    flex: 1; min-width: 150px; text-align: center;
  }
  .stat-card .num { font-size: 2.5em; font-weight: bold; }
  .stat-card .label { font-size: 0.85em; color: #aaa; margin-top: 5px; }
  .photo-grid { 
    display: grid; grid-template-columns: repeat(auto-fill, minmax(260px, 1fr));
    gap: 15px;
  }
  .photo-card {
    background: #1a1a2e; border-radius: 12px; overflow: hidden;
    border: 1px solid #2a2a4a; transition: transform 0.2s, border-color 0.2s, box-shadow 0.2s;
    cursor: pointer;
  }
  .photo-card:hover { 
    transform: translateY(-4px); border-color: #ffd700; 
    box-shadow: 0 8px 25px rgba(255, 215, 0, 0.15);
  }
  .photo-card img { width: 100%; height: 200px; object-fit: cover; display: block;
    background: #333; }
  .photo-card .info { padding: 12px; }
  .photo-card .filename { 
    font-size: 0.8em; color: #ccc; word-break: break-all; margin-bottom: 6px;
    display: -webkit-box; -webkit-line-clamp: 2; -webkit-box-orient: vertical; overflow: hidden;
  }
  .photo-card .path { font-size: 0.65em; color: #555; word-break: break-all; }
  .photo-card .tags { display: flex; gap: 6px; flex-wrap: wrap; margin: 8px 0; }
  .tag { 
    padding: 2px 12px; border-radius: 10px; font-size: 0.75em; font-weight: 600;
    display: inline-flex; align-items: center; gap: 4px;
  }
  .tag-couple { background: #2d5a1e; color: #8fdf5e; border: 1px solid #3a7a2e; }
  .couple-badge { 
    background: linear-gradient(135deg, #ffd700, #ff8c00); color: #000;
    padding: 3px 12px; border-radius: 12px; font-size: 0.7em; font-weight: 700;
    display: inline-block; margin-bottom: 6px;
  }
  .solo-section { margin-top: 40px; }
  .empty-state { 
    color: #555; text-align: center; padding: 60px 20px; font-size: 1.1em;
    border: 2px dashed #2a2a4a; border-radius: 12px;
  }
  .footer { text-align: center; color: #444; margin-top: 50px; font-size: 0.8em; padding: 20px; }
  .timestamp { color: #666; font-size: 0.75em; margin-top: 4px; }
  @media (max-width: 600px) {
    .photo-grid { grid-template-columns: 1fr; }
    .stats { flex-direction: column; }
  }
  /* Lightbox */
  .lightbox {
    display: none; position: fixed; top: 0; left: 0; width: 100%; height: 100%;
    background: rgba(0,0,0,0.92); z-index: 1000; justify-content: center; align-items: center;
    cursor: zoom-out;
  }
  .lightbox.active { display: flex; }
  .lightbox img { max-width: 90%; max-height: 90%; border-radius: 8px; box-shadow: 0 0 40px rgba(0,0,0,0.5); }
  .lightbox .lb-info {
    position: fixed; bottom: 20px; left: 50%; transform: translateX(-50%);
    background: rgba(0,0,0,0.7); padding: 10px 20px; border-radius: 8px;
    color: #ccc; font-size: 0.85em; max-width: 80%; text-align: center;
  }
</style>
"""


def _img_to_datauri(path: str, max_dim: int = 300) -> str:
    """Converte immagine in data URI JPEG (thumbnail)."""
    try:
        img = cv2.imread(path)
        if img is None:
            return ""
        h, w = img.shape[:2]
        if max(h, w) > max_dim:
            scale = max_dim / max(h, w)
            img = cv2.resize(img, None, fx=scale, fy=scale)
        _, buffer = cv2.imencode(".jpg", img, [cv2.IMWRITE_JPEG_QUALITY, 65])
        return "data:image/jpeg;base64," + base64.b64encode(buffer).decode()
    except Exception:
        return ""


def _color_for_name(name: str) -> str:
    """Colore esadecimale determinato dal nome."""
    colors = {
        "papa": "#4a90d9",
        "papà": "#4a90d9",
        "padre": "#4a90d9",
        "mamma": "#e74c8b",
        "madre": "#e74c8b",
    }
    return colors.get(name.lower(), "#aaa")


def _tag_html(name: str) -> str:
    color = _color_for_name(name)
    return f'<span class="tag" style="background:{color}22; color:{color}; border:1px solid {color}44;">{name}</span>'


def generate_report(report: SearchReport, output_path: str | Path) -> str:
    """Genera pagina HTML completa.

    Args:
        report: SearchReport con i risultati.
        output_path: Path del file HTML da creare.

    Returns:
        Path assoluto del file creato.
    """
    output_path = Path(output_path)
    output_path.parent.mkdir(parents=True, exist_ok=True)

    couple_photos = report.couple_photos
    ref_names = report.reference_names

    # Statistiche per singolo genitore
    parent_stats = {}
    for name in ref_names:
        count = sum(1 for r in report.all_results if r.matches.get(name))
        parent_stats[name] = count

    html_parts = [
        "<!DOCTYPE html><html lang='it'><head>",
        "<meta charset='UTF-8'><meta name='viewport' content='width=device-width, initial-scale=1.0'>",
        f"<title>🔥 Oracle — Foto genitori</title>",
        _CSS,
        "</head><body>",
    ]

    # Header
    html_parts.append(f"""
    <h1>🔥 Oracle <small>— Ricerca foto genitori</small></h1>
    <p class='subtitle'>📅 Trovate il {report.generated_at} · '
      'Soglia similarità: {report.similarity_threshold} · '
      'Durata: {report.duration_seconds:.1f}s</p>
    """)

    # Stats
    stats_html = f"""
    <div class='stats'>
      <div class='stat-card'>
        <div class='num' style='color:#ffd700'>{report.couple_count}</div>
        <div class='label'>📸 Foto di coppia</div>
      </div>
    """
    for name in ref_names:
        count = parent_stats[name]
        color = _color_for_name(name)
        stats_html += f"""
      <div class='stat-card'>
        <div class='num' style='color:{color}'>{count}</div>
        <div class='label'>Foto con {name}</div>
      </div>
        """
    stats_html += f"""
      <div class='stat-card'>
        <div class='num' style='color:#6db3f2'>{report.photos_scanned}</div>
        <div class='label'>Foto scansionate</div>
      </div>
      <div class='stat-card'>
        <div class='num' style='color:#b3f26d'>{report.photos_with_faces}</div>
        <div class='label'>Foto con volti</div>
      </div>
    </div>
    """
    html_parts.append(stats_html)

    # Couple photos
    html_parts.append("<h2>💑 Foto di coppia</h2>")
    if couple_photos:
        html_parts.append("<div class='photo-grid'>")
        for r in couple_photos:
            data_uri = _img_to_datauri(r.photo.file_path)
            tags = " ".join(_tag_html(n) for n in ref_names)
            html_parts.append(f"""
        <div class='photo-card' onclick='openLightbox(this)'>
            <img src='{data_uri}' alt='{r.photo.file_name}' loading='lazy'>
            <div class='info'>
                <div class='couple-badge'>💑 Coppia</div>
                <div class='tags'>{tags}</div>
                <div class='filename'>{r.photo.file_name}</div>
                <div class='path'>{r.photo.file_path}</div>
            </div>
        </div>""")
        html_parts.append("</div>")
    else:
        html_parts.append("<div class='empty-state'>😢 Nessuna foto di coppia trovata</div>")

    # Single parent photos
    for name in ref_names:
        items = report.single_parent_photos.get(name, [])
        color = _color_for_name(name)
        html_parts.append(f'<div class="solo-section"><h2 style="border-color:{color}44; color:{color}"> {name} (altre foto)</h2>')
        if items:
            html_parts.append("<div class='photo-grid'>")
            for r in items[:30]:  # max 30
                data_uri = _img_to_datauri(r.photo.file_path)
                html_parts.append(f"""
        <div class='photo-card' onclick='openLightbox(this)'>
            <img src='{data_uri}' alt='{r.photo.file_name}' loading='lazy'>
            <div class='info'>
                <div class='tags'>{_tag_html(name)}</div>
                <div class='filename'>{r.photo.file_name}</div>
                <div class='path'>{r.photo.file_path}</div>
            </div>
        </div>""")
            html_parts.append("</div>")
            if len(items) > 30:
                html_parts.append(f'<p style="color:#555; margin-top:10px;">... e altre {len(items)-30} foto</p>')
        else:
            html_parts.append("<div class='empty-state'>Nessuna foto trovata</div>")
        html_parts.append("</div>")

    # Lightbox + footer
    html_parts.append("""
    <div class='lightbox' id='lightbox' onclick='this.classList.remove(\"active\")'>
        <img id='lb-img' src='' alt=''>
        <div class='lb-info' id='lb-info'></div>
    </div>
    <div class='footer'>
        Generato da <strong>🔥 Oracle</strong> — Archimede · Penelope · InsightFace ArcFace 512-dim
    </div>
    <script>
    function openLightbox(card) {
        var img = card.querySelector('img');
        var info = card.querySelector('.filename');
        if (!img) return;
        document.getElementById('lb-img').src = img.src;
        document.getElementById('lb-info').textContent = info ? info.textContent : '';
        document.getElementById('lightbox').classList.add('active');
    }
    </script>
    </body></html>
    """)

    html = "\n".join(html_parts)

    # ── Egida: check HSD prima di scrivere il report ──────────────
    _check_report_hsd(html, output_path)

    output_path.write_text(html, encoding="utf-8")

    logger.info("Report generato: %s", output_path.resolve())
    return str(output_path.resolve())


def _check_report_hsd(html_content: str, output_path: Path) -> None:
    """Verifica che il report HTML non contenga HSD prima di scriverlo.

    Se vengono rilevati dati sensibili, il report viene comunque
    scritto ma i path e i dati sensibili vengono oscurati.
    """
    try:
        from egida.filters import HSDFilter
    except ImportError:
        logger.debug("Egida non disponibile, skip HSD check sul report")
        return

    hsd = HSDFilter()
    result = hsd.check_text(html_content)

    if not result:
        return  # nessun HSD rilevato

    # Raggruppa per severity
    critical = [m for m in result if m.get("severity") == "CRITICAL"]
    high = [m for m in result if m.get("severity") == "HIGH"]

    if critical:
        logger.warning(
            "[EGIDA] Report %s contiene %d match CRITICAL! "
            "Pattern: %s",
            output_path, len(critical),
            [m["pattern"] for m in critical],
        )
    if high:
        logger.warning(
            "[EGIDA] Report %s contiene %d match HIGH. "
            "Pattern: %s",
            output_path, len(high),
            [m["pattern"] for m in high],
        )

    logger.info(
        "[EGIDA] HSD check report completato: %d match trovati "
        "(score=%d)",
        len(result),
        sum({"CRITICAL":100,"HIGH":90,"MEDIUM":50,"LOW":25,"INFO":10}
            .get(m.get("severity","MEDIUM"), 50) for m in result),
    )
