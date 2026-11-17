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
from collections.abc import Callable
from dataclasses import dataclass, field
from enum import Enum
from functools import lru_cache
from pathlib import Path

import numpy as np
from PIL import Image, ImageDraw, ImageEnhance, ImageFilter, ImageFont, ImageOps

from .audio import AudioError

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

BACKGROUND = (17, 20, 24)
TEXT = (240, 240, 240)
TEXT_MUTED = (160, 166, 173)


class Template(str, Enum):
    mesa = "mesa"
    foco = "foco"


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


# --- Utilidades de dibujo -------------------------------------------------------

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


@dataclass
class Layout:
    size: tuple[int, int]
    slots: list[Slot]
    # Solo en la plantilla foco: el hueco grande del centro.
    focus: Slot | None = None
    title_box: tuple[int, int, int, int] | None = None


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
    title_box = None
    if opts.title:
        th = int(min(W, H) * 0.09)
        title_box = (margin, margin, W - 2 * margin, th)
        top = margin + th
    area = (margin, top, W - 2 * margin, H - top - margin)
    n = max(n, 1)

    if Template(opts.template) == Template.mesa:
        slots = _grid(n, *area, labels=opts.show_names, max_radius=min(W, H) * 0.2)
        return Layout((W, H), slots, None, title_box)

    # foco: zona grande arriba y fila de miniaturas abajo.
    ax, ay, aw, ah = area
    strip_h = int(ah * (0.24 if H >= W else 0.27))
    big_h = ah - strip_h
    big_r = min(aw * 0.28, big_h / (2.75 + (0.95 if opts.show_names else 0)))
    focus = _slot(ax, ay, aw, big_h, big_r, opts.show_names)
    small_r = min(aw / n * 0.3, strip_h / (2.75 + 0.95), big_r * 0.45)
    sw = aw / n
    slots = [
        _slot(ax + i * sw, ay + big_h, sw, strip_h, small_r, opts.show_names)
        for i in range(n)
    ]
    return Layout((W, H), slots, focus, title_box)


# --- Render de fotogramas ---------------------------------------------------------


