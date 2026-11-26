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


# ---------------------------------------------------------------- avisos ----

DIAS_ANTES = int(os.environ.get("AVISO_DIAS_ANTES", "3"))    # aviso "abre pronto"
DIAS_CIERRE = int(os.environ.get("AVISO_DIAS_CIERRE", "2"))  # aviso "último aviso"
NTFY_SERVIDOR = os.environ.get("NTFY_SERVIDOR", "https://ntfy.sh").rstrip("/")
NTFY_TEMA = os.environ.get("NTFY_TEMA", "").strip()
MAX_AVISOS_SUELTOS = 6  # con más avisos que esto, se manda un único resumen

TIPOS = ("nuevo", "pronto", "abierto", "cierre")
AVISO = {
    "nuevo": ("🆕 Nuevo curso MOC", "new", 4),
    "pronto": ("⏰ Abre pronto la inscripción", "alarm_clock", 3),
    "abierto": ("✅ Inscripción abierta", "white_check_mark", 4),
    "cierre": ("⚠️ Último aviso: cierra la inscripción", "warning", 5),
}


def dias_hasta(texto: str | None) -> int | None:
    f = parse_fecha(texto)
    return (f - HOY).days if f else None


def tipos_de_aviso(c: dict, es_nuevo: bool) -> list[str]:
    """Avisos que le tocan hoy a un curso según sus fechas."""
    tipos = []
    if es_nuevo and c["estado"] != "cerrado":
        tipos.append("nuevo")
    hasta_inicio = dias_hasta(c.get("inscripcion_inicio"))
    if c["estado"] == "próximo" and hasta_inicio is not None and hasta_inicio <= DIAS_ANTES:
        tipos.append("pronto")
    if c["estado"] == "abierto":
        tipos.append("abierto")
        hasta_fin = dias_hasta(c.get("inscripcion_fin"))
        if hasta_fin is not None and hasta_fin <= DIAS_CIERRE:
            tipos.append("cierre")
    return tipos


def texto_curso(c: dict) -> str:
    lineas = [c["titulo"]]
    ini, fin = c.get("inscripcion_inicio"), c.get("inscripcion_fin")
    if ini or fin:
        lineas.append(f"Inscripción: {ini or '?'} – {fin or '?'}")
        d = dias_hasta(ini)
        if c["estado"] == "próximo" and d is not None:
            lineas.append("Abre mañana" if d == 1 else f"Abre en {d} días")
        d = dias_hasta(fin)
        if c["estado"] == "abierto" and d is not None:
            lineas.append("¡Cierra hoy!" if d == 0 else "Cierra mañana" if d == 1 else f"Cierra en {d} días")
    if c.get("curso_inicio"):
        lineas.append(f"Curso: {c['curso_inicio']} – {c.get('curso_fin') or '?'}")
    return "\n".join(lineas)


def enviar_ntfy(titulo: str, mensaje: str, tags: list[str], prioridad: int, url: str) -> bool:
    datos = {"topic": NTFY_TEMA, "title": titulo, "message": mensaje,
             "tags": tags, "priority": prioridad, "click": url}
    for intento in range(3):
        try:
            requests.post(NTFY_SERVIDOR, json=datos, timeout=30).raise_for_status()
            return True
        except requests.RequestException as e:
            print(f"  ntfy falló ({e})", file=sys.stderr)
            time.sleep(5 * (intento + 1))
    return False


def avisar_ntfy(eventos: list[tuple[str, dict]], primera_vez: bool) -> None:
    if not eventos:
        return
    if not NTFY_TEMA:
        print("NTFY_TEMA no configurado: no se envían notificaciones.")
        return
    if primera_vez or len(eventos) > MAX_AVISOS_SUELTOS:
        cuerpo = "\n\n".join(f"{AVISO[t][0]}\n{texto_curso(c)}" for t, c in eventos)
        titulo = "Cursos MOC disponibles" if primera_vez else f"{len(eventos)} avisos de cursos MOC"
        enviar_ntfy(titulo, cuerpo[:3900], ["books"], 4, LISTADO)
    else:
        for t, c in eventos:
            titulo, tag, prioridad = AVISO[t]
            enviar_ntfy(titulo, texto_curso(c), [tag], prioridad, c["url"])
    print(f"Enviados {len(eventos)} avisos a ntfy.")


def toca_ejecutar() -> bool:
    """Con HORA_EJECUCION definida (ejecución programada), decide si toca hoy.

    GitHub programa en UTC, así que el workflow se lanza a dos horas UTC para
    cubrir horario de invierno y de verano; aquí se descarta la que no toca.
    """
    hora = os.environ.get("HORA_EJECUCION", "").strip()
    if not hora:
        return True
    ahora = datetime.now(ZoneInfo("Europe/Madrid"))
    if ahora.hour < int(hora):
        print(f"Son las {ahora:%H:%M} en Madrid: todavía no son las {hora}:00, no se ejecuta.")
        return False
    if RESUMEN.exists():
        m = re.search(r"Actualizado: (\d{4}-\d{2}-\d{2})T(\d{2})", RESUMEN.read_text("utf-8"))
        if m and m.group(1) == ahora.date().isoformat() and int(m.group(2)) >= int(hora):
            print("Ya se ha ejecutado hoy a partir de la hora programada.")
            return False
    return True


