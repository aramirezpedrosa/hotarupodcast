import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { api, Episode, formatTime, Profile, Voice, WordPosition } from "./api";
import Avatar from "./Avatar";
import CleanupPanel from "./CleanupPanel";
import Modal from "./Modal";
import ProfileForm from "./ProfileForm";
import StatusBadge from "./StatusBadge";
import VideoPanel from "./VideoPanel";

const POLL_MS = 2000;
const PUNCT = /^[,.;:!?)»…]/;

type ProfileMap = Record<string, Profile>;

function voiceName(v: Voice | undefined, profiles: ProfileMap): string {
  if (!v) return "?";
  return (v.profile_id && profiles[v.profile_id]?.name) || v.label;
}

export default function EpisodeView({ id }: { id: string }) {
  const [episode, setEpisode] = useState<Episode | null>(null);
  const [profiles, setProfiles] = useState<Profile[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [editError, setEditError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const audioRef = useRef<HTMLAudioElement>(null);

  // Carga inicial del episodio y de los perfiles.
  useEffect(() => {
    let cancelled = false;
    api
      .getEpisode(id)
      .then((ep) => {
        if (!cancelled) {
          setEpisode(ep);
          setError(null);
        }
      })
      .catch((e) => !cancelled && setError((e as Error).message));
    api.listProfiles().then((p) => !cancelled && setProfiles(p)).catch(() => {});
    return () => {
      cancelled = true;
    };
  }, [id]);

  // Mientras se transcribe o se limpia el audio, consulta el estado cada pocos segundos.
  const working =
    episode?.status === "pending" ||
    episode?.status === "processing" ||
    episode?.cleanup?.status === "pending" ||
    episode?.cleanup?.status === "processing" ||
    !!episode?.videos?.some((v) => v.status === "pending" || v.status === "processing");
  useEffect(() => {
    if (!working) return;
    const timer = window.setInterval(() => {
      api.getEpisode(id).then(setEpisode).catch(() => {});
    }, POLL_MS);
    return () => window.clearInterval(timer);
  }, [id, working]);

  // Toda edición pasa por aquí: una cada vez, y el error se muestra sin perder el estado.
  const edit = useCallback(async (action: () => Promise<Episode>) => {
    setBusy(true);
    setEditError(null);
    try {
      setEpisode(await action());
      return true;
    } catch (e) {
      setEditError((e as Error).message);
      return false;
    } finally {
      setBusy(false);
    }
  }, []);

  const canUndo = !!episode?.can_undo;
  const canRedo = !!episode?.can_redo;
  const undo = useCallback(() => canUndo && edit(() => api.undo(id)), [canUndo, edit, id]);
  const redo = useCallback(() => canRedo && edit(() => api.redo(id)), [canRedo, edit, id]);

  // Ctrl/Cmd+Z deshace; Ctrl/Cmd+Shift+Z o Ctrl+Y rehace (salvo escribiendo en un campo).
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      const t = e.target as HTMLElement;
      if (t.closest("input, textarea, [contenteditable]")) return;
      if (!(e.ctrlKey || e.metaKey)) return;
      const k = e.key.toLowerCase();
      if (k === "z" && !e.shiftKey) {
        e.preventDefault();
        undo();
      } else if ((k === "z" && e.shiftKey) || k === "y") {
        e.preventDefault();
        redo();
      }
    };
    window.addEventListener("keydown", onKey);
    return () => window.removeEventListener("keydown", onKey);
  }, [undo, redo]);

  const profileMap = useMemo(() => Object.fromEntries(profiles.map((p) => [p.id, p])), [profiles]);

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
          <VoicesPanel
            episode={episode}
            profiles={profiles}
            profileMap={profileMap}
            busy={busy}
            edit={edit}
            onProfileSaved={(p) =>
              setProfiles((list) => [...list.filter((x) => x.id !== p.id), p].sort((a, b) => a.name.localeCompare(b.name)))
            }
          />
          <Transcript
            episode={episode}
            profileMap={profileMap}
            busy={busy}
            edit={edit}
            onSeek={seek}
            onUndo={undo}
            onRedo={redo}
          />
          <CleanupPanel
            episode={episode}
            getPlayerTime={() => audioRef.current?.currentTime ?? 0}
            onChange={setEpisode}
          />
          <VideoPanel episode={episode} profileMap={profileMap} onChange={setEpisode} />
          {editError && (
            <div className="toast error" role="alert">
              {editError}
              <button className="link" onClick={() => setEditError(null)}>
                Cerrar
              </button>
            </div>
          )}
        </>
      )}
    </>
  );
}

type EditFn = (action: () => Promise<Episode>) => Promise<boolean>;

