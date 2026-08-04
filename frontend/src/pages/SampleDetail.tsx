import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { SAMPLE_STATES, type Sample, type SamplePlacement, type StorageLocation } from "../api/types";
import { useAuth } from "../auth/AuthContext";

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.data && typeof err.data === "object") {
      const data = err.data as Record<string, unknown>;
      if ("detail" in data) return String(data.detail);
      return Object.entries(data)
        .map(([field, msgs]) => `${field}: ${Array.isArray(msgs) ? msgs.join(", ") : msgs}`)
        .join(" · ");
    }
    return err.message;
  }
  return "Something went wrong.";
}

export default function SampleDetail() {
  const { id } = useParams<{ id: string }>();
  const navigate = useNavigate();
  const { me } = useAuth();

  const [sample, setSample] = useState<Sample | null>(null);
  const [lineage, setLineage] = useState<Sample[]>([]);
  const [placements, setPlacements] = useState<SamplePlacement[]>([]);
  const [locations, setLocations] = useState<StorageLocation[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);

  const [transferLocationId, setTransferLocationId] = useState("");
  const [transferRow, setTransferRow] = useState("");
  const [transferCol, setTransferCol] = useState("");

  const [aliquotCount, setAliquotCount] = useState("1");
  const [aliquotVolume, setAliquotVolume] = useState("");

  const [transitionState, setTransitionState] = useState("");
  const [reasonCode, setReasonCode] = useState("");
  const [reasonText, setReasonText] = useState("");

  async function load() {
    if (!id) return;
    const [s, lin, pl] = await Promise.all([api.getSample(id), api.lineage(id), api.placements(id)]);
    setSample(s);
    setLineage(lin);
    setPlacements(pl);
  }

  useEffect(() => {
    load();
    api.listLocations().then(setLocations);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [id]);

  async function runAction(fn: () => Promise<unknown>) {
    setError(null);
    setBusy(true);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(false);
    }
  }

  if (!sample) return <p>Loading…</p>;

  const boxLocations = locations.filter((l) => l.location_type === "box");

  return (
    <div>
      <p>
        <Link to="/samples">&larr; Back to samples</Link>
      </p>
      <h1>
        {sample.barcode} <span className={`badge state-${sample.state}`}>{sample.state}</span>
      </h1>
      {error && <p className="error">{error}</p>}

      <div className="grid-two">
        <section className="card">
          <h2>Details</h2>
          <dl>
            <dt>Accession #</dt>
            <dd>{sample.accession_number ?? "—"}</dd>
            <dt>Type</dt>
            <dd>{sample.sample_type}</dd>
            <dt>Volume</dt>
            <dd>
              {sample.volume} {sample.volume_unit}
            </dd>
            <dt>Subject ref</dt>
            <dd>{sample.subject_ref || "—"}</dd>
            <dt>Current location</dt>
            <dd>
              {sample.current_placement
                ? `${sample.current_placement.location.name} ${sample.current_placement.row_label}${sample.current_placement.column_number ?? ""}`
                : "Not placed"}
            </dd>
            <dt>Created by</dt>
            <dd>{sample.created_by}</dd>
            {sample.parent && (
              <>
                <dt>Parent</dt>
                <dd>
                  <Link to={`/samples/${sample.parent}`}>{sample.parent}</Link>
                </dd>
              </>
            )}
          </dl>
        </section>

        <section className="card">
          <h2>Actions</h2>

          {sample.state === "received" && (
            <button disabled={busy} onClick={() => runAction(() => api.accessionSample(sample.id))}>
              Accession
            </button>
          )}

          <details>
            <summary>Transfer</summary>
            <div className="inline-form">
              <select value={transferLocationId} onChange={(e) => setTransferLocationId(e.target.value)}>
                <option value="">Choose a box…</option>
                {boxLocations.map((l) => (
                  <option key={l.id} value={l.id}>
                    {l.name}
                  </option>
                ))}
              </select>
              <input placeholder="Row (A)" value={transferRow} onChange={(e) => setTransferRow(e.target.value)} />
              <input
                placeholder="Column (1)"
                type="number"
                value={transferCol}
                onChange={(e) => setTransferCol(e.target.value)}
              />
              <button
                disabled={busy || !transferLocationId}
                onClick={() =>
                  runAction(() =>
                    api.transferSample(sample.id, {
                      location_id: Number(transferLocationId),
                      row_label: transferRow,
                      column_number: transferCol ? Number(transferCol) : undefined,
                    })
                  )
                }
              >
                Move
              </button>
            </div>
          </details>

          <details>
            <summary>Aliquot</summary>
            <div className="inline-form">
              <input
                placeholder="Count"
                type="number"
                min={1}
                value={aliquotCount}
                onChange={(e) => setAliquotCount(e.target.value)}
              />
              <input
                placeholder={`Volume each (${sample.volume_unit})`}
                type="number"
                value={aliquotVolume}
                onChange={(e) => setAliquotVolume(e.target.value)}
              />
              <button
                disabled={busy || !aliquotVolume}
                onClick={() =>
                  runAction(() =>
                    api.aliquotSample(sample.id, {
                      count: Number(aliquotCount),
                      volume_each: Number(aliquotVolume),
                    }).then((children) => {
                      if (children[0]) navigate(`/samples/${children[0].id}`);
                    })
                  )
                }
              >
                Create aliquots
              </button>
            </div>
          </details>

          <details>
            <summary>Change state</summary>
            <div className="inline-form">
              <select value={transitionState} onChange={(e) => setTransitionState(e.target.value)}>
                <option value="">Choose a state…</option>
                {SAMPLE_STATES.filter((s) => s !== "disposed" || me?.can_dispose_samples).map((s) => (
                  <option key={s} value={s}>
                    {s}
                  </option>
                ))}
              </select>
              <input
                placeholder="Reason code (required to dispose/reject)"
                value={reasonCode}
                onChange={(e) => setReasonCode(e.target.value)}
              />
              <input placeholder="Reason text" value={reasonText} onChange={(e) => setReasonText(e.target.value)} />
              <button
                disabled={busy || !transitionState}
                onClick={() =>
                  runAction(() =>
                    api.transitionSample(sample.id, {
                      new_state: transitionState as Sample["state"],
                      reason_code: reasonCode,
                      reason_text: reasonText,
                    })
                  )
                }
              >
                Apply
              </button>
            </div>
          </details>
        </section>
      </div>

      <section className="card">
        <h2>Lineage</h2>
        <ul>
          {lineage.map((s) => (
            <li key={s.id}>
              <Link to={`/samples/${s.id}`}>{s.barcode}</Link> — gen {s.generation}, {s.state}
              {s.id === sample.id && " (this sample)"}
            </li>
          ))}
        </ul>
      </section>

      <section className="card">
        <h2>Placement history</h2>
        <table>
          <thead>
            <tr>
              <th>Location</th>
              <th>Position</th>
              <th>Placed</th>
              <th>Removed</th>
            </tr>
          </thead>
          <tbody>
            {placements.map((p) => (
              <tr key={p.id}>
                <td>{p.location.name}</td>
                <td>
                  {p.row_label}
                  {p.column_number ?? ""}
                </td>
                <td>{new Date(p.placed_at).toLocaleString()}</td>
                <td>{p.removed_at ? new Date(p.removed_at).toLocaleString() : "current"}</td>
              </tr>
            ))}
          </tbody>
        </table>
      </section>
    </div>
  );
}
