#!/usr/bin/env python3
"""Vigila los cursos MOC (en línea, no tutorizados) de la Comunidad de Madrid.

Recorre el buscador de actividades formativas de
https://innovacionyformacion.educa.madrid.org/actividades-formativas,
se queda con los cursos MOC y compara con lo guardado en data/cursos_moc.json.

Genera:
  - data/cursos_moc.json : estado de todos los MOC vistos.
  - CURSOS.md            : tabla con los MOC vigentes (abiertos / próximos).
  - nuevos.md            : solo si hay novedades (lo usa el workflow para abrir un issue).
"""

from __future__ import annotations

import json
import os
import re
import sys
import time
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import requests
from bs4 import BeautifulSoup

BASE = "https://innovacionyformacion.educa.madrid.org"
LISTADO = f"{BASE}/actividades-formativas"
RAIZ = Path(__file__).resolve().parent
ESTADO = RAIZ / "data" / "cursos_moc.json"
RESUMEN = RAIZ / "CURSOS.md"
NUEVOS = RAIZ / "nuevos.md"

MAX_PAGINAS = 100
PAUSA = 1.0  # segundos entre peticiones, para no sobrecargar la web
HOY = datetime.now(ZoneInfo("Europe/Madrid")).date()

RE_MOC = re.compile(r"^\s*MOO?C\b", re.IGNORECASE)
RE_FECHA = re.compile(r"(\d{2}/\d{2}/\d{4})")


def es_moc(curso: dict) -> bool:
    """Un curso es MOC si su título empieza por MOC/MOOC o es 'En línea, no tutorizado'."""
    return bool(RE_MOC.match(curso["titulo"])) or "no tutorizado" in curso["modalidad"].lower()


def parse_fecha(texto: str) -> date | None:
    try:
        return datetime.strptime(texto, "%d/%m/%Y").date()
    except (TypeError, ValueError):
        return None


def rango(texto: str) -> tuple[str | None, str | None]:
    fechas = RE_FECHA.findall(texto or "")
    return (fechas[0] if fechas else None, fechas[1] if len(fechas) > 1 else None)


def campo(li, nombre: str) -> str:
    div = li.select_one(f".views-field-{nombre} .field-content")
    return " ".join(div.get_text(" ", strip=True).split()) if div else ""


def estado_inscripcion(inicio: str | None, fin: str | None) -> str:
    ini, fi = parse_fecha(inicio), parse_fecha(fin)
    if ini and HOY < ini:
        return "próximo"
    if fi and HOY > fi:
        return "cerrado"
    if ini or fi:
        return "abierto"
    return "desconocido"


def descargar_pagina(sesion: requests.Session, pagina: int) -> str:
    for intento in range(4):
        try:
            r = sesion.get(LISTADO, params={"page": pagina}, timeout=60)
            r.raise_for_status()
            return r.text
        except requests.RequestException as e:
            if intento == 3:
                raise
            print(f"  error en página {pagina} ({e}); reintentando…", file=sys.stderr)
            time.sleep(5 * (intento + 1))
    raise RuntimeError("inalcanzable")


def parse_listado(html: str) -> list[dict]:
    soup = BeautifulSoup(html, "html.parser")
    cursos = []
    for li in soup.select("ul.listado-actividades-search > li.actividad"):
        enlace = li.select_one(".views-field-title a")
        if not enlace:
            continue
        url = requests.compat.urljoin(BASE, enlace["href"])
        ins_ini, ins_fin = rango(campo(li, "field-fecha-inicio-inscripcion"))
        cur_ini, cur_fin = rango(campo(li, "nothing-1"))
        cursos.append(
            {
                "url": url,
                "titulo": " ".join(enlace.get_text(" ", strip=True).split()),
                "modalidad": campo(li, "field-modalidad-formativa"),
                "inscripcion_inicio": ins_ini,
                "inscripcion_fin": ins_fin,
                "curso_inicio": cur_ini,
                "curso_fin": cur_fin,
                "area": campo(li, "field-areas-materias"),
                "centro": campo(li, "field-centro-organizador"),
                "descripcion": campo(li, "field-descripcion"),
            }
        )
    return cursos


def obtener_todos() -> list[dict]:
    sesion = requests.Session()
    sesion.headers["User-Agent"] = "Mozilla/5.0 (compatible; cursos-cam-monitor/1.0)"
    todos, vistos = [], set()
    for pagina in range(MAX_PAGINAS):
        cursos = parse_listado(descargar_pagina(sesion, pagina))
        nuevos = [c for c in cursos if c["url"] not in vistos]
        if not nuevos:
            break
        vistos.update(c["url"] for c in nuevos)
        todos.extend(nuevos)
        print(f"  página {pagina}: {len(cursos)} actividades")
        time.sleep(PAUSA)
    return todos


