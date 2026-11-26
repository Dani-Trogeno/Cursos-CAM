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
SOLICITUDES = RAIZ / "solicitudes.json"  # cursos para los que abrir un issue de confirmación
INSCRITOS = RAIZ / "inscritos.json"      # inscripciones automáticas hechas en esta ejecución

MAX_PAGINAS = 100
PAUSA = 1.0  # segundos entre peticiones, para no sobrecargar la web
HOY = datetime.now(ZoneInfo("Europe/Madrid")).date()

RE_MOC = re.compile(r"^\s*MOO?C\b", re.IGNORECASE)
RE_FECHA = re.compile(r"(\d{2}/\d{2}/\d{4})")


def es_moc(curso: dict) -> bool:
    """Un curso es MOC si su título empieza por MOC/MOOC o es 'En línea, no tutorizado'."""
    return bool(RE_MOC.match(curso["titulo"])) or "no tutorizado" in curso["modalidad"].lower()


# ------------------------------------------------- formato (solo asíncrono) ----
# Queremos MOC que se superen SOLO viendo vídeos/recursos y haciendo cuestionarios:
# sin sesiones síncronas (webinars, videoconferencias, presencialidad) ni foros/tareas evaluables.

RE_SINCRONO = re.compile(
    r"webinar|videoconferencia|en directo|\bs[ií]ncron|presencialidad telem|streaming|"
    r"sesi[oó]n(?:es)? (?:presencial|virtual|en l[ií]nea|online|telem[aá]tica|s[ií]ncrona)|"
    r"ponencias? a trav[eé]s", re.I)
RE_FOROS = re.compile(r"participaci[oó]n (?:activa )?en (?:los )?foros|foros? (?:es |son )?obligatori", re.I)
RE_TAREAS = re.compile(
    r"\btareas?\b(?! de autoevaluaci)|entrega de|proyecto final|trabajo final|valorad[oa] por la organizaci|"
    r"pr[aá]ctica evaluable|portafolio|r[uú]brica", re.I)
RE_CUESTIONARIOS = re.compile(r"cuestionario|autoevaluaci|\btest\b", re.I)


def analizar_ficha(html: str) -> dict:
    """Clasifica el formato del curso: 'apto', 'dudoso' o 'no apto' (con motivos)."""
    lineas, frases = frases_ficha(html)
    motivos, nivel = [], "apto"

    def frase(rx):
        return next((f for f in frases if rx.search(f)), None)

    try:
        i = lineas.index("Horas presenciales")
        presenciales = float(lineas[i + 1].replace(",", "."))
    except (ValueError, IndexError):
        presenciales = 0
    if presenciales > 0:
        motivos.append(f"Tiene {presenciales:g} horas presenciales/síncronas.")
        nivel = "no apto"
    if (f := frase(RE_SINCRONO)):
        motivos.append(f"Sesiones síncronas: «{f[:200]}»")
        nivel = "no apto"
    if (f := frase(RE_FOROS)):
        motivos.append(f"Participación obligatoria en foros: «{f[:200]}»")
        nivel = "no apto"
    if nivel != "no apto":
        if (f := frase(RE_TAREAS)):
            motivos.append(f"Puede requerir tareas o valoración adicional: «{f[:200]}»")
            nivel = "dudoso"
        if not frase(RE_CUESTIONARIOS):
            motivos.append("La ficha no menciona cuestionarios como forma de evaluación.")
            nivel = "dudoso"
    return {"formato": nivel, "formato_motivos": motivos}


ICONO_FORMATO = {"apto": "✅", "dudoso": "⚠️", "no apto": "❌"}
ICONO_ENCAJA = {"sí": "🟢 Sí", "posible": "🟡 Posible", "no": "🔴 No"}

PERFIL_FICHERO = RAIZ / "perfil.json"


