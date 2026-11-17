"""Fondos para el vídeo: paisajes de naturaleza generados por código o una foto propia.

Los paisajes se dibujan con numpy y Pillow a la resolución exacta del vídeo, con
una semilla fija: el mismo fondo sale idéntico en la vista previa y en el render, y
no dependen de imágenes de terceros (sin problemas de licencia).

Todos los fondos llevan al final un oscurecido suave y una viñeta, para que los
avatares, los nombres y el título se lean bien encima.
"""

from __future__ import annotations

from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageFilter, ImageOps

SOLID = (17, 20, 24)


@dataclass(frozen=True)
class Preset:
    id: str
    name: str
    description: str


PRESETS = [
    Preset("liso", "Liso", "Fondo oscuro, sin imagen"),
    Preset("bosque", "Bosque", "Pinos entre la niebla"),
    Preset("montanas", "Montañas", "Cordilleras al atardecer"),
    Preset("oceano", "Océano", "Olas bajo el cielo del anochecer"),
    Preset("aurora", "Aurora", "Aurora boreal sobre un lago helado"),
    Preset("pradera", "Pradera", "Colinas a la hora dorada"),
]
PRESET_IDS = tuple(p.id for p in PRESETS)


# --- Utilidades ---------------------------------------------------------------------


def _rgb(h: str) -> np.ndarray:
    h = h.lstrip("#")
    return np.array([int(h[i:i + 2], 16) for i in (0, 2, 4)], dtype=np.float32)


def _gradient(size: tuple[int, int], stops: list[tuple[float, str]]) -> np.ndarray:
    """Degradado vertical: stops = [(posición 0..1, color)]."""
    W, H = size
    y = np.linspace(0, 1, H)
    pos = np.array([p for p, _ in stops])
    cols = np.stack([_rgb(c) for _, c in stops])
    rows = np.stack([np.interp(y, pos, cols[:, k]) for k in range(3)], axis=1)
    return np.repeat(rows[:, None, :], W, axis=1).astype(np.float32)


def _ridge(rng: np.random.Generator, W: int, base: float, amp: float, roughness: int = 5) -> np.ndarray:
    """Perfil suave (montañas, colinas): suma de ondas con fases aleatorias."""
    x = np.linspace(0, 1, W)
    y = np.zeros(W)
    for k in range(1, roughness + 1):
        freq = rng.uniform(0.6, 1.4) * 2 ** (k - 1)
        y += np.sin(2 * np.pi * (freq * x + rng.uniform(0, 1))) / (1.7 ** k)
    y = (y - y.min()) / (np.ptp(y) + 1e-9)
    return base - amp * y


def _fill_below(img: np.ndarray, profile: np.ndarray, color: str, alpha: float = 1.0) -> None:
    H = img.shape[0]
    ys = np.arange(H)[:, None]
    mask = (ys >= profile[None, :]).astype(np.float32)[..., None] * alpha
    img[:] = img * (1 - mask) + _rgb(color) * mask


def _glow(img: np.ndarray, cx: float, cy: float, radius: float, color: str, strength: float) -> None:
    H, W = img.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2) / radius
    a = (np.exp(-d ** 2) * strength)[..., None]
    img[:] = img * (1 - a) + _rgb(color) * a


def _disc(img: np.ndarray, cx: float, cy: float, r: float, color: str) -> None:
    H, W = img.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt((xx - cx) ** 2 + (yy - cy) ** 2)
    a = np.clip(r - d + 1, 0, 1)[..., None]  # borde suavizado
    img[:] = img * (1 - a) + _rgb(color) * a


def _stars(img: np.ndarray, rng: np.random.Generator, count: int, max_y: float) -> None:
    H, W = img.shape[:2]
    xs = rng.integers(0, W, count)
    ys = (rng.random(count) ** 1.6 * max_y * H).astype(int)
    b = rng.uniform(0.25, 1.0, count)[:, None]
    img[ys, xs] = img[ys, xs] * (1 - b) + 255 * b


def _mist(img: np.ndarray, y: float, height: float, strength: float, color: str = "#dfe7ec") -> None:
    H = img.shape[0]
    ys = np.arange(H) / H
    a = (np.exp(-((ys - y) / height) ** 2) * strength)[:, None, None]
    img[:] = img * (1 - a) + _rgb(color) * a


def _finish(img: np.ndarray, darken: float = 0.25) -> Image.Image:
    """Oscurecido general + viñeta, para que el contenido destaque sobre el fondo."""
    H, W = img.shape[:2]
    yy, xx = np.mgrid[0:H, 0:W]
    d = np.sqrt(((xx - W / 2) / (W / 2)) ** 2 + ((yy - H / 2) / (H / 2)) ** 2) / np.sqrt(2)
    vignette = 1 - 0.45 * np.clip(d, 0, 1) ** 2
    out = img * (1 - darken) * vignette[..., None]
    return Image.fromarray(np.clip(out, 0, 255).astype(np.uint8))


