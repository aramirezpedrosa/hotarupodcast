import { useMemo, useState } from "react";
import { api, CleanupSettings, CleanupStrength, Episode, formatTime } from "./api";

const PREVIEW_SECONDS = 20;

const STRENGTHS: { value: CleanupStrength; label: string; help: string }[] = [
  { value: "suave", label: "Suave", help: "Apenas toca la voz; baja el fondo unos 8 dB en los silencios." },
  { value: "media", label: "Media", help: "Recomendada. Fondo unos 14 dB más bajo en los silencios." },
  { value: "fuerte", label: "Fuerte", help: "Para grabaciones ruidosas. Fondo unos 20 dB más bajo." },
];

// Busca un buen punto para la escucha previa: justo antes del primer silencio largo,
// para oír cómo baja el fondo al terminar una frase.
function suggestedStart(ep: Episode): number {
  const segs = ep.segments;
  for (let i = 0; i + 1 < segs.length; i++) {
    if (segs[i + 1].start - segs[i].end >= 1.5) return Math.max(0, segs[i].end - 6);
  }
  return 0;
}

export default function CleanupPanel({
  episode,
  getPlayerTime,
  onChange,
}: {
  episode: Episode;
  getPlayerTime: () => number;
  onChange: (ep: Episode) => void;
}) {
  const cleanup = episode.cleanup;
  const [settings, setSettings] = useState<CleanupSettings>(
    cleanup?.settings ?? { strength: "media", keep_breaths: true },
  );
  const [start, setStart] = useState(() => suggestedStart(episode));
  const [previewKey, setPreviewKey] = useState<string | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [starting, setStarting] = useState(false);

  const running = cleanup?.status === "pending" || cleanup?.status === "processing";
  const end = Math.min(start + PREVIEW_SECONDS, episode.duration ?? start + PREVIEW_SECONDS);
  const previewUrl = useMemo(
    () => (previewKey ? api.cleanupPreviewUrl(episode.id, start, end - start, settings) : null),
    // Solo se regenera al pulsar "Escuchar", no con cada cambio de ajuste.
    [previewKey],
  );
  const changed =
    cleanup && (cleanup.settings.strength !== settings.strength || cleanup.settings.keep_breaths !== settings.keep_breaths);

  async function apply() {
    setStarting(true);
    setError(null);
    try {
      onChange(await api.startCleanup(episode.id, settings));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setStarting(false);
    }
  }

  return (
    <section className="card">
      <h2>Limpieza de audio</h2>
      <p className="muted">
        Reduce el ruido de fondo, sobre todo en los huecos sin voz, sin dejarlos en silencio
        total. Al final iguala el volumen al estándar de podcast (−16 LUFS).
      </p>

      <fieldset className="strengths" disabled={running}>
        <legend className="small">Intensidad</legend>
        {STRENGTHS.map((s) => (
          <label key={s.value} className={`strength ${settings.strength === s.value ? "strength-on" : ""}`}>
            <input
              type="radio"
              name="strength"
              value={s.value}
              checked={settings.strength === s.value}
              onChange={() => setSettings({ ...settings, strength: s.value })}
            />
            <strong>{s.label}</strong>
            <span className="muted small">{s.help}</span>
          </label>
        ))}
      </fieldset>
      <label className="check">
        <input
          type="checkbox"
          checked={settings.keep_breaths}
          disabled={running}
          onChange={(e) => setSettings({ ...settings, keep_breaths: e.target.checked })}
        />
        Respetar respiraciones y finales de frase (deja más margen tras cada intervención)
      </label>

      <div className="preview">
        <div className="preview-head">
          <strong>Escucha previa</strong>
          <span className="muted small">
            de {formatTime(start)} a {formatTime(end)}
          </span>
          <button className="link" onClick={() => setStart(getPlayerTime())}>
            Usar el punto del reproductor
          </button>
          <button
            className="button-secondary"
            onClick={() => setPreviewKey(`${start}-${settings.strength}-${settings.keep_breaths}-${Date.now()}`)}
          >
            Escuchar con estos ajustes
          </button>
        </div>
        {previewUrl && (
          <div className="ab">
            <label>
              <span className="small muted">Antes</span>
              <audio controls preload="none" src={`${api.audioUrl(episode.id)}#t=${start.toFixed(2)},${end.toFixed(2)}`} />
            </label>
            <label>
              <span className="small muted">Después</span>
              <audio key={previewUrl} controls autoPlay src={previewUrl} />
            </label>
          </div>
        )}
      </div>

      <div className="actions">
        <button onClick={apply} disabled={running || starting}>
          {running ? "Limpiando el episodio…" : cleanup ? "Volver a aplicar" : "Aplicar a todo el episodio"}
        </button>
        {changed && !running && <span className="muted small">Has cambiado los ajustes desde la última limpieza.</span>}
        {error && <span className="error">{error}</span>}
      </div>

      {cleanup?.status === "error" && <p className="error">No se pudo limpiar el audio: {cleanup.error}</p>}
      {cleanup?.status === "done" && (
        <div className="cleaned">
          {cleanup.stale && (
            <p className="warning">
              Has cambiado los tramos de voz después de limpiar el audio. Vuelve a aplicar la
              limpieza para que use los tramos actuales.
            </p>
          )}
          <span className="small muted">Episodio limpio</span>
          <audio controls preload="none" src={api.cleanedUrl(episode)} />
          <a className="button-secondary" href={api.cleanedUrl(episode, true)} download>
            Descargar MP3 limpio
          </a>
        </div>
      )}
    </section>
  );
}
