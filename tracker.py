#!/usr/bin/env python3
"""
Monitor de pronósticos políticos.

Descarga probabilidades de Polymarket, Kalshi, Manifold y (opcional) Metaculus,
las filtra por los temas de config.yaml, guarda un historial en data/ y publica
una página en docs/index.html, más un feed RSS (docs/feed.xml) y un JSON
(docs/data.json).

Uso:
    python tracker.py           # corrida real
    python tracker.py --demo    # genera demo/index.html con datos ficticios
"""
from __future__ import annotations

import argparse
import csv
import difflib
import html
import json
import math
import os
import random
import re
import shutil
import sys
import time
import unicodedata
from datetime import datetime, timedelta, timezone
from pathlib import Path

import yaml

try:
    import requests
except ImportError:  # el modo demo no lo necesita
    requests = None

BASE = Path(__file__).resolve().parent
UA = {"User-Agent": "monitor-pronosticos/1.0"}
MESES = ["ene", "feb", "mar", "abr", "may", "jun", "jul", "ago", "sep", "oct", "nov", "dic"]
NOMBRES = {"polymarket": "Polymarket", "kalshi": "Kalshi", "manifold": "Manifold",
           "metaculus": "Metaculus", "consolidado": "Consolidado"}


# ============================================================ utilidades

