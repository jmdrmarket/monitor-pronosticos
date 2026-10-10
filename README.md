# Monitor de pronósticos

Sigue las probabilidades de los mercados de predicción sobre elecciones y conflictos internacionales. Cada 6 horas:

1. Descarga los mercados de política y asuntos internacionales de **Polymarket**, **Kalshi**, **Manifold** y, si lo configurás, **Metaculus**.
2. Se queda con los que coinciden con tus temas (`config.yaml`) y tienen volumen suficiente. Los deportes quedan afuera.
3. Guarda el historial en `data/snapshots/`, un archivo por mes (por ejemplo `2026-10.csv`).
4. Publica una página web (`docs/index.html`) con:
   - los mayores movimientos de las últimas 24 horas y 7 días;
   - cada tema con su probabilidad actual, el cambio y un minigráfico de 30 días;
   - las preguntas consolidadas entre plataformas, con alerta cuando difieren mucho;
   - sugerencias de mercados equivalentes para consolidar.
5. Publica un feed RSS (`docs/feed.xml`) con los cambios de 5 puntos o más en 24 horas, y un `docs/data.json` con todos los datos.

Todo corre gratis en GitHub. No hace falta tener una computadora prendida.

---

## Puesta en marcha (una sola vez, unos 15 minutos)

**1. Crear el repositorio.** Con una cuenta de GitHub, creá un repositorio nuevo (por ejemplo `monitor-pronosticos`). Para publicar la página gratis tiene que ser **público**. Con un repositorio privado, GitHub Pages requiere un plan pago.

**2. Subir los archivos.** En el repositorio, elegí *Add file → Upload files* y arrastrá todo el contenido de esta carpeta, incluida la carpeta oculta `.github`.
Si el navegador no deja subir carpetas ocultas, creá el archivo a mano: *Add file → Create new file*, nombre `.github/workflows/actualizar.yml`, y pegá su contenido.

**3. Dar permiso de escritura al robot.** Entrá a *Settings → Actions → General → Workflow permissions*, elegí **Read and write permissions** y guardá.

**4. Primera corrida.** En la pestaña *Actions*, abrí **Actualizar monitor** y tocá **Run workflow**. Tarda uno o dos minutos. Al terminar aparecen las carpetas `data/` y `docs/`.

**5. Publicar la página.** En *Settings → Pages*, en *Source* elegí **Deploy from a branch**, después la rama `main` y la carpeta `/docs`, y guardá. En unos minutos la página queda en
`https://TU-USUARIO.github.io/monitor-pronosticos/`.

A partir de ahí se actualiza sola cada 6 horas. Los cambios de 24 horas y 7 días aparecen a medida que se acumula historial: al día siguiente ya ves los de 24 horas, y a la semana, los de 7 días.

## Seguir los cambios sin entrar a la página

Agregá `https://TU-USUARIO.github.io/monitor-pronosticos/feed.xml` a cualquier lector RSS (Feedly, Inoreader, NetNewsWire). Cada vez que un mercado se mueve 5 puntos o más en 24 horas, llega una entrada nueva.

## Personalizar

Todo se ajusta en `config.yaml`, que se puede editar desde la web de GitHub (ícono del lápiz):

- **Temas**: agregá o quitá temas y palabras clave. Van en inglés, porque los mercados están en inglés.
- **Preguntas consolidadas**: en la página, cada mercado muestra su identificador (por ejemplo `kalshi:KXHOUSE-26-D`). La sección *Posibles equivalencias* sugiere pares parecidos con el bloque listo para copiar. Antes de agruparlos, revisá que pregunten exactamente lo mismo: misma fecha límite y mismas reglas de resolución.
- **Mínimos de volumen**: subilos si aparecen demasiados mercados chicos y ruidosos.
- **Rango visible**: `rango_visible: [0.02, 0.98]` oculta de las listas los mercados por debajo del 2% o por encima del 98%. En las preguntas de muchos candidatos cada nombre es un mercado aparte, y los que no tienen chances acumulan volumen igual: sin este filtro, la lista se llena de nombres en 0%.
- **Títulos en castellano**: salen de `traducciones.json`, un diccionario que se completa a mano (o con la tarea nocturna del análisis). Los títulos que todavía no están ahí se muestran en inglés. El original se ve al pasar el mouse sobre el título.
- **Frecuencia**: en `.github/workflows/actualizar.yml`, la línea `cron: "0 */6 * * *"` indica cada 6 horas. Para cada 3 horas, usá `"0 */3 * * *"`.

## Metaculus (opcional)

Metaculus pide un token gratuito. Creá una cuenta en metaculus.com y generá un token de API en la configuración de tu perfil. Después, en GitHub, entrá a *Settings → Secrets and variables → Actions → New repository secret*, con el nombre `METACULUS_TOKEN` y el token como valor. Sin el token, el monitor funciona igual con las otras tres plataformas.

## Probar en tu computadora

```bash
pip install -r requirements.txt
python tracker.py --demo   # página de ejemplo con datos ficticios en demo/index.html
python tracker.py          # corrida real, escribe en data/ y docs/
```

## Cómo leer los números

- **Probabilidad**: el precio del contrato "Sí". Por ejemplo, 41 centavos equivalen a un 41%. En Kalshi se usa el punto medio entre la mejor oferta de compra y de venta cuando la diferencia es chica; si no, el último precio operado.
- **pp**: puntos porcentuales. Pasar de 30% a 35% es +5 pp.
- **Consolidado**: promedio ponderado por el logaritmo del volumen. Manifold pesa menos porque usa dinero ficticio. Metaculus pesa según la cantidad de pronosticadores.
- **Advertencias**: en mercados chicos, pocas operaciones mueven mucho el precio. Además, los mercados tienden a sobrevalorar los resultados improbables. Antes de sacar conclusiones, leé las reglas de resolución de cada mercado.

## Si algo falla

Si una plataforma cambia su API, el monitor sigue funcionando con las demás y lo avisa al pie de la página ("Fuentes con error"). El detalle completo está en *Actions → la corrida → Descargar mercados y publicar*.