def cargar_perfil() -> dict:
    try:
        return json.loads(PERFIL_FICHERO.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def frases_ficha(html: str) -> tuple[list[str], list[str]]:
    """Líneas y frases del contenido de la ficha, sin la sección de competencia digital."""
    main = BeautifulSoup(html, "html.parser").select_one("main")
    lineas = (main.get_text("\n", strip=True) if main else "").split("\n")
    if "Competencia digital docente" in lineas:
        ini = lineas.index("Competencia digital docente")
        fin = next((j for j in range(ini + 1, len(lineas))
                    if lineas[j] in ("Metodología", "Evaluación", "Observaciones")), len(lineas))
        lineas = lineas[:ini] + lineas[fin:]
    texto = " ".join(" ".join(lineas).split())
    return lineas, [f for f in re.split(r"(?<=[.;:])\s+", texto) if f]


def evaluar_perfil(html: str, c: dict, perfil: dict) -> dict:
    """¿Puede la cuenta (según perfil.json) inscribirse? -> 'sí' / 'posible' / 'no' con motivos."""
    if not perfil:
        return {"perfil": "posible", "perfil_motivos": ["No hay perfil configurado (perfil.json)."]}
    _, frases = frases_ficha(html)
    motivos_no, motivos_posible = [], []

    def busca(patron):
        """Devuelve el fragmento de la ficha (±90 caracteres) donde aparece el patrón."""
        rx = re.compile(patron, re.I)
        for f in frases:
            if (m := rx.search(f)):
                ini, fin = max(0, m.start() - 90), min(len(f), m.end() + 90)
                return ("…" if ini else "") + f[ini:fin] + ("…" if fin < len(f) else "")
        return None

    if perfil.get("cuerpo") != "maestros" and (f := busca(
            r"(destinad|dirigid|exclusiv)[^.]*cuerpo de maestros|funcionarios de carrera del cuerpo de maestros")):
        motivos_no.append(f"Solo para el Cuerpo de Maestros: «{f[:180]}»")
    if not perfil.get("equipo_directivo"):
        if (f := busca(r"acreditar[^.]*equipo directivo|dirigid[ao] a docentes[^.]*equipos? directivos?")):
            motivos_no.append(f"Solo para equipos directivos: «{f[:180]}»")
        elif (f := busca(r"prioridad[^.]*equipos? directivos?")):
            motivos_posible.append(f"Prioridad para equipos directivos: «{f[:180]}»")
    if not perfil.get("coordinador_programas") and (f := busca(r"dirigid[ao] a coordinadores")):
        motivos_no.append(f"Solo para coordinadores del programa: «{f[:180]}»")
    if not perfil.get("tutor") and (f := busca(r"cargo de tutor|destinad[oa] a[^.]*tutores")):
        motivos_posible.append(f"Pensado para tutores: «{f[:180]}»")
    etapas = " ".join(perfil.get("etapas", [])).lower()
    tema = f"{c.get('titulo', '')} {c.get('area', '')}"
    for etapa, rx in (("Infantil", r"educaci[oó]n infantil|etapa (?:de )?infantil"),
                      ("Primaria", r"educaci[oó]n primaria|etapa (?:de )?primaria")):
        if etapa.lower() not in etapas and re.search(rx, tema, re.I) and not re.search(r"secundaria|eso\b|bachiller", tema, re.I):
            motivos_posible.append(f"Contenido de Educación {etapa}, no de la etapa de la cuenta.")
    if "fp" not in etapas and re.search(r"formaci[oó]n profesional espec[ií]fica", c.get("area", ""), re.I):
        motivos_posible.append("Curso de Formación Profesional específica.")

    if motivos_no:
        return {"perfil": "no", "perfil_motivos": motivos_no + motivos_posible}
    if motivos_posible:
        return {"perfil": "posible", "perfil_motivos": motivos_posible}
    return {"perfil": "sí", "perfil_motivos": []}


def encaja(c: dict) -> str:
    formato, perfil = c.get("formato"), c.get("perfil")
    if formato == "no apto" or perfil == "no":
        return "no"
    if formato == "apto" and perfil == "sí":
        return "sí"
    return "posible"


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
    icono = ICONO_FORMATO.get(c.get("formato", ""), "")
    return f"| {icono} [{titulo}]({c['url']}) | {ICONO_ENCAJA.get(c.get('encaja'), '?')} | {c['estado']} | {ins} | {dur} | {c.get('area', '').replace('|', '/')} |"


CABECERA = (
    "| Curso | Encaja | Inscripción | Plazo de inscripción | Duración | Área |\n"
    "|---|---|---|---|---|---|"
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
    if c.get("formato") == "dudoso":
        lineas.append("⚠️ Revisa el formato: podría tener tareas o no cita cuestionarios.")
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
    perfil = cargar_perfil()
    sesion_fichas = requests.Session()
    sesion_fichas.headers["User-Agent"] = "Mozilla/5.0 (compatible; cursos-cam-monitor/1.0)"
    for c in filter(es_moc, actividades):
        c["estado"] = estado_inscripcion(c["inscripcion_inicio"], c["inscripcion_fin"])
        previo = estado.get(c["url"]) or {}
        for campo in ("formato", "formato_motivos", "perfil", "perfil_motivos"):
            if campo in previo:
                c[campo] = previo[campo]
        if "formato" not in c or "perfil" not in c:
            try:
                r = sesion_fichas.get(c["url"], timeout=60)
                r.raise_for_status()
                c.update(analizar_ficha(r.text))
                c.update(evaluar_perfil(r.text, c, perfil))
            except requests.RequestException as e:
                c.update({"formato": "dudoso", "formato_motivos": [f"No se pudo leer la ficha ({e})."],
                          "perfil": "posible", "perfil_motivos": []})
            time.sleep(PAUSA)
        c["encaja"] = encaja(c)
        if previo.get("inscripcion_auto"):
            c["inscripcion_auto"] = previo["inscripcion_auto"]
        actuales.add(c["url"])
        c["visto_por_primera_vez"] = previo.get("visto_por_primera_vez", ahora)
        if previo.get("solicitud_inscripcion"):
            c["solicitud_inscripcion"] = previo["solicitud_inscripcion"]
        avisos = set(previo.get("avisos", []))
        if previo.get("aviso_apertura"):  # compatibilidad con la versión anterior
            avisos.add("abierto")
        for tipo in tipos_de_aviso(c, es_nuevo=not previo):
            if tipo not in avisos and c["formato"] != "no apto":
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

    # Cursos con el plazo abierto para los que aún no se ha pedido confirmación de inscripción.
    solicitudes = [c for c in estado.values()
                   if c.get("en_listado") and c["estado"] == "abierto" and not c.get("solicitud_inscripcion")
                   and c.get("formato") != "no apto"]
    for c in solicitudes:
        c["solicitud_inscripcion"] = ahora

    # Los que encajan (formato ✅ + perfil ✅) se inscriben solos; el resto pide confirmación.
    inscritos = []
    auto_activa = os.environ.get("INSCRIPCION_AUTOMATICA", "true").lower() not in ("false", "0", "no")
    if auto_activa and os.environ.get("CAM_USUARIO") and os.environ.get("CAM_PASSWORD"):
        for c in [c for c in solicitudes if c["encaja"] == "sí"]:
            ok, mensaje = inscribir(c["url"])
            print(("Inscrito: " if ok else "Fallo al inscribir: ") + mensaje)
            c["inscripcion_auto"] = {"fecha": ahora, "ok": ok, "mensaje": mensaje}
            if NTFY_TEMA:
                enviar_ntfy("✅ Inscrito automáticamente" if ok else "❌ Falló la inscripción automática",
                            mensaje if ok else f"{mensaje}\nTe pido confirmación en GitHub.",
                            ["white_check_mark" if ok else "x"], 4, c["url"])
            if ok:
                inscritos.append(c)
                solicitudes.remove(c)
    if inscritos:
        INSCRITOS.write_text(json.dumps(
            [{"titulo": c["titulo"], "cuerpo": cuerpo_inscrito(c)} for c in inscritos],
            ensure_ascii=False, indent=2), "utf-8")
    elif INSCRITOS.exists():
        INSCRITOS.unlink()
    if solicitudes:
        SOLICITUDES.write_text(json.dumps(
            [{"url": c["url"], "titulo": c["titulo"], "cuerpo": cuerpo_solicitud(c)} for c in solicitudes],
            ensure_ascii=False, indent=2), "utf-8")
    elif SOLICITUDES.exists():
        SOLICITUDES.unlink()

    ESTADO.parent.mkdir(exist_ok=True)
    ESTADO.write_text(json.dumps(dict(sorted(estado.items())), ensure_ascii=False, indent=2) + "\n", "utf-8")

    orden = lambda c: (c["estado"] != "abierto", parse_fecha(c.get("inscripcion_inicio")) or date.max)
    en_plazo = [c for c in estado.values() if c["en_listado"] and c["estado"] in ("abierto", "próximo")]
    vigentes = sorted((c for c in en_plazo if c.get("formato") != "no apto"), key=orden)
    descartados = sorted((c for c in en_plazo if c.get("formato") == "no apto"), key=orden)
    RESUMEN.write_text(
        "# Cursos MOC de la Comunidad de Madrid\n\n"
        f"Actualizado: {ahora} · {len(vigentes)} cursos MOC con inscripción abierta o próxima.\n\n"
        "Solo se incluyen MOC que se superan viendo vídeos/recursos y haciendo cuestionarios "
        "(✅ confirmado en la ficha · ⚠️ revisar la ficha).\n\n"
        f"Fuente: <{LISTADO}>\n\n"
        + CABECERA + "\n" + "\n".join(fila(c) for c in vigentes) + "\n"
        + ("\n## Descartados por formato (sesiones síncronas, foros o tareas)\n\n"
           + "\n".join(f"- [{c['titulo']}]({c['url']}): {' '.join(c.get('formato_motivos', []))}"
                       for c in descartados) + "\n" if descartados else ""),
        "utf-8",
    )

    # Si un curso es nuevo, no mandamos además "pronto"/"abierto" en la misma ejecución.
    nuevos_urls = {c["url"] for t, c in eventos if t == "nuevo"}
    eventos = [(t, c) for t, c in eventos if t == "nuevo" or c["url"] not in nuevos_urls]
    por_tipo = {t: [c for tt, c in eventos if tt == t] for t in TIPOS}
    print(f"{len(actividades)} actividades, {len(actuales)} MOC; avisos: "
          + ", ".join(f"{t}={len(v)}" for t, v in por_tipo.items()))

    auto_ok = {c["url"] for c in inscritos}
    eventos = [(t, c) for t, c in eventos if not (t == "abierto" and c["url"] in auto_ok)]
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


# ----------------------------------------------------------- inscripción ----

def bloque_encaje(c: dict) -> str:
    lineas = [f"**¿Encaja con la cuenta de prueba?** {ICONO_ENCAJA.get(c.get('encaja'), '?')}",
              f"- Formato: {ICONO_FORMATO.get(c.get('formato'), '?')} {c.get('formato', '?')}"]
    lineas += [f"  - {m}" for m in c.get("formato_motivos", [])]
    lineas.append(f"- Perfil: {c.get('perfil', '?')}")
    lineas += [f"  - {m}" for m in c.get("perfil_motivos", [])]
    if c.get("inscripcion_auto") and not c["inscripcion_auto"]["ok"]:
        lineas.append(f"\n❌ La inscripción automática falló: {c['inscripcion_auto']['mensaje']}")
    return "\n".join(lineas) + "\n\n"


def cuerpo_inscrito(c: dict) -> str:
    return (
        f"## {c['titulo']}\n\n"
        f"- **Plazo de inscripción:** {c.get('inscripcion_inicio') or '?'} – {c.get('inscripcion_fin') or '?'}\n"
        f"- **Duración del curso:** {c.get('curso_inicio') or '?'} – {c.get('curso_fin') or '?'}\n"
        f"- **Ficha:** {c['url']}\n\n"
        + bloque_encaje(c)
        + f"✅ **Inscripción automática:** {c['inscripcion_auto']['mensaje']}\n\n"
        "Si no quieres este curso, date de baja en la web (Mi cuenta → Inscripciones).\n"
    )


def cuerpo_solicitud(c: dict) -> str:
    return (
        f"## {c['titulo']}\n\n"
        f"- **Plazo de inscripción:** {c.get('inscripcion_inicio') or '?'} – {c.get('inscripcion_fin') or '?'}\n"
        f"- **Duración del curso:** {c.get('curso_inicio') or '?'} – {c.get('curso_fin') or '?'}\n"
        f"- **Área:** {c.get('area') or '?'}\n"
        f"- **Centro organizador:** {c.get('centro') or '?'}\n"
        f"- **Ficha:** {c['url']}\n\n"
        f"> {c.get('descripcion', '')}\n\n"
        + bloque_encaje(c)
        + "### ¿Inscribir a la cuenta de prueba?\n\n"
        "Para confirmar, **pon la etiqueta `inscribir`** a este issue o **escribe un comentario "
        "que diga `inscribir`**. Si no te interesa, ciérralo sin más.\n\n"
        f"<!-- curso: {c['url']} -->\n"
    )


def inscripciones_actuales(sesion: requests.Session) -> str:
    """HTML + texto de «Mis inscripciones», en minúsculas, para buscar el curso."""
    r = sesion.get(f"{BASE}/usuario/inscripciones", timeout=60)
    soup = BeautifulSoup(r.text, "html.parser")
    texto = " ".join(soup.get_text(" ", strip=True).split())
    return (r.text + " " + texto).lower()


def inscribir(url_curso: str) -> tuple[bool, str]:
    """Inicia sesión y pulsa «Inscribirme» en el curso indicado."""
    if not url_curso.startswith(BASE + "/actividades/"):
        return False, f"URL de curso no válida: {url_curso}"
    sesion = requests.Session()
    sesion.headers["User-Agent"] = "Mozilla/5.0 (compatible; cursos-cam-monitor/1.0)"
    ok, mensaje = iniciar_sesion(sesion)
    if not ok:
        return False, mensaje
    try:
        r = sesion.get(url_curso, timeout=60)
        r.raise_for_status()
        ficha = BeautifulSoup(r.text, "html.parser")
        og = ficha.select_one('meta[property="og:title"]')
        if og and og.get("content"):
            titulo = og["content"].strip()
        elif ficha.title:
            titulo = ficha.title.get_text(strip=True).split("|")[0].strip()
        else:
            titulo = url_curso
        enlace = ficha.select_one('a[href*="inscripcion_af"]')
        if not enlace:
            return False, (f"La ficha de «{titulo}» no tiene botón de inscripción "
                           "(¿plazo cerrado o ya inscrito?).")
        url_form = requests.compat.urljoin(BASE, enlace["href"])
        m = re.search(r"courseId=(\d+)", url_form)
        course_id = m.group(1) if m else None

        r = sesion.get(url_form, timeout=60)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        form = soup.select_one("form#node-inscripcion-af-form") or soup.select_one("form[id*=inscripcion]")
        if not form:
            avisos = [" ".join(e.get_text(" ", strip=True).split()) for e in soup.select(".alert, .messages")]
            return False, f"No aparece el formulario de inscripción. {' / '.join(avisos)[:300]}"
        boton = form.select_one('button[name="op"], input[type=submit][name="op"]')
        if not boton:
            return False, "El formulario no tiene botón «Inscribirme»."
        datos = {i["name"]: i.get("value", "") for i in form.select("input[name]")
                 if i.get("type") not in ("submit", "button", "checkbox", "radio")}
        for i in form.select("input[type=checkbox][name][checked], input[type=radio][name][checked]"):
            datos[i["name"]] = i.get("value", "1")
        datos["op"] = boton.get("value") or boton.get_text(strip=True)
        r = sesion.post(requests.compat.urljoin(url_form, form.get("action") or url_form), data=datos, timeout=60)
        soup = BeautifulSoup(r.text, "html.parser")
        errores = [" ".join(e.get_text(" ", strip=True).split()).removeprefix("× ")
                   for e in soup.select(".alert-danger, .messages--error, .form-item--error-message")]
        estados = [" ".join(e.get_text(" ", strip=True).split()).removeprefix("× ")
                   for e in soup.select(".alert-success, .messages--status")]
        if errores:
            return False, f"La web devolvió un error al inscribir en «{titulo}»: {' / '.join(errores)[:400]}"

        lista = inscripciones_actuales(sesion)
        slug = url_curso.rstrip("/").rsplit("/", 1)[-1].lower()
        confirmado = (titulo.lower() in lista or slug in lista
                      or bool(course_id and course_id in lista))
        if not confirmado and any("inscrito correctamente" in e.lower() for e in estados):
            return True, f"Inscripción realizada en «{titulo}» (la web confirma: «Se ha inscrito correctamente»)."
        detalle = f" Mensaje de la web: {' / '.join(estados)[:300]}" if estados else ""
        if confirmado:
            return True, f"Inscripción realizada en «{titulo}» y confirmada en «Mis inscripciones».{detalle}"
        if estados:
            return True, (f"Inscripción enviada en «{titulo}», pero no la he encontrado en «Mis inscripciones»; "
                          f"revísalo en la web.{detalle}")
        return False, (f"Se pulsó «Inscribirme» en «{titulo}» (HTTP {r.status_code}) pero no hay confirmación. "
                       "Revisa «Mis inscripciones» en la web.")
    finally:
        sesion.get(f"{BASE}/user/logout", timeout=60)


def inscribir_desde_issue() -> int:
    """Lo usa el workflow de confirmación: lee la URL del curso del cuerpo del issue."""
    cuerpo = os.environ.get("ISSUE_BODY", "")
    m = re.search(r"<!-- curso: (\S+) -->", cuerpo)
    if not m:
        print("No encuentro la URL del curso en el issue.")
        Path("resultado.md").write_text("❌ No encuentro la URL del curso en este issue.", "utf-8")
        return 1
    ok, mensaje = inscribir(m.group(1))
    print(mensaje)
    Path("resultado.md").write_text(("✅ " if ok else "❌ ") + mensaje + "\n", "utf-8")
    if NTFY_TEMA:
        enviar_ntfy("✅ Inscrito en curso MOC" if ok else "❌ No se pudo inscribir", mensaje,
                    ["white_check_mark" if ok else "x"], 4, os.environ.get("ISSUE_URL") or m.group(1))
    return 0 if ok else 1


def avisar_solicitud() -> int:
    """Tras crear el issue de confirmación, avisa por ntfy con enlace al issue."""
    titulo, url = sys.argv[sys.argv.index("--avisar-solicitud") + 1:][:2]
    if NTFY_TEMA:
        enviar_ntfy("📝 ¿Inscribir en este curso MOC?",
                    f"{titulo}\n\nAbre el issue y ponle la etiqueta «inscribir» o comenta «inscribir».",
                    ["memo"], 4, url)
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


def describir_formulario(html: str) -> None:
    """Describe la estructura de los formularios de una página SIN mostrar valores."""
    soup = BeautifulSoup(html, "html.parser")
    for form in soup.select("form"):
        print(f"\n=== form id={form.get('id')} action={form.get('action')} method={form.get('method')}")
        for el in form.select("input, select, textarea, button"):
            nombre, tipo = el.get("name"), el.get("type", el.name)
            if tipo == "hidden" and nombre in ("form_build_id", "form_token"):
                continue
            etiqueta = ""
            if el.get("id"):
                lab = form.select_one(f'label[for="{el["id"]}"]')
                etiqueta = " ".join(lab.get_text(" ", strip=True).split()) if lab else ""
            extra = ""
            if el.name == "select":
                ops = [" ".join(o.get_text(" ", strip=True).split()) for o in el.select("option")]
                extra = f" opciones={ops[:15]}"
            if tipo in ("radio", "checkbox"):
                extra = f" valor_opcion={el.get('value')!r}"
            if el.name == "button" or tipo == "submit":
                extra = f" texto={el.get_text(' ', strip=True) or el.get('value')!r}"
            req = " OBLIGATORIO" if el.has_attr("required") else ""
            dis = " DESACTIVADO" if el.has_attr("disabled") else ""
            print(f"  - {el.name}[{tipo}] name={nombre!r} etiqueta={etiqueta!r}{extra}{req}{dis}")
    main = soup.select_one("main") or soup
    textos = [" ".join(t.get_text(" ", strip=True).split()) for t in main.select("h1, h2, h3, legend, .alert, .messages, .description")]
    print("\nTextos de la página:", [t[:150] for t in textos if t][:30])
    print("Scripts/ajax:", sorted({s.get("src", "")[:80] for s in soup.select("script[src]") if "ajax" in s.get("src", "") or "inscrip" in s.get("src", "")}))


def diagnostico_inscripcion() -> int:
    sesion = requests.Session()
    sesion.headers["User-Agent"] = "Mozilla/5.0 (compatible; cursos-cam-monitor/1.0)"
    ok, mensaje = iniciar_sesion(sesion)
    print(mensaje)
    if not ok:
        return 1
    try:
        estado = json.loads(ESTADO.read_text("utf-8")) if ESTADO.exists() else {}
        for c in estado.values():
            if c.get("en_listado") and c.get("estado") == "abierto":
                r = sesion.get(c["url"], timeout=60)
                enlace = BeautifulSoup(r.text, "html.parser").select_one('a[href*="inscripcion_af"]')
                print(f"Curso: {c['titulo']} · enlace de inscripción: {enlace['href'] if enlace else 'NO'}")
                if enlace:
                    r = sesion.get(requests.compat.urljoin(BASE, enlace["href"]), timeout=60)
                    print(f"HTTP {r.status_code} {r.url.replace(BASE, '')}")
                    describir_formulario(r.text)
                    break
        r = sesion.get(f"{BASE}/user", timeout=60)
        print(f"\n--- Página de usuario: {r.url.replace(BASE, '')}")
        enlaces = sorted({a["href"] for a in BeautifulSoup(r.text, "html.parser").select("main a[href]")
                          if a["href"].startswith("/") and "logout" not in a["href"]})
        print("Enlaces:", enlaces[:40])
    finally:
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
    if "--inscribir-desde-issue" in sys.argv:
        sys.exit(inscribir_desde_issue())
    if "--avisar-solicitud" in sys.argv:
        sys.exit(avisar_solicitud())
    if "--diagnostico" in sys.argv:
        sys.exit(diagnostico_inscripcion())
    if "--login" in sys.argv:
        sys.exit(prueba_login())
    sys.exit(prueba_ntfy() if "--prueba" in sys.argv else main())
