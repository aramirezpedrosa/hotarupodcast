import { FormEvent, useEffect, useState } from "react";
import { api, EpisodeSummary, formatTime } from "./api";
import StatusBadge from "./StatusBadge";

export default function EpisodeList() {
  const [episodes, setEpisodes] = useState<EpisodeSummary[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [uploading, setUploading] = useState(false);

  useEffect(() => {
    api.listEpisodes().then(setEpisodes).catch((e) => setError(e.message));
  }, []);

  async function onSubmit(e: FormEvent<HTMLFormElement>) {
    e.preventDefault();
    const form = new FormData(e.currentTarget);
    // Los campos numéricos vacíos no se envían: el servidor detecta el número solo.
    for (const key of ["num_speakers", "min_speakers", "max_speakers", "title"]) {
      if (!form.get(key)) form.delete(key);
    }
    setUploading(true);
    setError(null);
    try {
      const ep = await api.uploadEpisode(form);
      window.location.hash = `#/episodios/${ep.id}`;
    } catch (err) {
      setError((err as Error).message);
      setUploading(false);
    }
  }

  return (
    <>
      <section className="card">
        <h1>Nuevo episodio</h1>
        <p className="muted">
          Sube el audio y detectaremos las distintas voces. Luego podrás revisarlas y
          completar los perfiles de cada interviniente.
        </p>
        <form onSubmit={onSubmit} className="upload-form">
          <label className="field wide">
            <span>Archivo de audio o vídeo</span>
            <input
              type="file"
              name="file"
              required
              accept="audio/*,video/mp4,video/quicktime,video/webm"
            />
          </label>
          <label className="field wide">
            <span>Título (opcional)</span>
            <input type="text" name="title" placeholder="Se usa el nombre del archivo" />
          </label>
          <label className="field">
            <span>Número de voces, si lo sabes</span>
            <input type="number" name="num_speakers" min={1} max={20} placeholder="Automático" />
          </label>
          <details className="field wide">
            <summary>Si no sabes el número exacto</summary>
            <div className="row">
              <label className="field">
                <span>Mínimo</span>
                <input type="number" name="min_speakers" min={1} max={20} />
              </label>
              <label className="field">
                <span>Máximo</span>
                <input type="number" name="max_speakers" min={1} max={20} />
              </label>
            </div>
          </details>
          <button type="submit" disabled={uploading}>
            {uploading ? "Subiendo…" : "Subir y detectar voces"}
          </button>
        </form>
        {error && <p className="error">{error}</p>}
      </section>

      <section className="card">
        <h2>Episodios</h2>
        {episodes === null && !error && <p className="muted">Cargando…</p>}
        {episodes?.length === 0 && <p className="muted">Todavía no hay episodios.</p>}
        <ul className="episode-list">
          {episodes?.map((ep) => (
            <li key={ep.id}>
              <a href={`#/episodios/${ep.id}`}>{ep.title}</a>
              <span className="muted">
                {ep.duration ? formatTime(ep.duration) : "—"}
                {ep.voice_count ? ` · ${ep.voice_count} voces` : ""}
              </span>
              <StatusBadge status={ep.status} />
            </li>
          ))}
        </ul>
      </section>
    </>
  );
}
