# HotaruPodcast

Herramienta para preparar podcasts: transcribe el audio, detecta las distintas voces y
(en próximas fases) permite completar los perfiles de cada interviniente, corregir qué
tramos dice cada uno, limpiar el ruido en los silencios y generar un vídeo donde se
anima el perfil de quien habla.

## Estado

**Fase 1 (esta versión): transcripción y voces sugeridas.**

- Subida de audio o vídeo (MP3, WAV, M4A, FLAC, OGG, MP4…).
- Transcripción con marcas de tiempo por palabra (WhisperX) y separación de voces
  (pyannote). Se puede indicar el número de voces, o un mínimo y máximo, para afinar.
- Lista de voces sugeridas ("Voz 1", "Voz 2"…), ordenadas por tiempo de habla, con un
  clip de muestra de unos 6 s de cada una para reconocerla.
- Fusionar voces cuando el detector separó a la misma persona en dos.
- Transcripción coloreada por voz; al pulsar la hora se escucha desde ese punto.
- Se guarda la huella de voz de cada interviniente para reconocerle en otros episodios
  (se usará en la fase 5).

**Fase 2: perfiles de intervinientes y editor de tramos.**

- Biblioteca de intervinientes común a todos los episodios (menú "Intervinientes"):
  nombre, cargo opcional, foto (JPG, PNG o WebP, hasta 5 MB), información adicional y color.
- En cada episodio, cada voz se asigna a un interviniente existente o se crea uno nuevo
  desde la propia tarjeta de la voz. Un interviniente solo puede estar en una voz por
  episodio; si aparece en dos, se fusionan.
- En la transcripción se selecciona con el ratón cualquier tramo (desde una palabra hasta
  varios párrafos) y se asigna a otra voz o a una voz nueva. Los turnos se recomponen solos
  y las voces que se quedan sin intervenciones desaparecen.
- Deshacer y rehacer (botones o Ctrl+Z / Ctrl+Shift+Z) para fusiones, asignaciones y
  reasignaciones; se guardan los últimos 50 cambios de cada episodio.

**Fase 3: reducción de ruido en los huecos sin voz.**

- Tres intensidades (suave, media, fuerte). En toda la pista se aplica un filtro paso alto
  y una reducción de ruido espectral (FFmpeg `afftdn`) que toca poco la voz. En los huecos
  sin voz el fondo baja además 8, 14 o 20 dB, con rampas de 120 ms: no se silencia ni
  desaparece de golpe.
- Los tramos de voz salen de la transcripción ya corregida, con margen antes y después.
  La opción "Respetar respiraciones" deja más margen tras cada intervención, y las pausas
  de menos de 0,6 s no se tocan.
- Al final se iguala el volumen a −16 LUFS, el estándar de podcast.
- Escucha previa de 20 s antes/después desde cualquier punto, antes de procesar todo.
- El episodio limpio se escucha y se descarga en MP3. Si después se corrigen tramos de voz,
  se avisa de que hay que volver a limpiarlo.

**Fase 4: vídeo con el interviniente que habla animado.**

- Dos plantillas: **Mesa redonda** (todos a la vez; el que habla se ilumina) y **Foco**
  (el que habla, grande en el centro, con fundido al cambiar de interviniente; el resto,
  en miniatura abajo).
- Cuatro formatos: 16:9 (1920×1080), 9:16 (1080×1920), 1:1 (1080×1080) y 4:5 (1080×1350).
- El avatar del que habla late con su voz: crece un poco y le salen un halo y un anillo
  de su color, según el volumen de cada fotograma, suavizado en unos 150 ms para que no
  parpadee. Opcionalmente se atenúa a los que no hablan.
- Usa el nombre, el cargo, la foto y el color de cada interviniente, o sus iniciales si no
  tiene foto, y el título del episodio. Puede llevar el audio limpio de la fase 3.
- Vista previa instantánea de un fotograma con el formato elegido; el render va en segundo
  plano con barra de progreso, y el resultado se descarga en MP4 (H.264 + AAC).
- Se dibuja con Pillow y se codifica con FFmpeg: no hace falta Node ni un navegador en el
  servidor. Como referencia, en un servidor de 4 núcleos un minuto de vídeo 1080p tarda
  unos 17 s en generarse.
