import { useEffect, useState } from "react";
import { api, Episode, Profile, Video, VideoAspect, VideoSettings, VideoTemplate } from "./api";

const TEMPLATES: { value: VideoTemplate; label: string; help: string }[] = [
  { value: "mesa", label: "Mesa redonda", help: "Todos a la vez; el que habla se ilumina y late con su voz." },
  { value: "foco", label: "Foco", help: "El que habla, en grande; el resto, en miniatura abajo." },
];

const ASPECTS: { value: VideoAspect; label: string; help: string }[] = [
  { value: "16:9", label: "16:9", help: "YouTube, web" },
  { value: "9:16", label: "9:16", help: "Shorts, Reels, TikTok" },
  { value: "1:1", label: "1:1", help: "Instagram, LinkedIn" },
  { value: "4:5", label: "4:5", help: "Feed de Instagram" },
];

const STATUS: Record<Video["status"], string> = {
  pending: "En cola",
  processing: "Generando",
  done: "Listo",
  error: "Error",
};

const DEFAULTS: VideoSettings = {
  template: "mesa",
  aspect: "16:9",
  dim_others: true,
  show_names: true,
  show_title: true,
  use_cleaned_audio: true,
};

export default function VideoPanel({
  episode,
  profileMap,
  onChange,
}: {
  episode: Episode;
  profileMap: Record<string, Profile>;
  onChange: (ep: Episode) => void;
}) {
  const [settings, setSettings] = useState<VideoSettings>(DEFAULTS);
  const [speaker, setSpeaker] = useState<string | null>(episode.voices[0]?.id ?? null);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  // La vista previa se pide con un pequeño retardo para no saturar al cambiar opciones.
  const [previewUrl, setPreviewUrl] = useState<string | null>(null);
  const voicesKey = episode.voices.map((v) => `${v.id}:${v.profile_id}:${v.color}:${v.label}`).join("|");
  useEffect(() => {
    const t = window.setTimeout(
      () => setPreviewUrl(api.videoPreviewUrl(episode.id, settings, speaker, 720) + `&k=${encodeURIComponent(voicesKey)}`),
      250,
    );
    return () => window.clearTimeout(t);
  }, [episode.id, settings, speaker, voicesKey]);

  const cleanedReady = episode.cleanup?.status === "done";
  const set = (patch: Partial<VideoSettings>) => setSettings((s) => ({ ...s, ...patch }));
  const name = (vid: string) => {
    const v = episode.voices.find((x) => x.id === vid);
    return (v?.profile_id && profileMap[v.profile_id]?.name) || v?.label || vid;
  };

  async function generate() {
    setBusy(true);
    setError(null);
    try {
      onChange(await api.createVideo(episode.id, settings));
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  async function remove(v: Video) {
    try {
      onChange(await api.deleteVideo(episode.id, v.id));
    } catch (e) {
      setError((e as Error).message);
    }
  }

  return (
    <section className="card">
      <h2>Vídeo</h2>
      <p className="muted">
        Genera un vídeo del episodio en el que el interviniente que habla se ilumina y late al
        ritmo de su voz. Usa los nombres, cargos, fotos y colores de los perfiles.
      </p>

      <div className="video-grid">
        <div className="video-options">
          <fieldset className="choice-group">
            <legend className="small">Plantilla</legend>
            {TEMPLATES.map((t) => (
              <label key={t.value} className={`strength ${settings.template === t.value ? "strength-on" : ""}`}>
                <input
                  type="radio"
                  name="template"
                  checked={settings.template === t.value}
                  onChange={() => set({ template: t.value })}
                />
                <strong>{t.label}</strong>
                <span className="muted small">{t.help}</span>
              </label>
            ))}
          </fieldset>

          <fieldset className="choice-group aspects">
            <legend className="small">Formato</legend>
            {ASPECTS.map((a) => (
              <label key={a.value} className={`aspect ${settings.aspect === a.value ? "strength-on" : ""}`}>
                <input
                  type="radio"
                  name="aspect"
                  checked={settings.aspect === a.value}
                  onChange={() => set({ aspect: a.value })}
                />
                <strong>{a.label}</strong>
                <span className="muted small">{a.help}</span>
              </label>
            ))}
          </fieldset>

          <label className="check">
            <input type="checkbox" checked={settings.dim_others} onChange={(e) => set({ dim_others: e.target.checked })} />
            Atenuar a los que no hablan
          </label>
          <label className="check">
            <input type="checkbox" checked={settings.show_names} onChange={(e) => set({ show_names: e.target.checked })} />
            Mostrar nombre y cargo
          </label>
          <label className="check">
            <input type="checkbox" checked={settings.show_title} onChange={(e) => set({ show_title: e.target.checked })} />
            Mostrar el título del episodio
          </label>
          <label className="check">
            <input
              type="checkbox"
              checked={settings.use_cleaned_audio && cleanedReady}
              disabled={!cleanedReady}
              onChange={(e) => set({ use_cleaned_audio: e.target.checked })}
            />
            Usar el audio limpio{!cleanedReady && " (primero aplica la limpieza de audio)"}
          </label>

          <div className="actions">
            <button onClick={generate} disabled={busy}>
              Generar vídeo
            </button>
            {error && <span className="error">{error}</span>}
          </div>
        </div>

        <div className="video-preview">
          <div className="preview-head">
            <strong>Vista previa</strong>
            <label className="small muted">
              Hablando:{" "}
              <select value={speaker ?? ""} onChange={(e) => setSpeaker(e.target.value || null)}>
                {episode.voices.map((v) => (
                  <option key={v.id} value={v.id}>
                    {name(v.id)}
                  </option>
                ))}
              </select>
            </label>
          </div>
          {previewUrl && (
            <img
              className={`frame frame-${settings.aspect.replace(":", "x")}`}
              src={previewUrl}
              alt="Vista previa del vídeo"
            />
          )}
        </div>
      </div>

      {episode.videos.length > 0 && (
        <>
          <h3>Vídeos generados</h3>
          <ul className="video-list">
            {episode.videos.map((v) => (
              <li key={v.id}>
                <div className="video-row">
                  <strong>
                    {TEMPLATES.find((t) => t.value === v.settings.template)?.label} · {v.settings.aspect}
                  </strong>
                  <span className={`badge badge-${v.status}`}>{STATUS[v.status]}</span>
                  {v.audio && <span className="muted small">audio {v.audio}</span>}
                  {v.status === "done" && (
                    <a className="button-secondary" href={api.videoUrl(episode.id, v.id, true)} download>
                      Descargar MP4
                    </a>
                  )}
                  {(v.status === "done" || v.status === "error") && (
                    <button className="link" onClick={() => remove(v)}>
                      Borrar
                    </button>
                  )}
                </div>
                {(v.status === "pending" || v.status === "processing") && (
                  <div className="bar progress">
                    <div style={{ width: `${Math.round(v.progress * 100)}%` }} />
                  </div>
                )}
                {v.status === "processing" && (
                  <span className="muted small">{Math.round(v.progress * 100)} %</span>
                )}
                {v.status === "error" && <p className="error">{v.error}</p>}
                {v.status === "done" && (
                  <video
                    className={`frame frame-${v.settings.aspect.replace(":", "x")}`}
                    controls
                    preload="metadata"
                    src={api.videoUrl(episode.id, v.id)}
                  />
                )}
              </li>
            ))}
          </ul>
        </>
      )}
    </section>
  );
}
