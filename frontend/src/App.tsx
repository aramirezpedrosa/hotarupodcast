import { useEffect, useState } from "react";
import EpisodeList from "./EpisodeList";
import EpisodeView from "./EpisodeView";
import ProfilesPage from "./ProfilesPage";

// Enrutado mínimo por hash: #/ (lista), #/episodios/<id> (detalle) y #/intervinientes.
function useRoute(): string {
  const [hash, setHash] = useState(window.location.hash);
  useEffect(() => {
    const onChange = () => setHash(window.location.hash);
    window.addEventListener("hashchange", onChange);
    return () => window.removeEventListener("hashchange", onChange);
  }, []);
  return hash;
}

export default function App() {
  const route = useRoute();
  const match = route.match(/^#\/episodios\/([a-z0-9-]+)/);

  return (
    <div className="app">
      <header className="topbar">
        <a href="#/" className="brand">
          HotaruPodcast
        </a>
        <nav>
          <a href="#/">Episodios</a>
          <a href="#/intervinientes">Intervinientes</a>
        </nav>
      </header>
      <main className="content">
        {match ? (
          <EpisodeView id={match[1]} />
        ) : route.startsWith("#/intervinientes") ? (
          <ProfilesPage />
        ) : (
          <EpisodeList />
        )}
      </main>
    </div>
  );
}
