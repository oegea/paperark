# Resultados empíricos

## Formato 2: bloques + ecualizador + LDPC (fotos de móvil simuladas)

Generado con `bench_v2.py`: una foto de 12 MP por bloque (o de la hoja entera en
los perfiles de un bloque y en el formato 1), con el bloque llenando el encuadre,
perspectiva, viñeteado, desenfoque, ruido y JPEG (`simulate_phone`). ✅ = bloque
leído y verificado (en pequeño, % de bits erróneos antes de corregir); 🟠 = parcial;
❌ = no leído. Corrección M. Cabecera: KB útiles por hoja A4.

Tiempo medio por foto: 9.1 s (varios procesos en paralelo).
| Escenario | v2 · 4 bloques · 0,17 mm<br>157 KB/hoja | v2 · 4 bloques · 0,13 mm<br>282 KB/hoja | v2 · 4 bloques · 0,21 mm<br>99 KB/hoja | v2 · 2 bloques · 0,21 mm<br>104 KB/hoja | v2 · 1 bloque · 0,30 mm<br>55 KB/hoja | v1 · hoja · 0,34 mm (antes)<br>35 KB/hoja |
|---|---|---|---|---|---|---|
| móvil nítido (desenfoque 1,1 px) | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ |
| desenfoque 1,6 px | ✅ | ✅ <sub>0.00%</sub> | ✅ | ✅ | ✅ | ✅ |
| desenfoque 2,2 px | ✅ <sub>0.00%</sub> | ✅ <sub>0.18%</sub> | ✅ | ✅ <sub>0.00%</sub> | ✅ <sub>0.01%</sub> | ✅ <sub>0.01%</sub> |
| desenfoque 2,8 px | ✅ <sub>0.04%</sub> | ❌ | ✅ | ✅ <sub>0.12%</sub> | ✅ <sub>0.88%</sub> | ✅ <sub>0.53%</sub> |
| 8 MP | ✅ | ✅ <sub>0.00%</sub> | ✅ | ✅ | ✅ | ✅ |
| JPEG q70 + ruido | ✅ | ✅ <sub>0.00%</sub> | ✅ | ✅ | ✅ | ✅ |
| inclinado 6º + perspectiva | ✅ | ✅ <sub>0.00%</sub> | ✅ | ✅ | ✅ | ✅ |
| mancha de café | ✅ | ✅ | ✅ | ✅ | ✅ | ✅ <sub>0.47%</sub> |
| esquina arrancada | ✅ <sub>0.25%</sub> | ✅ <sub>0.27%</sub> | ✅ <sub>0.26%</sub> | ✅ <sub>0.13%</sub> | ✅ <sub>0.03%</sub> | ✅ |
| doblez + rayas + polvo | ✅ <sub>0.37%</sub> | ✅ <sub>0.39%</sub> | ✅ <sub>0.38%</sub> | ✅ <sub>0.23%</sub> | ✅ <sub>0.10%</sub> | ✅ <sub>0.62%</sub> |
| tóner desvaído 45 % | ✅ | ✅ <sub>0.00%</sub> | ✅ | ✅ | ✅ | ✅ |

El perfil de 0,13 mm es el límite: no aguanta un desenfoque de 2,8 px. El formato 1
(última columna) lee ahora más casos que antes porque el ecualizador actúa como respaldo.

## Formato 1 (simulación de impresión, envejecimiento, escaneo y daños)

Generado con `bench.py`: una hoja A4 con datos aleatorios por perfil, pasada por el simulador
(`paperark/simulate.py`: ganancia de punto y difusión del tóner, papel amarillento con gradiente de
iluminación, ruido del sensor, giro, perspectiva, remuestreo a la resolución de escaneo, JPEG y daños
físicos) y decodificada. Columnas: **SER** = % de símbolos (bytes) erróneos antes de corregir;
**máx/cap** = mayor número de símbolos corregidos en una palabra RS frente a la capacidad de errores
(los borrados cuentan la mitad, por eso puede superarla); **borr.** = % de celdas declaradas borrado;
**marc.** = marcadores de alineación localizados. `PARCIAL` = la hoja se lee pero algunos bloques no se
corrigen y necesitan una hoja de paridad; `FALLO` = no se localiza la rejilla o la cabecera.


