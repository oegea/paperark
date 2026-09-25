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

3. CABECERA (little-endian): 0:8 magic "PAPERARK" | 8 version=1 | 9 flags (bit0 zstd, bit1 paridad, bit2 deflate/zlib)
   | 10 C | 11 k | 12 u32 page_index | 16 u32 total_pages | 20 u32 data_pages | 24 u32 parity_per_group
   | 28 u32 payload_len | 32 u64 stream_len | 40 sha256 fichero | 72 sha256 pagina | 104 nombre[20] | 124 crc32.

4. FLUJO DEL FICHERO: [u32 len][manifiesto JSON][cuerpo], troceado en paginas de n_cw*k bytes.
   Cuerpo = fichero original o comprimido con zstd o deflate (flag; el manifiesto lo repite). Paginas de paridad: grupos de hasta 255 paginas;
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

3. HEADER (little-endian): 0:8 magic "PAPERARK" | 8 version=1 | 9 flags (bit0 zstd, bit1 parity, bit2 deflate/zlib)
   | 10 C | 11 k | 12 u32 page_index | 16 u32 total_pages | 20 u32 data_pages | 24 u32 parity_per_group
   | 28 u32 payload_len | 32 u64 stream_len | 40 sha256 file | 72 sha256 sheet | 104 name[20] | 124 crc32.

4. FILE STREAM: [u32 len][JSON manifest][body], split into sheets of n_cw*k bytes.
   Body = original file or zstd- or deflate-compressed (flag; the manifest repeats it). Parity sheets: groups of up to 255 sheets;
   K = 255 - M data per group; for each byte position j, RS(K+M,K) over the K sheets of the group
   (same GF conventions) -> M parity sheets. Any K of the K+M sheets suffice.

5. READING: scan at 600 dpi grayscale; locate the 4 finders (black square with hole and core, areas
   196:100:36); global homography; refine with the markers (displacement field); sample the center of
   each cell; local threshold; contrast-less cells = erasures. Try the header in both orientations.
"""


SPEC2_TEXT = """PAPERARK v2 - ESPECIFICACION DEL FORMATO DE BACKUP EN PAPEL (software v{ver})
Esta hoja describe como recuperar los datos de las hojas siguientes sin disponer del programa original.

1. HOJA Y BLOQUES (px a 600 dpi; A4 = 4960x7016 px). Cada hoja tiene B = 1, 2 o 4 bloques independientes
   (B=2: mitades superior/inferior; B=4: A B arriba, C D abajo). Margen m=142, banda de texto sobre cada bloque
   t=283 (B=1) o 189 (B=2,4), separacion g=118. nc,nr = columnas y filas de bloques. aw = floor((4960-2m-(nc-1)g)/nc),
   ah = floor((7016-2m-nr*t-(nr-1)g)/nr); cols = floor(aw/C), rows = floor(ah/C) (C = px por celda).
   Bloque (r,c): x = m + c*(aw+g) + floor((aw-cols*C)/2), y = m + t + r*(ah+t+g). Celda negra = bit 1.
   La banda de texto dice "hoja·bloque" (p. ej. 3·A): cada bloque se fotografia por separado, de cerca.

2. GEOMETRIA DE UN BLOQUE (en celdas). Finders: 4 esquinas, 14x14: anillo negro 2, anillo blanco 2, nucleo
   negro 6x6; silencio 2. Marcadores 5x5 + silencio 1 (7x7), variantes 0 anillo+centro, 1 aspa X, 2 cruz +,
   3 bloque 3x3; variante (ix,iy) = 2*(min(iy,ny-1-iy) mod 2) + (min(ix,nx-1-ix) mod 2). Reticula por eje de
   N celdas: n = round((N-11)/40)+1 (+1 si n impar y N-7 impar); pos[k] = round(2+k*(N-11)/(n-1)) si k < ceil(n/2),
   si no N-7-pos[n-1-k]; se omiten los que solapan el bloque 16x16 de una esquina.
   Cabecera: 4 zonas de 60x40 celdas en (16,16), (16,cols-76), (rows-56,16), (rows-56,cols-76); sus celdas
   libres por filas: las 2040 primeras llevan la cabecera (igual en las 4), el resto es relleno.
   Datos: el resto de celdas, por filas (n_d celdas).

