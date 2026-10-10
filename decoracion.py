"""Elementos visuales de la página: color por tema, banderas, fotos y logos de plataforma.

- Banderas: biblioteca flag-icons (licencia MIT), por código ISO del país.
- Fotos: recursos/fotos/, todas de Wikimedia Commons con licencia libre; autor y
  licencia en recursos/fotos/fotos.json, que la página muestra al pie.
- Logos: recursos/logos/, solo para identificar de qué plataforma sale cada dato.
"""
import json
import re
from pathlib import Path

# Color de acento por tema (claro, oscuro). Es decorativo: ningún dato depende del color.
COLORES = {
    "guerra-rusia-ucrania": ("#2F5DA8", "#7EA6E8"),
    "medio-oriente": ("#A8641B", "#E3A35C"),
    "china-y-taiwan": ("#B03A3A", "#E88080"),
    "elecciones-en-america-latina": ("#2E7D52", "#6CC795"),
    "estados-unidos": ("#3E4C8C", "#9AA7E6"),
    "europa": ("#6B4C9A", "#B79BE0"),
}
BANDERAS_TEMA = {
    "guerra-rusia-ucrania": ["ru", "ua"], "medio-oriente": ["il", "ir"], "china-y-taiwan": ["cn", "tw"],
    "elecciones-en-america-latina": ["br", "ve", "ar"], "estados-unidos": ["us"], "europa": ["eu"],
}
LOGOS = {"polymarket": "polymarket.png", "kalshi": "kalshi.png", "manifold": "manifold.ico",
         "metaculus": "metaculus.ico"}

# País → palabras que lo nombran en los títulos (en inglés, como llegan).
_PAISES = [
    ("us", r"\bU\.S\.|\bUS\b|United States|\bTrump\b|\bMidterms?\b|\bU\.S\. House|\bSenate\b|Presidential Nominee|presidential nominee|\bTexas\b|Mar-a-Lago"),
    ("br", r"\bBrazil"), ("ve", r"\bVenezuela"), ("co", r"\bColombia"), ("mx", r"\bMexic"), ("ar", r"\bArgentin"),
    ("cl", r"\bChile"), ("pe", r"\bPeru\b"), ("ec", r"\bEcuador"), ("bo", r"\bBolivia"), ("cu", r"\bCuba"),
    ("ru", r"\bRussia|\bPutin\b|\bKremlin"), ("ua", r"\bUkrain|\bZelensk|\bKyiv\b|\bCrimea"),
    ("il", r"\bIsrael|\bNetanyahu\b"), ("ir", r"\bIran|\bKhamenei\b|\bPahlavi\b|\bKharg\b"),
    ("ps", r"\bGaza\b|\bHamas\b|\bPalestin"), ("lb", r"\bLebanon|\bHezbollah"), ("sy", r"\bSyria"),
    ("ye", r"\bYemen|\bHouthi"), ("sa", r"\bSaudi"), ("cn", r"\bChina\b|\bChinese\b|\bXi Jinping\b|\bBeijing\b"),
    ("tw", r"\bTaiwan"), ("fr", r"\bFrance\b|\bFrench\b|\bMacron\b"), ("de", r"\bGerman|\bMerz\b|\bAfD\b|\bCDU\b"),
    ("es", r"\bSpain\b|\bSpanish\b|\bSánchez\b|\bFeijóo\b|\bPSOE\b"), ("it", r"\bItaly\b|\bItalian\b|\bMeloni\b"),
    ("gb", r"\bUnited Kingdom\b|\bU\.K\.|\bStarmer\b|\bBritish\b"), ("hu", r"\bHungar|\bOrbán\b|\bOrban\b"),
    ("pl", r"\bPoland\b|\bPolish\b"), ("gl", r"\bGreenland"), ("eu", r"\bEU\b|European Union"),
]
_PAISES = [(c, re.compile(p)) for c, p in _PAISES]


def banderas(titulo, maximo=3):
    """Códigos ISO de los países que nombra el título, en el orden en que aparecen."""
    hallados = []
    for codigo, patron in _PAISES:
        m = patron.search(titulo)
        if m:
            hallados.append((m.start(), codigo))
    vistos, salida = set(), []
    for _, c in sorted(hallados):
        if c not in vistos:
            vistos.add(c)
            salida.append(c)
    return salida[:maximo]


class Fotos:
    def __init__(self, carpeta: Path):
        p = carpeta / "fotos.json"
        self.datos = json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}
        # alias más largos primero: "Donald Trump" antes que "Trump"
        self._alias = sorted(((a, n) for n, d in self.datos.items() for a in d["alias"] + [n]),
                             key=lambda x: -len(x[0]))
        self._patrones = [(re.compile(r"(?<![\w-])" + re.escape(a) + r"(?![\w-])"), n) for a, n in self._alias]
        self.usadas = set()

    def de(self, titulo):
        """La persona del mercado: primero la opción ("…: Marine Le Pen"), si no, la primera nombrada."""
        opcion = titulo.rpartition(": ")[2] if ": " in titulo else ""
        for alias, nombre in self._alias:
            if opcion == alias:
                return self._usar(nombre)
        # Si la opción es otra persona o un país ("¿A quién purgará Xi?: Dong Jun"), la foto de
        # quien aparece en la pregunta confundiría: solo se usa si la opción es una fecha o un plazo.
        if opcion and not (re.search(r"\d", opcion) or opcion.startswith(("Before", "No "))):
            return None
        mejor = None
        for patron, nombre in self._patrones:
            m = patron.search(titulo)
            if m and (mejor is None or m.start() < mejor[0]):
                mejor = (m.start(), nombre)
        return self._usar(mejor[1]) if mejor else None

    def _usar(self, nombre):
        self.usadas.add(nombre)
        return nombre, self.datos[nombre]

PAISES = {"us": "Estados Unidos", "br": "Brasil", "ve": "Venezuela", "co": "Colombia", "mx": "México",
          "ar": "Argentina", "cl": "Chile", "pe": "Perú", "ec": "Ecuador", "bo": "Bolivia", "cu": "Cuba",
          "ru": "Rusia", "ua": "Ucrania", "il": "Israel", "ir": "Irán", "ps": "Palestina", "lb": "Líbano",
          "sy": "Siria", "ye": "Yemen", "sa": "Arabia Saudita", "cn": "China", "tw": "Taiwán", "fr": "Francia",
          "de": "Alemania", "es": "España", "it": "Italia", "gb": "Reino Unido", "hu": "Hungría", "pl": "Polonia",
          "gl": "Groenlandia", "eu": "Unión Europea"}
