import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api, apiFetch } from "../api/client";
import { SAMPLE_STATES, SAMPLE_TYPES, type Paginated, type Sample } from "../api/types";

export default function SampleList() {
  const [samples, setSamples] = useState<Sample[]>([]);
  const [next, setNext] = useState<string | null>(null);
  const [state, setState] = useState("");
  const [sampleType, setSampleType] = useState("");
  const [search, setSearch] = useState("");
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);

  async function load() {
    setLoading(true);
    setError(null);
    try {
      const page = await api.listSamples({ state, sample_type: sampleType, search });
      setSamples(page.results);
      setNext(page.next);
    } catch {
      setError("Could not load samples.");
    } finally {
      setLoading(false);
    }
  }

  async function loadMore() {
    if (!next) return;
    const page = await apiFetch<Paginated<Sample>>(next);
    setSamples((prev) => [...prev, ...page.results]);
    setNext(page.next);
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [state, sampleType]);

  return (
    <div>
      <h1>Samples</h1>
      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <select value={state} onChange={(e) => setState(e.target.value)}>
          <option value="">All states</option>
          {SAMPLE_STATES.map((s) => (
            <option key={s} value={s}>
              {s}
            </option>
          ))}
        </select>
        <select value={sampleType} onChange={(e) => setSampleType(e.target.value)}>
          <option value="">All types</option>
          {SAMPLE_TYPES.map((t) => (
            <option key={t} value={t}>
              {t}
            </option>
          ))}
        </select>
        <input
          placeholder="Search barcode / accession #"
          value={search}
          onChange={(e) => setSearch(e.target.value)}
        />
        <button type="submit">Search</button>
      </form>

      {error && <p className="error">{error}</p>}
      {loading ? (
        <p>Loading…</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Barcode</th>
              <th>Accession #</th>
              <th>Type</th>
              <th>State</th>
              <th>Volume</th>
              <th>Location</th>
            </tr>
          </thead>
          <tbody>
            {samples.map((s) => (
              <tr key={s.id}>
                <td>
                  <Link to={`/samples/${s.id}`}>{s.barcode}</Link>
                </td>
                <td>{s.accession_number ?? "—"}</td>
                <td>{s.sample_type}</td>
                <td>
                  <span className={`badge state-${s.state}`}>{s.state}</span>
                </td>
                <td>
                  {s.volume} {s.volume_unit}
                </td>
                <td>
                  {s.current_placement
                    ? `${s.current_placement.location.name}${s.current_placement.row_label}${s.current_placement.column_number ?? ""}`
                    : "—"}
                </td>
              </tr>
            ))}
            {samples.length === 0 && (
              <tr>
                <td colSpan={6}>No samples found.</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
      {next && (
        <button className="secondary" onClick={loadMore}>
          Load more
        </button>
      )}
    </div>
  );
}
