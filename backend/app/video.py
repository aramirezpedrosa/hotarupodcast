"""Vídeo del episodio: los intervinientes en pantalla y el que habla "late" con su voz.

Se dibuja con Pillow y se codifica con FFmpeg (H.264 + AAC). Para que una hora de
vídeo se renderice en minutos y no en horas:

- El fondo (color, título y los intervinientes en reposo) se dibuja una sola vez.
- Cada interviniente ocupa un "hueco" (slot) rectangular. Su aspecto activo se
  precalcula para unos pocos niveles de volumen (LEVELS) y se guarda en caché.
- En cada fotograma solo se copian los huecos que cambian respecto al anterior.

Plantillas (ver docs del diseño):
- mesa: todos en fila o cuadrícula; el que habla se ilumina y crece.
- foco: el que habla, grande en el centro; el resto, en miniatura abajo.
"""

from __future__ import annotations

import math
import os
import subprocess
import threading
from collections import OrderedDict
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .audio import AudioError
from .backgrounds import make_background

FPS = 25
# Niveles de volumen precalculados para la animación (0 = en silencio).
LEVELS = 12
# Suavizado del volumen: sube rápido y baja en unos 150 ms, para que no parpadee.
ATTACK = 0.6
RELEASE_SECONDS = 0.15
# Margen tras cada intervención durante el que el interviniente sigue "activo".
HOLD_SECONDS = 0.3
# Duración del cambio de interviniente en la plantilla foco.
FOCUS_FADE_FRAMES = 6

# Animaciones con movimiento propio (ondas, barras): pasos del ciclo y fotogramas por paso.
PHASES = 12
FRAMES_PER_PHASE = 2
# Memoria máxima para los huecos ya dibujados (las animaciones con fases generan muchos).
PATCH_CACHE_BYTES = 400 * 1024 * 1024

BACKGROUND = (17, 20, 24)
TEXT = (240, 240, 240)
TEXT_MUTED = (160, 166, 173)


class Template(str, Enum):
    mesa = "mesa"
    foco = "foco"
    cara_a_cara = "cara_a_cara"
    audiograma = "audiograma"


class Animation(str, Enum):
    pulso = "pulso"    # halo y anillo que crecen con la voz
    ondas = "ondas"    # ondas que salen del avatar
    barras = "barras"  # ecualizador en círculo alrededor del avatar


class Aspect(str, Enum):
    horizontal = "16:9"
    vertical = "9:16"
    cuadrado = "1:1"
    retrato = "4:5"


SIZES = {
    Aspect.horizontal: (1920, 1080),
    Aspect.vertical: (1080, 1920),
    Aspect.cuadrado: (1080, 1080),
    Aspect.retrato: (1080, 1350),
}


@dataclass(frozen=True)
class Participant:
    voice_id: str
    name: str
    color: str
    role: str | None = None
    photo: Path | None = None


@dataclass(frozen=True)
class VideoOptions:
    template: Template = Template.mesa
    aspect: Aspect = Aspect.horizontal
    dim_others: bool = True
    show_names: bool = True
    title: str | None = None
    background: str = "liso"             # id de preset de naturaleza
    background_image: Path | None = None  # foto propia (tiene prioridad)
    animation: Animation = Animation.pulso
    subtitles: bool = False


# --- Utilidades de dibujo -------------------------------------------------------


def _shadowed_text(img: Image.Image, xy, text: str, f, fill, anchor: str) -> None:
    """Texto con una sombra difuminada debajo, para que se lea sobre fondos con imagen."""
    size = getattr(f, "size", 20)
    stroke = max(1, int(size * 0.08))
    blur = max(1.5, size * 0.12)
    dy = max(1, size * 0.05)
    # La sombra se dibuja en una capa del tamaño del texto (no de toda la imagen).
    l, t, r, b = ImageDraw.Draw(img).textbbox(xy, text, font=f, anchor=anchor, stroke_width=stroke)
    pad = int(blur * 3) + 2
    x0, y0 = max(0, int(l) - pad), max(0, int(t + dy) - pad)
    x1, y1 = min(img.width, int(r) + pad), min(img.height, int(b + dy) + pad)
    if x1 > x0 and y1 > y0:
        shadow = Image.new("RGBA", (x1 - x0, y1 - y0), (0, 0, 0, 0))
        ImageDraw.Draw(shadow).text(
            (xy[0] - x0, xy[1] + dy - y0), text, font=f, fill=(0, 0, 0, 200), anchor=anchor,
            stroke_width=stroke, stroke_fill=(0, 0, 0, 200),
        )
        img.alpha_composite(shadow.filter(ImageFilter.GaussianBlur(blur)), (x0, y0))
    ImageDraw.Draw(img).text(xy, text, font=f, fill=fill, anchor=anchor)