def ahora() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def a_iso(dt: datetime) -> str:
    return dt.astimezone(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def de_iso(s):
    if not s:
        return None
    try:
        dt = datetime.fromisoformat(str(s).replace("Z", "+00:00"))
        return dt if dt.tzinfo else dt.replace(tzinfo=timezone.utc)
    except ValueError:
        return None


def a_float(x):
    try:
        return None if x in (None, "") else float(x)
    except (TypeError, ValueError):
        return None


def normalizar(s: str) -> str:
    s = unicodedata.normalize("NFKD", str(s))
    return "".join(c for c in s if not unicodedata.combining(c)).lower()


def slug(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "-", normalizar(s)).strip("-")[:60]


def num_es(x: float, dec: int = 0) -> str:
    s = f"{x:,.{dec}f}"
    return s.replace(",", "X").replace(".", ",").replace("X", ".")


def get_json(url, params=None, headers=None, intentos=3):
    if requests is None:
        raise RuntimeError("Falta la librería requests (pip install requests)")
    ultimo = None
    for i in range(intentos):
        try:
            r = requests.get(url, params=params, headers={**UA, **(headers or {})}, timeout=30)
            if r.status_code == 429 or r.status_code >= 500:
                ultimo = RuntimeError(f"HTTP {r.status_code} en {url}")
                time.sleep(2 * (i + 1))
                continue
            r.raise_for_status()
            return r.json()
        except requests.RequestException as e:
            ultimo = e
            time.sleep(1 + i)
    raise ultimo


def _lista_json(v):
    if isinstance(v, list):
        return v
    if isinstance(v, str):
        try:
            x = json.loads(v)
            return x if isinstance(x, list) else []
        except json.JSONDecodeError:
            return []
    return []


# ============================================================ Polymarket

def _poly_registro(m, ev):
    precios = [a_float(p) for p in _lista_json(m.get("outcomePrices"))]
    if not precios or precios[0] is None:
        return None
    resultados = _lista_json(m.get("outcomes"))
    ev = ev or {}
    titulo_ev = ev.get("title") or ""
    sub = m.get("groupItemTitle") or ""
    if len(ev.get("markets") or []) > 1 and sub:
        titulo = f"{titulo_ev}: {sub}"
    else:
        titulo = m.get("question") or titulo_ev
    # Si no es un mercado Sí/No, se registra la probabilidad del primer resultado
    if resultados and normalizar(resultados[0]) not in ("yes", "si"):
        titulo += f" [{resultados[0]}]"
    s = ev.get("slug") or m.get("slug") or ""
    return {
        "plataforma": "polymarket", "id": str(m.get("id")), "titulo": titulo,
        "url": f"https://polymarket.com/event/{s}" if s else "https://polymarket.com",
        "prob": precios[0],
        "volumen": a_float(m.get("volumeNum")) or a_float(m.get("volume")) or 0.0,
        "cierre": m.get("endDate") or ev.get("endDate"), "dinero": "real",
    }


def obtener_polymarket(cfg):
    pc = cfg.get("polymarket") or {}
    if not pc.get("activo", True):
        return []
    salida, vistos = [], set()
    for etiqueta in pc.get("etiquetas", ["politics"]):
        offset = 0
        for _ in range(pc.get("max_paginas", 10)):
            eventos = get_json("https://gamma-api.polymarket.com/events", {
                "tag_slug": etiqueta, "closed": "false", "active": "true",
                "limit": 100, "offset": offset})
            if not eventos:
                break
            for ev in eventos:
                for m in ev.get("markets") or []:
                    if m.get("closed") or m.get("archived") or m.get("active") is False:
                        continue
                    mid = str(m.get("id"))
                    if mid in vistos:
                        continue
                    vistos.add(mid)
                    r = _poly_registro(m, ev)
                    if r:
                        salida.append(r)
            if len(eventos) < 100:
                break
            offset += 100
    return salida


# ============================================================ Kalshi

KALSHI = "https://api.elections.kalshi.com/trade-api/v2"


def _k_precio(m, campo):
    v = a_float(m.get(f"{campo}_dollars"))
    if v is not None:
        return v
    v = a_float(m.get(campo))
    return v / 100 if v is not None else None


def _kalshi_prob(m):
    bid, ask, ultimo = _k_precio(m, "yes_bid"), _k_precio(m, "yes_ask"), _k_precio(m, "last_price")
    if bid and ask and 0 < bid <= ask and ask - bid <= 0.10:
        return (bid + ask) / 2          # punto medio si el spread es razonable
    if ultimo and ultimo > 0:
        return ultimo                   # si no, último precio operado
    if bid and ask and ask > bid:
        return (bid + ask) / 2
    return None


def _kalshi_registro(m, ev):
    p = _kalshi_prob(m)
    if p is None:
        return None
    ev = ev or {}
    titulo_ev = ev.get("title") or m.get("title") or m.get("ticker")
    sub = m.get("yes_sub_title") or m.get("subtitle") or ""
    titulo = f"{titulo_ev}: {sub}" if len(ev.get("markets") or []) > 1 and sub else titulo_ev
    serie = ev.get("series_ticker") or (m.get("event_ticker") or m.get("ticker", "")).split("-")[0]
    vol = a_float(m.get("volume"))
    if vol is None:
        vol = a_float(m.get("volume_fp")) or 0.0
    return {
        "plataforma": "kalshi", "id": m.get("ticker"), "titulo": titulo,
        "url": f"https://kalshi.com/markets/{serie.lower()}", "prob": p, "volumen": vol,
        "cierre": m.get("close_time") or m.get("expiration_time"), "dinero": "real",
    }


def obtener_kalshi(cfg):
    kc = cfg.get("kalshi") or {}
    if not kc.get("activo", True):
        return []
    categorias = {c.lower() for c in kc.get("categorias", ["Politics"])}
    salida, cursor = [], None
    for _ in range(kc.get("max_paginas", 30)):
        params = {"status": "open", "with_nested_markets": "true", "limit": 200}
        if cursor:
            params["cursor"] = cursor
        datos = get_json(f"{KALSHI}/events", params)
        for ev in datos.get("events") or []:
            if (ev.get("category") or "").lower() not in categorias:
                continue
            for m in ev.get("markets") or []:
                if m.get("status") not in (None, "active", "open"):
                    continue
                r = _kalshi_registro(m, ev)
                if r:
                    salida.append(r)
        cursor = datos.get("cursor")
        if not cursor:
            break
    return salida


# ============================================================ Manifold

def _terminos(cfg):
    """Palabras de búsqueda para las plataformas que buscan por texto."""
    vistos, salida = set(), []
    for t in cfg.get("temas") or []:
        for k in t.get("palabras", []):
            k = k.rstrip("*")
            if len(k) >= 4 and k.lower() not in vistos:
                vistos.add(k.lower())
                salida.append(k)
    return salida


def _manifold_registro(m):
    if m.get("isResolved") or m.get("probability") is None:
        return None
    cierre = m.get("closeTime")
    return {
        "plataforma": "manifold", "id": m.get("id"), "titulo": m.get("question", ""),
        "url": m.get("url") or "https://manifold.markets", "prob": a_float(m.get("probability")),
        "volumen": a_float(m.get("volume")) or 0.0,
        "cierre": a_iso(datetime.fromtimestamp(cierre / 1000, tz=timezone.utc)) if cierre else None,
        "dinero": "ficticio",
    }


def obtener_manifold(cfg):
    mc = cfg.get("manifold") or {}
    if not mc.get("activo", True):
        return []
    salida, vistos = [], set()
    for termino in _terminos(cfg):
        datos = get_json("https://api.manifold.markets/v0/search-markets", {
            "term": termino, "filter": "open", "contractType": "BINARY",
            "limit": mc.get("max_por_busqueda", 30)})
        for m in datos or []:
            if m.get("id") in vistos:
                continue
            vistos.add(m.get("id"))
            r = _manifold_registro(m)
            if r:
                salida.append(r)
    return salida


# ============================================================ Metaculus (opcional)

def _metaculus_prob(post):
    q = post.get("question") or {}
    aggs = q.get("aggregations") or {}
    for k in ("recency_weighted", "unweighted", "metaculus_prediction"):
        ultimo = (aggs.get(k) or {}).get("latest") or {}
        c = ultimo.get("centers") or ultimo.get("means")
        if c:
            return a_float(c[0] if isinstance(c, list) else c)
    return None


def obtener_metaculus(cfg):
    mc = cfg.get("metaculus") or {}
    token = os.environ.get("METACULUS_TOKEN")
    if not mc.get("activo", True) or not token:
        return []
    salida, vistos = [], set()
    for termino in _terminos(cfg):
        datos = get_json("https://www.metaculus.com/api/posts/", {
            "search": termino, "statuses": "open", "forecast_type": "binary",
            "limit": mc.get("max_por_busqueda", 20)},
            headers={"Authorization": f"Token {token}"})
        for p in datos.get("results") or []:
            if p.get("id") in vistos:
                continue
            vistos.add(p.get("id"))
            prob = _metaculus_prob(p)
            if prob is None:
                continue
            q = p.get("question") or {}
            salida.append({
                "plataforma": "metaculus", "id": str(p.get("id")), "titulo": p.get("title", ""),
                "url": f"https://www.metaculus.com/questions/{p.get('id')}/", "prob": prob,
                "volumen": a_float(p.get("nr_forecasters") or q.get("nr_forecasters")) or 0.0,
                "cierre": q.get("scheduled_close_time") or p.get("scheduled_close_time"),
                "dinero": "expertos",
            })
    return salida


# ============================================================ obtención

def obtener_todo(cfg):
    registros, errores = [], []
    for nombre, fn in (("Polymarket", obtener_polymarket), ("Kalshi", obtener_kalshi),
                       ("Manifold", obtener_manifold), ("Metaculus", obtener_metaculus)):
        try:
            r = fn(cfg)
            print(f"  {nombre}: {len(r)} mercados descargados")
            registros += r
        except Exception as e:  # una fuente caída no frena las demás
            print(f"  {nombre}: ERROR {e}", file=sys.stderr)
            errores.append(f"{nombre}: {e}")
    return registros, errores


def obtener_uno(ref):
    """Descarga un mercado puntual pedido en 'preguntas'."""
    plat, _, mid = ref.partition(":")
    if plat == "polymarket":
        m = get_json(f"https://gamma-api.polymarket.com/markets/{mid}")
        ev = (m.get("events") or [None])[0]
        return _poly_registro(m, ev)
    if plat == "kalshi":
        return _kalshi_registro(get_json(f"{KALSHI}/markets/{mid}").get("market") or {}, None)
    if plat == "manifold":
        return _manifold_registro(get_json(f"https://api.manifold.markets/v0/market/{mid}"))
    return None


# ============================================================ temas y pesos

def _patron(k):
    k = normalizar(k).strip()
    if k.endswith("*"):
        return re.compile(r"\b" + re.escape(k[:-1]))
    return re.compile(r"\b" + re.escape(k) + r"\b")


def compilar_temas(cfg):
    glob = [_patron(k) for k in cfg.get("excluir_siempre") or []]
    return [{"nombre": t["nombre"],
             "inc": [_patron(k) for k in t.get("palabras", [])],
             "exc": [_patron(k) for k in t.get("excluir", [])] + glob}
            for t in cfg.get("temas") or []]


def temas_de(titulo, temas):
    t = normalizar(titulo)
    return [x["nombre"] for x in temas
            if any(p.search(t) for p in x["inc"]) and not any(p.search(t) for p in x["exc"])]


def pasa_minimo(r, cfg):
    m = cfg.get("minimos") or {}
    v = r.get("volumen") or 0
    return v >= {
        "polymarket": m.get("polymarket_usd", 10000),
        "kalshi": m.get("kalshi_contratos", 10000),
        "manifold": m.get("manifold_mana", 5000),
        "metaculus": m.get("metaculus_pronosticadores", 30),
    }.get(r["plataforma"], 0)


def peso(r):
    """Peso en el consolidado: logaritmo del volumen; dinero ficticio pesa menos."""
    b = math.log10(1 + max(r.get("volumen") or 0, 0))
    if r["plataforma"] == "manifold":
        b *= 0.4
    elif r["plataforma"] == "metaculus":
        b *= 2.0      # 100 pronosticadores ≈ US$ 10.000 operados
    return max(b, 0.1)


# ============================================================ almacenamiento

def cargar_snapshots(path: Path):
    if not path.exists():
        return []
    with path.open(newline="", encoding="utf-8") as f:
        return [{"ts": de_iso(r["ts"]), "clave": r["clave"], "prob": float(r["prob"]),
                 "volumen": float(r["volumen"] or 0)} for r in csv.DictReader(f)]


def guardar_snapshots(path: Path, filas):
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", newline="", encoding="utf-8") as f:
        w = csv.writer(f)
        w.writerow(["ts", "clave", "prob", "volumen"])
        for r in sorted(filas, key=lambda r: (r["ts"], r["clave"])):
            w.writerow([a_iso(r["ts"]), r["clave"], f"{r['prob']:.4f}", f"{r['volumen']:.0f}"])


def cargar_json(path: Path, defecto):
    return json.loads(path.read_text(encoding="utf-8")) if path.exists() else defecto


def guardar_json(path: Path, obj):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")


def historias(filas):
    h = {}
    for r in filas:
        h.setdefault(r["clave"], []).append((r["ts"], r["prob"]))
    for v in h.values():
        v.sort()
    return h


def valor_hace(serie, ts, horas):
    objetivo = ts - timedelta(hours=horas) + timedelta(minutes=90)  # tolerancia por demoras
    previo = None
    for t, p in serie:
        if t <= objetivo:
            previo = p
        else:
            break
    return previo


# ============================================================ corrida

def ejecutar(cfg, dir_datos: Path, dir_salida: Path, registros=None, errores=None,
             ts=None, generar=True, demo=False, usar_red=True):
    ts = ts or ahora()
    errores = list(errores or [])
    if registros is None:
        registros, errores = obtener_todo(cfg)
    temas = compilar_temas(cfg)

    actuales, pool = {}, {}
    for r in registros:
        r["clave"] = f"{r['plataforma']}:{r['id']}"
        r["temas"] = temas_de(r["titulo"], temas)
        pool[r["clave"]] = r
        if r["temas"] and pasa_minimo(r, cfg):
            actuales[r["clave"]] = r

    # Mercados pedidos explícitamente en "preguntas" (aunque no pasen filtros)
    preguntas = cfg.get("preguntas") or []
    for q in preguntas:
        for ref in q.get("mercados", []):
            if ref in actuales:
                continue
            if ref in pool:
                actuales[ref] = pool[ref]
            elif usar_red:
                try:
                    r = obtener_uno(ref)
                except Exception as e:
                    errores.append(f"{ref}: {e}")
                    r = None
                if r:
                    r["clave"], r["temas"] = ref, temas_de(r["titulo"], temas)
                    actuales[ref] = r
                else:
                    errores.append(f"{ref}: no se encontró (¿mercado cerrado?)")

    # Preguntas consolidadas
    consolidadas = []
    for q in preguntas:
        partes = []
        for ref in q.get("mercados", []):
            r = actuales.get(ref)
            if not r:
                continue
            p = 1 - r["prob"] if ref in (q.get("invertir") or []) else r["prob"]
            partes.append({"clave": ref, "prob": p, "peso": peso(r)})
        if not partes:
            continue
        total = sum(x["peso"] for x in partes)
        s = slug(q["nombre"])
        c = {"plataforma": "consolidado", "id": s, "clave": f"consolidado:{s}",
             "titulo": q["nombre"], "url": "", "prob": sum(x["prob"] * x["peso"] for x in partes) / total,
             "volumen": 0.0, "cierre": q.get("cierre"), "dinero": "mixto",
             "temas": [q["tema"]] if q.get("tema") else [], "partes": partes,
             "divergencia": max(x["prob"] for x in partes) - min(x["prob"] for x in partes)}
        actuales[c["clave"]] = c
        consolidadas.append(c)

    # Historial
    filas = cargar_snapshots(dir_datos / "snapshots.csv")
    meta = cargar_json(dir_datos / "mercados.json", {})
    for clave, r in actuales.items():
        filas.append({"ts": ts, "clave": clave, "prob": round(r["prob"], 4),
                      "volumen": r.get("volumen") or 0})
        meta[clave] = {"plataforma": r["plataforma"], "titulo": r["titulo"], "url": r["url"],
                       "temas": r["temas"], "cierre": r.get("cierre"),
                       "primera_vez": meta.get(clave, {}).get("primera_vez", a_iso(ts)),
                       "ultima_vez": a_iso(ts)}
    corte = ts - timedelta(days=cfg.get("retencion_dias", 365))
    filas = [f for f in filas if f["ts"] >= corte]
    meta = {k: v for k, v in meta.items() if (de_iso(v.get("ultima_vez")) or ts) >= corte}
    guardar_snapshots(dir_datos / "snapshots.csv", filas)
    guardar_json(dir_datos / "mercados.json", meta)

    if generar:
        generar_salidas(cfg, actuales, consolidadas, filas, ts, errores, dir_datos, dir_salida, demo)
    return actuales


# ============================================================ análisis para publicar

STOP = {"will", "the", "and", "for", "with", "from", "than", "that", "this", "which", "who",
        "what", "does", "before", "after", "end", "into", "between", "any", "next"}


def similitud(a, b):
    na, nb = normalizar(a), normalizar(b)
    r = difflib.SequenceMatcher(None, na, nb).ratio()
    ta = {w for w in re.findall(r"[a-z0-9]+", na) if len(w) > 2 and w not in STOP}
    tb = {w for w in re.findall(r"[a-z0-9]+", nb) if len(w) > 2 and w not in STOP}
    j = len(ta & tb) / len(ta | tb) if ta and tb else 0
    return max(r, j)


def equivalencias(actuales, preguntas, limite=12):
    agrupados = {ref for q in preguntas for ref in q.get("mercados", [])}
    por_tema = {}
    for r in actuales.values():
        if r["plataforma"] == "consolidado":
            continue
        for t in r["temas"]:
            por_tema.setdefault(t, []).append(r)
    pares, vistos = [], set()
    for lista in por_tema.values():
        for i, a in enumerate(lista):
            for b in lista[i + 1:]:
                if a["plataforma"] == b["plataforma"]:
                    continue
                if a["clave"] in agrupados and b["clave"] in agrupados:
                    continue
                k = tuple(sorted((a["clave"], b["clave"])))
                if k in vistos:
                    continue
                vistos.add(k)
                s = similitud(a["titulo"], b["titulo"])
                if s >= 0.6:
                    pares.append((s, a, b))
    pares.sort(key=lambda x: -x[0])
    return pares[:limite]


def preparar(actuales, filas, ts, dias):
    h = historias(filas)
    desde = ts - timedelta(days=dias)
    info = {}
    for clave, r in actuales.items():
        serie = h.get(clave, [])
        v24, v7 = valor_hace(serie, ts, 24), valor_hace(serie, ts, 24 * 7)
        info[clave] = {
            "d24": None if v24 is None else r["prob"] - v24,
            "d7": None if v7 is None else r["prob"] - v7,
            "hace7": v7, "hace24": v24,
            "serie": [(t, p) for t, p in serie if t >= desde],
        }
    return info


# ============================================================ formato

def tz_local(cfg):
    try:
        from zoneinfo import ZoneInfo
        return ZoneInfo(cfg.get("zona_horaria", "America/Argentina/Buenos_Aires"))
    except Exception:
        return timezone(timedelta(hours=-3))


def fecha_es(dt, con_hora=False, tz=None):
    if dt is None:
        return ""
    if tz:
        dt = dt.astimezone(tz)
    s = f"{dt.day} {MESES[dt.month - 1]} {dt.year}"
    return s + (f" a las {dt:%H:%M}" if con_hora else "")


def pct(p):
    return f"{round(p * 100)}%" if p is not None else "—"


def pp(d):
    if d is None:
        return "—"
    v = round(d * 100)
    return "0 pp" if v == 0 else f"{v:+d} pp".replace("-", "−")


def clase_delta(d):
    if d is None or round(d * 100) == 0:
        return "igual"
    return "sube" if d > 0 else "baja"


def volumen_txt(r):
    v = r.get("volumen") or 0
    p = r["plataforma"]
    if p == "metaculus":
        return f"{num_es(v)} pronosticadores"
    if p == "manifold":
        return f"{num_es(v / 1000)} mil maná (dinero ficticio)" if v >= 1000 else f"{num_es(v)} maná (dinero ficticio)"
    if p == "kalshi":
        return f"{num_es(v / 1e6, 1)} M contratos" if v >= 1e6 else f"{num_es(v / 1000)} mil contratos"
    if v >= 1e6:
        return f"US$ {num_es(v / 1e6, 1)} M operados"
    return f"US$ {num_es(v / 1000)} mil operados"


def minigrafico(serie, ancho=132, alto=34):
    if len(serie) < 2:
        return '<span class="sin-hist">Historial en construcción</span>'
    t0, t1 = serie[0][0].timestamp(), serie[-1][0].timestamp()
    ps = [p for _, p in serie]
    lo, hi = min(ps), max(ps)
    if hi - lo < 0.10:                    # escala mínima de 10 puntos
        c = (hi + lo) / 2
        lo, hi = max(0, c - 0.05), min(1, c + 0.05)
    pts = " ".join(
        f"{(t.timestamp() - t0) / max(t1 - t0, 1) * (ancho - 4) + 2:.1f},"
        f"{alto - 3 - (p - lo) / max(hi - lo, 1e-9) * (alto - 6):.1f}" for t, p in serie)
    cls = clase_delta(ps[-1] - ps[0])
    x, y = pts.split(" ")[-1].split(",")
    return (f'<svg class="spark {cls}" viewBox="0 0 {ancho} {alto}" width="{ancho}" height="{alto}" '
            f'aria-hidden="true"><polyline points="{pts}" fill="none" stroke="currentColor" '
            f'stroke-width="1.6" stroke-linejoin="round" stroke-linecap="round"/>'
            f'<circle cx="{x}" cy="{y}" r="2.6" fill="currentColor"/></svg>')


def e(s):
    return html.escape(str(s or ""), quote=True)


# ============================================================ HTML

CSS = """
:root{
  --papel:#EEF1F4; --superficie:#F8FAFB; --tinta:#17212E; --suave:#566273; --linea:#D3DAE2;
  --sube:#13755C; --baja:#B13A2D; --senal:#A86A0C; --riel:#DCE2E9; --relleno:#284466; --fantasma:#8A97A8;
  --ancho:1080px;
}
@media (prefers-color-scheme: dark){ :root:not([data-theme="light"]){
  --papel:#10161E; --superficie:#161E28; --tinta:#E4E9EF; --suave:#97A3B3; --linea:#283342;
  --sube:#41B58F; --baja:#E86A5C; --senal:#E0A43C; --riel:#222D3A; --relleno:#7FA2CB; --fantasma:#6B788A;
}}
:root[data-theme="dark"]{
  --papel:#10161E; --superficie:#161E28; --tinta:#E4E9EF; --suave:#97A3B3; --linea:#283342;
  --sube:#41B58F; --baja:#E86A5C; --senal:#E0A43C; --riel:#222D3A; --relleno:#7FA2CB; --fantasma:#6B788A;
}
*{box-sizing:border-box}
html{-webkit-text-size-adjust:100%}
body{margin:0;background:var(--papel);color:var(--tinta);
  font:16px/1.5 "Public Sans",system-ui,-apple-system,"Segoe UI",Roboto,sans-serif;
  font-variant-numeric:tabular-nums}
a{color:inherit;text-decoration-color:var(--linea);text-underline-offset:3px}
a:hover{text-decoration-color:currentColor}
a:focus-visible,summary:focus-visible{outline:2px solid var(--relleno);outline-offset:3px;border-radius:2px}
.envoltura{max-width:var(--ancho);margin:0 auto;padding:0 20px 64px}
h1,h2,h3,.num{font-family:"Archivo","Arial Narrow",system-ui,sans-serif}
header.cab{padding:48px 0 28px;border-bottom:1px solid var(--linea)}
h1{font-size:clamp(2.2rem,5vw,3.4rem);line-height:1;margin:0 0 14px;font-weight:800;
  font-variation-settings:"wdth" 78;letter-spacing:-.01em}
.bajada{max-width:62ch;margin:0;color:var(--suave);font-size:1.05rem}
.actualizado{margin:18px 0 0;font-size:.92rem}
.demo{background:var(--senal);color:#fff;padding:10px 14px;border-radius:4px;margin-top:20px;font-weight:600}
nav.temas{display:flex;flex-wrap:wrap;gap:8px 18px;padding:16px 0;border-bottom:1px solid var(--linea);font-size:.95rem}
nav.temas a{text-decoration:none;color:var(--suave)}
nav.temas a:hover{color:var(--tinta)}
section{padding-top:44px}
h2{font-size:1.75rem;line-height:1.1;margin:0 0 6px;font-weight:700;font-variation-settings:"wdth" 85}
.nota{color:var(--suave);margin:0 0 18px;font-size:.95rem;max-width:70ch}
.movimientos{display:grid;grid-template-columns:repeat(auto-fit,minmax(320px,1fr));gap:28px}
h3{font-size:1.05rem;margin:0 0 8px;font-weight:600;font-variation-settings:"wdth" 100}
.mov{list-style:none;margin:0;padding:0}
.mov li{display:grid;grid-template-columns:1fr auto;gap:2px 14px;padding:10px 0;border-top:1px solid var(--linea)}
.mov .t{font-size:.95rem;line-height:1.35}
.mov .plat{grid-column:1;font-size:.82rem;color:var(--suave)}
.mov .cambio{grid-row:1/span 2;grid-column:2;text-align:right;align-self:center;white-space:nowrap}
.mov .cambio b{display:block;font-family:"Archivo",sans-serif;font-size:1.15rem;font-variation-settings:"wdth" 80}
.mov .cambio span{font-size:.82rem;color:var(--suave)}
.vacio{color:var(--suave);font-style:italic}
.fila{display:grid;grid-template-columns:minmax(0,1fr) 190px 64px 64px 132px;gap:6px 20px;
  align-items:center;padding:14px 0;border-top:1px solid var(--linea)}
.fila:last-child{border-bottom:1px solid var(--linea)}
.fila.consol{background:var(--superficie);padding:16px 14px;margin:0 -14px;border-radius:6px;border-top-color:transparent}
.fila.consol + .fila.consol{margin-top:8px}
.tit a{font-weight:500;line-height:1.35}
.meta{margin:4px 0 0;font-size:.82rem;color:var(--suave);display:flex;flex-wrap:wrap;gap:2px 14px}
.clave{opacity:.75;user-select:all}
.prob{display:flex;align-items:center;gap:12px}
.num{font-size:1.9rem;font-weight:700;line-height:1;min-width:3.1ch;text-align:right;font-variation-settings:"wdth" 72}
.riel{position:relative;flex:1;height:8px;background:var(--riel);border-radius:4px}
.riel .lleno{position:absolute;left:0;top:0;bottom:0;background:var(--relleno);border-radius:4px}
.riel .antes{position:absolute;top:-4px;bottom:-4px;width:2px;margin-left:-1px;background:var(--fantasma)}
.d{text-align:right;line-height:1.2}
.d small{display:block;font-size:.72rem;color:var(--suave)}
.d span{font-weight:600}
.sube{color:var(--sube)} .baja{color:var(--baja)} .igual{color:var(--suave)}
.spark{display:block;width:132px;height:34px}
.spark.igual{color:var(--fantasma)}
.sin-hist{font-size:.78rem;color:var(--suave)}
.partes{grid-column:1/-1;font-size:.85rem;color:var(--suave);margin:0}
.diverge{color:var(--senal);font-weight:600}
details{margin-top:8px}
summary{cursor:pointer;color:var(--suave)}
.eq{list-style:none;padding:0;margin:12px 0 0}
.eq li{padding:12px 0;border-top:1px solid var(--linea);font-size:.92rem}
.eq code{font:12px/1.4 ui-monospace,Menlo,Consolas,monospace;background:var(--superficie);
  padding:6px 8px;display:block;margin-top:6px;border-radius:4px;overflow-x:auto;white-space:pre}
footer{margin-top:56px;padding-top:20px;border-top:1px solid var(--linea);font-size:.88rem;color:var(--suave)}
footer p{max-width:75ch}
.errores{color:var(--baja)}
@media (max-width:760px){
  .fila{grid-template-columns:max-content max-content 1fr;gap:10px 28px}
  .tit{grid-column:1/-1}
  .prob{grid-column:1/-1}
  .spark-c{display:none}
  .d{text-align:left}
}
@media (prefers-reduced-motion:no-preference){
  .riel .lleno{animation:llenar .7s ease-out both}
  @keyframes llenar{from{width:0}}
}
"""


def html_fila(r, i, tz, cfg):
    hace7 = i["hace7"]
    antes = (f'<span class="antes" style="left:{hace7 * 100:.1f}%" title="Hace 7 días: {pct(hace7)}"></span>'
             if hace7 is not None else "")
    etiqueta = f'{pct(r["prob"])}' + (f', hace 7 días {pct(hace7)}' if hace7 is not None else "")
    meta = []
    if r["plataforma"] == "consolidado":
        meta.append("Consolidado entre plataformas")
    else:
        meta.append(NOMBRES[r["plataforma"]])
        meta.append(volumen_txt(r))
    cierre = de_iso(r.get("cierre"))
    if cierre:
        meta.append(f"Cierra el {fecha_es(cierre, tz=tz)}")
    if r["plataforma"] != "consolidado":
        meta.append(f'<span class="clave" title="Identificador para config.yaml">{e(r["clave"])}</span>')
    titulo = (f'<a href="{e(r["url"])}" target="_blank" rel="noopener">{e(r["titulo"])}</a>'
              if r["url"] else f'<strong>{e(r["titulo"])}</strong>')
    extra = ""
    if r["plataforma"] == "consolidado":
        partes = ", ".join(f'{NOMBRES[p["clave"].split(":")[0]]} {pct(p["prob"])}' for p in r["partes"])
        div = r["divergencia"]
        aviso = (f' <span class="diverge">Las plataformas difieren en {round(div * 100)} puntos.</span>'
                 if div * 100 >= cfg.get("umbral_divergencia_pp", 8) else "")
        extra = f'<p class="partes">Por plataforma: {partes}.{aviso}</p>'
    return f"""
<article class="fila{' consol' if r['plataforma'] == 'consolidado' else ''}">
  <div class="tit">{titulo}<p class="meta">{''.join(f'<span>{m}</span>' for m in meta)}</p></div>
  <div class="prob" role="img" aria-label="{e(etiqueta)}">
    <span class="num">{pct(r['prob'])}</span>
    <span class="riel"><span class="lleno" style="width:{r['prob'] * 100:.1f}%"></span>{antes}</span>
  </div>
  <div class="d"><small>24 h</small><span class="{clase_delta(i['d24'])}">{pp(i['d24'])}</span></div>
  <div class="d"><small>7 días</small><span class="{clase_delta(i['d7'])}">{pp(i['d7'])}</span></div>
  <div class="spark-c">{minigrafico(i['serie'])}</div>
  {extra}
</article>"""


def html_movimientos(actuales, info, campo, previo, cfg):
    top = cfg.get("top_movimientos", 8)
    lista = [(info[k][campo], r) for k, r in actuales.items() if info[k][campo] is not None]
    lista = [x for x in lista if abs(x[0]) >= 0.01]
    lista.sort(key=lambda x: -abs(x[0]))
    if not lista:
        return '<p class="vacio">Todavía no hay historial suficiente para este período.</p>'
    items = []
    for d, r in lista[:top]:
        antes = info[r["clave"]][previo]
        link = (f'<a href="{e(r["url"])}" target="_blank" rel="noopener">{e(r["titulo"])}</a>'
                if r["url"] else e(r["titulo"]))
        items.append(f"""<li><span class="t">{link}</span>
  <span class="plat">{NOMBRES[r['plataforma']]}{', ' + e(r['temas'][0]) if r['temas'] else ''}</span>
  <span class="cambio"><b class="{clase_delta(d)}">{pp(d)}</b><span>{pct(antes)} a {pct(r['prob'])}</span></span></li>""")
    return f'<ul class="mov">{"".join(items)}</ul>'


def generar_html(cfg, actuales, consolidadas, info, ts, errores, demo):
    tz = tz_local(cfg)
    titulo = cfg.get("titulo", "Monitor de pronósticos")
    nombres_temas = [t["nombre"] for t in cfg.get("temas") or []]
    nombres_temas += sorted({t for c in consolidadas for t in c["temas"]} - set(nombres_temas))
    plataformas = sorted({NOMBRES[r["plataforma"]] for r in actuales.values() if r["plataforma"] != "consolidado"})
    n = sum(1 for r in actuales.values() if r["plataforma"] != "consolidado")

    secciones = []
    if consolidadas:
        filas = "".join(html_fila(c, info[c["clave"]], tz, cfg) for c in consolidadas)
        secciones.append(f"""<section id="consolidadas"><h2>Preguntas consolidadas</h2>
<p class="nota">El mismo evento medido en varias plataformas. La probabilidad es un promedio ponderado por volumen; Manifold (dinero ficticio) pesa menos.</p>{filas}</section>""")

    max_t = cfg.get("max_por_tema", 15)
    for t in nombres_temas:
        del_tema = [r for r in actuales.values() if t in r["temas"]]
        if not del_tema:
            continue
        del_tema.sort(key=lambda r: (r["plataforma"] != "consolidado", -(r.get("volumen") or 0)))
        filas = "".join(html_fila(r, info[r["clave"]], tz, cfg) for r in del_tema[:max_t])
        resto = len(del_tema) - max_t
        nota = (f'<p class="nota">Se muestran los {max_t} con más volumen de {len(del_tema)}. La lista completa está en data.json.</p>'
                if resto > 0 else "")
        secciones.append(f'<section id="{slug(t)}"><h2>{e(t)}</h2>{nota}{filas}</section>')

    pares = equivalencias(actuales, cfg.get("preguntas") or [])
    if pares:
        items = "".join(
            f"""<li>{e(a['titulo'])} <em>({NOMBRES[a['plataforma']]}, {pct(a['prob'])})</em><br>
{e(b['titulo'])} <em>({NOMBRES[b['plataforma']]}, {pct(b['prob'])})</em>
<code>  - nombre: "{e(a['titulo'][:70])}"
    tema: "{e(a['temas'][0] if a['temas'] else '')}"
    mercados: ["{e(a['clave'])}", "{e(b['clave'])}"]</code></li>""" for _, a, b in pares)
        secciones.append(f"""<section id="equivalencias"><h2>Posibles equivalencias</h2>
<p class="nota">Mercados de distintas plataformas con títulos parecidos. Si de verdad preguntan lo mismo (revisá fechas y reglas de resolución), copiá el bloque en la sección <em>preguntas</em> de config.yaml para consolidarlos.</p>
<details><summary>Ver {len(pares)} sugerencias</summary><ul class="eq">{items}</ul></details></section>""")

    nav = "".join(f'<a href="#{slug(t)}">{e(t)}</a>' for t in nombres_temas
                  if any(t in r["temas"] for r in actuales.values()))
    if consolidadas:
        nav = '<a href="#consolidadas">Preguntas consolidadas</a>' + nav
    aviso_demo = ('<p class="demo">Datos de demostración: los mercados y números de esta página son ficticios.</p>'
                  if demo else "")
    err = (f'<p class="errores">Fuentes con error en esta actualización: {e("; ".join(errores))}</p>'
           if errores else "")

    return f"""<!doctype html>
<html lang="es"><head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>{e(titulo)}</title>
<link rel="alternate" type="application/rss+xml" title="{e(titulo)}" href="feed.xml">
<link rel="preconnect" href="https://fonts.googleapis.com"><link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>
<link href="https://fonts.googleapis.com/css2?family=Archivo:wdth,wght@62..125,400..800&family=Public+Sans:wght@400;500;600&display=swap" rel="stylesheet">
<style>{CSS}</style>
</head><body><div class="envoltura">
<header class="cab">
  <h1>{e(titulo)}</h1>
  <p class="bajada">Probabilidades de los mercados de predicción sobre elecciones y conflictos internacionales, con su evolución y las diferencias entre plataformas.</p>
  <p class="actualizado">Actualizado el {fecha_es(ts, True, tz)} (hora de Buenos Aires). {n} mercados seguidos en {e(', '.join(plataformas)) or 'ninguna plataforma'}. <a href="feed.xml">Suscribirse por RSS</a>.</p>
  {aviso_demo}
</header>
<nav class="temas" aria-label="Temas">{nav}</nav>
<section id="movimientos"><h2>Lo que más se movió</h2>
<p class="nota">Cambio en puntos porcentuales (pp). En cada mercado, la marca gris sobre la barra indica dónde estaba hace 7 días.</p>
<div class="movimientos">
  <div><h3>Últimas 24 horas</h3>{html_movimientos(actuales, info, 'd24', 'hace24', cfg)}</div>
  <div><h3>Últimos 7 días</h3>{html_movimientos(actuales, info, 'd7', 'hace7', cfg)}</div>
</div></section>
{''.join(secciones)}
<footer>
  <p>Fuentes: Polymarket y Kalshi (dinero real), Manifold (dinero ficticio) y Metaculus (pronosticadores, si está configurado). Un precio de 41 centavos equivale a una probabilidad implícita del 41%. Los mercados con poco volumen se excluyen, pero incluso los incluidos pueden moverse por pocas operaciones. Esto no es asesoramiento financiero.</p>
  {err}
</footer>
</div></body></html>"""


def generar_feed(cfg, actuales, info, ts, dir_datos, dir_salida):
    umbral = cfg.get("umbral_feed_pp", 5) / 100
    items = cargar_json(dir_datos / "feed.json", [])
    recientes = {it["clave"] for it in items if (de_iso(it["ts"]) or ts) > ts - timedelta(hours=24)}
    for clave, r in actuales.items():
        d = info[clave]["d24"]
        if d is None or abs(d) < umbral or clave in recientes:
            continue
        items.append({"ts": a_iso(ts), "clave": clave, "url": r["url"],
                      "titulo": f"{r['titulo']}: {pct(info[clave]['hace24'])} a {pct(r['prob'])} ({pp(d)} en 24 h)",
                      "desc": f"{NOMBRES[r['plataforma']]}. Tema: {', '.join(r['temas']) or 'sin tema'}."})
    items = sorted(items, key=lambda x: x["ts"], reverse=True)[:150]
    guardar_json(dir_datos / "feed.json", items)
    from email.utils import format_datetime
    entradas = "".join(
        f"<item><title>{e(it['titulo'])}</title><link>{e(it['url'])}</link>"
        f"<guid isPermaLink=\"false\">{e(it['clave'] + '@' + it['ts'])}</guid>"
        f"<pubDate>{format_datetime(de_iso(it['ts']))}</pubDate><description>{e(it['desc'])}</description></item>"
        for it in items)
    xml = (f'<?xml version="1.0" encoding="UTF-8"?><rss version="2.0"><channel>'
           f'<title>{e(cfg.get("titulo", "Monitor de pronósticos"))}: movimientos</title>'
           f'<link>./</link><description>Cambios de al menos {cfg.get("umbral_feed_pp", 5)} puntos en 24 horas.</description>'
           f'<language>es</language>{entradas}</channel></rss>')
    (dir_salida / "feed.xml").write_text(xml, encoding="utf-8")


def generar_salidas(cfg, actuales, consolidadas, filas, ts, errores, dir_datos, dir_salida, demo):
    dir_salida.mkdir(parents=True, exist_ok=True)
    info = preparar(actuales, filas, ts, cfg.get("dias_grafico", 30))
    (dir_salida / "index.html").write_text(
        generar_html(cfg, actuales, consolidadas, info, ts, errores, demo), encoding="utf-8")
    generar_feed(cfg, actuales, info, ts, dir_datos, dir_salida)
    guardar_json(dir_salida / "data.json", {
        "actualizado": a_iso(ts),
        "mercados": sorted([{
            "clave": k, "plataforma": r["plataforma"], "titulo": r["titulo"], "url": r["url"],
            "temas": r["temas"], "probabilidad": round(r["prob"], 4),
            "cambio_24h": None if info[k]["d24"] is None else round(info[k]["d24"], 4),
            "cambio_7d": None if info[k]["d7"] is None else round(info[k]["d7"], 4),
            "volumen": r.get("volumen"), "cierre": r.get("cierre")}
            for k, r in actuales.items()], key=lambda x: -(x["volumen"] or 0)),
    })
    (dir_salida / ".nojekyll").write_text("")


# ============================================================ demo

def demo(cfg):
    random.seed(11)
    base = [
        ("polymarket", "900101", "Russia x Ukraine ceasefire in 2026?", .18, 4.2e6, 0.004),
        ("kalshi", "KXUKRCEASE-26", "Will Russia and Ukraine agree to a ceasefire before 2027?", .23, 1.8e5, 0.004),
        ("manifold", "mfUkr26", "Russia-Ukraine ceasefire by end of 2026?", .27, 4.1e4, 0.002),
        ("polymarket", "900102", "Will Putin meet Zelensky in 2026?", .11, 9.5e5, 0),
        ("polymarket", "900201", "Israel strikes Iran by December 31?", .31, 2.1e6, -0.003),
        ("polymarket", "900202", "Hamas-Israel ceasefire holds through November?", .57, 9.0e5, 0),
        ("kalshi", "KXIRANDEAL-26", "Will the US and Iran sign a nuclear deal in 2026?", .12, 6.0e4, 0),
        ("polymarket", "900301", "China invades Taiwan before 2027?", .04, 3.5e6, 0),
        ("metaculus", "40102", "Will China impose a blockade on Taiwan before 2027?", .06, 420, 0),
        ("polymarket", "900401", "Brazil presidential election winner: Candidate A", .48, 8.0e6, 0.003),
        ("kalshi", "KXBRAPRES-26-A", "Brazil presidential election: Candidate A", .45, 2.5e5, 0.003),
        ("polymarket", "900402", "Colombia presidential election winner: Candidate C", .36, 1.2e6, -0.002),
        ("polymarket", "900501", "Which party wins the House in the 2026 midterms? Democrats", .64, 1.4e7, 0.001),
        ("kalshi", "KXHOUSE-26-D", "Will Democrats win the House in the 2026 midterms?", .62, 3.1e6, 0.001),
        ("polymarket", "900601", "Snap legislative election in France in 2026?", .22, 4.0e5, 0),
        ("polymarket", "900602", "Will a NATO member invoke Article 4 in 2026?", .15, 3.0e5, 0),
        ("polymarket", "999999", "Will Brazil win the World Cup?", .2, 9e6, 0),   # debe quedar excluido
    ]
    cfg = {**cfg, "preguntas": [
        {"nombre": "Alto el fuego Rusia–Ucrania antes de 2027", "tema": "Guerra Rusia–Ucrania",
         "mercados": ["polymarket:900101", "kalshi:KXUKRCEASE-26", "manifold:mfUkr26"]},
        {"nombre": "Demócratas ganan la Cámara de Representantes (2026)", "tema": "Estados Unidos",
         "mercados": ["polymarket:900501", "kalshi:KXHOUSE-26-D"]}]}
    raiz = BASE / "demo"
    shutil.rmtree(raiz, ignore_errors=True)
    fin = ahora().replace(minute=0, second=0)
    pasos = 30 * 4
    estado = {b[1]: b[3] for b in base}
    for k in range(pasos + 1):
        ts = fin - timedelta(hours=6 * (pasos - k))
        regs = []
        for plat, mid, titulo, _, vol, deriva in base:
            p = estado[mid] + deriva / 4 + random.gauss(0, 0.006)
            if mid == "900201" and k == pasos - 2:   # salto en las últimas 24 h
                p += 0.11
            if mid == "900401" and k == pasos - 12:  # salto hace unos días
                p -= 0.07
            p = min(max(p, 0.01), 0.99)
            estado[mid] = p
            regs.append({"plataforma": plat, "id": mid, "titulo": titulo, "url": "",
                         "prob": p, "volumen": vol * (0.6 + 0.4 * k / pasos),
                         "cierre": "2026-12-31T23:59:00Z",
                         "dinero": {"manifold": "ficticio", "metaculus": "expertos"}.get(plat, "real")})
        ejecutar(cfg, raiz / "datos", raiz, registros=regs, ts=ts,
                 generar=(k == pasos), demo=True, usar_red=False)
    print(f"Demo generada en {raiz / 'index.html'}")


# ============================================================ main

def main():
    ap = argparse.ArgumentParser(description="Monitor de pronósticos políticos")
    ap.add_argument("--demo", action="store_true", help="generar una página de ejemplo con datos ficticios")
    ap.add_argument("--config", default=str(BASE / "config.yaml"))
    args = ap.parse_args()
    cfg = yaml.safe_load(Path(args.config).read_text(encoding="utf-8")) or {}
    if args.demo:
        demo(cfg)
        return
    print("Descargando mercados…")
    actuales = ejecutar(cfg, BASE / "data", BASE / "docs")
    print(f"Listo: {len(actuales)} mercados seguidos. Página en docs/index.html")


if __name__ == "__main__":
    main()