function VoicesPanel({
  episode,
  profiles,
  profileMap,
  busy,
  edit,
  onProfileSaved,
}: {
  episode: Episode;
  profiles: Profile[];
  profileMap: ProfileMap;
  busy: boolean;
  edit: EditFn;
  onProfileSaved: (p: Profile) => void;
}) {
  const [selected, setSelected] = useState<string[]>([]);
  const [creatingFor, setCreatingFor] = useState<Voice | null>(null);
  const [editingProfile, setEditingProfile] = useState<Profile | null>(null);
  const total = episode.voices.reduce((acc, v) => acc + v.talk_time, 0) || 1;
  const assigned = new Set(episode.voices.map((v) => v.profile_id).filter(Boolean));

  const toggle = (vid: string) =>
    setSelected((s) => (s.includes(vid) ? s.filter((x) => x !== vid) : [...s, vid]));

  async function merge() {
    // La voz que más habla de las seleccionadas conserva su nombre y color.
    const [target, ...rest] = episode.voices.filter((v) => selected.includes(v.id));
    const ok = await edit(() => api.mergeVoices(episode.id, target.id, rest.map((v) => v.id)));
    if (ok) setSelected([]);
  }

  const assign = (voice: Voice, profileId: string | null) =>
    edit(() => api.patchVoice(episode.id, voice.id, { profile_id: profileId }));

  return (
    <section className="card">
      <h2>Intervinientes ({episode.voices.length})</h2>
      <p className="muted">
        Escucha la muestra de cada voz y asígnale su interviniente. Si dos voces son la misma
        persona, márcalas y fusiónalas.
      </p>
      <ul className="voices">
        {episode.voices.map((v) => {
          const profile = v.profile_id ? profileMap[v.profile_id] : undefined;
          const share = v.talk_time / total;
          return (
            <li
              key={v.id}
              className={`voice ${selected.includes(v.id) ? "voice-selected" : ""}`}
              style={{ borderLeftColor: v.color }}
            >
              <div className="voice-head">
                <input
                  type="checkbox"
                  checked={selected.includes(v.id)}
                  onChange={() => toggle(v.id)}
                  aria-label={`Seleccionar ${voiceName(v, profileMap)} para fusionar`}
                />
                <Avatar profile={profile} fallback={v.label.replace(/\D/g, "") || v.label} color={v.color} />
                <div className="voice-title">
                  <span className="voice-name">{voiceName(v, profileMap)}</span>
                  <span className="muted small">
                    {profile ? (profile.role ?? v.label) : "Sin interviniente asignado"}
                  </span>
                </div>
              </div>
              <div className="bar">
                <div style={{ width: `${Math.round(share * 100)}%`, background: v.color }} />
              </div>
              <p className="muted small">
                {formatTime(v.talk_time)} hablando · {Math.round(share * 100)} % · {v.segment_count}{" "}
                {v.segment_count === 1 ? "intervención" : "intervenciones"}
              </p>
              {v.sample && (
                <audio controls preload="none" src={api.sampleUrl(episode.id, v)} className="sample" />
              )}
              <div className="assign-row">
                <select
                  value={v.profile_id ?? ""}
                  disabled={busy}
                  aria-label={`Interviniente de ${v.label}`}
                  onChange={(e) => {
                    const val = e.target.value;
                    if (val === "__new__") setCreatingFor(v);
                    else void assign(v, val || null);
                  }}
                >
                  <option value="">Sin asignar</option>
                  {profiles.map((p) => (
                    <option key={p.id} value={p.id} disabled={assigned.has(p.id) && p.id !== v.profile_id}>
                      {p.name}
                      {p.role ? ` · ${p.role}` : ""}
                    </option>
                  ))}
                  <option value="__new__">+ Nuevo interviniente…</option>
                </select>
                {profile && (
                  <button className="link" onClick={() => setEditingProfile(profile)}>
                    Editar perfil
                  </button>
                )}
              </div>
            </li>
          );
        })}
      </ul>
      <div className="actions">
        <button disabled={selected.length < 2 || busy} onClick={merge}>
          {`Fusionar ${selected.length >= 2 ? selected.length : ""} voces seleccionadas`}
        </button>
      </div>

      {creatingFor && (
        <Modal title={`Interviniente para ${creatingFor.label}`} onClose={() => setCreatingFor(null)}>
          <ProfileForm
            defaultColor={creatingFor.color.toUpperCase()}
            onCancel={() => setCreatingFor(null)}
            onSaved={async (p) => {
              onProfileSaved(p);
              const voice = creatingFor;
              setCreatingFor(null);
              await assign(voice, p.id);
            }}
          />
        </Modal>
      )}
      {editingProfile && (
        <Modal title={`Editar ${editingProfile.name}`} onClose={() => setEditingProfile(null)}>
          <ProfileForm
            profile={editingProfile}
            onCancel={() => setEditingProfile(null)}
            onSaved={async (p) => {
              onProfileSaved(p);
              setEditingProfile(null);
              // El color del perfil se aplica a la voz en el servidor: recargamos el episodio.
              await edit(() => api.getEpisode(episode.id));
            }}
          />
        </Modal>
      )}
    </section>
  );
}

interface Selection {
  start: WordPosition;
  end: WordPosition;
  text: string;
  top: number;
  left: number;
}