## Celda 4 px, ECC M (144 KB útiles por hoja)

| Escenario | Resultado | SER % | máx/cap | borr. % | marc. | t (s) |
|---|---|---|---|---|---|---|
| limpio 600dpi | ✅ OK | 0.00 | 0/38 | 0.0 | 1196/1196 | 4.4 |
| 300dpi | ✅ OK | 0.00 | 0/38 | 0.0 | 1196/1196 | 4.2 |
| girado 5º, perspectiva | ✅ OK | 0.06 | 2/38 | 0.0 | 1195/1196 | 5.4 |
| 180º | ✅ OK | 0.00 | 0/38 | 0.0 | 1196/1196 | 5.0 |
| amarillento + gradiente + ruido | ✅ OK | 0.00 | 0/38 | 0.0 | 1196/1196 | 4.0 |
| tóner desvaído 50% | ✅ OK | 0.00 | 0/38 | 0.0 | 1196/1196 | 3.9 |
| desenfoque fuerte | ✅ OK | 0.00 | 0/38 | 0.0 | 1196/1196 | 4.0 |
| JPEG q60 300dpi | ✅ OK | 0.00 | 1/38 | 0.0 | 1196/1196 | 4.1 |
| esquina rota (1.5% área) | ✅ OK | 0.10 | 2/38 | 0.1 | 1194/1196 | 6.1 |
| esquina rota (4% área) | ✅ OK | 2.44 | 10/38 | 2.6 | 1161/1196 | 6.3 |
| esquina rota (10% área) | ✅ OK | 8.71 | 29/38 | 8.8 | 1085/1196 | 6.2 |
| esquina rota (16% área) | ✅ OK | 12.27 | 39/38 | 12.3 | 1044/1196 | 6.3 |
| tira inferior 20% perdida | ✅ OK | 18.15 | 47/38 | 18.6 | 958/1196 | 13.1 |
| tira inferior 30% perdida | 🟠 PARCIAL | – | 822 fallidas | 29.8 | 838/1196 | 12.7 |
| tira lateral 15% perdida | ✅ OK | 11.22 | 35/38 | 11.2 | 1038/1196 | 17.8 |
| mancha grande (12% área) | ✅ OK | 0.00 | 1/38 | 0.0 | 1196/1196 | 4.6 |
| muy amarillo + desvaído + ruido | ✅ OK | 2.48 | 17/38 | 0.0 | 1196/1196 | 4.8 |
| manchas de café x3 | ✅ OK | 0.01 | 1/38 | 0.0 | 1196/1196 | 5.2 |
| doblez + rayas + polvo | ✅ OK | 1.67 | 12/38 | 0.0 | 1178/1196 | 4.6 |
| todo a la vez | ✅ OK | 1.56 | 10/38 | 0.3 | 1173/1196 | 9.2 |

## Celda 3 px, ECC M (257 KB útiles por hoja)