def fila(c: dict) -> str:
    ins = f"{c.get('inscripcion_inicio') or '?'} – {c.get('inscripcion_fin') or '?'}"
    dur = f"{c.get('curso_inicio') or '?'} – {c.get('curso_fin') or '?'}"
    titulo = c["titulo"].replace("|", "/")
    return f"| [{titulo}]({c['url']}) | {c['estado']} | {ins} | {dur} | {c.get('area', '').replace('|', '/')} |"


CABECERA = (
    "| Curso | Inscripción | Plazo de inscripción | Duración | Área |\n"
    "|---|---|---|---|---|"
)


def main() -> int:
    print(f"Consultando {LISTADO} ({HOY.isoformat()})…")
    actividades = obtener_todos()
    if not actividades:
        print("No se ha encontrado ninguna actividad: ¿ha cambiado la web?", file=sys.stderr)
        return 1

    estado = json.loads(ESTADO.read_text("utf-8")) if ESTADO.exists() else {}
    primera_vez = not estado
    ahora = datetime.now(ZoneInfo("Europe/Madrid")).isoformat(timespec="seconds")

    nuevos, abiertos_ahora = [], []
    actuales = set()
    for c in filter(es_moc, actividades):
        c["estado"] = estado_inscripcion(c["inscripcion_inicio"], c["inscripcion_fin"])
        previo = estado.get(c["url"])
        actuales.add(c["url"])
        if previo is None:
            c["visto_por_primera_vez"] = ahora
            c["aviso_apertura"] = c["estado"] == "abierto"
            if c["estado"] != "cerrado":
                nuevos.append(c)
        else:
            c["visto_por_primera_vez"] = previo.get("visto_por_primera_vez", ahora)
            c["aviso_apertura"] = previo.get("aviso_apertura", False)
            if c["estado"] == "abierto" and not c["aviso_apertura"]:
                c["aviso_apertura"] = True
                abiertos_ahora.append(c)
        c["ultima_vez_visto"] = ahora
        c["en_listado"] = True
        estado[c["url"]] = c

    for url, c in estado.items():
        if url not in actuales:
            c["en_listado"] = False
            c["estado"] = estado_inscripcion(c.get("inscripcion_inicio"), c.get("inscripcion_fin"))

    ESTADO.parent.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(dict(sorted(estado.items())), ensure_ascii=False, indent=2) + "\n", "utf-8")

    vigentes = sorted(
        (c for c in estado.values() if c["en_listado"] and c["estado"] in ("abierto", "próximo")),
        key=lambda c: (c["estado"] != "abierto", parse_fecha(c.get("inscripcion_inicio")) or date.max),
    )
    RESUMEN.write_text(
        "# Cursos MOC de la Comunidad de Madrid\n\n"
        f"Actualizado: {ahora} · {len(vigentes)} cursos MOC con inscripción abierta o próxima.\n\n"
        f"Fuente: <{LISTADO}>\n\n"
        + CABECERA + "\n" + "\n".join(fila(c) for c in vigentes) + "\n",
        "utf-8",
    )

    print(f"{len(actividades)} actividades, {len(actuales)} MOC, "
          f"{len(nuevos)} nuevos, {len(abiertos_ahora)} abren inscripción.")

    if nuevos or abiertos_ahora:
        partes = []
        if primera_vez:
            partes.append("Primera ejecución: estos son los cursos MOC disponibles ahora mismo.\n")
        if nuevos:
            partes.append("## 🆕 Cursos MOC nuevos\n\n" + CABECERA + "\n" + "\n".join(map(fila, nuevos)) + "\n")
        if abiertos_ahora:
            partes.append("## ✅ Ya se puede inscribir en\n\n" + CABECERA + "\n" + "\n".join(map(fila, abiertos_ahora)) + "\n")
        repo, rama = os.environ.get("GITHUB_REPOSITORY"), os.environ.get("GITHUB_REF_NAME", "main")
        if repo:
            partes.append(f"\nListado completo: https://github.com/{repo}/blob/{rama}/CURSOS.md")
        partes.append(f"\nFuente: {LISTADO}\n")
        NUEVOS.write_text("\n".join(partes), "utf-8")
    elif NUEVOS.exists():
        NUEVOS.unlink()
    return 0


if __name__ == "__main__":
    sys.exit(main())
