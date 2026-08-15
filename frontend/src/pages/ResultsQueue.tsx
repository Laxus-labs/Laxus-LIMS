import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import type { ResultStatus, TestResult } from "../api/types";
import { useAuth } from "../auth/AuthContext";

function formatError(err: unknown): string {
  if (err instanceof ApiError) {
    if (err.data && typeof err.data === "object") {
      const data = err.data as Record<string, unknown>;
      if ("detail" in data) return String(data.detail);
    }
    return err.message;
  }
  return "Something went wrong.";
}

const STATUS_FILTERS: { value: ResultStatus | ""; label: string }[] = [
  { value: "draft", label: "Draft (not submitted)" },
  { value: "submitted", label: "Awaiting review" },
  { value: "reviewed", label: "Awaiting approval" },
  { value: "", label: "All" },
];

export default function ResultsQueue() {
  const { me } = useAuth();
  const [status, setStatus] = useState<ResultStatus | "">("draft");
  const [results, setResults] = useState<TestResult[]>([]);
  const [error, setError] = useState<string | null>(null);
  const [busy, setBusy] = useState<number | null>(null);
  const [passwords, setPasswords] = useState<Record<number, string>>({});
  const [rejectReasons, setRejectReasons] = useState<Record<number, string>>({});
  const [oosResolutions, setOosResolutions] = useState<Record<number, string>>({});

  async function load() {
    const data = await api.listTestResults(status ? { status } : {});
    setResults(data);
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [status]);

  async function run(id: number, fn: () => Promise<unknown>) {
    setError(null);
    setBusy(id);
    try {
      await fn();
      await load();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setBusy(null);
    }
  }

  return (
    <div>
      <h1>Results queue</h1>
      <div className="filters">
        {STATUS_FILTERS.map((f) => (
          <button
            key={f.value}
            className={status === f.value ? "" : "secondary"}
            onClick={() => setStatus(f.value)}
          >
            {f.label}
          </button>
        ))}
      </div>
      {error && <p className="error">{error}</p>}

      <table>
        <thead>
          <tr>
            <th>Sample</th>
            <th>Test</th>
            <th>Value</th>
            <th>OOS</th>
            <th>Status</th>
            <th>Entered by</th>
            <th>Actions</th>
          </tr>
        </thead>
        <tbody>
          {results.map((r) => (
            <tr key={r.id}>
              <td>
                <Link to={`/samples/${r.order.sample.id}`}>{r.order.sample.barcode}</Link>
              </td>
              <td>{r.order.test_definition.name}</td>
              <td>{r.numeric_value ?? r.text_value}</td>
              <td>{r.is_out_of_spec ? <span className="badge state-rejected">OOS</span> : "—"}</td>
              <td>
                <span className={`badge state-${r.status}`}>{r.status}</span>
              </td>
              <td>{r.entered_by}</td>
              <td>
                <div className="inline-form">
                  {r.status === "draft" && (
                    <button disabled={busy === r.id} onClick={() => run(r.id, () => api.submitResult(r.id))}>
                      Submit for review
                    </button>
                  )}
                  {r.status === "submitted" && me?.can_review_results && (
                    <>
                      <input
                        type="password"
                        placeholder="Password"
                        value={passwords[r.id] ?? ""}
                        onChange={(e) => setPasswords((p) => ({ ...p, [r.id]: e.target.value }))}
                      />
                      <button
                        disabled={busy === r.id || !passwords[r.id]}
                        onClick={() =>
                          run(r.id, () => api.reviewResult(r.id, { password: passwords[r.id] }))
                        }
                      >
                        Review
                      </button>
                      <input
                        placeholder="Reject reason"
                        value={rejectReasons[r.id] ?? ""}
                        onChange={(e) => setRejectReasons((p) => ({ ...p, [r.id]: e.target.value }))}
                      />
                      <button
                        className="secondary"
                        disabled={busy === r.id || !rejectReasons[r.id]}
                        onClick={() => run(r.id, () => api.rejectResult(r.id, rejectReasons[r.id]))}
                      >
                        Reject
                      </button>
                    </>
                  )}
                  {r.status === "reviewed" && me?.can_approve_results && (
                    <>
                      <input
                        type="password"
                        placeholder="Password"
                        value={passwords[r.id] ?? ""}
                        onChange={(e) => setPasswords((p) => ({ ...p, [r.id]: e.target.value }))}
                      />
                      <button
                        disabled={busy === r.id || !passwords[r.id]}
                        onClick={() =>
                          run(r.id, () => api.approveResult(r.id, { password: passwords[r.id] }))
                        }
                      >
                        Approve
                      </button>
                    </>
                  )}
                  {r.is_out_of_spec && !r.oos_closed_at && me?.can_approve_results && (
                    <>
                      <input
                        type="password"
                        placeholder="Password"
                        value={passwords[r.id] ?? ""}
                        onChange={(e) => setPasswords((p) => ({ ...p, [r.id]: e.target.value }))}
                      />
                      <input
                        placeholder="OOS resolution"
                        value={oosResolutions[r.id] ?? ""}
                        onChange={(e) => setOosResolutions((p) => ({ ...p, [r.id]: e.target.value }))}
                      />
                      <button
                        className="secondary"
                        disabled={busy === r.id || !passwords[r.id] || !oosResolutions[r.id]}
                        onClick={() =>
                          run(r.id, () =>
                            api.closeOos(r.id, {
                              password: passwords[r.id],
                              resolution_text: oosResolutions[r.id],
                            })
                          )
                        }
                      >
                        Close OOS
                      </button>
                    </>
                  )}
                </div>
              </td>
            </tr>
          ))}
          {results.length === 0 && (
            <tr>
              <td colSpan={7}>Nothing here.</td>
            </tr>
          )}
        </tbody>
      </table>
    </div>
  );
}