| Escenario | Resultado | SER % | máx/cap | borr. % | marc. | t (s) |
|---|---|---|---|---|---|---|
| limpio 600dpi | ✅ OK | 0.00 | 0/38 | 0.0 | 2048/2048 | 7.0 |
| 300dpi | 🟠 PARCIAL | – | 144 fallidas | 0.2 | 1920/2048 | 6.1 |
| girado 5º, perspectiva | ✅ OK | 0.05 | 2/38 | 0.0 | 2046/2048 | 11.9 |
| 180º | ✅ OK | 0.00 | 0/38 | 0.0 | 2048/2048 | 6.8 |
| amarillento + gradiente + ruido | ✅ OK | 0.00 | 0/38 | 0.0 | 2048/2048 | 6.7 |
| tóner desvaído 50% | ✅ OK | 0.00 | 0/38 | 0.0 | 2048/2048 | 6.6 |
| desenfoque fuerte | ✅ OK | 1.12 | 10/38 | 0.0 | 2041/2048 | 7.6 |
| JPEG q60 300dpi | 🟠 PARCIAL | – | 1470 fallidas | 0.5 | 1694/2048 | 6.4 |
| esquina rota (1.5% área) | ✅ OK | 0.08 | 2/38 | 0.1 | 2044/2048 | 10.8 |
| esquina rota (4% área) | ✅ OK | 2.53 | 11/38 | 2.6 | 1989/2048 | 11.4 |
| esquina rota (10% área) | ✅ OK | 8.76 | 30/38 | 8.8 | 1860/2048 | 11.4 |
| esquina rota (16% área) | ✅ OK | 12.29 | 40/38 | 12.3 | 1788/2048 | 11.1 |
| tira inferior 20% perdida | ✅ OK | 18.34 | 47/38 | 18.6 | 1670/2048 | 19.0 |
| tira inferior 30% perdida | 🟠 PARCIAL | – | 1470 fallidas | 29.8 | 1442/2048 | 17.5 |
| tira lateral 15% perdida | ✅ OK | 11.31 | 35/38 | 11.4 | 1780/2048 | 22.3 |
| mancha grande (12% área) | ✅ OK | 0.00 | 1/38 | 0.0 | 2048/2048 | 7.3 |
| muy amarillo + desvaído + ruido | ✅ OK | 1.96 | 12/38 | 0.0 | 2048/2048 | 8.0 |
| manchas de café x3 | ✅ OK | 0.01 | 1/38 | 0.0 | 2048/2048 | 8.0 |
| doblez + rayas + polvo | ✅ OK | 1.71 | 11/38 | 0.1 | 2024/2048 | 8.1 |
| todo a la vez | 🟠 PARCIAL | – | 3 fallidas | 0.3 | 1978/2048 | 14.8 |

## Celda 5 px, ECC M (91 KB útiles por hoja)

| Escenario | Resultado | SER % | máx/cap | borr. % | marc. | t (s) |
|---|---|---|---|---|---|---|
| limpio 600dpi | ✅ OK | 0.00 | 0/38 | 0.0 | 732/732 | 2.7 |
| 300dpi | ✅ OK | 0.00 | 0/38 | 0.0 | 732/732 | 2.2 |
| girado 5º, perspectiva | ✅ OK | 0.04 | 2/38 | 0.0 | 732/732 | 6.7 |
| 180º | ✅ OK | 0.00 | 0/38 | 0.0 | 732/732 | 2.8 |
| amarillento + gradiente + ruido | ✅ OK | 0.00 | 0/38 | 0.0 | 732/732 | 2.6 |
| tóner desvaído 50% | ✅ OK | 0.00 | 0/38 | 0.0 | 732/732 | 2.7 |
| desenfoque fuerte | ✅ OK | 0.00 | 0/38 | 0.0 | 732/732 | 2.7 |
| JPEG q60 300dpi | ✅ OK | 0.00 | 0/38 | 0.0 | 732/732 | 2.3 |
| esquina rota (1.5% área) | ✅ OK | 0.10 | 2/38 | 0.1 | 730/732 | 4.0 |
| esquina rota (4% área) | ✅ OK | 2.33 | 11/38 | 2.6 | 711/732 | 4.3 |
| esquina rota (10% área) | ✅ OK | 8.61 | 29/38 | 8.8 | 663/732 | 4.6 |
| esquina rota (16% área) | ✅ OK | 12.25 | 39/38 | 12.3 | 637/732 | 4.4 |
| tira inferior 20% perdida | ✅ OK | 18.01 | 47/38 | 18.6 | 596/732 | 6.7 |
| tira inferior 30% perdida | 🟠 PARCIAL | – | 523 fallidas | 29.8 | 504/732 | 6.5 |
| tira lateral 15% perdida | ✅ OK | 11.05 | 38/38 | 11.1 | 638/732 | 7.4 |
| mancha grande (12% área) | ✅ OK | 0.00 | 0/38 | 0.0 | 732/732 | 3.3 |
| muy amarillo + desvaído + ruido | ✅ OK | 0.04 | 2/38 | 0.0 | 732/732 | 2.8 |
| manchas de café x3 | ✅ OK | 0.01 | 1/38 | 0.0 | 732/732 | 3.9 |
| doblez + rayas + polvo | ✅ OK | 1.80 | 10/38 | 0.0 | 725/732 | 3.2 |
| todo a la vez | ✅ OK | 1.03 | 8/38 | 0.3 | 724/732 | 6.4 |