_FONT_CANDIDATES = {
    True: [
        "DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans-Bold.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Bold.ttf",
        "/Library/Fonts/Arial Bold.ttf",
        "C:/Windows/Fonts/arialbd.ttf",
    ],
    False: [
        "DejaVuSans.ttf",
        "/usr/share/fonts/truetype/dejavu/DejaVuSans.ttf",
        "/usr/share/fonts/truetype/liberation/LiberationSans-Regular.ttf",
        "/Library/Fonts/Arial.ttf",
        "C:/Windows/Fonts/arial.ttf",
    ],
}


@lru_cache(maxsize=64)
def font(size: int, bold: bool = False) -> ImageFont.FreeTypeFont:
    custom = os.environ.get("HOTARU_FONT_BOLD" if bold else "HOTARU_FONT")
    for path in ([custom] if custom else []) + _FONT_CANDIDATES[bold]:
        try:
            return ImageFont.truetype(path, size)
        except OSError:
            continue
    return ImageFont.load_default(size=size)


def hex_rgb(color: str) -> tuple[int, int, int]:
    c = color.lstrip("#")
    return int(c[0:2], 16), int(c[2:4], 16), int(c[4:6], 16)


def initials(name: str) -> str:
    parts = [p for p in name.split() if p]
    if not parts:
        return "?"
    if len(parts) == 1:
        return parts[0][:2].upper()
    return (parts[0][0] + parts[-1][0]).upper()


def fit_text(text: str, size: int, bold: bool, max_width: int) -> tuple[str, ImageFont.FreeTypeFont]:
    """Reduce la letra hasta un 30 % y, si aún no cabe, recorta con puntos suspensivos."""
    for s in range(size, int(size * 0.7) - 1, -2):
        f = font(s, bold)
        if f.getlength(text) <= max_width:
            return text, f
    f = font(int(size * 0.7), bold)
    while text and f.getlength(text + "…") > max_width:
        text = text[:-1]
    return text.rstrip() + "…", f


@lru_cache(maxsize=256)
def _avatar(photo: str | None, name: str, color: str, diameter: int) -> Image.Image:
    """Avatar circular RGBA: la foto recortada o las iniciales sobre el color."""
    big = diameter * 2  # se dibuja al doble y se reduce, para bordes suaves
    mask = Image.new("L", (big, big), 0)
    ImageDraw.Draw(mask).ellipse((0, 0, big - 1, big - 1), fill=255)
    if photo:
        try:
            img = ImageOps.fit(Image.open(photo).convert("RGB"), (big, big), Image.LANCZOS)
        except OSError:
            img = None
    else:
        img = None
    if img is None:
        img = Image.new("RGB", (big, big), hex_rgb(color))
        d = ImageDraw.Draw(img)
        f = font(int(big * 0.36), True)
        d.text((big / 2, big / 2), initials(name), font=f, fill=(255, 255, 255), anchor="mm")
    out = img.convert("RGBA")
    out.putalpha(mask)
    return out.resize((diameter, diameter), Image.LANCZOS)


# --- Maquetación ----------------------------------------------------------------


@dataclass
class Slot:
    """Rectángulo de pantalla de un interviniente; el avatar va centrado en (cx, cy)."""

    x: int
    y: int
    w: int
    h: int
    cx: int
    cy: int
    radius: int
    name_size: int
    role_size: int
    labels: bool = True
    panel: bool = False  # cara a cara: panel suave con el color del interviniente


@dataclass
class Layout:
    size: tuple[int, int]
    slots: list[Slot]
    # Solo en la plantilla foco: el hueco grande del centro.
    focus: Slot | None = None
    title_box: tuple[int, int, int, int] | None = None
    # Banda de subtítulos y banda de la forma de onda (audiograma): (x, y, w, h).
    caption_box: tuple[int, int, int, int] | None = None
    wave_box: tuple[int, int, int, int] | None = None


def _slot(x, y, w, h, radius, labels, avatar_cy=None) -> Slot:
    name_size = max(14, int(radius * 0.26))
    role_size = max(12, int(radius * 0.19))
    if avatar_cy is None:
        label_h = (name_size + role_size + radius * 0.45) if labels else 0
        avatar_cy = y + (h - label_h) / 2
    return Slot(int(x), int(y), int(w), int(h), int(x + w / 2), int(avatar_cy), int(radius),
                name_size, role_size, labels)


def _grid(n: int, x: int, y: int, w: int, h: int, labels: bool, max_radius: float) -> list[Slot]:
    """Reparte n huecos en la cuadrícula que deja los avatares más grandes."""
    best = None
    for cols in range(1, n + 1):
        rows = math.ceil(n / cols)
        sw, sh = w / cols, h / rows
        # El avatar con el pulso al máximo (1.3 r) y los rótulos deben caber en el hueco.
        r = min(sw * 0.36, sh / (2.75 + (0.95 if labels else 0)), max_radius)
        if best is None or r > best[0]:
            best = (r, cols, rows)
    r, cols, rows = best
    sw, sh = w / cols, h / rows
    slots = []
    for i in range(n):
        row, col = divmod(i, cols)
        in_row = min(cols, n - row * cols)
        offset = (cols - in_row) * sw / 2  # la última fila incompleta, centrada
        slots.append(_slot(x + offset + col * sw, y + row * sh, sw, sh, r, labels))
    return slots


