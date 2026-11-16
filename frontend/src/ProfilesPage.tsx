import { useEffect, useState } from "react";
import { api, Profile } from "./api";
import Avatar from "./Avatar";
import Modal from "./Modal";
import ProfileForm from "./ProfileForm";

// Biblioteca de intervinientes, compartida por todos los episodios.
export default function ProfilesPage() {
  const [profiles, setProfiles] = useState<Profile[] | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [editing, setEditing] = useState<Profile | "new" | null>(null);

  const reload = () => api.listProfiles().then(setProfiles).catch((e) => setError(e.message));
  useEffect(() => {
    reload();
  }, []);

  return (
    <section className="card">
      <div className="title-row">
        <h1>Intervinientes</h1>
        <button onClick={() => setEditing("new")}>Nuevo interviniente</button>
      </div>
      <p className="muted">
        Los perfiles se reutilizan entre episodios: crea una vez a tu presentador o a un invitado
        recurrente y asígnalo a su voz en cada episodio.
      </p>
      {error && <p className="error">{error}</p>}
      {profiles?.length === 0 && <p className="muted">Todavía no hay intervinientes.</p>}
      <ul className="profile-list">
        {profiles?.map((p) => (
          <li key={p.id}>
            <button className="profile-row" onClick={() => setEditing(p)}>
              <Avatar profile={p} fallback={p.name} color={p.color} size={48} />
              <span>
                <strong>{p.name}</strong>
                {p.role && <span className="muted"> · {p.role}</span>}
                {p.info && <span className="muted small profile-info">{p.info}</span>}
              </span>
            </button>
          </li>
        ))}
      </ul>
      {editing && (
        <Modal
          title={editing === "new" ? "Nuevo interviniente" : `Editar ${editing.name}`}
          onClose={() => setEditing(null)}
        >
          <ProfileForm
            profile={editing === "new" ? undefined : editing}
            onCancel={() => setEditing(null)}
            onSaved={() => {
              setEditing(null);
              reload();
            }}
            onDeleted={() => {
              setEditing(null);
              reload();
            }}
          />
        </Modal>
      )}
    </section>
  );
}
