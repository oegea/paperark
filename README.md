# PaperArk — backups en papel para décadas

Codifica cualquier fichero en un PDF imprimible en blanco y negro y lo recupera
a partir de escaneos, aunque las hojas estén amarillentas, giradas, manchadas,
con esquinas arrancadas o falten hojas enteras.

* **Densidad (formato 2)**: la hoja se divide en **4 bloques** que se fotografían
  de cerca, uno a uno, guiados por la pantalla: **~99 KB por A4 con el móvil**
  (celda de 0,21 mm, casi 3 veces la hoja entera del formato 1, 35 KB). Con una
  buena impresora láser, 157 KB (0,17 mm) o incluso 282 KB (0,13 mm): con tóner
  compatible las celdas más finas acumulan defectos de impresión. Con escáner, ~300 KB por hoja. Ver [la investigación](#por-qué-cabe-tanto).
* **Lector con ecualización y LDPC**: el lector deshace el emborronado de la
  cámara entre celdas vecinas (ecualizador 2D ajustado con las celdas conocidas
  y sus propias decisiones) y da una fiabilidad a cada bit; los datos llevan
  códigos LDPC (los del 5G y la TV por satélite) con decodificación blanda.
* **Compresión**: se prueban zstd, xz, brotli y bzip2 (todos estándar y
  documentados) y se queda el más pequeño.
* **Robustez por bloque**: cada palabra LDPC se reparte por todo el bloque; manchas,
  esquinas arrancadas, dobleces y tóner desvaído se corrigen (ver BENCH.md). La
  cabecera va 4 veces con Reed-Solomon RS(255,64).
* **Robustez entre hojas**: bloques de paridad RS entre bloques: con M bloques de
  paridad por grupo se recuperan *cualesquiera* M bloques perdidos (con 4 por
  grupo, una hoja entera).
* **Compatibilidad**: el formato 1 (hoja completa, Reed-Solomon) se sigue leyendo,
  ahora también con el ecualizador como respaldo.
* **Integridad**: SHA-256 del fichero y de cada hoja en la cabecera (x4 copias,
  RS(255,64)) y en texto legible; verificación al final.
* **Orden**: los escaneos se aceptan en cualquier orden, se ignoran duplicados
  y se listan las hojas que faltan. Modo estricto opcional (exige orden).
* **PDF listo para guardar**: portada con el nombre del fichero, tamaño, fecha,
  huella SHA-256, lista de hojas y un **QR con los metadatos** que abre la
  pantalla de recuperación en el móvil; cabecera legible en cada hoja; página
  de "cómo funciona" y especificación completa del formato al final, para poder
  reimplementar el lector dentro de décadas.
* **Recuperación desde el móvil**: la pantalla de recuperación funciona en el
  teléfono (fotografiar hoja a hoja), en el escritorio (escaneos o PDF) o en
  ambos a la vez compartiendo sesión mediante un QR; muestra en vivo qué hojas
  hay, cuáles faltan y verifica el resultado.

Ver [DESIGN.md](DESIGN.md) para el diseño y [BENCH.md](BENCH.md) para los resultados empíricos (formato 2: 65 de 66 fotos simuladas leídas, incluidas manchas, esquinas arrancadas y desenfoque; formato 1: 89 de 100 escenarios de escáner íntegros y el resto recuperables con paridad).

* **Todo en el navegador si quieres**: tanto la generación del PDF como la
  recuperación pueden ejecutarse en tu propio navegador con Python sobre
  WebAssembly (Pyodide + OpenCV), con el mismo código que el servidor: el
  fichero y las fotos no salen de tu dispositivo, hay barra de progreso y no
  dependen de límites de tiempo del servidor (imprescindible en Vercel). Los
  PDF de escáner se rasterizan con pdf.js. Descarga un motor de 20–35 MB la
  primera vez; después queda en caché.
* **Español e inglés**: interfaz web en ambos idiomas (conmutador ES/EN en la
  cabecera, se recuerda), mensajes del servidor y PDF en el idioma elegido
  (`--lang en` en la CLI o el selector "Idioma del PDF" del asistente).

## Por qué cabe tanto

Resumen de la investigación (detalle en DESIGN.md §9):

1. **El límite no es la compresión, es el canal óptico.** Tras comprimir bien, los
   bytes son casi aleatorios: no hay "patrones" que el papel pueda aprovechar
   (teorema de separación de Shannon). La ganancia está en cuántos bits por mm²
   sobreviven a imprimir y fotografiar.
2. **El detector era el cuello de botella.** Con celdas de 2-4 píxeles de cámara, el
   desenfoque mezcla cada celda con sus vecinas y mirar el centro de la celda da
   un 12-25 % de bits erróneos, aunque la información sigue ahí. Un ecualizador
   2D lo baja por debajo del 1 %.
3. **Decodificación blanda.** LDPC con fiabilidades por bit aguanta a tasa 0,85
   lo que Reed-Solomon con decisión dura solo aguanta a tasa 0,58.
4. **Acercar la cámara.** Los bits que caben en una foto son más o menos fijos (unos
   12 MP a ~3-4 px por celda): partir la hoja en 4 bloques y hacer una foto de
   cada uno multiplica por ~4 lo que cabe en el papel.
5. **Compresores extremos (paq8px, cmix).** Ganan un 30-45 % en texto, pero usan
   coma flotante, cambian de formato entre versiones, necesitan GB de RAM y no
   funcionan en el navegador: inaceptable para leer un backup dentro de décadas.
   Se usan compresores estándar y se elige el mejor.

## Instalación

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Uso

```bash
# fichero -> PDF (4 bloques por hoja para el móvil, 4 bloques de paridad = 1 hoja)
.venv/bin/python -m paperark encode documento.zip -o documento.pdf --parity 4

# escáner de 600 dpi: un bloque por hoja, celda de 0,13 mm (~300 KB/hoja)
.venv/bin/python -m paperark encode documento.zip -o documento.pdf --blocks 1 --cell 3 --parity 2

# formato 1 (hoja completa, Reed-Solomon)
.venv/bin/python -m paperark encode documento.zip -o documento.pdf --blocks 0 --cell 8 --parity 2

# escaneos (PNG/JPG/TIFF/PDF, en cualquier orden) -> fichero
.venv/bin/python -m paperark decode escaneos/*.png -o restaurado/

# cuántas hojas ocupará
.venv/bin/python -m paperark estimate 5000000 --blocks 4 --cell 5 --ecc M --parity 8

# interfaz web (escritorio y móvil en la misma red; imprime la URL para el móvil)
.venv/bin/python -m paperark serve

# la URL impresa en la portada y en el QR: opción --url o variable PAPERARK_BASE_URL
.venv/bin/python -m paperark encode documento.zip -o documento.pdf --parity 4 --url https://mi-servidor
```

Impresión: láser, **tamaño real (100 %)**, papel sin ácido. Móvil: una foto de
cerca por bloque, que llene la pantalla, con buena luz. Escáner: 600 dpi en gris
(una página escaneada entrega todos sus bloques).

> Las cifras de densidad del formato 2 salen del simulador de fotos de móvil.
> Antes de confiar un archivo importante a un perfil denso, imprime una hoja y
> compruébala con tu móvil en la pantalla de recuperación (modo comprobación).

## Tests y banco de pruebas

```bash
.venv/bin/python -m pytest -q
.venv/bin/python bench.py          # formato 1: impresión+envejecimiento+escaneo+daños
.venv/bin/python bench_v2.py       # formato 2 frente al 1 con fotos de móvil simuladas
```

## Publicar

PaperArk es una aplicación Python (FastAPI) con trabajo de CPU en el servidor (codificación y decodificación), así que no vale un hosting estático: hace falta un servidor pequeño. Con 1 CPU y 1 GB de RAM va sobrado para uso personal o de un equipo.

### Con Docker (recomendado)

```bash
docker build -t paperark .
docker run -d --name paperark -p 8000:8000 \
  -e PAPERARK_BASE_URL=https://paperark.tu-dominio.com \
  --restart unless-stopped paperark
```

`PAPERARK_BASE_URL` es la URL pública que se imprime en las portadas y dentro de los QR: **fíjala antes de imprimir nada que quieras conservar**, porque las hojas la llevan grabada. Pon delante un proxy con HTTPS (Caddy, nginx, Traefik) o despliega la imagen en Fly.io, Railway, Render o cualquier VPS.

Ejemplo con Caddy (HTTPS automático):

```
paperark.tu-dominio.com {
    reverse_proxy localhost:8000
}
```

### Vercel (preset FastAPI)

Funciona: generación y recuperación se hacen en el navegador (Pyodide); la función solo sirve la web y, si se desmarca la opción local, decodifica en el servidor (sin pool de procesos). El repositorio ya incluye `main.py` (expone `app`) y `vercel.json` (60 s por petición, 1 GB). En el proyecto de Vercel: preset **FastAPI**, variable de entorno `PAPERARK_BASE_URL=https://tu-proyecto.vercel.app` (o tu dominio). Al no haber procesos persistentes, la sesión de recuperación puede perderse si las peticiones caen en instancias distintas (con poco tráfico no suele ocurrir); si desmarcas la generación en el navegador, el servidor genera en una sola petición con el límite de 60 s. Para uso serio, un contenedor persistente (Fly.io, Railway, Render, VPS) es mejor opción.

### Sin Docker

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
PAPERARK_BASE_URL=https://paperark.tu-dominio.com .venv/bin/uvicorn paperark.web:app --host 0.0.0.0 --port 8000
```

### Código

Repositorio: https://github.com/oegea/paperark (MIT).

Notas para un servicio público: las sesiones de recuperación viven en memoria (se pierden al reiniciar y no se comparten entre varios procesos: usa `--workers 1` o pega las sesiones a un proceso), y los ficheros subidos se procesan en memoria y no se guardan en disco. Si vas a exponerlo a desconocidos, pon un límite de tamaño de subida en el proxy.

## Licencia

MIT. Vibe coded by [Oriol Egea](https://www.linkedin.com/in/oriolegea/).

Desplegado en https://paperark-1c5k.vercel.app
