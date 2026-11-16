import { api, initials, Profile } from "./api";

// Foto del perfil o, si no hay, iniciales sobre su color.
export default function Avatar({
  profile,
  fallback,
  color,
  size = 40,
}: {
  profile?: Profile | null;
  fallback: string;
  color: string;
  size?: number;
}) {
  const url = profile ? api.photoUrl(profile) : null;
  const style = { width: size, height: size, fontSize: size * 0.38, borderColor: color };
  if (url) {
    return <img className="avatar avatar-photo" src={url} alt={profile?.name ?? ""} style={style} />;
  }
  return (
    <span className="avatar" style={{ ...style, background: color }} aria-hidden>
      {initials(profile?.name ?? fallback)}
    </span>
  );
}
