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

Próximas fases: perfiles (nombre, cargo, foto, información) y editor de tramos ·
reducción de ruido en los huecos sin voz · render de vídeo · reconocimiento de invitados
recurrentes.

## Estructura

```
backend/   API en Python (FastAPI). La lógica de voces está en app/pipeline/voices.py
frontend/  Interfaz web (React + Vite)
```

Los datos se guardan en disco, una carpeta por episodio en `data/episodes/<id>/`.

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
