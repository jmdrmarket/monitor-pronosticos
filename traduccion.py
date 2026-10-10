"""Traducción de los títulos de los mercados con el diccionario traducciones.json.

Los títulos de las preguntas con varias opciones llegan como "Pregunta: opción"
(por ejemplo "Democratic Presidential Nominee 2028: Gavin Newsom"). Se traduce
la pregunta y la opción por separado, así cada traducción sirve para todos los
candidatos. Una entrada para el título completo tiene prioridad sobre las partes.
Las fechas sueltas ("December 31", "June 30, 2027") se traducen solas.
Solo biblioteca estándar: lo usan tracker.py y los scripts de la tarea nocturna.
"""
import json
import re
from pathlib import Path

MESES = {"January": "enero", "February": "febrero", "March": "marzo", "April": "abril",
         "May": "mayo", "June": "junio", "July": "julio", "August": "agosto",
         "September": "septiembre", "October": "octubre", "November": "noviembre",
         "December": "diciembre"}
_FECHA = re.compile(r"(%s) (\d{1,2})(?:, (\d{4}))?" % "|".join(MESES))
_SUFIJO = re.compile(r"^(.*) \[(.+)\]$")     # opción que no es "Sí", agregada por tracker.py


def cargar(path) -> dict:
    p = Path(path)
    if not p.exists():
        return {}
    datos = json.loads(p.read_text(encoding="utf-8"))
    return datos.get("textos", {}) if isinstance(datos, dict) else {}


def fecha(texto):
    m = _FECHA.fullmatch(texto.strip())
    if not m:
        return None
    return f"{int(m[2])} de {MESES[m[1]]}" + (f" de {m[3]}" if m[3] else "")


def _partes(titulo):
    """Devuelve (pregunta, opción, sufijo); opción es None si no hay ': '."""
    sufijo = None
    m = _SUFIJO.match(titulo)
    if m:
        titulo, sufijo = m[1], m[2]
    if ": " in titulo:
        pregunta, _, opcion = titulo.rpartition(": ")
        return pregunta.strip(), opcion.strip(), sufijo
    return titulo.strip(), None, sufijo


def traducir(titulo, dic):
    """Título en castellano, o None si falta alguna parte en el diccionario."""
    titulo = titulo.strip()
    if titulo in dic:
        return dic[titulo]
    pregunta, opcion, sufijo = _partes(titulo)
    base = None
    if opcion is None:
        base = dic.get(pregunta)
    elif f"{pregunta}: {opcion}" in dic:
        base = dic[f"{pregunta}: {opcion}"]
    else:
        p, o = dic.get(pregunta), dic.get(opcion) or fecha(opcion)
        if p and o:
            # "¿… antes del…?" + "31 de diciembre" → "¿… antes del 31 de diciembre?"
            if p.endswith("…?") and opcion not in dic and fecha(opcion):
                base = f"{p[:-2]} {o}?"
            else:
                base = f"{p}: {o}"
    if base is None:
        return None
    if sufijo:
        s = dic.get(sufijo)
        if s is None:
            return None
        base += f" [{s}]"
    return base


def faltantes(titulo, dic):
    """Textos que hay que agregar al diccionario para traducir este título."""
    titulo = titulo.strip()
    if traducir(titulo, dic) is not None:
        return []
    pregunta, opcion, sufijo = _partes(titulo)
    falta = []
    if pregunta not in dic:
        falta.append(pregunta)
    if opcion is not None and opcion not in dic and not fecha(opcion):
        falta.append(opcion)
    if sufijo and sufijo not in dic:
        falta.append(sufijo)
    return falta