# --- Paisajes -----------------------------------------------------------------------


def _bosque(size: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    W, H = size
    img = _gradient(size, [(0, "#1d3b3a"), (0.45, "#5f8a83"), (0.7, "#a9c3b8"), (1, "#24413a")])
    layers = [("#4f7268", 0.55, 0.10, 0.012), ("#36564e", 0.65, 0.09, 0.016), ("#213b35", 0.76, 0.08, 0.022),
              ("#11221e", 0.88, 0.07, 0.03)]
    for i, (color, base, amp, tree_w) in enumerate(layers):
        ground = _ridge(rng, W, base * H, amp * H, 3)
        canvas = Image.new("L", size, 0)
        draw = ImageDraw.Draw(canvas)
        x = -tree_w * W
        while x < W * 1.05:
            tw = tree_w * W * rng.uniform(0.8, 1.3)
            th = tw * rng.uniform(3.2, 5.0)
            gx = int(np.clip(x, 0, W - 1))
            gy = ground[gx]
            # Pino: tres triángulos superpuestos.
            for t in range(3):
                top = gy - th * (1 - t * 0.28)
                half = tw * (0.45 + t * 0.2)
                draw.polygon([(x, top), (x - half, top + th * 0.45), (x + half, top + th * 0.45)], fill=255)
            draw.rectangle([x - tw * 0.06, gy - th * 0.2, x + tw * 0.06, gy + 2], fill=255)
            x += tw * rng.uniform(0.55, 1.0)
        draw.polygon([(0, H)] + [(xx, ground[xx]) for xx in range(0, W, 8)] + [(W, ground[-1]), (W, H)], fill=255)
        mask = np.asarray(canvas, dtype=np.float32)[..., None] / 255
        img = img * (1 - mask) + _rgb(color) * mask
        if i < len(layers) - 1:
            _mist(img, base - 0.02, 0.07, 0.35)
    return img


def _montanas(size: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    W, H = size
    img = _gradient(size, [(0, "#2a2350"), (0.35, "#7a4a7a"), (0.55, "#e08a6a"), (0.68, "#f6c48a"), (1, "#3a2a40")])
    _glow(img, W * 0.3, H * 0.45, min(W, H) * 0.4, "#ffd9a0", 0.6)
    _disc(img, W * 0.3, H * 0.45, min(W, H) * 0.06, "#fff1d2")
    for color, base, amp, rough in [("#9a6a86", 0.66, 0.18, 6), ("#6e4a6e", 0.74, 0.22, 6),
                                    ("#47304f", 0.82, 0.22, 5), ("#261a2e", 0.93, 0.15, 4)]:
        _fill_below(img, _ridge(rng, W, base * H, amp * H, rough), color)
        _mist(img, base - 0.03, 0.05, 0.12, "#f2c9b0")
    return img


def _oceano(size: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    W, H = size
    horizon = 0.55
    img = _gradient(size, [(0, "#0d1b3d"), (0.35, "#2c4f86"), (horizon, "#e7a77a"), (horizon + 0.001, "#1f4f73"),
                           (1, "#081a2c")])
    _glow(img, W * 0.3, H * horizon, min(W, H) * 0.3, "#ffc79a", 0.5)
    _disc(img, W * 0.3, H * (horizon - 0.04), min(W, H) * 0.05, "#ffe2bf")
    _fill_below(img, np.full(W, H * horizon), "#1c4767", 1.0)
    # Reflejo del sol en el agua.
    yy, xx = np.mgrid[0:H, 0:W]
    below = yy > H * horizon
    refl = np.exp(-((xx - W * 0.3) / (W * 0.05)) ** 2) * (np.sin(yy * 0.9) > 0.2) * below
    a = (refl * 0.45)[..., None]
    img = img * (1 - a) + _rgb("#ffd2a6") * a
    # Olas: líneas onduladas más claras, más juntas cerca del horizonte.
    canvas = Image.new("L", size, 0)
    d = ImageDraw.Draw(canvas)
    n = 22
    for i in range(n):
        f = (i / n) ** 1.8
        y0 = H * (horizon + 0.02 + f * (1 - horizon))
        amp = 2 + f * H * 0.012
        ph = rng.uniform(0, 6.28)
        pts = [(x, y0 + amp * np.sin(x / (40 + f * 160) + ph)) for x in range(0, W + 10, 10)]
        d.line(pts, fill=int(60 + 90 * (1 - f)), width=max(1, int(1 + f * 3)))
    a = np.asarray(canvas.filter(ImageFilter.GaussianBlur(1)), dtype=np.float32)[..., None] / 255 * 0.55
    img = img * (1 - a) + _rgb("#9cc6e0") * a
    return img


def _aurora(size: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    W, H = size
    img = _gradient(size, [(0, "#020814"), (0.6, "#0b2233"), (1, "#05121c")])
    _stars(img, rng, int(W * H / 2500), 0.75)
    x = np.linspace(0, 1, W)
    yy = np.arange(H)[:, None] / H
    for color, y0, amp, width, strength in [("#38f0a0", 0.28, 0.10, 0.10, 0.55),
                                            ("#2bd4c4", 0.36, 0.08, 0.07, 0.45),
                                            ("#9a6cf0", 0.22, 0.07, 0.05, 0.30)]:
        ph = rng.uniform(0, 6.28, 3)
        curve = y0 + amp * (np.sin(2 * np.pi * (1.1 * x + ph[0])) * 0.6 + np.sin(2 * np.pi * (2.7 * x + ph[1])) * 0.3)
        # Cortinas: intensas en la curva y deshilachadas hacia arriba.
        dist = curve[None, :] - yy
        curtain = np.where(dist > 0, np.exp(-dist / width), np.exp(-(dist / (width * 0.25)) ** 2))
        wobble = sum(np.sin(2 * np.pi * (f * x + rng.uniform(0, 1))) for f in (17, 31, 53)) / 3
        rays = (0.78 + 0.22 * wobble)[None, :]
        a = np.clip(curtain * rays * strength, 0, 1)[..., None]
        img = img * (1 - a) + _rgb(color) * a
    # Montañas y su reflejo en el lago.
    ridge = _ridge(rng, W, H * 0.72, H * 0.14, 6)
    _fill_below(img, ridge, "#061017")
    lake = H * 0.78
    top = img[: int(lake)][::-1][: H - int(lake)]
    reflection = np.zeros_like(img[int(lake):])
    reflection[: len(top)] = top
    img[int(lake):] = img[int(lake):] * 0.55 + reflection * 0.3
    return img


def _pradera(size: tuple[int, int], rng: np.random.Generator) -> np.ndarray:
    W, H = size
    img = _gradient(size, [(0, "#3e6fa8"), (0.4, "#9fb9c9"), (0.6, "#f4d29c"), (1, "#5e6a2a")])
    _glow(img, W * 0.8, H * 0.5, min(W, H) * 0.4, "#ffe2a8", 0.6)
    _disc(img, W * 0.8, H * 0.5, min(W, H) * 0.055, "#fff5dc")
    for color, base, amp in [("#a7a25a", 0.66, 0.08), ("#7f8a3c", 0.75, 0.10), ("#5a6b2a", 0.86, 0.10),
                             ("#3a4a1c", 0.97, 0.10)]:
        _fill_below(img, _ridge(rng, W, base * H, amp * H, 3), color)
        _mist(img, base - 0.02, 0.03, 0.10, "#ffe2b0")
    return img


_PAINTERS = {
    "bosque": _bosque,
    "montanas": _montanas,
    "oceano": _oceano,
    "aurora": _aurora,
    "pradera": _pradera,
}


@lru_cache(maxsize=12)
def preset_background(preset: str, size: tuple[int, int]) -> Image.Image:
    if preset == "liso" or preset not in _PAINTERS:
        return Image.new("RGB", size, SOLID)
    # Se pinta a resolución reducida y se escala: más rápido y con un acabado suave.
    W, H = size
    scale = min(1.0, 960 / max(W, H))
    small = (max(1, int(W * scale)), max(1, int(H * scale)))
    rng = np.random.default_rng(sum(map(ord, preset)) * 7919)
    img = _PAINTERS[preset](small, rng)
    out = _finish(img)
    if small != size:
        out = out.resize(size, Image.LANCZOS)
    return out


@lru_cache(maxsize=12)
def image_background(path: str, size: tuple[int, int], mtime: float = 0.0) -> Image.Image:
    """Foto propia recortada al formato, con oscurecido y viñeta."""
    img = ImageOps.fit(ImageOps.exif_transpose(Image.open(path)).convert("RGB"), size, Image.LANCZOS)
    img = img.filter(ImageFilter.GaussianBlur(max(1, min(size) / 600)))  # quita detalle que distrae
    return _finish(np.asarray(img, dtype=np.float32), darken=0.35)


def make_background(spec: str | None, size: tuple[int, int], custom: Path | None = None) -> Image.Image:
    if custom is not None:
        return image_background(str(custom), size, custom.stat().st_mtime)
    return preset_background(spec or "liso", size)
