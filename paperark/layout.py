"""Geometría de la página: rejilla de celdas, marcadores y zonas reservadas.

Todo se define en CELDAS sobre una rejilla de `cols` x `rows`. La celda (r, c)
ocupa el cuadrado [c, c+1) x [r, r+1) en coordenadas de rejilla. El tamaño físico
de la celda es `cell` píxeles de impresora a 600 dpi.

Elementos (ver DESIGN.md):
  * Finder: 4 esquinas, 14x14 celdas (anillo negro 2, anillo blanco 2, núcleo
    negro 6x6) + zona de silencio de 2 celdas => reserva 16x16 por esquina.
  * Marcadores de alineación: patrón 5x5 (borde negro 1, blanco 1, centro negro
    1) + silencio 1 => reserva 7x7. Retícula ~cada `align_spacing` celdas,
    simétrica bajo rotación de 180º.
  * Zonas de cabecera: 4 rectángulos de 80x60 celdas junto a cada finder; en
    cada uno se escriben las 4080 celdas de la cabecera codificada (idéntica
    en las 4 copias). Las celdas sobrantes son relleno.
  * Resto: celdas de datos, ordenadas por filas (row-major).
"""
from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache

import numpy as np

DPI = 600
PAPERS = {"A4": (210.0, 297.0), "LETTER": (215.9, 279.4)}
MARGIN_MM = 10.0        # márgenes izquierdo/derecho/inferior
TOP_MM = 8.0            # margen superior antes del texto legible
TEXT_BAND_MM = 14.0     # banda de texto legible por humanos

FINDER = 14
FINDER_RES = 16
ALIGN = 5
ALIGN_RES = 7
HEADER_W = 80
HEADER_H = 60
HEADER_BYTES = 128
HEADER_K = 64                  # RS(255,64) x2
HEADER_CELLS = 2 * 255 * 8     # 4080

KIND_DATA, KIND_FINDER, KIND_ALIGN, KIND_HEADER, KIND_FILLER = 0, 1, 2, 3, 4

CELL_SIZES = (3, 4, 5, 6, 8, 10)  # formato 1; 8 y 10: perfiles para fotografía con móvil

# --- formato 2: la hoja se divide en 1, 2 o 4 bloques independientes -------
# Cada bloque tiene sus finders, marcadores y cabecera, y se fotografía por
# separado (más cerca = más píxeles por celda). Bloques de 2: mitades superior
# e inferior; de 4: cuadrícula 2x2 (A B / C D).
V2_CELL_SIZES = (3, 4, 5, 6, 7, 8)
PANEL_COUNTS = (1, 2, 4)
PANEL_GRID = {1: (1, 1), 2: (1, 2), 4: (2, 2)}   # (columnas, filas)
SHEET_MARGIN_MM = 6.0
LABEL_MM = {1: 12.0, 2: 8.0, 4: 8.0}             # banda legible sobre cada bloque
GUTTER_MM = 5.0                                  # separación entre bloques
V2_HEADER_W = 60
V2_HEADER_H = 40
V2_HEADER_BYTES = 64
V2_HEADER_CELLS = 255 * 8                        # RS(255,64) x1 = 2040
PANEL_LETTERS = "ABCD"


def mm_to_px(mm: float) -> int:
    return int(round(mm / 25.4 * DPI))


def finder_pattern() -> np.ndarray:
    p = np.zeros((FINDER, FINDER), dtype=np.uint8)
    p[:, :] = 1
    p[2:-2, 2:-2] = 0
    p[4:-4, 4:-4] = 1
    return p


def align_pattern(variant: int = 0) -> np.ndarray:
    """4 variantes de marcador 5x5 (todas simétricas bajo giro de 180º):
    0 anillo + punto, 1 aspa X, 2 cruz +, 3 bloque 3x3. La variante de cada
    marcador depende de la paridad de su posición en la retícula (medida desde
    el borde más cercano), de modo que un desplazamiento de un periodo de la
    retícula no puede confundirse con la posición correcta."""
    p = np.zeros((ALIGN, ALIGN), dtype=np.uint8)
    if variant == 0:
        p[:, :] = 1
        p[1:-1, 1:-1] = 0
        p[2, 2] = 1
    elif variant == 1:
        for i in range(ALIGN):
            p[i, i] = 1
            p[i, ALIGN - 1 - i] = 1
    elif variant == 2:
        p[2, :] = 1
        p[:, 2] = 1
    else:
        p[1:-1, 1:-1] = 1
    return p


