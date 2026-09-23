"""Texto de la página de especificación (para que el formato sea reconstruible
dentro de décadas sin este software)."""
from . import __version__

SPEC_TEXT = """PAPERARK v1 - ESPECIFICACION DEL FORMATO DE BACKUP EN PAPEL (software v{ver})
Esta hoja describe como recuperar los datos de las hojas siguientes sin disponer del programa original.

1. GEOMETRIA (todo en celdas cuadradas de C px a 600 dpi; C esta impreso en cada hoja y en la cabecera)
   Hoja A4 (210x297 mm): margen izq/der/inf 10 mm; margen superior 8 mm + banda de texto 14 mm.
   Rejilla: cols = floor((4960-2*236)/C), rows = floor((7016-519-236)/C). Celda (r,c) negra = bit 1.
   Finders: 4 esquinas, 14x14 celdas: anillo negro 2, anillo blanco 2, nucleo negro 6x6; silencio 2 celdas.
   Marcadores de alineacion: 5x5 con 1 celda de silencio alrededor (7x7). Cuatro variantes: 0 = anillo negro +
   centro negro, 1 = aspa X (diagonales), 2 = cruz + (fila y columna centrales), 3 = bloque negro 3x3 central.
   Variante del marcador (ix,iy) de una reticula nx x ny: 2*(min(iy,ny-1-iy) mod 2) + (min(ix,nx-1-ix) mod 2).
   Reticula cada ~40 celdas: n = round((N-11)/40)+1 (+1 si n es impar y N-7 impar), pos[k] = round(2 + k*(N-11)/(n-1))
   para k < ceil(n/2) y pos[k] = N-7-pos[n-1-k] para el resto (simetrica). Se omiten los que solapan
   con el bloque 16x16 de cada esquina.
   Zonas de cabecera: 4 rectangulos de 80x60 celdas pegados por dentro a cada esquina (16,16), (16,cols-96),
   (rows-76,16), (rows-76,cols-96). Sus celdas libres (no marcador) en orden por filas: las 4080 primeras
   llevan la cabecera codificada (identica en las 4 zonas), el resto es relleno.
   Celdas de datos: todas las demas, en orden por filas (row-major).

2. CODIFICACION (GF(256), polinomio 0x11D, generador alfa=2, raices alfa^0..alfa^(nsym-1), fcr=0)
   Mascara: XOR con xorshift32 (x^=x<<13; x^=x>>17; x^=x<<5; semilla 0x9E3779B9; byte = x & 0xFF),
   una secuencia por bloque (cabecera: 510 bytes; datos: todas las celdas de datos; relleno: aparte).
   Cabecera: 128 bytes -> 2 mitades de 64 -> RS(255,64) cada una -> intercaladas byte a byte -> mascara
   -> 4080 bits (MSB primero).
   Datos: n_cw = floor(floor(n_celdas_datos/8)/255) palabras RS(255,k); el byte i de la palabra c ocupa el
   slot i*n_cw + c; cada slot son 8 celdas consecutivas (MSB primero). Mascara XOR sobre los bytes de slot.
   La carga util de la pagina son los n_cw*k bytes de datos concatenados (los sobrantes son ceros).

3. CABECERA (little-endian): 0:8 magic "PAPERARK" | 8 version=1 | 9 flags (bit0 zstd, bit1 paridad)
   | 10 C | 11 k | 12 u32 page_index | 16 u32 total_pages | 20 u32 data_pages | 24 u32 parity_per_group
   | 28 u32 payload_len | 32 u64 stream_len | 40 sha256 fichero | 72 sha256 pagina | 104 nombre[20] | 124 crc32.

4. FLUJO DEL FICHERO: [u32 len][manifiesto JSON][cuerpo], troceado en paginas de n_cw*k bytes.
   Cuerpo = fichero original o comprimido con zstd (flag). Paginas de paridad: grupos de hasta 255 paginas;
   K = 255 - M datos por grupo; para cada posicion de byte j, RS(K+M,K) sobre las K paginas del grupo
   (mismas convenciones GF) -> M paginas de paridad. Cualquier K de las K+M paginas bastan.

5. LECTURA: escanear a 600 dpi en gris; localizar los 4 finders (cuadrado negro con hueco y nucleo, areas
   196:100:36); homografia global; refinar con los marcadores (campo de desplazamiento); muestrear el centro
   de cada celda; umbral local; celdas sin contraste = borrados. Probar la cabecera en las 2 orientaciones.
"""


