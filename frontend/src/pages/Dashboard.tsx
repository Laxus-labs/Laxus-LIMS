import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { api } from "../api/client";
import type { Sample, TatSummary, TestOrder } from "../api/types";

export default function Dashboard() {
  const [tat, setTat] = useState<TatSummary | null>(null);
  const [overdueSamples, setOverdueSamples] = useState<Sample[]>([]);
  const [overdueOrders, setOverdueOrders] = useState<TestOrder[]>([]);
  const [dueForDisposal, setDueForDisposal] = useState<Sample[]>([]);

  useEffect(() => {
    api.tatSummary().then(setTat);
    api.overdueSamples().then(setOverdueSamples);
    api.overdueOrders().then(setOverdueOrders);
    api.samplesDueForDisposal().then(setDueForDisposal);
  }, []);

  return (
    <div>
      <h1>Operations dashboard</h1>

      <section className="card">
        <h2>Turnaround time</h2>
        {tat && (
          <dl>
            <dt>Completed orders (all time)</dt>
            <dd>{tat.total_completed}</dd>
            <dt>Average TAT</dt>
            <dd>{tat.average_tat_hours != null ? `${tat.average_tat_hours} hours` : "—"}</dd>
            <dt>On-time compliance</dt>
            <dd>{tat.tat_compliance_pct != null ? `${tat.tat_compliance_pct}%` : "—"}</dd>
          </dl>
        )}
      </section>

      <section className="card">
        <h2>Overdue samples</h2>
        <table>
          <thead>
            <tr>
              <th>Barcode</th>
              <th>State</th>
              <th>Customer</th>
            </tr>
          </thead>
          <tbody>
            {overdueSamples.map((s) => (
              <tr key={s.id}>
                <td>
                  <Link to={`/samples/${s.id}`}>{s.barcode}</Link>
                </td>
                <td>
                  <span className={`badge state-${s.state}`}>{s.state}</span>
                </td>
                <td>{s.customer?.name ?? "—"}</td>
              </tr>
            ))}
            {overdueSamples.length === 0 && (
              <tr>
                <td colSpan={3}>Nothing overdue.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Overdue test orders</h2>
        <table>
          <thead>
            <tr>
              <th>Sample</th>
              <th>Test</th>
              <th>Status</th>
              <th>Due</th>
            </tr>
          </thead>
          <tbody>
            {overdueOrders.map((o) => (
              <tr key={o.id}>
                <td>
                  <Link to={`/samples/${o.sample.id}`}>{o.sample.barcode}</Link>
                </td>
                <td>{o.test_definition.name}</td>
                <td>
                  <span className={`badge state-${o.status}`}>{o.status}</span>
                </td>
                <td>{o.due_at ? new Date(o.due_at).toLocaleString() : "—"}</td>
              </tr>
            ))}
            {overdueOrders.length === 0 && (
              <tr>
                <td colSpan={4}>Nothing overdue.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>

      <section className="card">
        <h2>Due for disposal</h2>
        <table>
          <thead>
            <tr>
              <th>Barcode</th>
              <th>State</th>
              <th>Retention until</th>
            </tr>
          </thead>
          <tbody>
            {dueForDisposal.map((s) => (
              <tr key={s.id}>
                <td>
                  <Link to={`/samples/${s.id}`}>{s.barcode}</Link>
                </td>
                <td>
                  <span className={`badge state-${s.state}`}>{s.state}</span>
                </td>
                <td>{s.retention_until ? new Date(s.retention_until).toLocaleString() : "—"}</td>
              </tr>
            ))}
            {dueForDisposal.length === 0 && (
              <tr>
                <td colSpan={3}>Nothing due.</td>
              </tr>
            )}
          </tbody>
        </table>
      </section>
    </div>
  );
}
