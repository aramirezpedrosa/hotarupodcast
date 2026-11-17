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
  profile_id: string | null;
}

export interface Profile {
  id: string;
  name: string;
  role: string | null;
  info: string | null;
  color: string;
  photo: string | null;
}

export interface ProfileInput {
  name: string;
  role?: string | null;
  info?: string | null;
  color?: string;
}

export interface WordPosition {
  segment_id: string;
  word_index: number;
}

export type CleanupStrength = "suave" | "media" | "fuerte";

export interface CleanupSettings {
  strength: CleanupStrength;
  keep_breaths: boolean;
}

export interface Cleanup {
  status: EpisodeStatus;
  settings: CleanupSettings;
  error: string | null;
  updated_at: string;
  stale: boolean;
}

export type VideoTemplate = "mesa" | "foco";
export type VideoAspect = "16:9" | "9:16" | "1:1" | "4:5";

export interface VideoSettings {
  template: VideoTemplate;
  aspect: VideoAspect;
  dim_others: boolean;
  show_names: boolean;
  show_title: boolean;
  use_cleaned_audio: boolean;
  background: string;
}

export interface BackgroundOption {
  id: string;
  name: string;
  description: string | null;
  kind: "preset" | "custom";
}

export interface Video {
  id: string;
  settings: VideoSettings;
  status: EpisodeStatus;
  progress: number;
  error: string | null;
  created_at: string;
  audio: string | null;
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
  cleanup: Cleanup | null;
  videos: Video[];
  can_undo: boolean;
  can_redo: boolean;
}

export interface EpisodeSummary {
  id: string;
  title: string;
  status: EpisodeStatus;
  created_at: string;
  duration: number | null;
  voice_count: number;
}

function jsonInit(method: string, body: unknown): RequestInit {
  return {
    method,
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify(body),
  };
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
  if (res.status === 204) return undefined as T;
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
    request<Episode>(
      `/api/episodes/${id}/voices/merge`,
      jsonInit("POST", { target_id: targetId, source_ids: sourceIds }),
    ),
  patchVoice: (
    id: string,
    voiceId: string,
    patch: { profile_id?: string | null; label?: string; color?: string },
  ) => request<Episode>(`/api/episodes/${id}/voices/${voiceId}`, jsonInit("PATCH", patch)),
  reassign: (id: string, start: WordPosition, end: WordPosition, voiceId: string | null) =>
    request<Episode>(
      `/api/episodes/${id}/segments/reassign`,
      jsonInit("POST", { start, end, voice_id: voiceId }),
    ),
  undo: (id: string) => request<Episode>(`/api/episodes/${id}/undo`, { method: "POST" }),
  redo: (id: string) => request<Episode>(`/api/episodes/${id}/redo`, { method: "POST" }),

  startCleanup: (id: string, settings: CleanupSettings) =>
    request<Episode>(`/api/episodes/${id}/cleanup`, jsonInit("PUT", settings)),
  cleanupPreviewUrl: (id: string, start: number, duration: number, s: CleanupSettings) =>
    `/api/episodes/${id}/cleanup/preview?` +
    new URLSearchParams({
      start: start.toFixed(2),
      duration: String(duration),
      strength: s.strength,
      keep_breaths: String(s.keep_breaths),
    }),
  cleanedUrl: (ep: Episode, download = false) =>
    `/api/episodes/${ep.id}/cleanup/audio?v=${encodeURIComponent(ep.cleanup?.updated_at ?? "")}` +
    (download ? "&download=true" : ""),

  videoPreviewUrl: (id: string, s: VideoSettings, speaker: string | null, width = 640) =>
    `/api/episodes/${id}/video/preview.png?` +
    new URLSearchParams({
      template: s.template,
      aspect: s.aspect,
      dim_others: String(s.dim_others),
      show_names: String(s.show_names),
      show_title: String(s.show_title),
      background: s.background,
      width: String(width),
      ...(speaker ? { speaker } : {}),
    }),
  createVideo: (id: string, s: VideoSettings) =>
    request<Episode>(`/api/episodes/${id}/videos`, jsonInit("POST", s)),
  deleteVideo: (id: string, videoId: string) =>
    request<Episode>(`/api/episodes/${id}/videos/${videoId}`, { method: "DELETE" }),
  listBackgrounds: () => request<BackgroundOption[]>("/api/backgrounds"),
  uploadBackground: (file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<BackgroundOption>("/api/backgrounds", { method: "POST", body: form });
  },
  deleteBackground: (id: string) => request<void>(`/api/backgrounds/${id}`, { method: "DELETE" }),
  backgroundThumbUrl: (id: string, aspect: VideoAspect, width = 240) =>
    `/api/backgrounds/${id}/thumb.jpg?` + new URLSearchParams({ aspect, width: String(width) }),
  videoUrl: (id: string, videoId: string, download = false) =>
    `/api/episodes/${id}/videos/${videoId}/file` + (download ? "?download=true" : ""),

  listProfiles: () => request<Profile[]>("/api/profiles"),
  createProfile: (body: ProfileInput) => request<Profile>("/api/profiles", jsonInit("POST", body)),
  updateProfile: (id: string, body: Partial<ProfileInput>) =>
    request<Profile>(`/api/profiles/${id}`, jsonInit("PATCH", body)),
  deleteProfile: (id: string) => request<void>(`/api/profiles/${id}`, { method: "DELETE" }),
  uploadPhoto: (id: string, file: File) => {
    const form = new FormData();
    form.append("file", file);
    return request<Profile>(`/api/profiles/${id}/photo`, { method: "PUT", body: form });
  },
  deletePhoto: (id: string) =>
    request<Profile>(`/api/profiles/${id}/photo`, { method: "DELETE" }),
  photoUrl: (p: Profile) => (p.photo ? `/api/profiles/${p.id}/photo?v=${p.photo}` : null),

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

export function initials(name: string): string {
  const parts = name.trim().split(/\s+/).filter(Boolean);
  if (parts.length === 0) return "?";
  if (parts.length === 1) return parts[0].slice(0, 2).toUpperCase();
  return (parts[0][0] + parts[parts.length - 1][0]).toUpperCase();
}