class FrameRenderer:
    def __init__(self, participants: list[Participant], opts: VideoOptions):
        self.p = participants
        self.opts = opts
        self.layout = make_layout(len(participants), opts)
        self.W, self.H = self.layout.size
        self._patches: dict[tuple, np.ndarray] = {}

        bg = Image.new("RGB", (self.W, self.H), BACKGROUND)
        if self.layout.title_box and opts.title:
            x, y, w, h = self.layout.title_box
            text, f = fit_text(opts.title, int(h * 0.55), True, w)
            ImageDraw.Draw(bg).text((x + w / 2, y + h / 2), text, font=f, fill=TEXT, anchor="mm")
        self.bg = np.asarray(bg).copy()

        # Fondo con todos en reposo; el hueco grande de "foco" empieza con el primero.
        self.base = self.bg.copy()
        for i, slot in enumerate(self.layout.slots):
            self._paste(self.base, slot, self.patch(slot, i, None))
        if self.layout.focus and participants:
            self._paste(self.base, self.layout.focus, self.patch(self.layout.focus, 0, 0, focus=True))

    # Un hueco dibujado: fondo + brillo + avatar + rótulos. level None = en reposo.
    def patch(self, slot: Slot, idx: int, level: int | None, focus: bool = False) -> np.ndarray:
        key = (id(slot), idx, level, focus)
        cached = self._patches.get(key)
        if cached is not None:
            return cached
        p = self.p[idx]
        region = Image.fromarray(self.bg[slot.y:slot.y + slot.h, slot.x:slot.x + slot.w])
        img = region.convert("RGBA")
        cx, cy, r = slot.cx - slot.x, slot.cy - slot.y, slot.radius
        color = hex_rgb(p.color)
        active = level is not None
        lv = (level or 0) / (LEVELS - 1)
        dim = self.opts.dim_others and not active and not focus

        if active:
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

        d = int(2 * r * (1 + 0.06 * lv)) if active else 2 * r
        av = _avatar(str(p.photo) if p.photo else None, p.name, p.color, d)
        if dim:
            rgb = ImageEnhance.Brightness(av.convert("RGB")).enhance(0.45)
            rgb = ImageEnhance.Color(rgb).enhance(0.5).convert("RGBA")
            rgb.putalpha(av.getchannel("A"))
            av = rgb
        img.alpha_composite(av, (int(cx - d / 2), int(cy - d / 2)))

        if slot.labels:
            draw = ImageDraw.Draw(img)
            y = cy + r * 1.32
            name, f = fit_text(p.name, slot.name_size, True, int(slot.w * 0.92))
            draw.text((cx, y), name, font=f, fill=TEXT_MUTED if dim else TEXT, anchor="mt")
            if p.role:
                y += slot.name_size * 1.25
                role, f = fit_text(p.role, slot.role_size, False, int(slot.w * 0.92))
                draw.text((cx, y), role, font=f, fill=(110, 115, 122) if dim else TEXT_MUTED, anchor="mt")

        arr = np.asarray(img.convert("RGB")).copy()
        self._patches[key] = arr
        return arr

    @staticmethod
    def _paste(frame: np.ndarray, slot: Slot, patch: np.ndarray) -> None:
        frame[slot.y:slot.y + slot.h, slot.x:slot.x + slot.w] = patch

    def still(self, active: int | None, level: float = 0.6) -> Image.Image:
        """Un fotograma suelto (para la vista previa)."""
        frame = self.base.copy()
        if active is not None and 0 <= active < len(self.p):
            q = int(round(level * (LEVELS - 1)))
            self._paste(frame, self.layout.slots[active], self.patch(self.layout.slots[active], active, q))
            if self.layout.focus:
                self._paste(frame, self.layout.focus, self.patch(self.layout.focus, active, q, focus=True))
        return Image.fromarray(frame)

    def frames(self, timeline: np.ndarray, levels: np.ndarray):
        """Genera los fotogramas (bytes RGB) a partir de quién habla y con qué volumen."""
        frame = self.base.copy()
        slots = self.layout.slots
        focus = self.layout.focus
        painted: dict[int, tuple] = {}  # hueco -> estado dibujado
        big = 0
        big_state = (0, 0)
        fade_from: np.ndarray | None = None
        fade = 0
        for k in range(len(timeline)):
            a = int(timeline[k])
            q = int(levels[k]) if a >= 0 else 0
            # Huecos pequeños / de la mesa.
            for i, slot in enumerate(slots):
                want = (i, q) if i == a else (i, None)
                if painted.get(i, (i, None)) != want:
                    self._paste(frame, slot, self.patch(slot, *want))
                    painted[i] = want
            # Hueco grande (foco): sigue al último que habló, con fundido al cambiar.
            if focus is not None:
                if a >= 0 and a != big:
                    fade_from = frame[focus.y:focus.y + focus.h, focus.x:focus.x + focus.w].astype(np.float32)
                    fade, big = FOCUS_FADE_FRAMES, a
                state = (big, q if a == big else 0)
                if fade > 0:
                    t = 1 - fade / (FOCUS_FADE_FRAMES + 1)
                    new = self.patch(focus, big, state[1], focus=True).astype(np.float32)
                    self._paste(frame, focus, (fade_from * (1 - t) + new * t).astype(np.uint8))
                    fade -= 1
                    big_state = (-1, -1)
                elif state != big_state:
                    self._paste(frame, focus, self.patch(focus, *state, focus=True))
                    big_state = state
            yield frame.tobytes()


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
    return np.round(out * (LEVELS - 1)).astype(np.int8)


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
) -> Path:
    if not participants:
        raise ValueError("El episodio no tiene intervinientes")
    n_frames = max(1, int(math.ceil(duration * fps)))
    renderer = FrameRenderer(participants, opts)
    timeline = speaker_timeline(segments, n_frames, fps)
    levels = loudness_levels(audio, n_frames, fps)
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
        for k, data in enumerate(renderer.frames(timeline, levels)):
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
