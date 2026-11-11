"""Motor real: WhisperX (transcripción + alineación por palabra) y pyannote (diarización).

Necesita `pip install -e ".[ml]"` y un token de Hugging Face con acceso aceptado
al modelo de diarización (por defecto pyannote/speaker-diarization-community-1).
Los modelos se cargan una vez y se reutilizan entre episodios.
"""

from __future__ import annotations

import logging
import threading
from pathlib import Path

from ..config import Settings
from ..models import SpeakerHints
from .base import EngineResult, RawWord

log = logging.getLogger(__name__)


class WhisperXEngine:
    def __init__(self, settings: Settings):
        self.settings = settings
        self._lock = threading.Lock()
        self._asr = None
        self._diarizer = None
        self._align: dict[str, tuple] = {}

    def _load(self):
        import whisperx  # import diferido: es pesado y opcional
        from whisperx.diarize import DiarizationPipeline

        s = self.settings
        if self._asr is None:
            log.info("Cargando Whisper %s en %s", s.whisper_model, s.device)
            self._asr = whisperx.load_model(
                s.whisper_model, s.device, compute_type=s.compute_type, language=s.language
            )
        if self._diarizer is None:
            if not s.hf_token:
                raise RuntimeError(
                    "Falta HOTARU_HF_TOKEN: la diarización con pyannote necesita un token "
                    "de Hugging Face con el modelo aceptado."
                )
            self._diarizer = DiarizationPipeline(
                model_name=s.diarization_model, token=s.hf_token, device=s.device
            )
        return whisperx

    def _align_model(self, whisperx, language: str):
        if language not in self._align:
            self._align[language] = whisperx.load_align_model(
                language_code=language, device=self.settings.device
            )
        return self._align[language]

    def run(self, audio_path: Path, hints: SpeakerHints) -> EngineResult:
        with self._lock:
            whisperx = self._load()
            s = self.settings
            audio = whisperx.load_audio(str(audio_path))

            result = self._asr.transcribe(audio, batch_size=s.batch_size, language=s.language)
            language = result.get("language") or s.language or "es"

            model_a, metadata = self._align_model(whisperx, language)
            result = whisperx.align(
                result["segments"], model_a, metadata, audio, s.device,
                return_char_alignments=False,
            )

            diarize_df, embeddings = self._diarizer(
                audio,
                num_speakers=hints.num_speakers,
                min_speakers=hints.min_speakers,
                max_speakers=hints.max_speakers,
                return_embeddings=True,
            )
            result = whisperx.assign_word_speakers(diarize_df, result)

        words: list[RawWord] = []
        for seg in result.get("segments", []):
            seg_words = seg.get("words") or []
            if not seg_words:
                words.append(
                    RawWord(seg.get("text", ""), seg.get("start"), seg.get("end"), seg.get("speaker"))
                )
                continue
            for w in seg_words:
                words.append(
                    RawWord(
                        text=w.get("word", ""),
                        start=w.get("start"),
                        end=w.get("end"),
                        speaker=w.get("speaker") or seg.get("speaker"),
                    )
                )
        return EngineResult(language=language, words=words, embeddings=embeddings or {})
