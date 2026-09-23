# PaperArk — backups en papel para décadas

Codifica cualquier fichero en un PDF imprimible en blanco y negro y lo recupera
a partir de escaneos, aunque las hojas estén amarillentas, giradas, manchadas,
con esquinas arrancadas o falten hojas enteras.

* **Densidad**: 143 KB útiles por A4 en el perfil estándar (celda de 0,17 mm,
  30 % de paridad); hasta 297 KB con celda de 0,13 mm y paridad mínima; 34 KB
  en el perfil para **fotografiar con el móvil** (celda de 0,34 mm, legible
  desde 8 MP), 21 KB en el de máxima tolerancia.
* **Robustez por hoja**: Reed-Solomon RS(255,k) sobre GF(256) con intercalado
  uniforme por toda la página y decodificación con borrados: una hoja sobrevive
  a la pérdida de ~15 % de su superficie (errores) o ~23 % (regiones detectadas
  como ilegibles) en el perfil M, y hasta ~28 %/~49 % en el perfil X.
* **Robustez entre hojas**: hojas de paridad RS entre páginas: con M hojas de
  paridad por grupo se recuperan *cualesquiera* M hojas perdidas (o el
  equivalente en bloques ilegibles repartidos por varias hojas).
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

Ver [DESIGN.md](DESIGN.md) para el diseño y [BENCH.md](BENCH.md) para los resultados empíricos (89 de 100 escenarios íntegros, 0 fallos; el resto son hojas parciales recuperables con paridad).

* **Español e inglés**: interfaz web en ambos idiomas (conmutador ES/EN en la
  cabecera, se recuerda), mensajes del servidor y PDF en el idioma elegido
  (`--lang en` en la CLI o el selector "Idioma del PDF" del asistente).

## Instalación

```bash
python3 -m venv .venv && .venv/bin/pip install -r requirements.txt
```

## Uso

```bash
# fichero -> PDF (perfil estándar, 2 hojas de paridad)
.venv/bin/python -m paperark encode documento.zip -o documento.pdf --parity 2

# escaneos (PNG/JPG/TIFF/PDF, en cualquier orden) -> fichero
.venv/bin/python -m paperark decode escaneos/*.png -o restaurado/

# cuántas hojas ocupará
.venv/bin/python -m paperark estimate 5000000 --cell 4 --ecc M --parity 3

# interfaz web (escritorio y móvil en la misma red; imprime la URL para el móvil)
.venv/bin/python -m paperark serve

# la URL impresa en la portada y en el QR: opción --url o variable PAPERARK_BASE_URL
.venv/bin/python -m paperark encode documento.zip -o documento.pdf --cell 8 --parity 2 --url https://mi-servidor
```

Impresión: láser, **tamaño real (100 %)**, papel sin ácido. Escaneo: **600 dpi,
escala de grises** (300 dpi funciona con celda ≥ 4 px pero con menos margen).

## Tests y banco de pruebas

```bash
.venv/bin/python -m pytest -q
.venv/bin/python bench.py          # simulación impresión+envejecimiento+daños
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
