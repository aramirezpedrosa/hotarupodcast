import { useEffect, useMemo, useRef, useState } from "react";
import { api, Episode, formatTime, Voice } from "./api";
import StatusBadge from "./StatusBadge";

const POLL_MS = 2000;

export default function EpisodeView({ id }: { id: string }) {
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [error, setError] = useState<string | null>(null);
  const audioRef = useRef<HTMLAudioElement>(null);

  // Consulta el estado hasta que el procesado termine.
  useEffect(() => {
    let cancelled = false;
    let timer: number | undefined;
    const load = async () => {
      try {
        const ep = await api.getEpisode(id);
        if (cancelled) return;
        setEpisode(ep);
        setError(null);
        if (ep.status === "pending" || ep.status === "processing") {
          timer = window.setTimeout(load, POLL_MS);
        }
      } catch (e) {
        if (!cancelled) setError((e as Error).message);
      }
    };
    load();
    return () => {
      cancelled = true;
      window.clearTimeout(timer);
    };
  }, [id]);

  if (error && !episode) return <p className="error">{error}</p>;
  if (!episode) return <p className="muted">Cargando…</p>;

  const seek = (t: number) => {
    const a = audioRef.current;
    if (!a) return;
    a.currentTime = t;
    void a.play();
  };

  return (
    <>
      <section className="card">
        <div className="title-row">
          <h1>{episode.title}</h1>
          <StatusBadge status={episode.status} />
        </div>
        <p className="muted">
          {episode.original_filename}
          {episode.duration ? ` · ${formatTime(episode.duration)}` : ""}
        </p>
        <audio ref={audioRef} controls src={api.audioUrl(episode.id)} className="player" />
        {(episode.status === "pending" || episode.status === "processing") && (
          <p className="muted">
            Transcribiendo y separando voces. En un ordenador sin GPU tarda más o menos lo
            mismo que dura el audio.
          </p>
        )}
        {episode.status === "error" && (
          <div className="error">
            <p>No se pudo procesar: {episode.error}</p>
            <button onClick={() => api.reprocess(episode.id).then(setEpisode)}>Reintentar</button>
          </div>
        )}
      </section>

      {episode.status === "done" && (
        <>
          <VoicesPanel episode={episode} onChange={setEpisode} />
          <Transcript episode={episode} onSeek={seek} />
        </>
      )}
    </>
  );
}

function VoicesPanel({
  episode,
  onChange,
}: {
  episode: Episode;
  onChange: (ep: Episode) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);
  const total = episode.voices.reduce((acc, v) => acc + v.talk_time, 0) || 1;

  const toggle = (vid: string) =>
    setSelected((s) => (s.includes(vid) ? s.filter((x) => x !== vid) : [...s, vid]));

  async function merge() {
    // La voz que más habla de las seleccionadas conserva su nombre y color.
    const ordered = episode.voices.filter((v) => selected.includes(v.id));
    const [target, ...rest] = ordered;
    setBusy(true);
    setError(null);
    try {
      onChange(await api.mergeVoices(episode.id, target.id, rest.map((v) => v.id)));
      setSelected([]);
    } catch (e) {
      setError((e as Error).message);
    } finally {
      setBusy(false);
    }
  }

  return (
    <section className="card">
      <h2>Voces detectadas ({episode.voices.length})</h2>
      <p className="muted">
        Escucha la muestra de cada voz. Si dos voces son la misma persona, márcalas y
        fusiónalas. En el siguiente paso podrás poner nombre, cargo y foto a cada una.
      </p>
      <ul className="voices">
        {episode.voices.map((v) => (
          <VoiceCard
            key={v.id}
            episodeId={episode.id}
            voice={v}
            share={v.talk_time / total}
            checked={selected.includes(v.id)}
            onToggle={() => toggle(v.id)}
          />
        ))}
      </ul>
      <div className="actions">
        <button disabled={selected.length < 2 || busy} onClick={merge}>
          {busy ? "Fusionando…" : `Fusionar ${selected.length >= 2 ? selected.length : ""} voces seleccionadas`}
        </button>
        {error && <span className="error">{error}</span>}
      </div>
    </section>
  );
}

function VoiceCard({
  episodeId,
  voice,
  share,
  checked,
  onToggle,
}: {
  episodeId: string;
  voice: Voice;
  share: number;
  checked: boolean;
  onToggle: () => void;
}) {
  return (
    <li className={`voice ${checked ? "voice-selected" : ""}`} style={{ borderColor: voice.color }}>
      <label className="voice-head">
        <input type="checkbox" checked={checked} onChange={onToggle} />
        <span className="avatar" style={{ background: voice.color }}>
          {voice.label.replace(/\D/g, "")}
        </span>
        <span className="voice-name">{voice.label}</span>
      </label>
      <div className="bar">
        <div style={{ width: `${Math.round(share * 100)}%`, background: voice.color }} />
      </div>
      <p className="muted small">
        {formatTime(voice.talk_time)} hablando · {Math.round(share * 100)} % · {voice.segment_count}{" "}
        {voice.segment_count === 1 ? "intervención" : "intervenciones"}
      </p>
      {voice.sample && (
        <audio controls preload="none" src={api.sampleUrl(episodeId, voice)} className="sample" />
      )}
    </li>
  );
}

function Transcript({ episode, onSeek }: { episode: Episode; onSeek: (t: number) => void }) {
  const voices = useMemo(
    () => Object.fromEntries(episode.voices.map((v) => [v.id, v])),
    [episode.voices],
  );
  return (
    <section className="card">
      <h2>Transcripción</h2>
      {episode.segments.length === 0 && <p className="muted">No se detectó voz en el audio.</p>}
      <ol className="transcript">
        {episode.segments.map((s) => {
          const v = voices[s.voice_id];
          return (
            <li key={s.id} style={{ borderLeftColor: v?.color }}>
              <div className="seg-head">
                <span className="seg-voice" style={{ color: v?.color }}>
                  {v?.label ?? s.voice_id}
                </span>
                <button className="link" onClick={() => onSeek(s.start)} title="Escuchar desde aquí">
                  {formatTime(s.start)}
                </button>
              </div>
              <p>{s.text}</p>
            </li>
          );
        })}
      </ol>
    </section>
  );
}