def main() -> int:
    if not toca_ejecutar():
        if NUEVOS.exists():
            NUEVOS.unlink()
        return 0
    print(f"Consultando {LISTADO} ({HOY.isoformat()})…")
    actividades = obtener_todos()
    if not actividades:
        print("No se ha encontrado ninguna actividad: ¿ha cambiado la web?", file=sys.stderr)
        return 1

    estado = json.loads(ESTADO.read_text("utf-8")) if ESTADO.exists() else {}
    primera_vez = not estado
    ahora = datetime.now(ZoneInfo("Europe/Madrid")).isoformat(timespec="seconds")

    eventos: list[tuple[str, dict]] = []  # (tipo, curso)
    actuales = set()
    for c in filter(es_moc, actividades):
        c["estado"] = estado_inscripcion(c["inscripcion_inicio"], c["inscripcion_fin"])
        previo = estado.get(c["url"]) or {}
        actuales.add(c["url"])
        c["visto_por_primera_vez"] = previo.get("visto_por_primera_vez", ahora)
        avisos = set(previo.get("avisos", []))
        if previo.get("aviso_apertura"):  # compatibilidad con la versión anterior
            avisos.add("abierto")
        for tipo in tipos_de_aviso(c, es_nuevo=not previo):
            if tipo not in avisos:
                eventos.append((tipo, c))
        # Un curso nuevo ya cubre los avisos de "pronto"/"abierto" que le correspondan hoy.
        avisos.update(tipos_de_aviso(c, es_nuevo=not previo))
        c["avisos"] = sorted(avisos)
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

    # Si un curso es nuevo, no mandamos además "pronto"/"abierto" en la misma ejecución.
    nuevos_urls = {c["url"] for t, c in eventos if t == "nuevo"}
    eventos = [(t, c) for t, c in eventos if t == "nuevo" or c["url"] not in nuevos_urls]
    por_tipo = {t: [c for tt, c in eventos if tt == t] for t in TIPOS}
    print(f"{len(actividades)} actividades, {len(actuales)} MOC; avisos: "
          + ", ".join(f"{t}={len(v)}" for t, v in por_tipo.items()))

    avisar_ntfy(eventos, primera_vez)

    nuevos, abiertos_ahora = por_tipo["nuevo"], por_tipo["abierto"]
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


# ----------------------------------------------------------------- login ----

def iniciar_sesion(sesion: requests.Session) -> tuple[bool, str]:
    """Inicia sesión en la web con CAM_USUARIO / CAM_PASSWORD (secretos de GitHub)."""
    usuario, clave = os.environ.get("CAM_USUARIO", ""), os.environ.get("CAM_PASSWORD", "")
    if not usuario or not clave:
        return False, "Faltan los secretos CAM_USUARIO y/o CAM_PASSWORD."
    r = sesion.get(f"{BASE}/user/login", timeout=60)
    r.raise_for_status()
    form = BeautifulSoup(r.text, "html.parser").select_one("form#user-login-form")
    if not form:
        return False, "No se encuentra el formulario de login (¿ha cambiado la web?)."
    datos = {i["name"]: i.get("value", "") for i in form.select("input[type=hidden][name]")}
    datos.update({"name": usuario, "pass": clave, "op": "Iniciar sesión"})
    r = sesion.post(f"{BASE}/user/login", data=datos, timeout=60)
    soup = BeautifulSoup(r.text, "html.parser")
    if soup.select_one('a[href*="/user/logout"]') or "user/login" not in r.url and "/user/" in r.url:
        return True, "Sesión iniciada correctamente."
    errores = [" ".join(e.get_text(" ", strip=True).split()).removeprefix("× ")
               for e in soup.select(".form-item--error-message, .messages--error")]
    motivo = " / ".join(errores) or "motivo desconocido"
    return False, f"No se pudo iniciar sesión: {motivo[:300]}"


def prueba_login() -> int:
    sesion = requests.Session()
    sesion.headers["User-Agent"] = "Mozilla/5.0 (compatible; cursos-cam-monitor/1.0)"
    ok, mensaje = iniciar_sesion(sesion)
    print(mensaje)
    if not ok:
        return 1
    # Solo lectura: abre (sin enviar) el formulario de inscripción de un curso MOC vigente.
    estado = json.loads(ESTADO.read_text("utf-8")) if ESTADO.exists() else {}
    for c in estado.values():
        if not c.get("en_listado") or c.get("estado") not in ("abierto", "próximo"):
            continue
        r = sesion.get(c["url"], timeout=60)
        enlace = BeautifulSoup(r.text, "html.parser").select_one('a[href*="inscripcion_af"]')
        if not enlace:
            continue
        r = sesion.get(requests.compat.urljoin(BASE, enlace["href"]), timeout=60)
        soup = BeautifulSoup(r.text, "html.parser")
        titulo = soup.title.get_text(strip=True) if soup.title else "?"
        formulario = soup.select_one("form[id*=inscripcion]")
        print(f"Formulario de inscripción de «{c['titulo']}»: HTTP {r.status_code}, "
              f"página «{titulo}», formulario {'encontrado' if formulario else 'NO encontrado'}.")
        if formulario:
            campos = sorted({i.get("name") for i in formulario.select("input,select,textarea") if i.get("name")})
            print("Campos del formulario:", ", ".join(campos))
        break
    sesion.get(f"{BASE}/user/logout", timeout=60)
    print("Sesión cerrada.")
    return 0


def prueba_ntfy() -> int:
    if not NTFY_TEMA:
        print("Falta el secreto NTFY_TEMA.", file=sys.stderr)
        return 1
    print(f"Enviando prueba a {NTFY_SERVIDOR}/{NTFY_TEMA[:3]}… (tema de {len(NTFY_TEMA)} caracteres)")
    ok = enviar_ntfy("🔔 Prueba de Cursos MOC", "Si ves esto, los avisos funcionan.", ["bell"], 4, LISTADO)
    print("Prueba enviada correctamente." if ok else "No se pudo enviar la prueba.")
    return 0 if ok else 1


if __name__ == "__main__":
    if "--login" in sys.argv:
        sys.exit(prueba_login())
    sys.exit(prueba_ntfy() if "--prueba" in sys.argv else main())
