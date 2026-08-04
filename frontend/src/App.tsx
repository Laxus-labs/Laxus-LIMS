import { type ReactNode } from "react";
import { NavLink, Navigate, Route, Routes } from "react-router-dom";
import { useAuth } from "./auth/AuthContext";
import Login from "./pages/Login";
import SampleList from "./pages/SampleList";
import SampleDetail from "./pages/SampleDetail";
import BatchAccession from "./pages/BatchAccession";
import LocationBrowser from "./pages/LocationBrowser";

function RequireAuth({ children }: { children: ReactNode }) {
  const { me, loading } = useAuth();
  if (loading) return <p>Loading…</p>;
  if (!me) return <Navigate to="/login" replace />;
  return <>{children}</>;
}

function Layout({ children }: { children: ReactNode }) {
  const { me, logout } = useAuth();
  return (
    <div className="app">
      <nav>
        <div className="brand">LIMS</div>
        <NavLink to="/samples">Samples</NavLink>
        <NavLink to="/accession">Batch accession</NavLink>
        <NavLink to="/storage">Storage</NavLink>
        <div className="spacer" />
        {me && (
          <>
            <span className="muted">
              {me.username} ({me.role})
            </span>
            <button className="secondary" onClick={logout}>
              Sign out
            </button>
          </>
        )}
      </nav>
      <main>{children}</main>
    </div>
  );
}

export default function App() {
  const { me } = useAuth();

  return (
    <Routes>
      <Route path="/login" element={me ? <Navigate to="/samples" replace /> : <Login />} />
      <Route
        path="/*"
        element={
          <RequireAuth>
            <Layout>
              <Routes>
                <Route path="/" element={<Navigate to="/samples" replace />} />
                <Route path="/samples" element={<SampleList />} />
                <Route path="/samples/:id" element={<SampleDetail />} />
                <Route path="/accession" element={<BatchAccession />} />
                <Route path="/storage" element={<LocationBrowser />} />
                <Route path="*" element={<Navigate to="/samples" replace />} />
              </Routes>
            </Layout>
          </RequireAuth>
        }
      />
    </Routes>
  );
}
