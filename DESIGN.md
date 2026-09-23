# PaperArk — diseño del códec de backup en papel

Objetivo: guardar ficheros en hojas de papel blanco y negro con la máxima
densidad compatible con una recuperación fiable dentro de décadas, cuando el
papel esté amarillento, tenga manchas, dobleces, esquinas rotas o falten hojas.

## 1. Qué se ha estudiado y qué se ha tomado de cada sitio

| Sistema | Qué aporta | Qué se ha adoptado / descartado |
|---|---|---|
| **QR (ISO/IEC 18004)** | Reed-Solomon sobre GF(256), *finder patterns* en esquinas, *alignment patterns* internos, máscara para evitar zonas uniformes, intercalado de bloques. | Adoptado todo el esquema conceptual, pero redimensionado: finders de 14 celdas, retícula de ~1 200 marcadores por hoja, máscara fija pseudoaleatoria (no hay que elegir entre 8), palabras RS(255,k) intercaladas por toda la página. |
| **Twibright Optar** (200 kB/A4) | Cruces de sincronía en retícula, código Golay(24,12) (corrige 3 bits de 24), celdas de 3×3 px a 600 dpi, franjas de intercalado. | La idea de retícula densa de marcas y la celda de 3 px como perfil "denso". Golay se descarta: 50 % de redundancia para corregir errores de bit aislados; en papel el daño real es en ráfagas (manchas, roturas) y RS por bytes con intercalado y *borrados* lo aprovecha mucho mejor. |
| **PaperBack** (Oleh Yuschuk, hasta 500 kB/A4) | Puntos individuales a 600 dpi, RS, bloques con CRC, redundancia configurable. | Densidad "nominal" descartada: exige impresora y escáner perfectos; nuestro perfil 3 px (≈300 kB/A4) es el límite prudente. Se adopta el hash por bloque (aquí por hoja y por fichero). |
| **MRPODS (arXiv 2312.10275)** | Evaluación de PaperBack a 200 dpi con compresión previa para archivo indefinido. | Compresión zstd antes de codificar; recomendación de papel ISO 9706. |
| **Códigos 2D multinivel / de color** (TIFS 2006, HCC2D) | Más bits por celda con grises o colores. | Descartado: el amarilleo y el desvanecimiento del tóner destruyen los niveles intermedios; blanco/negro es lo único estable durante siglos. |
| **Erasure codes entre bloques (RS de Backblaze, RaptorQ)** | Recuperar cualquier subconjunto de bloques perdidos. | RS(K+M, K) por columnas entre hojas: MDS (óptimo, cualquiera K de K+M bastan), especificable en 10 líneas. RaptorQ se descarta: necesita algo más de K símbolos, y su especificación (RFC 6330) es enorme para un formato que debe reimplementarse a ciegas dentro de 50 años. |

## 2. Modelo de daño

Lo que ocurre en papel, en orden de probabilidad:

1. **Cambios globales**: amarilleo, pérdida de contraste del tóner, gradiente de iluminación del escáner, giro y ligera perspectiva, escala distinta (fotocopia, "ajustar a página"). → Se resuelve con geometría autocalibrada (no se asume dpi) y umbral local.
2. **Ruido de celda aislado**: polvo, huecos de tóner, JPEG, celdas al límite del umbral. → RS por bytes con 8 celdas por byte lo tolera hasta ~1 % de celdas; la fiabilidad por celda convierte las dudosas en borrados (cuestan la mitad).
3. **Daño en ráfaga**: manchas de café, dobleces, rayas, esquinas y tiras arrancadas. → Intercalado uniforme: cada palabra RS reparte sus 255 bytes por toda la hoja, así una mancha del 10 % de la superficie quita ~10 % de los bytes de *cada* palabra en vez de destruir palabras enteras. Las regiones sin contraste, sin marcadores o con densidad de negro anómala se declaran **borrados** (posición conocida), y RS corrige el doble de borrados que de errores (se declaran como máximo nsym−16 borrados por palabra para conservar margen de verificación: ver `codec._rs_decode`).
4. **Hojas enteras perdidas o ilegibles**: → hojas de paridad RS entre páginas: M hojas de paridad por grupo recuperan cualquier combinación de M hojas. Una hoja leída *parcialmente* aporta sus bloques buenos: solo los bloques ilegibles consumen paridad.

## 3. Geometría de la hoja

Todo se mide en **celdas** cuadradas de C píxeles a 600 dpi (C ∈ {3,4,5,6}; 0,127–0,254 mm). El lector no necesita conocer la resolución del escaneo: mide el tamaño de los finders y las distancias entre ellos y deduce el perfil.