- Fondos: liso o cinco paisajes de naturaleza generados (bosque entre la niebla, montañas
  al atardecer, océano al anochecer, aurora boreal y pradera a la hora dorada), en
  cualquier formato. También se pueden subir fotos propias (JPG, PNG o WebP, hasta 15 MB),
  que se recortan al formato y se oscurecen con una viñeta. Los rótulos llevan una sombra
  suave para leerse sobre cualquier fondo. El fondo es fijo, así que no ralentiza el render.

**Fase 5: reconocer a los intervinientes recurrentes por su voz.**

- Al procesar un episodio, cada voz sin asignar se compara con la huella de voz de los
  intervinientes que ya aparecen en otros episodios. Si se parece lo suficiente, la tarjeta
  de la voz pregunta "¿Es María López?" con el parecido, y se acepta con "Sí" o se descarta
  con "No es" (no se vuelve a sugerir para esa voz). Arriba, "Asignar a todos" acepta de
  una vez las sugerencias seguras.
- La huella de cada interviniente es la media de sus voces en todos los episodios donde
  está asignado, ponderada por el tiempo de habla, así que mejora con cada episodio. No se
  guarda aparte: deshacer, fusionar o borrar un episodio o un perfil la actualiza sola.
- Cada interviniente se sugiere como mucho en una voz por episodio, y nunca si ya está.
- Umbrales (similitud coseno de las huellas de pyannote): se sugiere desde 0,5 y se marca
  como segura desde 0,7. Se cambian con `HOTARU_MATCH_THRESHOLD` y `HOTARU_MATCH_CONFIDENT`.

Próximas fases: más plantillas (cara a cara,
audiograma, subtítulos protagonistas) y estilos de animación (ondas, barras).

## Estructura

```
backend/   API en Python (FastAPI). La lógica de voces está en app/pipeline/voices.py
frontend/  Interfaz web (React + Vite)
```

Los datos se guardan en disco: una carpeta por episodio en `data/episodes/<id>/` y los
intervinientes en `data/profiles/`; las fotos de fondo, en `data/backgrounds/`.

## Puesta en marcha

Requisitos: Python 3.10+, Node 20+ y FFmpeg.

### Servidor

```bash
cd backend
python -m venv .venv && source .venv/bin/activate
pip install -e ".[ml,dev]"        # [ml] instala WhisperX, pyannote y torch
export HOTARU_HF_TOKEN=hf_xxx      # token de Hugging Face (ver abajo)
uvicorn app.main:create_app --factory --port 8000
```

Para probar la interfaz sin modelos (genera turnos ficticios), instala sin `[ml]` y
arranca con `HOTARU_ENGINE=fake`.

**Token de Hugging Face:** crea un token de lectura en
https://huggingface.co/settings/tokens y acepta las condiciones de
`pyannote/speaker-diarization-community-1` con la misma cuenta.

### Interfaz web

```bash
cd frontend
npm install
npm run dev     # http://localhost:5173 (redirige /api al puerto 8000)
```

### Configuración

| Variable | Por defecto | Para qué sirve |
| --- | --- | --- |
| `HOTARU_ENGINE` | `whisperx` | `fake` para desarrollo sin modelos |
| `HOTARU_DATA_DIR` | `data` | Carpeta de episodios |
| `HOTARU_WHISPER_MODEL` | `large-v3` | Modelo de Whisper (`medium`, `small`… son más rápidos) |
| `HOTARU_LANGUAGE` | `es` | Idioma; vacío para detección automática |
| `HOTARU_DEVICE` | `cpu` | `cuda` si hay GPU NVIDIA |
| `HOTARU_COMPUTE_TYPE` | `int8` | `float16` en GPU |
| `HOTARU_BATCH_SIZE` | `8` | Súbelo en GPU (16–32) |
| `HOTARU_HF_TOKEN` | — | Token de Hugging Face para pyannote |
| `HOTARU_DIARIZATION_MODEL` | community-1 | Otro modelo de pyannote |
| `HOTARU_SAMPLE_SECONDS` | `6` | Duración del clip de muestra de cada voz |

En CPU, un episodio tarda aproximadamente lo mismo que dura; con GPU, unas 10 veces menos.

## Pruebas

```bash
cd backend && pytest -q
cd frontend && npm run build
```
