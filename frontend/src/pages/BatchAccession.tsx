import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api } from "../api/client";
import { SAMPLE_TYPES, type AccessionBatchResult, type Customer } from "../api/types";

export default function BatchAccession() {
  const [barcodes, setBarcodes] = useState("");
  const [sampleType, setSampleType] = useState<(typeof SAMPLE_TYPES)[number]>("serum");
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [customerId, setCustomerId] = useState("");
  const [result, setResult] = useState<AccessionBatchResult | null>(null);
  const [error, setError] = useState<string | null>(null);
  const [submitting, setSubmitting] = useState(false);

  useEffect(() => {
    api.listCustomers({}).then((page) => setCustomers(page.results));
  }, []);

  async function submit() {
    const scans = barcodes
      .split(/[\s,]+/)
      .map((b) => b.trim())
      .filter(Boolean)
      .map((barcode) => ({
        barcode,
        sample_type: sampleType,
        customer_id: customerId ? Number(customerId) : undefined,
      }));

    if (scans.length === 0) return;

    setSubmitting(true);
    setError(null);
    setResult(null);
    try {
      setResult(await api.accessionBatch(scans));
      setBarcodes("");
    } catch (err) {
      setError(err instanceof ApiError ? err.message : "Batch accession failed.");
    } finally {
      setSubmitting(false);
    }
  }

  return (
    <div>
      <h1>Batch accession</h1>
      <p>Scan or paste one barcode per line (or separated by commas/spaces).</p>

      <div className="card">
        <label>
          Sample type for this batch
          <select value={sampleType} onChange={(e) => setSampleType(e.target.value as (typeof SAMPLE_TYPES)[number])}>
            {SAMPLE_TYPES.map((t) => (
              <option key={t} value={t}>
                {t}
              </option>
            ))}
          </select>
        </label>
        <label>
          Customer for this batch (optional)
          <select value={customerId} onChange={(e) => setCustomerId(e.target.value)}>
            <option value="">No customer</option>
            {customers.map((c) => (
              <option key={c.id} value={c.id}>
                {c.name} ({c.code})
              </option>
            ))}
          </select>
        </label>
        <textarea
          rows={8}
          placeholder="BC-0001&#10;BC-0002&#10;BC-0003"
          value={barcodes}
          onChange={(e) => setBarcodes(e.target.value)}
          autoFocus
        />
        <button disabled={submitting || !barcodes.trim()} onClick={submit}>
          {submitting ? "Submitting…" : "Accession batch"}
        </button>
      </div>

      {error && <p className="error">{error}</p>}

      {result && (
        <div className="card">
          <h2>
            {result.total_scanned} scanned — {result.created.length} created, {result.duplicates.length} already known
          </h2>
          {result.created.length > 0 && (
            <>
              <h3>Created</h3>
              <ul>
                {result.created.map((s) => (
                  <li key={s.id}>
                    <Link to={`/samples/${s.id}`}>{s.barcode}</Link>
                  </li>
                ))}
              </ul>
            </>
          )}
          {result.duplicates.length > 0 && (
            <>
              <h3>Already accessioned (duplicate scans)</h3>
              <ul>
                {result.duplicates.map((s) => (
                  <li key={s.id}>
                    <Link to={`/samples/${s.id}`}>{s.barcode}</Link>
                  </li>
                ))}
              </ul>
            </>
          )}
        </div>
      )}
    </div>
  );
}
