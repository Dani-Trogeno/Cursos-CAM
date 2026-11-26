# Cursos MOC – Comunidad de Madrid

Vigila cada día el buscador de actividades formativas de
[innovacionyformacion.educa.madrid.org](https://innovacionyformacion.educa.madrid.org/actividades-formativas)
y avisa cuando aparecen **cursos MOC** (en línea, no tutorizados) en los que te puedes matricular.

👉 **Listado actual: [CURSOS.md](CURSOS.md)**

## Cómo funciona

1. El workflow [`buscar-cursos.yml`](.github/workflows/buscar-cursos.yml) se ejecuta todos los días
   (y a mano desde *Actions → Buscar cursos MOC → Run workflow*).
2. [`buscar_cursos.py`](buscar_cursos.py) recorre todas las páginas del buscador y se queda con las
   actividades cuyo título empieza por **MOC/MOOC** o cuya modalidad es **«En línea, no tutorizado»**.
3. Compara con lo ya visto en [`data/cursos_moc.json`](data/cursos_moc.json) y actualiza [CURSOS.md](CURSOS.md).
4. Si hay novedades, abre un **issue** con la etiqueta `cursos-moc`:
   - 🆕 un curso MOC nuevo con inscripción abierta o próxima;
   - ✅ un curso ya conocido cuyo plazo de inscripción acaba de abrirse.

GitHub te envía un correo por cada issue nuevo (si sigues el repositorio: *Watch → All activity*
o al menos *Issues*).

## Ejecutar en local

```bash
pip install -r requirements.txt
python buscar_cursos.py
```

## Ajustes

- **Hora**: cambia el `cron` del workflow (está en UTC).
- **Qué cuenta como MOC**: función `es_moc()` en `buscar_cursos.py`.