3. MASCARA: xorshift32 (x^=x<<13; x^=x>>17; x^=x<<5; semilla 0x9E3779B9; byte = x & 0xFF, bits MSB primero).
   Secuencias independientes desde la semilla para: cabecera (255 bytes), datos (n_d bits), relleno.

4. CABECERA: 64 bytes -> RS(255,64) (GF(256), polinomio 0x11D, alfa=2, raices alfa^0..alfa^190) -> XOR mascara
   -> 2040 bits. Campos (little-endian): 0 "PARK" | 4 version=2 | 5 flags (bit1 paridad) | 6 C | 7 tasa
   (1: 0.85, 2: 0.75, 3: 0.62, 4: 0.50) | 8 B | 9 compresion | 10 u16 0 | 12 u32 indice de bloque | 16 u32 total
   | 20 u32 bloques de datos | 24 u32 paridad por grupo | 28 u32 bytes utiles | 32 u64 longitud del flujo
   | 40 sha256(fichero)[0:12] | 52 sha256(bloque)[0:8] | 60 crc32(0..59). Hoja = indice div B, bloque = indice mod B.

5. DATOS: LDPC IRA. nb = ceil(n_d/16384), N = floor(n_d/nb), K = floor(floor(N*tasa)/8)*8, M = N-K, DV = 3.
   Lista s[t] = t mod M (t < K*DV), barajada con Fisher-Yates (i de L-1 a 1: j = r mod (i+1), r = siguiente
   xorshift32 de semilla 0x5EED0003, valor de 32 bits). Bit de informacion i -> comprobaciones s[3i..3i+2]; si
   se repite una, se intercambia con el primer enchufe siguiente (ciclico, desde el bit i+1) que no deje
   repeticiones en ninguno de los dos bits. Paridad: p[j] = p[j-1] XOR (XOR de los bits de info de la
   comprobacion j), p[-1] = 0. Palabra = info(K) + paridad(M). Bit i de la palabra b -> celda de datos
   ((i*S) mod N)*nb + b, S = menor entero >= round(0.618*N) con mcd(S,N)=1; XOR mascara. Carga util = nb*K/8 bytes; la palabra b lleva los bytes [b*K/8, (b+1)*K/8), MSB primero.

6. FLUJO: [u32 len][manifiesto JSON][cuerpo], troceado en bloques de nb*K/8 bytes. Cuerpo comprimido segun el
   manifiesto ("compression": zstd, xz, brotli, bzip2, deflate o none). Paridad: grupos de hasta 255 bloques;
   para cada posicion de byte, RS(K+M,K) sobre los bloques del grupo (mismo GF): cualquier K bastan.

7. LECTURA: localizar los finders del bloque; homografia; refinar con los marcadores; muestrear cada celda.
   Con camara, estimar cada celda con un ecualizador lineal (5x5 celdas vecinas) ajustado por minimos
   cuadrados con las celdas conocidas y las propias decisiones; LLR por celda; decodificar por propagacion
   de creencias (min-sum). Probar la cabecera en las 2 orientaciones.
"""


SPEC2_TEXT_EN = """PAPERARK v2 - PAPER BACKUP FORMAT SPECIFICATION (software v{ver})
This sheet describes how to recover the data on the following sheets without the original software.

1. SHEET AND BLOCKS (px at 600 dpi; A4 = 4960x7016 px). Each sheet holds B = 1, 2 or 4 independent blocks
   (B=2: top/bottom halves; B=4: A B on top, C D below). Margin m=142, text band above each block t=283 (B=1)
   or 189 (B=2,4), gap g=118. nc,nr = block columns and rows. aw = floor((4960-2m-(nc-1)g)/nc),
   ah = floor((7016-2m-nr*t-(nr-1)g)/nr); cols = floor(aw/C), rows = floor(ah/C) (C = px per cell).
   Block (r,c): x = m + c*(aw+g) + floor((aw-cols*C)/2), y = m + t + r*(ah+t+g). Black cell = bit 1.
   The text band reads "sheet·block" (e.g. 3·A): each block is photographed on its own, up close.

