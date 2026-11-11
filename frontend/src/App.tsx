import { useEffect, useState } from "react";
import EpisodeList from "./EpisodeList";
import EpisodeView from "./EpisodeView";

// Enrutado mínimo por hash: #/ (lista) y #/episodios/<id> (detalle).
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
      </header>
      <main className="content">
        {match ? <EpisodeView id={match[1]} /> : <EpisodeList />}
      </main>
    </div>
  );
}
