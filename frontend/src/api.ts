export type EpisodeStatus = "pending" | "processing" | "done" | "error";

export interface Word {
  text: string;
  start: number;
  end: number;
}

export interface Segment {
  id: string;
  start: number;
  end: number;
  voice_id: string;
  text: string;
  words: Word[];
}

export interface Voice {
  id: string;
  label: string;
  color: string;
  source_speakers: string[];
  talk_time: number;
  segment_count: number;
  sample: { start: number; end: number } | null;
}

export interface Episode {
  id: string;
  title: string;
  original_filename: string;
  status: EpisodeStatus;
  error: string | null;
  created_at: string;
  duration: number | null;
  language: string | null;
  voices: Voice[];
  segments: Segment[];
}

export interface EpisodeSummary {
  id: string;
  title: string;
  status: EpisodeStatus;
  created_at: string;
  duration: number | null;
  voice_count: number;
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  const res = await fetch(path, init);
  if (!res.ok) {
    let detail = res.statusText;
    try {
      const body = await res.json();
      detail = typeof body.detail === "string" ? body.detail : JSON.stringify(body.detail);
    } catch {
      /* respuesta sin JSON */
    }
    throw new Error(detail);
  }
  return res.json() as Promise<T>;
}

export const api = {
  listEpisodes: () => request<EpisodeSummary[]>("/api/episodes"),
  getEpisode: (id: string) => request<Episode>(`/api/episodes/${id}`),
  uploadEpisode: (form: FormData) =>
    request<Episode>("/api/episodes", { method: "POST", body: form }),
  reprocess: (id: string) =>
    request<Episode>(`/api/episodes/${id}/reprocess`, { method: "POST" }),
  mergeVoices: (id: string, targetId: string, sourceIds: string[]) =>
    request<Episode>(`/api/episodes/${id}/voices/merge`, {
      method: "POST",
      headers: { "Content-Type": "application/json" },
      body: JSON.stringify({ target_id: targetId, source_ids: sourceIds }),
    }),
  audioUrl: (id: string) => `/api/episodes/${id}/audio`,
  sampleUrl: (id: string, voice: Voice) =>
    // El inicio y fin en la URL evitan servir un clip antiguo de caché tras fusionar.
    `/api/episodes/${id}/voices/${voice.id}/sample?t=${voice.sample?.start}-${voice.sample?.end}`,
};

export function formatTime(seconds: number): string {
  const s = Math.max(0, Math.floor(seconds));
  const h = Math.floor(s / 3600);
  const m = Math.floor((s % 3600) / 60);
  const sec = String(s % 60).padStart(2, "0");
  return h > 0 ? `${h}:${String(m).padStart(2, "0")}:${sec}` : `${m}:${sec}`;
}