def make_layout(n: int, opts: VideoOptions) -> Layout:
    W, H = SIZES[Aspect(opts.aspect)]
    margin = int(min(W, H) * 0.05)
    top = margin
    bottom = H - margin
    title_box = caption_box = None
    if opts.title:
        th = int(min(W, H) * 0.09)
        title_box = (margin, margin, W - 2 * margin, th)
        top = margin + th
    if opts.subtitles:
        ch = int(min(W, H) * (0.2 if H > W else 0.17))
        caption_box = (margin, bottom - ch, W - 2 * margin, ch)
        bottom -= ch + int(margin * 0.3)
    area = (margin, top, W - 2 * margin, bottom - top)
    n = max(n, 1)
    template = Template(opts.template)
    labels = opts.show_names

    if template == Template.mesa or (template == Template.cara_a_cara and n == 1):
        slots = _grid(n, *area, labels=labels, max_radius=min(W, H) * 0.2)
        return Layout((W, H), slots, None, title_box, caption_box)

    ax, ay, aw, ah = area
    if template == Template.audiograma:
        # El que habla y la forma de onda del audio: en horizontal, uno al lado del otro;
        # en vertical o cuadrado, el que habla arriba y la onda debajo, a todo el ancho.
        if W > H:
            fw = int(aw * 0.34)
            big_r = min(fw * 0.36, ah / (2.75 + (0.95 if labels else 0)))
            focus = _slot(ax, ay, fw, ah, big_r, labels)
            wave = (ax + fw, ay + int(ah * 0.2), aw - fw, int(ah * 0.6))
            return Layout((W, H), [], focus, title_box, caption_box, wave)
        wave_h = int(ah * (0.3 if H > W else 0.34))
        big_h = ah - wave_h
        big_r = min(aw * 0.28, big_h / (2.75 + (0.95 if labels else 0)))
        focus = _slot(ax, ay, aw, big_h, big_r, labels)
        return Layout((W, H), [], focus, title_box, caption_box, (ax, ay + big_h, aw, wave_h))

    if template == Template.cara_a_cara:
        # Los dos primeros, enfrentados a pantalla partida; el resto, en una fila abajo.
        rest = n - 2
        strip_h = int(ah * 0.22) if rest else 0
        hh = ah - strip_h
        gap = int(margin * 0.5)
        if W >= H:
            boxes = [(ax, ay, (aw - gap) / 2, hh), (ax + (aw + gap) / 2, ay, (aw - gap) / 2, hh)]
        else:
            boxes = [(ax, ay, aw, (hh - gap) / 2), (ax, ay + (hh + gap) / 2, aw, (hh - gap) / 2)]
        bw, bh = boxes[0][2], boxes[0][3]
        r = min(bw * 0.3, bh / (2.75 + (0.95 if labels else 0)))
        slots = [_slot(*b, r, labels) for b in boxes]
        for sl in slots:
            sl.panel = True
        if rest:
            sw = aw / rest
            small_r = min(sw * 0.3, strip_h / (2.75 + 0.95), r * 0.45)
            slots += [_slot(ax + i * sw, ay + hh, sw, strip_h, small_r, labels) for i in range(rest)]
        return Layout((W, H), slots, None, title_box, caption_box)

    # foco: zona grande arriba y fila de miniaturas abajo.
    strip_h = int(ah * (0.24 if H >= W else 0.27))
    big_h = ah - strip_h
    big_r = min(aw * 0.28, big_h / (2.75 + (0.95 if labels else 0)))
    focus = _slot(ax, ay, aw, big_h, big_r, labels)
    small_r = min(aw / n * 0.3, strip_h / (2.75 + 0.95), big_r * 0.45)
    sw = aw / n
    slots = [
        _slot(ax + i * sw, ay + big_h, sw, strip_h, small_r, labels)
        for i in range(n)
    ]
    return Layout((W, H), slots, focus, title_box, caption_box)


# --- Subtítulos -------------------------------------------------------------------


@dataclass
class Caption:
    """Un trozo de subtítulo: unas pocas palabras seguidas de un mismo interviniente."""

    start: float
    end: float
    idx: int
    words: list[tuple[float, float, str]]