// Convierte la selección del navegador en posiciones de palabra (inicio y fin incluidos).
function readSelection(container: HTMLElement): Selection | null {
  const sel = window.getSelection();
  if (!sel || sel.isCollapsed || sel.rangeCount === 0) return null;
  const range = sel.getRangeAt(0);
  if (!container.contains(range.commonAncestorContainer)) return null;

  const spans = Array.from(container.querySelectorAll<HTMLElement>("[data-w]"));
  const spanOf = (node: Node) =>
    (node.nodeType === Node.ELEMENT_NODE ? (node as HTMLElement) : node.parentElement)?.closest<HTMLElement>("[data-w]");

  let a = spanOf(range.startContainer);
  let b = spanOf(range.endContainer);
  if (!a || !b) return null;
  let ia = spans.indexOf(a);
  let ib = spans.indexOf(b);
  // Una selección que termina justo al principio de una palabra no la incluye.
  if (range.endOffset === 0 && ib > ia) ib -= 1;
  // Una que empieza al final de una palabra (en su espacio) empieza en la siguiente.
  const aText = range.startContainer.textContent ?? "";
  if (range.startOffset >= aText.trimEnd().length && ia < ib) ia += 1;
  a = spans[ia];
  b = spans[ib];

  const pos = (el: HTMLElement): WordPosition => ({
    segment_id: el.dataset.seg!,
    word_index: Number(el.dataset.w),
  });
  const rect = range.getBoundingClientRect();
  const text = spans
    .slice(ia, ib + 1)
    .map((s) => s.textContent)
    .join("")
    .trim();
  return { start: pos(a), end: pos(b), text, top: rect.top, left: rect.left + rect.width / 2 };
}

function Transcript({
  episode,
  profileMap,
  busy,
  edit,
  onSeek,
  onUndo,
  onRedo,
}: {
  episode: Episode;
  profileMap: ProfileMap;
  busy: boolean;
  edit: EditFn;
  onSeek: (t: number) => void;
  onUndo: () => void;
  onRedo: () => void;
}) {
  const ref = useRef<HTMLOListElement>(null);
  const [selection, setSelection] = useState<Selection | null>(null);
  const voices = useMemo(
    () => Object.fromEntries(episode.voices.map((v) => [v.id, v])),
    [episode.voices],
  );

  useEffect(() => {
    const update = () => setSelection(ref.current ? readSelection(ref.current) : null);
    document.addEventListener("selectionchange", update);
    return () => document.removeEventListener("selectionchange", update);
  }, []);

  async function assignTo(voiceId: string | null) {
    if (!selection) return;
    const ok = await edit(() => api.reassign(episode.id, selection.start, selection.end, voiceId));
    if (ok) {
      window.getSelection()?.removeAllRanges();
      setSelection(null);
    }
  }

  const short = selection && selection.text.length > 40 ? selection.text.slice(0, 40) + "…" : selection?.text;

  return (
    <section className="card">
      <div className="title-row">
        <h2>Transcripción</h2>
        <div className="history">
          <button className="button-secondary" onClick={onUndo} disabled={!episode.can_undo || busy} title="Deshacer (Ctrl+Z)">
            Deshacer
          </button>
          <button className="button-secondary" onClick={onRedo} disabled={!episode.can_redo || busy} title="Rehacer (Ctrl+Shift+Z)">
            Rehacer
          </button>
        </div>
      </div>
      <p className="muted small">
        Selecciona con el ratón el texto que dijo otra persona y elige a quién corresponde. Puedes
        seleccionar desde una palabra hasta varios párrafos.
      </p>
      {episode.segments.length === 0 && <p className="muted">No se detectó voz en el audio.</p>}
      <ol className="transcript" ref={ref}>
        {episode.segments.map((s) => {
          const v = voices[s.voice_id];
          return (
            <li key={s.id} style={{ borderLeftColor: v?.color }}>
              <div className="seg-head" contentEditable={false}>
                <span className="seg-voice" style={{ color: v?.color }}>
                  {voiceName(v, profileMap)}
                </span>
                <button className="link" onClick={() => onSeek(s.start)} title="Escuchar desde aquí">
                  {formatTime(s.start)}
                </button>
              </div>
              <p>
                {s.words.map((w, i) => {
                  const next = s.words[i + 1];
                  const space = next && !PUNCT.test(next.text) ? " " : "";
                  return (
                    <span key={i} data-seg={s.id} data-w={i}>
                      {w.text + space}
                    </span>
                  );
                })}
              </p>
            </li>
          );
        })}
      </ol>
      {selection && (
        <div
          className="reassign-bar"
          style={{ top: Math.max(8, selection.top - 56), left: selection.left }}
          onMouseDown={(e) => e.preventDefault() /* no perder la selección al pulsar */}
        >
          <span className="muted small">«{short}» lo dice:</span>
          {episode.voices.map((v) => (
            <button
              key={v.id}
              className="chip"
              style={{ borderColor: v.color }}
              disabled={busy}
              onClick={() => assignTo(v.id)}
            >
              <span className="dot" style={{ background: v.color }} />
              {voiceName(v, profileMap)}
            </button>
          ))}
          <button className="chip" disabled={busy} onClick={() => assignTo(null)}>
            + Voz nueva
          </button>
        </div>
      )}
    </section>
  );
}