## Celda 4 px, ECC H (118 KB útiles por hoja)

| Escenario | Resultado | SER % | máx/cap | borr. % | marc. | t (s) |
|---|---|---|---|---|---|---|
| limpio 600dpi | ✅ OK | 0.00 | 0/54 | 0.0 | 1196/1196 | 4.1 |
| 300dpi | ✅ OK | 0.00 | 0/54 | 0.0 | 1196/1196 | 4.1 |
| girado 5º, perspectiva | ✅ OK | 0.06 | 2/54 | 0.0 | 1195/1196 | 5.3 |
| 180º | ✅ OK | 0.00 | 0/54 | 0.0 | 1196/1196 | 4.1 |
| amarillento + gradiente + ruido | ✅ OK | 0.00 | 0/54 | 0.0 | 1196/1196 | 4.1 |
| tóner desvaído 50% | ✅ OK | 0.00 | 0/54 | 0.0 | 1196/1196 | 4.0 |
| desenfoque fuerte | ✅ OK | 0.00 | 0/54 | 0.0 | 1196/1196 | 4.1 |
| JPEG q60 300dpi | ✅ OK | 0.00 | 1/54 | 0.0 | 1196/1196 | 4.1 |
| esquina rota (1.5% área) | ✅ OK | 0.10 | 2/54 | 0.1 | 1194/1196 | 6.3 |
| esquina rota (4% área) | ✅ OK | 2.45 | 10/54 | 2.6 | 1161/1196 | 6.9 |
| esquina rota (10% área) | ✅ OK | 8.72 | 29/54 | 8.8 | 1085/1196 | 6.7 |
| esquina rota (16% área) | ✅ OK | 12.28 | 39/54 | 12.3 | 1044/1196 | 6.8 |
| tira inferior 20% perdida | ✅ OK | 18.14 | 47/54 | 18.6 | 958/1196 | 13.9 |
| tira inferior 30% perdida | ✅ OK | 29.46 | 76/54 | 29.8 | 838/1196 | 14.0 |
| tira lateral 15% perdida | ✅ OK | 11.23 | 35/54 | 11.2 | 1038/1196 | 18.2 |
| mancha grande (12% área) | ✅ OK | 0.00 | 1/54 | 0.0 | 1196/1196 | 4.9 |
| muy amarillo + desvaído + ruido | ✅ OK | 2.41 | 14/54 | 0.0 | 1196/1196 | 5.4 |
| manchas de café x3 | ✅ OK | 0.01 | 2/54 | 0.0 | 1196/1196 | 5.6 |
| doblez + rayas + polvo | ✅ OK | 1.66 | 12/54 | 0.0 | 1178/1196 | 5.1 |
| todo a la vez | ✅ OK | 2.13 | 14/54 | 0.3 | 1174/1196 | 9.0 |

## Celda 3 px, ECC L (297 KB útiles por hoja)