def marker_variant(ix: int, iy: int, nx: int, ny: int) -> int:
    """Variante del marcador en la posición (ix, iy) de una retícula nx x ny."""
    px = min(ix, nx - 1 - ix) % 2
    py = min(iy, ny - 1 - iy) % 2
    return py * 2 + px


@dataclass(frozen=True)
class Profile:
    paper: str
    cell: int
    align_spacing: int = 40
    panels: int = 0          # 0 = formato 1 (hoja completa); 1, 2, 4 = formato 2

    @property
    def key(self) -> str:
        return f"{self.paper}-{self.cell}" if not self.panels else f"{self.paper}-{self.cell}-p{self.panels}"

    @property
    def version(self) -> int:
        return 2 if self.panels else 1


class Layout:
    def __init__(self, profile: Profile):
        self.profile = profile
        w_mm, h_mm = PAPERS[profile.paper]
        self.cell = profile.cell
        self.page_w = mm_to_px(w_mm)
        self.page_h = mm_to_px(h_mm)
        self.panels = profile.panels
        self.version = profile.version
        if not self.panels:
            self.x0 = mm_to_px(MARGIN_MM)
            self.y0 = mm_to_px(TOP_MM + TEXT_BAND_MM)
            avail_w = self.page_w - 2 * self.x0
            avail_h = self.page_h - self.y0 - mm_to_px(MARGIN_MM)
            self.panel_origins = [(self.x0, self.y0)]
            self.label_rects = []
            self.header_w, self.header_h, self.header_cells = HEADER_W, HEADER_H, HEADER_CELLS
        else:
            nc, nr = PANEL_GRID[self.panels]
            m, lab, gut = mm_to_px(SHEET_MARGIN_MM), mm_to_px(LABEL_MM[self.panels]), mm_to_px(GUTTER_MM)
            avail_w = (self.page_w - 2 * m - (nc - 1) * gut) // nc
            avail_h = (self.page_h - 2 * m - nr * lab - (nr - 1) * gut) // nr
            gw, gh = (avail_w // self.cell) * self.cell, (avail_h // self.cell) * self.cell
            self.panel_origins, self.label_rects = [], []
            for r in range(nr):
                for c in range(nc):
                    x = m + c * (avail_w + gut) + (avail_w - gw) // 2
                    y = m + lab + r * (avail_h + lab + gut)
                    self.panel_origins.append((x, y))
                    self.label_rects.append((x, y - lab, gw, lab))
            self.x0, self.y0 = self.panel_origins[0]
            self.header_w, self.header_h, self.header_cells = V2_HEADER_W, V2_HEADER_H, V2_HEADER_CELLS
        self.cols = avail_w // self.cell
        self.rows = avail_h // self.cell
        self._build()

    # ------------------------------------------------------------------
    def _lattice(self, n_cells: int) -> list[int]:
        """Posiciones (celda superior-izquierda del bloque 7x7) de los marcadores
        a lo largo de un eje de `n_cells` celdas, simétricas bajo inversión."""
        S = self.profile.align_spacing
        span = n_cells - 4 - ALIGN_RES  # de 2 hasta n-9
        n = int(round(span / S)) + 1
        if n % 2 == 1 and (n_cells - ALIGN_RES) % 2 == 1:
            n += 1  # con n impar el marcador central no podría ser simétrico
        pos = [0] * n
        for k in range(n):
            if k < (n + 1) // 2:
                pos[k] = int(round(2 + k * span / (n - 1)))
            else:
                pos[k] = n_cells - ALIGN_RES - pos[n - 1 - k]
        return pos

    def _build(self):
        R, C = self.rows, self.cols
        kind = np.zeros((R, C), dtype=np.uint8)
        fixed = np.zeros((R, C), dtype=np.uint8)
        fp = finder_pattern()
        # finders
        self.finder_centers = [(7.0, 7.0), (C - 7.0, 7.0), (C - 7.0, R - 7.0), (7.0, R - 7.0)]  # (x, y)
        for (cy, cx) in [(0, 0), (0, C - FINDER), (R - FINDER, 0), (R - FINDER, C - FINDER)]:
            fixed[cy:cy + FINDER, cx:cx + FINDER] = fp
        for (cy, cx) in [(0, 0), (0, C - FINDER_RES), (R - FINDER_RES, 0), (R - FINDER_RES, C - FINDER_RES)]:
            kind[cy:cy + FINDER_RES, cx:cx + FINDER_RES] = KIND_FINDER
        # marcadores de alineación
        xs, ys = self._lattice(C), self._lattice(R)
        self.align_xs, self.align_ys = xs, ys
        markers = []
        variants = {}
        for iy, y in enumerate(ys):
            for ix, x in enumerate(xs):
                if (x < FINDER_RES or x + ALIGN_RES > C - FINDER_RES) and (y < FINDER_RES or y + ALIGN_RES > R - FINDER_RES):
                    continue  # solapa un finder
                v = marker_variant(ix, iy, len(xs), len(ys))
                kind[y:y + ALIGN_RES, x:x + ALIGN_RES] = KIND_ALIGN
                fixed[y + 1:y + 1 + ALIGN, x + 1:x + 1 + ALIGN] = align_pattern(v)
                markers.append((x, y))
                variants[(x, y)] = v
        self.markers = markers  # esquina superior-izquierda del bloque 7x7
        self.marker_variant = variants
        # zonas de cabecera
        HW, HH, HC = self.header_w, self.header_h, self.header_cells
        self.header_zones = [
            (FINDER_RES, FINDER_RES),
            (FINDER_RES, C - FINDER_RES - HW),
            (R - FINDER_RES - HH, FINDER_RES),
            (R - FINDER_RES - HH, C - FINDER_RES - HW),
        ]
        header_idx = []
        for (zy, zx) in self.header_zones:
            sub = kind[zy:zy + HH, zx:zx + HW]
            free = np.argwhere(sub == KIND_DATA)
            flat = (free[:, 0] + zy) * C + (free[:, 1] + zx)
            flat = np.sort(flat)
            assert len(flat) >= HC, "zona de cabecera demasiado pequeña"
            use, rest = flat[:HC], flat[HC:]
            kind.flat[use] = KIND_HEADER
            kind.flat[rest] = KIND_FILLER
            header_idx.append(use)
        self.header_idx = header_idx
        self.kind = kind
        self.fixed = fixed
        self.data_idx = np.flatnonzero(kind == KIND_DATA)
        self.filler_idx = np.flatnonzero(kind == KIND_FILLER)
        self.n_slots = len(self.data_idx) // 8
        self.n_codewords = self.n_slots // 255

    # ------------------------------------------------------------------
    def payload_bytes(self, k: int) -> int:
        return self.n_codewords * k

    def describe(self) -> dict:
        return {
            "version": self.version,
            "panels": self.panels or 1,
            "paper": self.profile.paper,
            "cell_px": self.cell,
            "cell_mm": round(self.cell * 25.4 / DPI, 4),
            "cols": self.cols,
            "rows": self.rows,
            "cells": int(self.rows * self.cols),
            "data_cells": int(len(self.data_idx)),
            "markers": len(self.markers),
            "codewords": self.n_codewords,
        }


@lru_cache(maxsize=64)
def get_layout(paper: str, cell: int, align_spacing: int = 40, panels: int = 0) -> Layout:
    return Layout(Profile(paper, cell, align_spacing, panels))


def layout_for(profile: Profile) -> Layout:
    return get_layout(profile.paper, profile.cell, profile.align_spacing, profile.panels)


def all_profiles() -> list[Profile]:
    v1 = [Profile(p, c) for p in PAPERS for c in CELL_SIZES]
    v2 = [Profile(p, c, 40, n) for p in PAPERS for n in PANEL_COUNTS for c in V2_CELL_SIZES]
    return v2 + v1