SPEC_TEXT_EN = """PAPERARK v1 - PAPER BACKUP FORMAT SPECIFICATION (software v{ver})
This sheet describes how to recover the data on the following sheets without the original software.

1. GEOMETRY (everything in square cells of C px at 600 dpi; C is printed on every sheet and in the header)
   A4 sheet (210x297 mm): left/right/bottom margin 10 mm; top margin 8 mm + 14 mm human-readable band.
   Grid: cols = floor((4960-2*236)/C), rows = floor((7016-519-236)/C). Cell (r,c) black = bit 1.
   Finders: 4 corners, 14x14 cells: black ring 2, white ring 2, black core 6x6; quiet zone 2 cells.
   Alignment markers: 5x5 with 1 quiet cell around (7x7). Four variants: 0 = black ring + black center,
   1 = X (diagonals), 2 = + (center row and column), 3 = solid 3x3 black center block.
   Variant of marker (ix,iy) in an nx x ny lattice: 2*(min(iy,ny-1-iy) mod 2) + (min(ix,nx-1-ix) mod 2).
   Lattice every ~40 cells: n = round((N-11)/40)+1 (+1 if n is odd and N-7 odd), pos[k] = round(2 + k*(N-11)/(n-1))
   for k < ceil(n/2) and pos[k] = N-7-pos[n-1-k] for the rest (symmetric). Markers overlapping the 16x16 corner
   block are omitted.
   Header zones: 4 rectangles of 80x60 cells inside each corner at (16,16), (16,cols-96), (rows-76,16),
   (rows-76,cols-96). Their free cells (not markers) in row-major order: the first 4080 carry the encoded header
   (identical in the 4 zones), the rest is filler.
   Data cells: all the others, in row-major order.

2. CODING (GF(256), polynomial 0x11D, generator alpha=2, roots alpha^0..alpha^(nsym-1), fcr=0)
   Mask: XOR with xorshift32 (x^=x<<13; x^=x>>17; x^=x<<5; seed 0x9E3779B9; byte = x & 0xFF),
   one sequence per block (header: 510 bytes; data: all data cells; filler: separate).
   Header: 128 bytes -> 2 halves of 64 -> RS(255,64) each -> interleaved byte by byte -> mask
   -> 4080 bits (MSB first).
   Data: n_cw = floor(floor(n_data_cells/8)/255) RS(255,k) codewords; byte i of codeword c goes to slot
   i*n_cw + c; each slot is 8 consecutive cells (MSB first). XOR mask over the slot bytes.
   The sheet payload is the n_cw*k data bytes concatenated (the remainder is zeros).

3. HEADER (little-endian): 0:8 magic "PAPERARK" | 8 version=1 | 9 flags (bit0 zstd, bit1 parity)
   | 10 C | 11 k | 12 u32 page_index | 16 u32 total_pages | 20 u32 data_pages | 24 u32 parity_per_group
   | 28 u32 payload_len | 32 u64 stream_len | 40 sha256 file | 72 sha256 sheet | 104 name[20] | 124 crc32.

4. FILE STREAM: [u32 len][JSON manifest][body], split into sheets of n_cw*k bytes.
   Body = original file or zstd-compressed (flag). Parity sheets: groups of up to 255 sheets;
   K = 255 - M data per group; for each byte position j, RS(K+M,K) over the K sheets of the group
   (same GF conventions) -> M parity sheets. Any K of the K+M sheets suffice.

5. READING: scan at 600 dpi grayscale; locate the 4 finders (black square with hole and core, areas
   196:100:36); global homography; refine with the markers (displacement field); sample the center of
   each cell; local threshold; contrast-less cells = erasures. Try the header in both orientations.
"""


def spec_text(lang: str = "es") -> str:
    return (SPEC_TEXT_EN if (lang or "").lower().startswith("en") else SPEC_TEXT).format(ver=__version__)
