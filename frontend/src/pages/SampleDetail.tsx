import { useEffect, useState } from "react";
import { Link, useNavigate, useParams } from "react-router-dom";
import { ApiError, api, fetchCoaHtml } from "../api/client";
import {
  SAMPLE_STATES,
  type AcceptanceChecklist,
  type CertificateOfAnalysis,
  type RejectionReasonCode,
  type Sample,
  type SamplePlacement,
  type SlaStatus,
  type StorageLocation,
  type TestDefinition,
  type TestOrder,
} from "../api/types";
import { useAuth } from "../auth/AuthContext";

const CHECKLIST_ITEMS: { key: keyof AcceptanceChecklist; label: string }[] = [
  { key: "container_integrity_ok", label: "Container integrity" },
  { key: "seal_integrity_ok", label: "Seal integrity" },
  { key: "quantity_ok", label: "Quantity" },
  { key: "label_ok", label: "Label" },
  { key: "documentation_ok", label: "Documentation" },
  { key: "transport_conditions_ok", label: "Transport/storage conditions" },
];

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

  const [rejectionReasons, setRejectionReasons] = useState<RejectionReasonCode[]>([]);
  const [checklist, setChecklist] = useState<AcceptanceChecklist>({});
  const [withDeviation, setWithDeviation] = useState(false);
  const [deviationNotes, setDeviationNotes] = useState("");
  const [holdReasonCode, setHoldReasonCode] = useState("");
  const [holdReasonText, setHoldReasonText] = useState("");
  const [rejectReasonCode, setRejectReasonCode] = useState("");
  const [rejectReasonText, setRejectReasonText] = useState("");
  const [rejectPassword, setRejectPassword] = useState("");
  const [disposePassword, setDisposePassword] = useState("");

  const [slaStatus, setSlaStatus] = useState<SlaStatus | null>(null);
  const [committedReportDate, setCommittedReportDate] = useState("");
  const [retentionUntil, setRetentionUntil] = useState("");

  const [testDefinitions, setTestDefinitions] = useState<TestDefinition[]>([]);
  const [testOrders, setTestOrders] = useState<TestOrder[]>([]);
  const [certificates, setCertificates] = useState<CertificateOfAnalysis[]>([]);
  const [orderTestDefId, setOrderTestDefId] = useState("");
  const [resultValues, setResultValues] = useState<Record<number, string>>({});

  async function load() {
    if (!id) return;
    const [s, lin, pl, sla, orders, certs] = await Promise.all([
      api.getSample(id),
      api.lineage(id),
      api.placements(id),
      api.slaStatus(id),
      api.listTestOrders({ sample: id }),
      api.listCertificates(id),
    ]);
    setSample(s);
    setLineage(lin);
    setPlacements(pl);
    setSlaStatus(sla);
    setTestOrders(orders);
    setCertificates(certs);
  }

  useEffect(() => {
    load();
    api.listLocations().then(setLocations);
    api.rejectionReasons().then(setRejectionReasons);
    api.listTestDefinitions().then(setTestDefinitions);
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
            <dt>Customer</dt>
            <dd>{sample.customer ? `${sample.customer.name} (${sample.customer.code})` : "—"}</dd>
            <dt>Current location</dt>
            <dd>
              {sample.current_placement
                ? `${sample.current_placement.location.name} ${sample.current_placement.row_label}${sample.current_placement.column_number ?? ""}`
                : "Not placed"}
            </dd>
            <dt>Acceptance decision</dt>
            <dd>
              {sample.acceptance_decision}
              {sample.acceptance_decision === "accepted_with_deviation" &&
                sample.deviation_notes &&
                ` — ${sample.deviation_notes}`}
            </dd>
            {sample.state === "rejected" && (
              <>
                <dt>Rejection reason</dt>
                <dd>
                  {sample.rejection_reason ? `${sample.rejection_reason.code} — ${sample.rejection_reason.label}` : "—"}
                  {sample.rejection_reason_other && ` (${sample.rejection_reason_other})`}
                </dd>
                <dt>QA approved by</dt>
                <dd>{sample.qa_approved_by ?? "—"}</dd>
                <dt>Client notified</dt>
                <dd>
                  {sample.client_notified_at ? new Date(sample.client_notified_at).toLocaleString() : "Not yet"}
                  {!sample.client_notified_at && (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => runAction(() => api.notifyClient(sample.id))}
                    >
                      Mark notified
                    </button>
                  )}
                </dd>
              </>
            )}
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

          {(sample.state === "received" || sample.state === "on_hold") && (
            <details open>
              <summary>Acceptance review</summary>
              <div className="inline-form" style={{ flexDirection: "column", alignItems: "flex-start" }}>
                {CHECKLIST_ITEMS.map(({ key, label }) => (
                  <label key={key}>
                    {label}:{" "}
                    <select
                      value={checklist[key] === undefined ? "" : String(checklist[key])}
                      onChange={(e) => {
                        const v = e.target.value;
                        setChecklist((prev) => {
                          const next = { ...prev };
                          if (v === "") delete next[key];
                          else next[key] = v === "true";
                          return next;
                        });
                      }}
                    >
                      <option value="">Not checked</option>
                      <option value="true">Pass</option>
                      <option value="false">Fail</option>
                    </select>
                  </label>
                ))}

                <div>
                  <label>
                    <input type="checkbox" checked={withDeviation} onChange={(e) => setWithDeviation(e.target.checked)} />
                    Accept with deviation
                  </label>
                  {withDeviation && (
                    <input
                      placeholder="Deviation notes (required)"
                      value={deviationNotes}
                      onChange={(e) => setDeviationNotes(e.target.value)}
                    />
                  )}
                  <button
                    disabled={busy}
                    onClick={() =>
                      runAction(() =>
                        api.acceptSample(sample.id, {
                          with_deviation: withDeviation,
                          deviation_notes: deviationNotes,
                          checklist,
                        })
                      )
                    }
                  >
                    Accept
                  </button>
                </div>

                <div>
                  <input
                    placeholder="Hold reason code"
                    value={holdReasonCode}
                    onChange={(e) => setHoldReasonCode(e.target.value)}
                  />
                  <input
                    placeholder="Hold reason text"
                    value={holdReasonText}
                    onChange={(e) => setHoldReasonText(e.target.value)}
                  />
                  <button
                    className="secondary"
                    disabled={busy || !holdReasonCode || sample.state === "on_hold"}
                    onClick={() =>
                      runAction(() =>
                        api.holdSample(sample.id, {
                          reason_code: holdReasonCode,
                          reason_text: holdReasonText,
                          checklist,
                        })
                      )
                    }
                  >
                    Put on hold
                  </button>
                </div>

                <div>
                  <select value={rejectReasonCode} onChange={(e) => setRejectReasonCode(e.target.value)}>
                    <option value="">Choose a rejection reason…</option>
                    {rejectionReasons.map((r) => (
                      <option key={r.code} value={r.code}>
                        {r.code} — {r.label}
                      </option>
                    ))}
                  </select>
                  <input
                    placeholder={
                      rejectionReasons.find((r) => r.code === rejectReasonCode)?.requires_details
                        ? "Details (required)"
                        : "Details (optional)"
                    }
                    value={rejectReasonText}
                    onChange={(e) => setRejectReasonText(e.target.value)}
                  />
                  <input
                    type="password"
                    placeholder="Your password (signs the rejection)"
                    value={rejectPassword}
                    onChange={(e) => setRejectPassword(e.target.value)}
                  />
                  <button
                    className="secondary"
                    disabled={busy || !rejectReasonCode || !rejectPassword || !me?.can_approve_qa}
                    title={!me?.can_approve_qa ? "QA approval role required" : undefined}
                    onClick={() =>
                      runAction(() =>
                        api.rejectSample(sample.id, {
                          rejection_reason_code: rejectReasonCode,
                          reason_text: rejectReasonText,
                          password: rejectPassword,
                          checklist,
                        })
                      ).then(() => setRejectPassword(""))
                    }
                  >
                    Reject
                  </button>
                  {!me?.can_approve_qa && <p className="muted">Rejection requires a QA-approval role.</p>}
                </div>
              </div>
            </details>
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
                {SAMPLE_STATES.filter(
                  (s) =>
                    // received/on_hold/accessioned/rejected go through the
                    // Acceptance review panel above, which also satisfies
                    // the DB-enforced reason/QA-approval requirements a
                    // plain transition() to "rejected" would fail on.
                    !["received", "on_hold", "accessioned", "rejected"].includes(s) &&
                    (s !== "disposed" || me?.can_dispose_samples)
                ).map((s) => (
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
              {transitionState === "disposed" && (
                <input
                  type="password"
                  placeholder="Your password (signs the disposal)"
                  value={disposePassword}
                  onChange={(e) => setDisposePassword(e.target.value)}
                />
              )}
              <button
                disabled={busy || !transitionState || (transitionState === "disposed" && !disposePassword)}
                onClick={() =>
                  runAction(() =>
                    api.transitionSample(sample.id, {
                      new_state: transitionState as Sample["state"],
                      reason_code: reasonCode,
                      reason_text: reasonText,
                      password: disposePassword,
                    })
                  ).then(() => setDisposePassword(""))
                }
              >
                Apply
              </button>
            </div>
          </details>
        </section>
      </div>

      <section className="card">
        <h2>Turnaround &amp; retention</h2>
        <dl>
          <dt>TAT status</dt>
          <dd>
            {slaStatus && <span className={`badge tat-${slaStatus.tat_status}`}>{slaStatus.tat_status}</span>}
          </dd>
          <dt>Expected completion</dt>
          <dd>{slaStatus?.expected_completion_date ? new Date(slaStatus.expected_completion_date).toLocaleString() : "—"}</dd>
          <dt>Actual COA date</dt>
          <dd>{slaStatus?.actual_completion_date ? new Date(slaStatus.actual_completion_date).toLocaleString() : "—"}</dd>
          <dt>Retention until</dt>
          <dd>{sample.retention_until ? new Date(sample.retention_until).toLocaleString() : "—"}</dd>
        </dl>
        <div className="inline-form">
          <label>
            Committed report date
            <input
              type="datetime-local"
              value={committedReportDate}
              onChange={(e) => setCommittedReportDate(e.target.value)}
            />
          </label>
          <button
            className="secondary"
            disabled={busy || !committedReportDate}
            onClick={() =>
              runAction(() => api.setCommittedReportDate(sample.id, new Date(committedReportDate).toISOString()))
            }
          >
            Set
          </button>
          <label>
            Retention until
            <input type="datetime-local" value={retentionUntil} onChange={(e) => setRetentionUntil(e.target.value)} />
          </label>
          <button
            className="secondary"
            disabled={busy || !retentionUntil}
            onClick={() => runAction(() => api.setRetention(sample.id, new Date(retentionUntil).toISOString()))}
          >
            Set
          </button>
        </div>
      </section>

      <section className="card">
        <h2>Testing</h2>
        <div className="inline-form">
          <select value={orderTestDefId} onChange={(e) => setOrderTestDefId(e.target.value)}>
            <option value="">Choose a test…</option>
            {testDefinitions.map((t) => (
              <option key={t.id} value={t.id}>
                {t.name} ({t.code})
              </option>
            ))}
          </select>
          <button
            disabled={busy || !orderTestDefId}
            onClick={() =>
              runAction(() =>
                api.placeOrder({ sample_id: sample.id, test_definition_id: Number(orderTestDefId) })
              ).then(() => setOrderTestDefId(""))
            }
          >
            Order test
          </button>
        </div>
        <table>
          <thead>
            <tr>
              <th>Test</th>
              <th>Status</th>
              <th>Due</th>
              <th>Result</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {testOrders.map((o) => (
              <tr key={o.id}>
                <td>{o.test_definition.name}</td>
                <td>
                  <span className={`badge state-${o.status}`}>{o.status}</span>
                </td>
                <td>{o.due_at ? new Date(o.due_at).toLocaleString() : "—"}</td>
                <td>
                  {o.has_result
                    ? "recorded"
                    : (o.status === "requested" || o.status === "scheduled" || o.status === "in_progress") && (
                        <div className="inline-form">
                          <input
                            placeholder="Value"
                            value={resultValues[o.id] ?? ""}
                            onChange={(e) => setResultValues((prev) => ({ ...prev, [o.id]: e.target.value }))}
                          />
                          <button
                            className="secondary"
                            disabled={busy || !resultValues[o.id]}
                            onClick={() =>
                              runAction(() =>
                                api.enterResult(o.id, { numeric_value: resultValues[o.id] })
                              )
                            }
                          >
                            Enter result
                          </button>
                        </div>
                      )}
                </td>
                <td>
                  {(o.status === "requested" || o.status === "scheduled") && (
                    <button className="secondary" disabled={busy} onClick={() => runAction(() => api.startOrder(o.id))}>
                      Start
                    </button>
                  )}
                  {(o.status === "requested" || o.status === "scheduled" || o.status === "in_progress") && (
                    <button
                      className="secondary"
                      disabled={busy}
                      onClick={() => runAction(() => api.cancelOrder(o.id, "Cancelled from sample page."))}
                    >
                      Cancel
                    </button>
                  )}
                </td>
              </tr>
            ))}
            {testOrders.length === 0 && (
              <tr>
                <td colSpan={5}>No tests ordered yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Certificates of analysis</h2>
        <button
          disabled={busy}
          onClick={() => runAction(() => api.generateCoa(sample.id))}
        >
          Generate COA
        </button>
        <table>
          <thead>
            <tr>
              <th>Number</th>
              <th>Status</th>
              <th>Generated</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {certificates.map((c) => (
              <tr key={c.id}>
                <td>{c.coa_number || "(draft)"}</td>
                <td>
                  <span className={`badge state-${c.status}`}>{c.status}</span>
                </td>
                <td>{new Date(c.generated_at).toLocaleString()}</td>
                <td>
                  <button
                    className="secondary"
                    disabled={busy}
                    onClick={async () => {
                      const html = await fetchCoaHtml(c.id);
                      const url = URL.createObjectURL(new Blob([html], { type: "text/html" }));
                      window.open(url, "_blank");
                    }}
                  >
                    View
                  </button>
                  {c.status === "draft" && me?.can_approve_results && (
                    <ReleaseCoaButton coaId={c.id} busy={busy} onDone={load} onError={setError} />
                  )}
                </td>
              </tr>
            ))}
            {certificates.length === 0 && (
              <tr>
                <td colSpan={4}>No certificates yet.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

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

function ReleaseCoaButton({
  coaId,
  busy,
  onDone,
  onError,
}: {
  coaId: number;
  busy: boolean;
  onDone: () => Promise<void>;
  onError: (msg: string) => void;
}) {
  const [password, setPassword] = useState("");
  const [releasing, setReleasing] = useState(false);

  return (
    <span className="inline-form">
      <input
        type="password"
        placeholder="Password to release"
        value={password}
        onChange={(e) => setPassword(e.target.value)}
      />
      <button
        className="secondary"
        disabled={busy || releasing || !password}
        onClick={async () => {
          setReleasing(true);
          try {
            await api.releaseCoa(coaId, { password });
            setPassword("");
            await onDone();
          } catch (err) {
            onError(formatError(err));
          } finally {
            setReleasing(false);
          }
        }}
      >
        Release
      </button>
    </span>
  );
}