| Escenario | Resultado | SER % | máx/cap | borr. % | marc. | t (s) |
|---|---|---|---|---|---|---|
| limpio 600dpi | ✅ OK | 0.00 | 0/24 | 0.0 | 2048/2048 | 6.4 |
| 300dpi | 🟠 PARCIAL | – | 1108 fallidas | 0.1 | 1939/2048 | 6.3 |
| girado 5º, perspectiva | ✅ OK | 0.05 | 2/24 | 0.0 | 2046/2048 | 11.7 |
| 180º | ✅ OK | 0.00 | 0/24 | 0.0 | 2048/2048 | 6.5 |
| amarillento + gradiente + ruido | ✅ OK | 0.00 | 0/24 | 0.0 | 2048/2048 | 6.8 |
| tóner desvaído 50% | ✅ OK | 0.00 | 0/24 | 0.0 | 2048/2048 | 6.1 |
| desenfoque fuerte | ✅ OK | 1.70 | 11/24 | 0.0 | 2042/2048 | 6.8 |
| JPEG q60 300dpi | 🟠 PARCIAL | – | 1470 fallidas | 0.5 | 1695/2048 | 6.5 |
| esquina rota (1.5% área) | ✅ OK | 0.08 | 2/24 | 0.1 | 2044/2048 | 10.8 |
| esquina rota (4% área) | ✅ OK | 2.53 | 11/24 | 2.6 | 1989/2048 | 11.9 |
| esquina rota (10% área) | ✅ OK | 8.75 | 30/24 | 8.8 | 1860/2048 | 10.4 |
| esquina rota (16% área) | ✅ OK | 12.29 | 40/24 | 12.3 | 1788/2048 | 10.3 |
| tira inferior 20% perdida | 🟠 PARCIAL | – | 1470 fallidas | 18.6 | 1670/2048 | 17.5 |
| tira inferior 30% perdida | 🟠 PARCIAL | – | 1470 fallidas | 29.8 | 1442/2048 | 16.8 |
| tira lateral 15% perdida | ✅ OK | 11.31 | 34/24 | 11.4 | 1780/2048 | 22.5 |
| mancha grande (12% área) | ✅ OK | 0.00 | 1/24 | 0.0 | 2048/2048 | 7.5 |
| muy amarillo + desvaído + ruido | ✅ OK | 1.98 | 16/24 | 0.0 | 2048/2048 | 7.2 |
| manchas de café x3 | ✅ OK | 0.01 | 1/24 | 0.0 | 2048/2048 | 7.6 |
| doblez + rayas + polvo | ✅ OK | 1.70 | 11/24 | 0.1 | 2023/2048 | 7.0 |
| todo a la vez | 🟠 PARCIAL | – | 1024 fallidas | 0.3 | 1982/2048 | 16.2 |

**Total: 89 de 100 escenarios recuperados íntegramente.**

## Lectura de los resultados

* El perfil recomendado (**4 px, ECC M, 143 KB/hoja**) recupera íntegra la hoja en todos los escenarios salvo la pérdida de una tira del 30 % (29,8 % de celdas borradas, por encima del 23,5 % de borrados que admite RS(255,179) con margen de verificación); esa hoja queda *parcial* y una única hoja de paridad la completa.
* Pérdidas de superficie: esquina del 16 % del área, tira lateral del 15 % y tira inferior del 20 % se recuperan sin paridad. Con ECC H el margen sube (54 errores / 92 borrados por palabra).
* La celda de **3 px (257 KB/hoja)** exige escanear a 600 dpi: a 300 dpi quedan 1,5 px por celda y no es legible; a 400 dpi con todos los daños a la vez quedan 3 bloques sin corregir (paridad entre hojas lo resuelve). A 600 dpi aguanta el resto de escenarios, incluido desenfoque fuerte.
* La celda de **5 px (91 KB/hoja)** es la más tolerante: recupera incluso la tira inferior del 20 % con margen y todo a 300 dpi.
* El amarilleo, el gradiente de iluminación, el tóner desvaído al 50 %, el giro de 5° con perspectiva, el escaneo al revés (180°) y el JPEG de calidad 60 no producen prácticamente errores de símbolo: los absorben el umbral local, la máscara y la rectificación por marcadores.
* Tiempo de decodificación: 2–6 s por hoja en un portátil (hasta ~15 s en hojas muy dañadas con celda de 3 px).

## Lo que la simulación no cubre

* Impresoras y escáneres reales (ganancia de punto, bandas, deformación del rodillo): el modelo es conservador pero no sustituye a una prueba física; antes de archivar, imprimir una hoja, escanearla y comprobar que el margen (`máx/cap`) queda por debajo del 50 %.
* Fotografías con móvil: desenfoque y perspectiva no uniformes; no soportado de forma fiable.
* Degradación química a décadas (papel ácido, humedad): solo se puede mitigar con papel ISO 9706 y almacenamiento adecuado; el margen de corrección es la reserva para eso.