def build_captions(
    words: list[tuple[float, float, str, int]], max_chars: int, gap: float = 1.0
) -> list[Caption]:
    """Agrupa las palabras en subtítulos cortos: corta al cambiar de interviniente, en las
    pausas, al llegar al máximo de letras o tras un punto si ya hay bastante texto."""
    out: list[Caption] = []
    cur: list[tuple[float, float, str]] = []
    cur_idx, length = -1, 0
    for start, end, text, idx in sorted(words):
        text = text.strip()
        if not text:
            continue
        brk = cur and (
            idx != cur_idx
            or start - cur[-1][1] > gap
            or length + 1 + len(text) > max_chars
            or (cur[-1][2][-1] in ".?!…" and length >= max_chars * 0.4)
        )
        if brk:
            out.append(Caption(cur[0][0], cur[-1][1], cur_idx, cur))
            cur, length = [], 0
        cur.append((start, end, text))
        cur_idx = idx
        length += len(text) + (1 if length else 0)
    if cur:
        out.append(Caption(cur[0][0], cur[-1][1], cur_idx, cur))
    # Cada subtítulo se queda en pantalla un poco más, sin pisar al siguiente.
    for a, b in zip(out, out[1:]):
        a.end = min(a.end + 0.6, b.start)
    if out:
        out[-1].end += 0.6
    return out


def _wrap(text_words: list[str], f, max_width: float) -> list[list[int]]:
    """Reparte las palabras (por índice) en líneas que caben en max_width."""
    lines: list[list[int]] = [[]]
    width = 0.0
    space = f.getlength(" ")
    for i, w in enumerate(text_words):
        wl = f.getlength(w)
        if lines[-1] and width + space + wl > max_width:
            lines.append([])
            width = 0.0
        width += (space if lines[-1] else 0) + wl
        lines[-1].append(i)
    return lines


# --- Render de fotogramas ---------------------------------------------------------