* Márgenes: 10 mm izquierda/derecha/abajo; 8 mm arriba + banda de 14 mm de texto legible (nombre, hoja i de N, SHA-256 del fichero y de la hoja, parámetros, instrucciones de escaneo).
* **Finders** (4 esquinas): 14×14 celdas, anillo negro 2 / anillo blanco 2 / núcleo negro 6, zona de silencio de 2 celdas. Se detectan por jerarquía de contornos (áreas 196:100:36), lo que es invariante a escala y rotación. Bastan 2 de los 4 (con 2 se generan hipótesis de rectángulo y se elige la que más marcadores encuentra).
* **Marcadores de alineación**: 5×5 con silencio de 1 celda, en retícula de ~40 celdas (≈6,8 mm con C=4) exactamente simétrica bajo giro de 180°. ~1 200 por hoja A4 (3 % de la superficie). Hay **cuatro variantes** (anillo+punto, aspa, cruz, bloque; correlación mutua ≤ 0,55) asignadas por la paridad de fila y columna medida desde el borde más cercano: así una rejilla desplazada un periodo entero (que con solo 2 finders y un 3 % de error de escala es alcanzable) no encaja con los marcadores, un fallo que apareció en las pruebas con hojas a las que faltaba la tira inferior. El decodificador localiza cada uno por correlación normalizada (±3 celdas), reajusta la homografía global por RANSAC con los encontrados (iterando: así los finders imprecisos o ausentes no importan) y construye un **campo de desplazamiento** interpolado bilinealmente para corregir el alabeo local del papel.
* **Zonas de cabecera**: 4 rectángulos de 80×60 celdas junto a cada esquina; 4080 celdas de cabecera codificada en cada uno (4 copias idénticas: una esquina rota no pierde la cabecera).
* **Datos**: el resto de celdas, en orden por filas.

## 4. Codificación

* **Cabecera** (128 B): magic, versión, flags, C, k, índice de hoja, total, hojas de datos, paridad por grupo, longitud útil, longitud del flujo, SHA-256 del fichero, SHA-256 de la hoja, nombre, CRC32. Dos mitades de 64 B → RS(255,64) cada una (tolera 95 bytes erróneos de 255, el 37 %), intercaladas byte a byte.
* **Datos**: n palabras RS(255,k), k ∈ {207 (L), 179 (M), 147 (H), 115 (X)}. El byte i de la palabra c va al *slot* i·n + c → los bytes de cada palabra están a ~5 filas de distancia y desplazados horizontalmente: cualquier daño localizado se reparte uniformemente.
* **Máscara**: XOR con xorshift32 (semilla fija). Garantiza ~50 % de negro en cualquier ventana, lo que permite el umbral local por mínimo/máximo, la detección de regiones inválidas por densidad, y evita confundir datos con marcadores.
* **Flujo del fichero**: `[u32 len][manifiesto JSON: nombre, tamaño, sha256, compresión, fecha][cuerpo (zstd si reduce)]`, troceado en hojas de n·k bytes.
* **Paridad entre hojas**: grupos de hasta 255 hojas (K datos + M paridad); para cada posición de byte j, RS(K+M,K) sobre las K hojas (mismas convenciones GF(256)). El decodificador reconstruye por columnas y agrupa las columnas por patrón de borrado, de modo que una hoja parcial solo gasta paridad en sus bloques rotos.

Capacidad útil por hoja A4 (bytes), sin contar compresión:

| Celda | mm | L (19 % paridad) | **M (30 %)** | H (42 %) | X (55 %) |
|---|---|---|---|---|---|
| 3 px | 0,127 | 297 KB | **257 KB** | 211 KB | 165 KB |
| 4 px | 0,169 | 166 KB | **143 KB** | 118 KB | 92 KB |
| 5 px | 0,212 | 105 KB | **91 KB** | 75 KB | 58 KB |
| 6 px | 0,254 | 72 KB | **62 KB** | 51 KB | 40 KB |

Por comparación: Optar 200 KB (celda 3 px, 50 % de paridad Golay); PaperBack hasta 500 KB con puntos de 1 px sin margen real; QR versión 40 2,9 KB.

## 5. Decodificación