2. BLOCK GEOMETRY (in cells). Finders: 4 corners, 14x14: black ring 2, white ring 2, black core 6x6; quiet 2.
   Markers 5x5 + quiet 1 (7x7), variants 0 ring+center, 1 X, 2 +, 3 solid 3x3;
   variant (ix,iy) = 2*(min(iy,ny-1-iy) mod 2) + (min(ix,nx-1-ix) mod 2). Lattice along an axis of N cells:
   n = round((N-11)/40)+1 (+1 if n odd and N-7 odd); pos[k] = round(2+k*(N-11)/(n-1)) if k < ceil(n/2), else
   N-7-pos[n-1-k]; markers overlapping a 16x16 corner block are omitted.
   Header: 4 zones of 60x40 cells at (16,16), (16,cols-76), (rows-56,16), (rows-56,cols-76); their free cells
   in row-major order: the first 2040 carry the header (same in all 4), the rest is filler.
   Data: all other cells, row-major (n_d cells).

3. MASK: xorshift32 (x^=x<<13; x^=x>>17; x^=x<<5; seed 0x9E3779B9; byte = x & 0xFF, bits MSB first).
   Independent sequences from the seed for: header (255 bytes), data (n_d bits), filler.

4. HEADER: 64 bytes -> RS(255,64) (GF(256), polynomial 0x11D, alpha=2, roots alpha^0..alpha^190) -> XOR mask
   -> 2040 bits. Fields (little-endian): 0 "PARK" | 4 version=2 | 5 flags (bit1 parity) | 6 C | 7 rate
   (1: 0.85, 2: 0.75, 3: 0.62, 4: 0.50) | 8 B | 9 compression | 10 u16 0 | 12 u32 block index | 16 u32 total
   | 20 u32 data blocks | 24 u32 parity per group | 28 u32 useful bytes | 32 u64 stream length
   | 40 sha256(file)[0:12] | 52 sha256(block)[0:8] | 60 crc32(0..59). Sheet = index div B, block = index mod B.

5. DATA: IRA LDPC. nb = ceil(n_d/16384), N = floor(n_d/nb), K = floor(floor(N*rate)/8)*8, M = N-K, DV = 3.
   List s[t] = t mod M (t < K*DV), shuffled with Fisher-Yates (i from L-1 down to 1: j = r mod (i+1), r = next
   32-bit xorshift32 value, seed 0x5EED0003). Info bit i -> checks s[3i..3i+2]; if one repeats, swap it with the
   first later socket (cyclic, starting at bit i+1) that leaves no repetition in either bit.
   Parity: p[j] = p[j-1] XOR (XOR of the info bits of check j), p[-1] = 0. Word = info(K) + parity(M).
   Bit i of word b -> data cell ((i*S) mod N)*nb + b, S = smallest integer >= round(0.618*N) with gcd(S,N)=1; XOR mask. Payload = nb*K/8 bytes; word b carries bytes
   [b*K/8, (b+1)*K/8), MSB first.

6. STREAM: [u32 len][JSON manifest][body], split into blocks of nb*K/8 bytes. Body compressed as the manifest
   says ("compression": zstd, xz, brotli, bzip2, deflate or none). Parity: groups of up to 255 blocks; for each
   byte position, RS(K+M,K) over the group's blocks (same GF): any K suffice.

7. READING: locate the block's finders; homography; refine with the markers; sample every cell. With a camera,
   estimate each cell with a linear equalizer (5x5 neighbouring cells) fitted by least squares on the known
   cells and on its own decisions; per-cell LLR; decode by belief propagation (min-sum). Try the header in
   both orientations.
"""


def spec_text(lang: str = "es", version: int = 1) -> str:
    en = (lang or "").lower().startswith("en")
    if version == 2:
        return (SPEC2_TEXT_EN if en else SPEC2_TEXT).format(ver=__version__)
    return (SPEC_TEXT_EN if en else SPEC_TEXT).format(ver=__version__)