class FrameRenderer:
    def __init__(
        self,
        participants: list[Participant],
        opts: VideoOptions,
        words: list[tuple[float, float, str, int]] | None = None,
    ):
        self.p = participants
        self.opts = opts
        self.animation = Animation(opts.animation)
        self.layout = make_layout(len(participants), opts)
        self.W, self.H = self.layout.size
        self._patches: OrderedDict[tuple, np.ndarray] = OrderedDict()
        self._patch_bytes = 0
        self._caption_cache: OrderedDict[tuple, np.ndarray] = OrderedDict()

        self.captions: list[Caption] = []
        if self.layout.caption_box:
            cw, ch = self.layout.caption_box[2:]
            self.caption_font = font(int(ch * 0.27), True)
            per_line = max(12, int(cw * 0.9 / (self.caption_font.getlength("abcde fghij") / 11)))
            self.captions = self._fit_captions(build_captions(words or [], max_chars=int(per_line * 1.7)))

        bg = make_background(opts.background, (self.W, self.H), opts.background_image).convert("RGBA")
        if self.layout.title_box and opts.title:
            x, y, w, h = self.layout.title_box
            text, f = fit_text(opts.title, int(h * 0.55), True, w)
            _shadowed_text(bg, (x + w / 2, y + h / 2), text, f, TEXT, "mm")
        self.bg = np.asarray(bg.convert("RGB")).copy()
        if self.layout.wave_box:
            x, y, w, h = self.layout.wave_box
            self.wave_bg = self.bg[y:y + h, x:x + w].copy()

        # Fondo con todos en reposo; el hueco grande de "foco" empieza con el primero.
        self.base = self.bg.copy()
        for i, slot in enumerate(self.layout.slots):
            self._paste(self.base, slot, self.patch(slot, i, None))
        if self.layout.focus and participants:
            self._paste(self.base, self.layout.focus, self.patch(self.layout.focus, 0, 0, focus=True))

    def _fit_captions(self, caps: list[Caption]) -> list[Caption]:
        """Parte los subtítulos que no caben en dos líneas."""
        out = []
        width = self.layout.caption_box[2] * 0.92
        for c in caps:
            lines = _wrap([t for _, _, t in c.words], self.caption_font, width)
            if len(lines) <= 2:
                out.append(c)
                continue
            for j in range(0, len(lines), 2):
                idx = [i for line in lines[j:j + 2] for i in line]
                ws = [c.words[i] for i in idx]
                out.append(Caption(ws[0][0], ws[-1][1], c.idx, ws))
        for a, b in zip(out, out[1:]):
            if a.idx == b.idx and a.end > b.start:
                a.end = b.start
        return out

    # Un hueco dibujado: fondo + brillo + avatar + rótulos. level None = en reposo.
    def patch(
        self, slot: Slot, idx: int, level: int | None, focus: bool = False, phase: int = 0
    ) -> np.ndarray:
        if level is None or self.animation == Animation.pulso:
            phase = 0
        elif level is not None:
            # Con movimiento propio basta la mitad de niveles: menos huecos distintos que dibujar.
            level = min(LEVELS - 1, (level // 2) * 2 + 1)
        key = (id(slot), idx, level, focus, phase)
        cached = self._patches.get(key)
        if cached is not None:
            self._patches.move_to_end(key)
            return cached
        p = self.p[idx]
        region = Image.fromarray(self.bg[slot.y:slot.y + slot.h, slot.x:slot.x + slot.w])
        img = region.convert("RGBA")
        cx, cy, r = slot.cx - slot.x, slot.cy - slot.y, slot.radius
        color = hex_rgb(p.color)
        active = level is not None
        lv = (level or 0) / (LEVELS - 1)
        dim = self.opts.dim_others and not active and not focus

        if slot.panel:
            # Cara a cara: cada mitad lleva un panel suave del color del interviniente.
            panel = Image.new("RGBA", img.size, (0, 0, 0, 0))
            pad = int(min(slot.w, slot.h) * 0.02)
            ImageDraw.Draw(panel).rounded_rectangle(
                (pad, pad, slot.w - pad, slot.h - pad), radius=int(min(slot.w, slot.h) * 0.06),
                fill=(*color, int(60 + 40 * lv) if active else 34),
                outline=(*color, 150 if active else 60), width=max(2, int(r * 0.02)),
            )
            img = Image.alpha_composite(img, panel)

        if active and self.animation == Animation.ondas:
            img = _draw_waves(img, cx, cy, r, color, lv, phase)
        elif active and self.animation == Animation.barras:
            img = _draw_bars(img, cx, cy, r, color, lv, phase, seed=idx)
        elif active:
            # Halo difuminado y anillo que crecen con el volumen.
            glow = Image.new("RGBA", img.size, (0, 0, 0, 0))
            gr = r * (1.05 + 0.2 * lv)
            ImageDraw.Draw(glow).ellipse(
                (cx - gr, cy - gr, cx + gr, cy + gr), fill=(*color, int(90 + 110 * lv))
            )
            glow = glow.filter(ImageFilter.GaussianBlur(max(2, r * 0.12)))
            img = Image.alpha_composite(img, glow)
            ring = Image.new("RGBA", img.size, (0, 0, 0, 0))
            rr = r * (1.06 + 0.16 * lv)
            ImageDraw.Draw(ring).ellipse(
                (cx - rr, cy - rr, cx + rr, cy + rr),
                outline=(*color, int(160 + 95 * lv)),
                width=max(2, int(r * 0.05)),
            )
            img = Image.alpha_composite(img, ring)

        grow = 0.06 if self.animation == Animation.pulso else 0.03
        d = int(2 * r * (1 + grow * lv)) if active else 2 * r
        av = _avatar(str(p.photo) if p.photo else None, p.name, p.color, d)
        if dim:
            rgb = ImageEnhance.Brightness(av.convert("RGB")).enhance(0.45)
            rgb = ImageEnhance.Color(rgb).enhance(0.5).convert("RGBA")
            rgb.putalpha(av.getchannel("A"))
            av = rgb
        img.alpha_composite(av, (int(cx - d / 2), int(cy - d / 2)))

        if slot.labels:
            y = cy + r * 1.32
            name, f = fit_text(p.name, slot.name_size, True, int(slot.w * 0.92))
            _shadowed_text(img, (cx, y), name, f, TEXT_MUTED if dim else TEXT, "mt")
            if p.role:
                y += slot.name_size * 1.25
                role, f = fit_text(p.role, slot.role_size, False, int(slot.w * 0.92))
                _shadowed_text(img, (cx, y), role, f, (128, 133, 140) if dim else TEXT_MUTED, "mt")

        arr = np.asarray(img.convert("RGB")).copy()
        self._patches[key] = arr
        self._patch_bytes += arr.nbytes
        while self._patch_bytes > PATCH_CACHE_BYTES and len(self._patches) > 1:
            _, old = self._patches.popitem(last=False)
            self._patch_bytes -= old.nbytes
        return arr

    @staticmethod
    def _paste(frame: np.ndarray, slot: Slot, patch: np.ndarray) -> None:
        frame[slot.y:slot.y + slot.h, slot.x:slot.x + slot.w] = patch

    def still(self, active: int | None, level: float = 0.6) -> Image.Image:
        """Un fotograma suelto (para la vista previa)."""
        frame = self.base.copy()
        q = int(round(level * (LEVELS - 1)))
        ph = PHASES // 3
        if active is not None and 0 <= active < len(self.p):
            if active < len(self.layout.slots):
                slot = self.layout.slots[active]
                self._paste(frame, slot, self.patch(slot, active, q, phase=ph))
            if self.layout.focus:
                self._paste(frame, self.layout.focus, self.patch(self.layout.focus, active, q, True, ph))
        if self.layout.wave_box:
            # Una forma de onda de ejemplo.
            n = 200
            t = np.linspace(0, 1, n)
            curve = np.clip(0.35 + 0.3 * np.sin(t * 31) * np.sin(t * 7) + 0.2 * np.sin(t * 97), 0, 1)
            tl = np.full(n, active if active is not None else -1)
            self._draw_wave(frame, n - 1, tl, curve)
        if self.layout.caption_box and active is not None:
            cap = next((c for c in self.captions if c.idx == active), None)
            if cap is None:
                cap = Caption(0, 1, active, [(0, 0, w) for w in "Así se verán los subtítulos del episodio".split()])
            self._draw_caption(frame, cap, -2, len(cap.words) // 2)
        return Image.fromarray(frame)

    # --- Audiograma ---------------------------------------------------------------

    def _draw_wave(self, frame: np.ndarray, k: int, timeline: np.ndarray, curve: np.ndarray) -> None:
        """Barras del volumen de los últimos segundos; la más reciente, a la derecha.
        Cada barra lleva el color de quien hablaba en ese momento."""
        x0, y0, w, h = self.layout.wave_box
        band = self.wave_bg.copy()
        pitch = max(6, int(round(w / 110)))
        bar = max(3, int(pitch * 0.6))
        n = w // pitch
        mid = h // 2
        colors = [np.array(hex_rgb(p.color), dtype=np.float32) for p in self.p]
        idle = np.array(TEXT_MUTED, dtype=np.float32) * 0.6
        for j in range(n):
            f = k - (n - 1 - j)
            if f < 0:
                continue
            v = float(curve[f])
            half = max(1, int(v * h * 0.46))
            who = int(timeline[f])
            c = colors[who] if 0 <= who < len(colors) else idle
            # Las barras antiguas se apagan un poco hacia la izquierda.
            fade = 0.45 + 0.55 * (j / max(1, n - 1))
            xa = j * pitch + (pitch - bar) // 2
            seg = band[mid - half:mid + half, xa:xa + bar].astype(np.float32)
            band[mid - half:mid + half, xa:xa + bar] = (seg * (1 - fade) + c * fade).astype(np.uint8)
        frame[y0:y0 + h, x0:x0 + w] = band

    # --- Subtítulos ---------------------------------------------------------------

    def _draw_caption(self, frame: np.ndarray, cap: Caption | None, cap_id: int, current: int) -> None:
        x0, y0, w, h = self.layout.caption_box
        key = (cap_id, current)
        img = self._caption_cache.get(key)
        if img is None:
            region = Image.fromarray(self.bg[y0:y0 + h, x0:x0 + w]).convert("RGBA")
            if cap is not None:
                f = self.caption_font
                texts = [t for _, _, t in cap.words]
                lines = _wrap(texts, f, w * 0.92)[:2]
                lh = f.size * 1.3
                color = hex_rgb(self.p[cap.idx].color) if 0 <= cap.idx < len(self.p) else TEXT
                y = h / 2 - lh * len(lines) / 2 + lh / 2
                space = f.getlength(" ")
                for line in lines:
                    total = sum(f.getlength(texts[i]) for i in line) + space * (len(line) - 1)
                    x = w / 2 - total / 2
                    for i in line:
                        fill = color if i == current else (TEXT if i < current else TEXT_MUTED)
                        _shadowed_text(region, (x, y), texts[i], f, fill, "lm")
                        x += f.getlength(texts[i]) + space
                    y += lh
            img = np.asarray(region.convert("RGB")).copy()
            self._caption_cache[key] = img
            while len(self._caption_cache) > 64:
                self._caption_cache.popitem(last=False)
        frame[y0:y0 + h, x0:x0 + w] = img

    def frames(self, timeline: np.ndarray, levels: np.ndarray, curve: np.ndarray | None = None, fps: int = FPS):
        """Genera los fotogramas (bytes RGB) a partir de quién habla y con qué volumen."""
        frame = self.base.copy()
        slots = self.layout.slots
        focus = self.layout.focus
        if curve is None:
            curve = levels.astype(np.float32) / (LEVELS - 1)
        painted: dict[int, tuple] = {}  # hueco -> estado dibujado
        big = 0
        big_state: tuple = (0, 0, 0)
        fade_from: np.ndarray | None = None
        fade = 0
        caps = self.captions
        ci = 0
        cap_state: tuple | None = None
        for k in range(len(timeline)):
            a = int(timeline[k])
            q = int(levels[k]) if a >= 0 else 0
            ph = (k // FRAMES_PER_PHASE) % PHASES
            # Huecos pequeños / de la mesa.
            for i, slot in enumerate(slots):
                want = (i, q, False, ph) if i == a else (i, None, False, 0)
                if painted.get(i, (i, None, False, 0)) != want:
                    self._paste(frame, slot, self.patch(slot, *want))
                    painted[i] = want
            # Hueco grande (foco, audiograma): sigue al último que habló, con fundido al cambiar.
            if focus is not None:
                if a >= 0 and a != big:
                    fade_from = frame[focus.y:focus.y + focus.h, focus.x:focus.x + focus.w].astype(np.float32)
                    fade, big = FOCUS_FADE_FRAMES, a
                state = (big, q, ph) if a == big else (big, 0, 0)
                if fade > 0:
                    t = 1 - fade / (FOCUS_FADE_FRAMES + 1)
                    new = self.patch(focus, state[0], state[1], True, state[2]).astype(np.float32)
                    self._paste(frame, focus, (fade_from * (1 - t) + new * t).astype(np.uint8))
                    fade -= 1
                    big_state = (-1, -1, -1)
                elif state != big_state:
                    self._paste(frame, focus, self.patch(focus, state[0], state[1], True, state[2]))
                    big_state = state
            if self.layout.wave_box:
                self._draw_wave(frame, k, timeline, curve)
            if self.layout.caption_box:
                t = k / fps
                while ci < len(caps) and caps[ci].end <= t:
                    ci += 1
                cap = caps[ci] if ci < len(caps) and caps[ci].start <= t else None
                if cap is None:
                    want_cap: tuple = (-1, 0)
                else:
                    cur = sum(1 for ws, _, _ in cap.words if ws <= t) - 1
                    want_cap = (ci, cur)
                if want_cap != cap_state:
                    self._draw_caption(frame, cap, *want_cap)
                    cap_state = want_cap
            yield frame.tobytes()


# --- Animaciones ---------------------------------------------------------------------


def _layer(img: Image.Image, cx, cy, extent):
    """Capa transparente cuadrada alrededor del avatar (más barata que una del hueco entero)."""
    x0 = max(0, int(cx - extent))
    y0 = max(0, int(cy - extent))
    x1 = min(img.width, int(math.ceil(cx + extent)))
    y1 = min(img.height, int(math.ceil(cy + extent)))
    return Image.new("RGBA", (max(1, x1 - x0), max(1, y1 - y0)), (0, 0, 0, 0)), x0, y0


def _draw_waves(img: Image.Image, cx, cy, r, color, lv: float, phase: int) -> Image.Image:
    """Tres ondas que nacen en el borde del avatar y se alejan desvaneciéndose."""
    # Un brillo suave detrás, que también sigue a la voz.
    glow, gx, gy = _layer(img, cx, cy, r * 1.3)
    gr = r * (1.02 + 0.06 * lv)
    ImageDraw.Draw(glow).ellipse(
        (cx - gx - gr, cy - gy - gr, cx - gx + gr, cy - gy + gr), fill=(*color, int(40 + 80 * lv))
    )
    img.alpha_composite(glow.filter(ImageFilter.GaussianBlur(max(2, r * 0.08))), (gx, gy))
    layer, x0, y0 = _layer(img, cx, cy, r * 1.42)
    d = ImageDraw.Draw(layer)
    width = max(2, int(r * (0.025 + 0.025 * lv)))
    lx, ly = cx - x0, cy - y0
    for j in range(3):
        t = ((phase / PHASES) + j / 3) % 1.0
        rr = r * (1.04 + (0.22 + 0.12 * lv) * t)
        alpha = int((1 - t) ** 1.5 * (120 + 135 * lv))
        d.ellipse((lx - rr, ly - rr, lx + rr, ly + rr), outline=(*color, alpha), width=width)
    img.alpha_composite(layer, (x0, y0))
    return img


def _draw_bars(img: Image.Image, cx, cy, r, color, lv: float, phase: int, seed: int) -> Image.Image:
    """Ecualizador en círculo: barras radiales cuya altura sigue a la voz."""
    n = 40
    rng = np.random.default_rng(1234 + seed)
    speeds = rng.uniform(0.6, 1.6, n)
    offsets = rng.uniform(0, 2 * np.pi, n)
    t = 2 * np.pi * phase / PHASES
    mod = 0.45 + 0.55 * np.abs(np.sin(speeds * t * 2 + offsets))
    layer, x0, y0 = _layer(img, cx, cy, r * 1.4)
    lx, ly = cx - x0, cy - y0
    d = ImageDraw.Draw(layer)
    inner = r * 1.08
    width = max(2, int(2 * np.pi * inner / n * 0.45))
    for i in range(n):
        ang = 2 * np.pi * i / n - np.pi / 2
        length = r * (0.04 + 0.24 * lv * mod[i])
        c, s_ = math.cos(ang), math.sin(ang)
        d.line(
            (lx + c * inner, ly + s_ * inner, lx + c * (inner + length), ly + s_ * (inner + length)),
            fill=(*color, int(170 + 85 * lv)), width=width,
        )
    img.alpha_composite(layer, (x0, y0))
    return img


# --- Datos de audio y de quién habla ------------------------------------------------


def speaker_timeline(
    segments: list[tuple[float, float, int]], n_frames: int, fps: int = FPS
) -> np.ndarray:
    """Índice del interviniente activo en cada fotograma (-1 si nadie habla)."""
    tl = np.full(n_frames, -1, dtype=np.int16)
    ordered = sorted(segments)
    for j, (s, e, idx) in enumerate(ordered):
        nxt = ordered[j + 1][0] if j + 1 < len(ordered) else math.inf
        end = min(e + HOLD_SECONDS, nxt)
        a, b = int(s * fps), min(n_frames, int(math.ceil(end * fps)))
        tl[a:b] = idx
    return tl


def loudness_levels(audio: Path, n_frames: int, fps: int = FPS, rate: int = 8000) -> np.ndarray:
    """Volumen de cada fotograma, suavizado y cuantizado en LEVELS niveles."""
    return quantize(loudness_curve(audio, n_frames, fps, rate))


def quantize(curve: np.ndarray) -> np.ndarray:
    return np.round(curve * (LEVELS - 1)).astype(np.int8)


def loudness_curve(audio: Path, n_frames: int, fps: int = FPS, rate: int = 8000) -> np.ndarray:
    """Volumen de cada fotograma entre 0 y 1, suavizado: sube rápido y baja con suavidad."""
    spf = rate // fps
    cmd = ["ffmpeg", "-v", "error", "-i", str(audio), "-vn", "-ac", "1", "-ar", str(rate),
           "-f", "f32le", "pipe:1"]
    try:
        proc = subprocess.Popen(cmd, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL)
    except FileNotFoundError as e:
        raise AudioError("No se encontró ffmpeg; instala FFmpeg.") from e
    rms = np.zeros(n_frames, dtype=np.float32)
    k = 0
    chunk_frames = fps * 10
    while k < n_frames:
        buf = proc.stdout.read(spf * chunk_frames * 4)
        if not buf:
            break
        x = np.frombuffer(buf[: len(buf) - len(buf) % 4], dtype=np.float32)
        m = len(x) // spf
        if m == 0:
            break
        block = x[: m * spf].reshape(m, spf)
        take = min(m, n_frames - k)
        rms[k:k + take] = np.sqrt((block[:take] ** 2).mean(axis=1))
        k += take
    proc.stdout.close()
    proc.wait()

    db = 20 * np.log10(rms + 1e-6)
    loud = db[db > -60]
    ref = np.percentile(loud, 95) if len(loud) else -20.0
    norm = np.clip((db - (ref - 30)) / 30, 0, 1)
    # Sube rápido y baja con suavidad.
    release = math.exp(-1 / (RELEASE_SECONDS * fps))
    out = np.empty_like(norm)
    v = 0.0
    for i, x in enumerate(norm):
        v = v + ATTACK * (x - v) if x > v else v * release + x * (1 - release)
        out[i] = v
    return out


# --- Render completo ---------------------------------------------------------------


def render_video(
    participants: list[Participant],
    segments: list[tuple[float, float, int]],
    audio: Path,
    duration: float,
    opts: VideoOptions,
    dst: Path,
    progress: Callable[[float], None] | None = None,
    fps: int = FPS,
    words: list[tuple[float, float, str, int]] | None = None,
) -> Path:
    if not participants:
        raise ValueError("El episodio no tiene intervinientes")
    n_frames = max(1, int(math.ceil(duration * fps)))
    renderer = FrameRenderer(participants, opts, words)
    timeline = speaker_timeline(segments, n_frames, fps)
    curve = loudness_curve(audio, n_frames, fps)
    levels = quantize(curve)
    W, H = renderer.W, renderer.H

    tmp = dst.with_name(dst.stem + ".part.mp4")
    cmd = [
        "ffmpeg", "-y", "-v", "error",
        "-f", "rawvideo", "-pix_fmt", "rgb24", "-s", f"{W}x{H}", "-r", str(fps), "-i", "pipe:0",
        "-i", str(audio),
        "-map", "0:v", "-map", "1:a",
        "-c:v", "libx264", "-preset", "veryfast", "-crf", "22", "-tune", "stillimage",
        "-pix_fmt", "yuv420p", "-c:a", "aac", "-b:a", "192k",
        "-shortest", "-movflags", "+faststart", str(tmp),
    ]
    try:
        enc = subprocess.Popen(cmd, stdin=subprocess.PIPE, stderr=subprocess.PIPE)
    except FileNotFoundError as e:
        raise AudioError("No se encontró ffmpeg; instala FFmpeg.") from e
    err: list[bytes] = []
    t = threading.Thread(target=lambda: err.append(enc.stderr.read()), daemon=True)
    t.start()
    step = max(1, n_frames // 100)
    try:
        for k, data in enumerate(renderer.frames(timeline, levels, curve, fps)):
            enc.stdin.write(data)
            if progress and k % step == 0:
                progress(k / n_frames)
    except BrokenPipeError:
        pass
    finally:
        enc.stdin.close()
        enc.wait()
        t.join()
    if enc.returncode != 0:
        tmp.unlink(missing_ok=True)
        msg = (err[0] if err else b"").decode(errors="replace").strip()
        raise AudioError(msg.splitlines()[-1] if msg else "No se pudo generar el vídeo")
    tmp.replace(dst)
    if progress:
        progress(1.0)
    return dst
