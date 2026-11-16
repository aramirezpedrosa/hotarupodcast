import { FormEvent, useState } from "react";
import { api, Profile } from "./api";
import Avatar from "./Avatar";

// Crear o editar un interviniente: nombre, cargo, información, color y foto.
export default function ProfileForm({
  profile,
  defaultColor = "#3B82F6",
  onSaved,
  onCancel,
  onDeleted,
}: {
  profile?: Profile;
  defaultColor?: string;
  onSaved: (p: Profile) => void;
  onCancel: () => void;
  onDeleted?: () => void;
}) {
  const [name, setName] = useState(profile?.name ?? "");
  const [role, setRole] = useState(profile?.role ?? "");
  const [info, setInfo] = useState(profile?.info ?? "");
  const [color, setColor] = useState(profile?.color ?? defaultColor);
  const [photo, setPhoto] = useState<File | null>(null);
  const [preview, setPreview] = useState<string | null>(null);
  const [removePhoto, setRemovePhoto] = useState(false);
  const [busy, setBusy] = useState(false);
  const [error, setError] = useState<string | null>(null);

  function pickPhoto(file: File | null) {
    setPhoto(file);
    setRemovePhoto(false);
    setPreview(file ? URL.createObjectURL(file) : null);
  }

  async function submit(e: FormEvent) {
    e.preventDefault();
    setBusy(true);
    setError(null);
    try {
      const body = { name, role: role || null, info: info || null, color };
      let saved = profile
        ? await api.updateProfile(profile.id, body)
        : await api.createProfile(body);
      if (photo) saved = await api.uploadPhoto(saved.id, photo);
      else if (removePhoto && saved.photo) saved = await api.deletePhoto(saved.id);
      onSaved(saved);
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  async function remove() {
    if (!profile || !onDeleted) return;
    if (!window.confirm(`¿Borrar el perfil de ${profile.name}? Las voces que lo usan se quedarán sin él.`)) return;
    setBusy(true);
    try {
      await api.deleteProfile(profile.id);
      onDeleted();
    } catch (err) {
      setError((err as Error).message);
      setBusy(false);
    }
  }

  const shownProfile =
    profile && !removePhoto ? profile : profile ? { ...profile, photo: null } : null;

  return (
    <form className="profile-form" onSubmit={submit}>
      <div className="photo-row">
        {preview ? (
          <img className="avatar avatar-photo" src={preview} alt="" style={{ width: 72, height: 72, borderColor: color }} />
        ) : (
          <Avatar profile={shownProfile} fallback={name || "?"} color={color} size={72} />
        )}
        <div className="photo-actions">
          <label className="button-secondary">
            {profile?.photo || preview ? "Cambiar foto" : "Subir foto"}
            <input
              type="file"
              accept="image/jpeg,image/png,image/webp"
              hidden
              onChange={(e) => pickPhoto(e.target.files?.[0] ?? null)}
            />
          </label>
          {(preview || (profile?.photo && !removePhoto)) && (
            <button
              type="button"
              className="link"
              onClick={() => {
                pickPhoto(null);
                setRemovePhoto(true);
              }}
            >
              Quitar foto
            </button>
          )}
          <span className="muted small">JPG, PNG o WebP, hasta 5 MB</span>
        </div>
      </div>
      <label className="field">
        <span>Nombre</span>
        <input type="text" value={name} onChange={(e) => setName(e.target.value)} required maxLength={120} autoFocus />
      </label>
      <label className="field">
        <span>Cargo (opcional)</span>
        <input type="text" value={role} onChange={(e) => setRole(e.target.value)} maxLength={160} placeholder="p. ej. Presentadora, Directora de…" />
      </label>
      <label className="field">
        <span>Información adicional (opcional)</span>
        <textarea value={info} onChange={(e) => setInfo(e.target.value)} rows={3} maxLength={4000} placeholder="Biografía, enlaces, notas…" />
      </label>
      <label className="field color-field">
        <span>Color en la transcripción y el vídeo</span>
        <input type="color" value={color} onChange={(e) => setColor(e.target.value.toUpperCase())} />
      </label>
      {error && <p className="error">{error}</p>}
      <div className="actions">
        <button type="submit" disabled={busy || !name.trim()}>
          {busy ? "Guardando…" : "Guardar"}
        </button>
        <button type="button" className="button-secondary" onClick={onCancel} disabled={busy}>
          Cancelar
        </button>
        {profile && onDeleted && (
          <button type="button" className="danger" onClick={remove} disabled={busy}>
            Borrar perfil
          </button>
        )}
      </div>
    </form>
  );
}
