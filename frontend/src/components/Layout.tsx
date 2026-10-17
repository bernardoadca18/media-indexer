import { NavLink, Outlet, useLocation } from "react-router-dom";
import { FolderOpen, Search, Upload } from "lucide-react";
export function Layout() {
  const { pathname } = useLocation();
  const current =
    pathname === "/search"
      ? "Busca de mídia"
      : pathname === "/ingest"
        ? "Importação"
        : pathname.includes("/sessions/")
          ? "Sessão de mídia"
          : pathname.startsWith("/projects/")
            ? "Projeto"
            : "Biblioteca";
  return (
    <div className="studio-shell">
      <a className="skip-link" href="#main-content">
        Pular para o conteúdo
      </a>
      <aside className="studio-sidebar">
        <NavLink to="/" className="studio-brand">
          <b>N</b>
          <span>
            NeoVids<small>BIBLIOTECA AUDIOVISUAL</small>
          </span>
        </NavLink>
        <nav aria-label="Navegação principal">
          {[
            { path: "/", icon: FolderOpen, label: "Projetos" },
            { path: "/search", icon: Search, label: "Buscar mídia" },
            { path: "/ingest", icon: Upload, label: "Importar mídia" },
          ].map(({ path, icon: Icon, label }) => (
            <NavLink
              key={path}
              to={path}
              end={path === "/"}
              className={({ isActive }) =>
                isActive || (path === "/" && pathname.startsWith("/projects/"))
                  ? "active"
                  : ""
              }
            >
              <Icon size={18} aria-hidden="true" />
              <span>{label}</span>
            </NavLink>
          ))}
        </nav>
        <footer>Bolas Industries</footer>
      </aside>
      <div className="studio-body">
        <header className="studio-topbar">
          NeoVids <span>/</span>
          <strong>{current}</strong>
        </header>
        <main id="main-content" tabIndex={-1}>
          <Outlet />
        </main>
      </div>
    </div>
  );
}