1. Gris → umbral adaptativo (bloque ≈ 1/40 de la imagen) → contornos con jerarquía → candidatos a finder.
2. Hipótesis de cuadrilátero (4, 3 o 2 finders) → perfil por relación de distancias → homografía a 4 px/celda.
3. Iteración: localizar marcadores (correlación normalizada ≥ 0,7 con la plantilla de su variante, sobremuestreo ×2), reajustar la homografía global por RANSAC con los encontrados y repetir hasta que estén casi todos y los residuos sean < 0,5 px (un desplazamiento global fraccionario sesga la lectura de las celdas contiguas a los marcadores).
4. Campo de desplazamiento (bilineal sobre la retícula, huecos rellenados por vecinos) → posición subpíxel de cada centro de celda.
5. Máscara de enfoque (compensa la difusión del tóner y del escáner) + muestreo bilineal → valor por celda → umbral local (mínimo/máximo en 15×15) → bit, fiabilidad y borrado (contraste < 40/255, densidad de negro fuera de 22–78 % en 11×11, o marcadores vecinos ausentes).
6. Cabecera: 4 copias × 2 orientaciones (0°/180°; 90° se descarta porque la hoja no es cuadrada).
7. Datos: síndromes vectorizados (numpy) para saltar las palabras limpias; el resto se corrige con borrados (duros + los bytes menos fiables) en un *pool* de procesos. SHA-256 de la hoja.
8. Sesión: ordena por cabecera, ignora duplicados, lista faltantes, reconstruye con paridad, decodifica el flujo, verifica SHA-256 del fichero. Modo estricto opcional que exige orden y permite marcar hojas perdidas.

## 6. Decisiones que responden a la revisión externa

* **Orden de escaneo**: por defecto cualquier orden (una caja con 400 hojas desordenadas se resuelve sola); el modo estricto se mantiene como opción.
* **Dos niveles de corrección**: intra-hoja (RS + intercalado + borrados) e inter-hoja (RS por columnas). Se ha preferido intercalar toda la hoja frente a "tiles" independientes: los tiles concentran el daño (una mancha mata un tile entero) y solo tienen sentido si el nivel inter-hoja trabaja por tile; aquí el nivel inter-hoja trabaja por *palabra*, que es el equivalente con mejor rendimiento.
* **RaptorQ**: descartado a favor de RS MDS por simplicidad de especificación y porque con ≤ 255 hojas por grupo no aporta nada.
* **Muchas referencias geométricas**: sí, ~1 200 marcadores por hoja con corrección local, no solo 4 esquinas.

## 6b. Portada, metadatos y móvil

* **Portada**: nombre del fichero en grande, tamaño, fecha, número de hojas, huella SHA-256 completa, huella de cada hoja, instrucciones de recuperación y un **QR** con la URL `{base}/restore#m=<base64url(JSON)>` que contiene nombre, tamaño, SHA-256, hojas de datos/paridad, celda, k y compresión (`paperark/meta.py`). Al escanearlo con la cámara del móvil se abre la pantalla de recuperación ya identificada; si se fotografía la portada, el servidor lee el QR con OpenCV y aplica los metadatos. La primera hoja de datos decodificada los confirma (mismo SHA-256) o los rechaza.
* **Perfiles móvil**: celdas de 8 y 10 px (0,34 y 0,42 mm). Medido con el simulador de foto (`simulate_phone`: hoja al 85 % de una foto vertical, perspectiva, viñeteado, desenfoque óptico, ruido, JPEG): 8 px se lee sin errores desde 8 MP incluso con desenfoque de 1,6 px; 6 px es marginal a 12 MP; 4 px solo con 48 MP. La cámara del portátil (1–2 MP) no sirve con ningún perfil.
* **Sesión compartida**: el escritorio crea la sesión y muestra un QR `{base}/restore?s=<id>`; el móvil abre la misma sesión; ambos hacen *polling* del estado.

## 7. Recomendaciones físicas

* Impresora láser (el tóner es un polímero fundido, estable; la tinta de inyección se corre y desvanece). Negro puro, sin "ahorro de tóner", **100 % de escala**.
* Papel permanente ISO 9706 / sin ácido, ≥ 90 g. Guardar plano, en oscuridad, seco (< 50 % HR), sin grapas metálicas.
* Escanear a 600 dpi en gris, sin corrección automática, sin "mejora de documento". Fotografías de móvil no están soportadas de forma fiable (desenfoque y perspectiva no uniformes).
* Perfil recomendado: celda 4 px, ECC M, 2–3 hojas de paridad por cada 20–30 hojas. Para copias que se van a leer dentro de mucho tiempo con equipo desconocido: celda 5 px, ECC H.
* Imprimir siempre la hoja de especificación (primera del PDF): describe el formato para poder reescribir el lector sin este código.

## 8. Resultados empíricos (simulación)

Ver `bench.py` (impresión con ganancia de punto y difusión, papel amarillento con gradiente, ruido del sensor, giro, perspectiva, JPEG, daños físicos). Resultados en `BENCH.md`.
