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

## Avisos por correo electrónico

Los avisos se envían por correo (Gmail):

| Aviso | Cuándo |
|---|---|
| 🆕 Nuevo curso MOC | En cuanto aparece en la web |
| ⏰ Abre pronto la inscripción | 3 días antes de que empiece el plazo |
| ✅ Inscripción abierta / inscrito automáticamente | El día que se abre el plazo |
| 📝 ¿Inscribir en este curso? | Cuando hace falta tu confirmación |
| ⚠️ Último aviso | 2 días antes de que cierre el plazo |

### Configuración

1. En la cuenta de Gmail que enviará los correos, activa la **verificación en dos pasos** y crea una
   **contraseña de aplicación** en <https://myaccount.google.com/apppasswords> (16 letras).
2. En GitHub: *Settings → Secrets and variables → Actions → New repository secret*, crea:
   - **`CORREO_REMITENTE`**: la dirección de Gmail que envía.
   - **`CORREO_PASSWORD`**: la contraseña de aplicación (no la contraseña normal).
   - **`CORREO_DESTINO`**: la dirección que recibe los avisos (varias, separadas por comas).
3. Prueba: *Actions → Buscar cursos MOC → Run workflow* marcando «Solo enviar un correo de prueba».
4. Opcional, en *Variables*: `AVISO_DIAS_ANTES` (por defecto 3) y `AVISO_DIAS_CIERRE` (por defecto 2).

## Inscripción automática

Cada MOC se valora frente al perfil de la cuenta de prueba ([`perfil.json`](perfil.json): profesor de
Secundaria de Matemáticas, tutor, sin cargo directivo) y su formato:

| Encaja | Cuándo | Qué hace el sistema |
|---|---|---|
| 🟢 **Sí** | Formato ✅ y la ficha no pone restricciones al perfil | **Se inscribe automáticamente** al abrir el plazo, avisa por correo y deja un issue cerrado (`inscripcion-automatica`) como registro |
| 🟡 **Posible** | Formato ⚠️, prioridad para otros colectivos o contenido de otra etapa | Pide confirmación con un issue (ver abajo) |
| 🔴 **No** | Formato ❌ o la ficha lo restringe (Cuerpo de Maestros, equipos directivos, coordinadores…) | Nada |

Para desactivar la inscripción automática: *Settings → Secrets and variables → Actions → Variables*,
crea `INSCRIPCION_AUTOMATICA` = `false` (entonces todo pasa por confirmación).

### Inscripción con confirmación

Con los secretos **`CAM_USUARIO`** y **`CAM_PASSWORD`** (cuenta de prueba), el sistema puede inscribirse en los MOC:

1. Cuando se abre el plazo de inscripción de un MOC, se crea un issue **«📝 ¿Inscribir en: …?»**
   (etiqueta `solicitud-inscripcion`) y llega un correo con el enlace a ese issue.
2. Para confirmar, **pon la etiqueta `inscribir`** al issue o **comenta `inscribir`** (solo cuenta el dueño del repositorio).
3. El workflow [`inscribir.yml`](.github/workflows/inscribir.yml) inicia sesión, pulsa «Inscribirme»,
   comprueba que el curso aparece en «Mis inscripciones», comenta el resultado, cierra el issue
   (etiqueta `inscrito`) y avisa por correo.

Si no te interesa un curso, cierra el issue sin más. Las bajas de un curso se hacen a mano en la web.
