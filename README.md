# Cursos MOC – Comunidad de Madrid

Vigila cada día el buscador de actividades formativas de
[innovacionyformacion.educa.madrid.org](https://innovacionyformacion.educa.madrid.org/actividades-formativas)
y avisa cuando aparecen **cursos MOC** (en línea, no tutorizados) en los que te puedes matricular.

👉 **Listado actual: [CURSOS.md](CURSOS.md)**

## Cómo funciona

1. El workflow [`buscar-cursos.yml`](.github/workflows/buscar-cursos.yml) se ejecuta todos los días a las 11:00
   (y a mano desde *Actions → Buscar cursos MOC → Run workflow*).
2. [`buscar_cursos.py`](buscar_cursos.py) recorre todas las páginas del buscador y se queda con las
   actividades cuyo título empieza por **MOC/MOOC** o cuya modalidad es **«En línea, no tutorizado»**.
   Después lee la ficha de cada una y comprueba el **formato**: solo valen los cursos que se superan
   viendo vídeos/recursos y haciendo cuestionarios.
   - ✅ **apto**: sin sesiones síncronas, foros obligatorios ni tareas.
   - ⚠️ **dudoso**: la ficha menciona tareas, «valoración de la organización» o no cita cuestionarios (se avisa igualmente, indicando el motivo).
   - ❌ **no apto**: webinars, videoconferencias, sesiones en directo, horas presenciales o participación obligatoria en foros. No se avisa ni se pide inscripción.
3. Compara con lo ya visto en [`data/cursos_moc.json`](data/cursos_moc.json) y actualiza [CURSOS.md](CURSOS.md).
4. Si hay novedades, abre un **issue** con la etiqueta `cursos-moc`:
   - 🆕 un curso MOC nuevo con inscripción abierta o próxima;
   - ✅ un curso ya conocido cuyo plazo de inscripción acaba de abrirse.

GitHub te envía un correo por cada issue nuevo (si sigues el repositorio: *Watch → All activity*
o al menos *Issues*).

## Avisos al móvil con ntfy

Además de los issues, envía notificaciones push con [ntfy](https://ntfy.sh):

| Aviso | Cuándo |
|---|---|
| 🆕 Nuevo curso MOC | En cuanto aparece en la web |
| ⏰ Abre pronto la inscripción | 3 días antes de que empiece el plazo |
| ✅ Inscripción abierta | El día que se abre el plazo |
| ⚠️ Último aviso | 2 días antes de que cierre el plazo |

Al pulsar la notificación se abre la ficha del curso.

### Configuración

1. Instala la app **ntfy** ([Android](https://play.google.com/store/apps/details?id=io.heckel.ntfy) /
   [iPhone](https://apps.apple.com/app/ntfy/id1625396347)).
2. Inventa un nombre de tema difícil de adivinar (cualquiera que lo conozca puede leer los avisos),
   por ejemplo `cursos-moc-7f3k9q2x`, y suscríbete a él en la app (servidor `ntfy.sh`).
3. En GitHub: *Settings → Secrets and variables → Actions → New repository secret*,
   nombre **`NTFY_TEMA`**, valor: el nombre del tema.
4. Opcional, en la pestaña *Variables* de esa misma página:
   `AVISO_DIAS_ANTES` (por defecto 3) y `AVISO_DIAS_CIERRE` (por defecto 2).

Si hay muchos avisos a la vez (por ejemplo, la primera ejecución) se envía una sola notificación resumen.

## Ejecutar en local

```bash
pip install -r requirements.txt
python buscar_cursos.py
```

## Ajustes

- **Hora**: se ejecuta a las 11:00 (hora de Madrid). Para cambiarla, edita el `cron` del workflow
  (en UTC: la hora de Madrid menos 1 y menos 2) y el valor de `HORA_EJECUCION`.
- **Qué cuenta como MOC**: función `es_moc()` en `buscar_cursos.py`.

## Inscripción automática

Cada MOC se valora frente al perfil de la cuenta de prueba ([`perfil.json`](perfil.json): profesor de
Secundaria de Matemáticas, tutor, sin cargo directivo) y su formato:

| Encaja | Cuándo | Qué hace el sistema |
|---|---|---|
| 🟢 **Sí** | Formato ✅ y la ficha no pone restricciones al perfil | **Se inscribe automáticamente** al abrir el plazo, avisa por ntfy y deja un issue cerrado (`inscripcion-automatica`) como registro |
| 🟡 **Posible** | Formato ⚠️, prioridad para otros colectivos o contenido de otra etapa | Pide confirmación con un issue (ver abajo) |
| 🔴 **No** | Formato ❌ o la ficha lo restringe (Cuerpo de Maestros, equipos directivos, coordinadores…) | Nada |

Para desactivar la inscripción automática: *Settings → Secrets and variables → Actions → Variables*,
crea `INSCRIPCION_AUTOMATICA` = `false` (entonces todo pasa por confirmación).

### Inscripción con confirmación

Con los secretos **`CAM_USUARIO`** y **`CAM_PASSWORD`** (cuenta de prueba), el sistema puede inscribirse en los MOC:

1. Cuando se abre el plazo de inscripción de un MOC, se crea un issue **«📝 ¿Inscribir en: …?»**
   (etiqueta `solicitud-inscripcion`) y llega un aviso por ntfy que abre ese issue.
2. Para confirmar, **pon la etiqueta `inscribir`** al issue o **comenta `inscribir`** (solo cuenta el dueño del repositorio).
3. El workflow [`inscribir.yml`](.github/workflows/inscribir.yml) inicia sesión, pulsa «Inscribirme»,
   comprueba que el curso aparece en «Mis inscripciones», comenta el resultado, cierra el issue
   (etiqueta `inscrito`) y avisa por ntfy.

Si no te interesa un curso, cierra el issue sin más. Las bajas de un curso se hacen a mano en la web.
