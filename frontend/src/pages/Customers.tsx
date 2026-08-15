import { useEffect, useState } from "react";
import { ApiError, api } from "../api/client";
import { CUSTOMER_CATEGORIES, type Customer } from "../api/types";

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

const emptyForm = {
  name: "",
  code: "",
  category: "other" as (typeof CUSTOMER_CATEGORIES)[number],
  contact_person: "",
  phone: "",
  email: "",
  gst_number: "",
  billing_address: "",
  shipping_address: "",
};

export default function Customers() {
  const [customers, setCustomers] = useState<Customer[]>([]);
  const [search, setSearch] = useState("");
  const [showInactive, setShowInactive] = useState(false);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [form, setForm] = useState(emptyForm);
  const [submitting, setSubmitting] = useState(false);

  async function load() {
    setLoading(true);
    try {
      const page = await api.listCustomers({ search, is_active: showInactive ? "all" : undefined });
      setCustomers(page.results);
    } catch {
      setError("Could not load customers.");
    } finally {
      setLoading(false);
    }
  }

  useEffect(() => {
    load();
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [showInactive]);

  async function submit() {
    setSubmitting(true);
    setError(null);
    try {
      await api.createCustomer(form);
      setForm(emptyForm);
      await load();
    } catch (err) {
      setError(formatError(err));
    } finally {
      setSubmitting(false);
    }
  }

  async function toggleActive(customer: Customer) {
    try {
      if (customer.is_active) {
        await api.deactivateCustomer(customer.id);
      } else {
        await api.reactivateCustomer(customer.id);
      }
      await load();
    } catch (err) {
      setError(formatError(err));
    }
  }

  return (
    <div>
      <h1>Customers</h1>

      <section className="card">
        <h2>Register a customer</h2>
        <div className="inline-form">
          <input placeholder="Name" value={form.name} onChange={(e) => setForm({ ...form, name: e.target.value })} />
          <input placeholder="Code" value={form.code} onChange={(e) => setForm({ ...form, code: e.target.value })} />
          <select value={form.category} onChange={(e) => setForm({ ...form, category: e.target.value as typeof form.category })}>
            {CUSTOMER_CATEGORIES.map((c) => (
              <option key={c} value={c}>
                {c}
              </option>
            ))}
          </select>
          <input
            placeholder="Contact person"
            value={form.contact_person}
            onChange={(e) => setForm({ ...form, contact_person: e.target.value })}
          />
          <input placeholder="Phone" value={form.phone} onChange={(e) => setForm({ ...form, phone: e.target.value })} />
          <input placeholder="Email" value={form.email} onChange={(e) => setForm({ ...form, email: e.target.value })} />
          <input
            placeholder="GST number"
            value={form.gst_number}
            onChange={(e) => setForm({ ...form, gst_number: e.target.value })}
          />
          <button disabled={submitting || !form.name || !form.code} onClick={submit}>
            {submitting ? "Saving…" : "Add customer"}
          </button>
        </div>
      </section>

      {error && <p className="error">{error}</p>}

      <form
        className="filters"
        onSubmit={(e) => {
          e.preventDefault();
          load();
        }}
      >
        <input placeholder="Search name / code / GST" value={search} onChange={(e) => setSearch(e.target.value)} />
        <label>
          <input type="checkbox" checked={showInactive} onChange={(e) => setShowInactive(e.target.checked)} />
          Show inactive
        </label>
        <button type="submit">Search</button>
      </form>

      {loading ? (
        <p>Loading…</p>
      ) : (
        <table>
          <thead>
            <tr>
              <th>Name</th>
              <th>Code</th>
              <th>Category</th>
              <th>Contact</th>
              <th>GST</th>
              <th>Status</th>
              <th></th>
            </tr>
          </thead>
          <tbody>
            {customers.map((c) => (
              <tr key={c.id}>
                <td>{c.name}</td>
                <td>{c.code}</td>
                <td>{c.category}</td>
                <td>
                  {c.contact_person} {c.phone && `(${c.phone})`}
                </td>
                <td>{c.gst_number || "—"}</td>
                <td>{c.is_active ? "Active" : "Inactive"}</td>
                <td>
                  <button className="secondary" onClick={() => toggleActive(c)}>
                    {c.is_active ? "Deactivate" : "Reactivate"}
                  </button>
                </td>
              </tr>
            ))}
            {customers.length === 0 && (
              <tr>
                <td colSpan={7}>No customers found.</td>
              </tr>
            )}
          </tbody>
        </table>
      )}
    </div>
  );
}
