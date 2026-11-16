import { EpisodeStatus } from "./api";

const LABELS: Record<EpisodeStatus, string> = {
  pending: "En cola",
  processing: "Procesando",
  done: "Listo",
  error: "Error",
};

export default function StatusBadge({ status }: { status: EpisodeStatus }) {
  return <span className={`badge badge-${status}`}>{LABELS[status]}</span>;
}
