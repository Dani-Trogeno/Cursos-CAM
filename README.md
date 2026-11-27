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

## Correo con botón «Inscribirse»

Cada MOC se valora frente al perfil de la cuenta de prueba ([`perfil.json`](perfil.json): profesor de
Secundaria de Matemáticas, tutor, sin cargo directivo) y su formato (🟢 Sí · 🟡 Posible · 🔴 No).

1. **7 días antes** de que abra el plazo de inscripción llega un correo con asunto
   **`[Cursos MOC] <nombre del curso>`**: fechas, compatibilidad con el perfil (formato y perfil, con
   los motivos) y un botón **«Inscribirse»**. (Si un curso aparece con menos de 7 días o ya abierto,
   el correo se envía en cuanto se detecta.)
2. El botón abre una **respuesta ya preparada** (`INSCRIBIR <código> - <curso>`): solo hay que enviarla.
3. Cada día a las 11:00 el sistema lee el buzón de Gmail por IMAP. Si encuentra la respuesta
   (enviada desde `CORREO_DESTINO` y con el código correcto), confirma por correo que la inscripción
   queda programada.
4. **El día que abre el plazo** inscribe a la cuenta y envía el resultado por correo. Si falla, lo
   reintenta cada día mientras el plazo siga abierto.

No se inscribe en nada sin pulsar «Inscribirse». Los MOC con formato ❌ (sesiones síncronas o foros)
no generan correo.

### Configuración

1. En la cuenta de Gmail que envía (y recibe las respuestas): **verificación en dos pasos** y una
   **contraseña de aplicación** en <https://myaccount.google.com/apppasswords>.
2. Secretos de GitHub (*Settings → Secrets and variables → Actions*):
   - **`CORREO_REMITENTE`**: la dirección de Gmail que envía y en la que se leen las respuestas.
   - **`CORREO_PASSWORD`**: la contraseña de aplicación.
   - **`CORREO_DESTINO`**: quién recibe los correos (y desde dónde se aceptan las respuestas).
   - **`CAM_USUARIO`** / **`CAM_PASSWORD`**: la cuenta de prueba de la web.
3. Opcional, variable **`AVISO_DIAS_PROPUESTA`** (por defecto 7).
4. Prueba: *Actions → Buscar cursos MOC → Run workflow* marcando «Solo enviar un correo de prueba».
