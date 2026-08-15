import type {
  AcceptanceChecklist,
  AccessionBatchResult,
  CertificateOfAnalysis,
  Customer,
  Me,
  Paginated,
  RejectionReasonCode,
  Sample,
  SamplePlacement,
  SampleState,
  SlaStatus,
  Specification,
  StorageLocation,
  TatSummary,
  TestDefinition,
  TestOrder,
  TestResult,
} from "./types";

const BASE_URL = import.meta.env.VITE_API_BASE_URL ?? "http://localhost:8000/api";
const REFRESH_KEY = "lims_refresh_token";

let accessToken: string | null = null;

export const tokens = {
  setAccess(token: string | null) {
    accessToken = token;
  },
  getAccess() {
    return accessToken;
  },
  setRefresh(token: string | null) {
    if (token) localStorage.setItem(REFRESH_KEY, token);
    else localStorage.removeItem(REFRESH_KEY);
  },
  getRefresh() {
    return localStorage.getItem(REFRESH_KEY);
  },
  clear() {
    accessToken = null;
    localStorage.removeItem(REFRESH_KEY);
  },
};

export class ApiError extends Error {
  status: number;
  data: unknown;

  constructor(status: number, data: unknown) {
    super(typeof data === "object" && data && "detail" in data ? String((data as { detail: unknown }).detail) : `Request failed (${status})`);
    this.status = status;
    this.data = data;
  }
}

async function refreshAccessToken(): Promise<boolean> {
  const refresh = tokens.getRefresh();
  if (!refresh) return false;
  const resp = await fetch(`${BASE_URL}/auth/token/refresh/`, {
    method: "POST",
    headers: { "Content-Type": "application/json" },
    body: JSON.stringify({ refresh }),
  });
  if (!resp.ok) {
    tokens.clear();
    return false;
  }
  const data = await resp.json();
  tokens.setAccess(data.access);
  return true;
}

interface RequestOptions {
  method?: string;
  body?: unknown;
  params?: Record<string, string | number | boolean | undefined>;
}

function buildUrl(path: string, params?: RequestOptions["params"]) {
  // CursorPagination returns absolute "next"/"previous" URLs — pass those
  // straight through instead of re-joining them onto BASE_URL.
  const url = new URL(path.startsWith("http") ? path : `${BASE_URL}${path}`);
  if (params) {
    for (const [key, value] of Object.entries(params)) {
      if (value !== undefined && value !== "") url.searchParams.set(key, String(value));
    }
  }
  return url.toString();
}

export async function apiFetch<T>(path: string, options: RequestOptions = {}, retry = true): Promise<T> {
  const headers: Record<string, string> = {};
  if (options.body !== undefined) headers["Content-Type"] = "application/json";
  if (accessToken) headers["Authorization"] = `Bearer ${accessToken}`;

  const resp = await fetch(buildUrl(path, options.params), {
    method: options.method ?? "GET",
    headers,
    body: options.body !== undefined ? JSON.stringify(options.body) : undefined,
  });

  if (resp.status === 401 && retry) {
    const refreshed = await refreshAccessToken();
    if (refreshed) return apiFetch<T>(path, options, false);
  }

  if (resp.status === 204) return undefined as T;

  const data = resp.headers.get("content-type")?.includes("application/json") ? await resp.json() : null;

  if (!resp.ok) throw new ApiError(resp.status, data);
  return data as T;
}

export interface ScanInput {
  barcode: string;
  sample_type: string;
  customer_id?: number;
  volume?: number;
  volume_unit?: string;
  subject_ref?: string;
}

export const api = {
  login: (username: string, password: string) =>
    apiFetch<{ access: string; refresh: string }>("/auth/token/", {
      method: "POST",
      body: { username, password },
    }),

  me: () => apiFetch<Me>("/me/"),

  listSamples: (params: { state?: string; sample_type?: string; search?: string; cursor?: string }) =>
    apiFetch<Paginated<Sample>>("/samples/", { params }),

  getSample: (id: string) => apiFetch<Sample>(`/samples/${id}/`),

  accessionBatch: (scans: ScanInput[]) =>
    apiFetch<AccessionBatchResult>("/samples/accession-batch/", { method: "POST", body: { scans } }),

  accessionSample: (id: string) => apiFetch<Sample>(`/samples/${id}/accession/`, { method: "POST" }),

  acceptSample: (
    id: string,
    payload: {
      with_deviation?: boolean;
      deviation_notes?: string;
      checklist?: AcceptanceChecklist;
      reason_code?: string;
      reason_text?: string;
    }
  ) => apiFetch<Sample>(`/samples/${id}/accept/`, { method: "POST", body: payload }),

  holdSample: (
    id: string,
    payload: { reason_code: string; reason_text?: string; checklist?: AcceptanceChecklist }
  ) => apiFetch<Sample>(`/samples/${id}/hold/`, { method: "POST", body: payload }),

  rejectSample: (
    id: string,
    payload: {
      rejection_reason_code: string;
      password: string;
      reason_text?: string;
      checklist?: AcceptanceChecklist;
    }
  ) => apiFetch<Sample>(`/samples/${id}/reject/`, { method: "POST", body: payload }),

  notifyClient: (id: string) => apiFetch<Sample>(`/samples/${id}/notify-client/`, { method: "POST" }),

  rejectionReasons: () => apiFetch<RejectionReasonCode[]>("/samples/rejection-reasons/"),

  transferSample: (
    id: string,
    payload: { location_id: number; row_label?: string; column_number?: number; reason_code?: string; reason_text?: string }
  ) => apiFetch<SamplePlacement>(`/samples/${id}/transfer/`, { method: "POST", body: payload }),

  aliquotSample: (id: string, payload: { count: number; volume_each: number; reason_code?: string }) =>
    apiFetch<Sample[]>(`/samples/${id}/aliquot/`, { method: "POST", body: payload }),

  transitionSample: (
    id: string,
    payload: { new_state: SampleState; reason_code?: string; reason_text?: string; password?: string }
  ) => apiFetch<Sample>(`/samples/${id}/transition/`, { method: "POST", body: payload }),

  lineage: (id: string) => apiFetch<Sample[]>(`/samples/${id}/lineage/`),

  placements: (id: string) => apiFetch<SamplePlacement[]>(`/samples/${id}/placements/`),

  expiring: (days: number) => apiFetch<Sample[]>("/samples/expiring/", { params: { days } }),

  listLocations: () => apiFetch<StorageLocation[]>("/locations/"),

  locationContents: (id: number) => apiFetch<Sample[]>(`/locations/${id}/contents/`),

  listCustomers: (params: { search?: string; is_active?: string; cursor?: string } = {}) =>
    apiFetch<Paginated<Customer>>("/customers/", { params }),

  createCustomer: (payload: {
    name: string;
    code: string;
    category?: string;
    contact_person?: string;
    phone?: string;
    email?: string;
    billing_address?: string;
    shipping_address?: string;
    gst_number?: string;
  }) => apiFetch<Customer>("/customers/", { method: "POST", body: payload }),

  updateCustomer: (id: number, payload: Partial<Customer>) =>
    apiFetch<Customer>(`/customers/${id}/`, { method: "PATCH", body: payload }),

  deactivateCustomer: (id: number, reason_text?: string) =>
    apiFetch<Customer>(`/customers/${id}/deactivate/`, { method: "POST", body: { reason_text } }),

  reactivateCustomer: (id: number, reason_text?: string) =>
    apiFetch<Customer>(`/customers/${id}/reactivate/`, { method: "POST", body: { reason_text } }),

  // --- TAT / SLA / retention ---

  slaStatus: (id: string) => apiFetch<SlaStatus>(`/samples/${id}/sla-status/`),

  overdueSamples: () => apiFetch<Sample[]>("/samples/overdue/"),

  samplesDueForDisposal: () => apiFetch<Sample[]>("/samples/due-for-disposal/"),

  setCommittedReportDate: (id: string, committed_report_date: string) =>
    apiFetch<Sample>(`/samples/${id}/set-committed-report-date/`, {
      method: "POST",
      body: { committed_report_date },
    }),

  setRetention: (id: string, retention_until: string) =>
    apiFetch<Sample>(`/samples/${id}/set-retention/`, { method: "POST", body: { retention_until } }),

  // --- testing ---

  listTestDefinitions: () => apiFetch<TestDefinition[]>("/test-definitions/"),

  listSpecifications: (test_definition?: number) =>
    apiFetch<Specification[]>("/specifications/", { params: { test_definition } }),

  listTestOrders: (params: { sample?: string; status?: string } = {}) =>
    apiFetch<TestOrder[]>("/test-orders/", { params }),

  placeOrder: (payload: {
    sample_id: string;
    test_definition_id: number;
    priority?: number;
    due_at?: string;
    assigned_to_id?: number;
    reason_code?: string;
  }) => apiFetch<TestOrder>("/test-orders/", { method: "POST", body: payload }),

  startOrder: (id: number) => apiFetch<TestOrder>(`/test-orders/${id}/start/`, { method: "POST" }),

  cancelOrder: (id: number, reason_text: string) =>
    apiFetch<TestOrder>(`/test-orders/${id}/cancel/`, { method: "POST", body: { reason_text } }),

  enterResult: (
    id: number,
    payload: { numeric_value?: number | string; text_value?: string; instrument_ref?: string; comments?: string }
  ) => apiFetch<TestResult>(`/test-orders/${id}/enter-result/`, { method: "POST", body: payload }),

  overdueOrders: () => apiFetch<TestOrder[]>("/test-orders/overdue/"),

  dueSoonOrders: (hours = 24) => apiFetch<TestOrder[]>("/test-orders/due-soon/", { params: { hours } }),

  tatSummary: () => apiFetch<TatSummary>("/test-orders/tat-summary/"),

  listTestResults: (params: { status?: string; is_out_of_spec?: string } = {}) =>
    apiFetch<TestResult[]>("/test-results/", { params }),

  submitResult: (id: number) => apiFetch<TestResult>(`/test-results/${id}/submit/`, { method: "POST" }),

  reviewResult: (id: number, payload: { password: string; reason_text?: string }) =>
    apiFetch<TestResult>(`/test-results/${id}/review/`, { method: "POST", body: payload }),

  approveResult: (id: number, payload: { password: string; reason_text?: string }) =>
    apiFetch<TestResult>(`/test-results/${id}/approve/`, { method: "POST", body: payload }),

  rejectResult: (id: number, reason_text: string) =>
    apiFetch<TestResult>(`/test-results/${id}/reject/`, { method: "POST", body: { reason_text } }),

  closeOos: (id: number, payload: { password: string; resolution_text: string }) =>
    apiFetch<TestResult>(`/test-results/${id}/close-oos/`, { method: "POST", body: payload }),

  generateCoa: (sampleId: string) =>
    apiFetch<CertificateOfAnalysis>(`/samples/${sampleId}/generate-coa/`, { method: "POST" }),

  listCertificates: (sample?: string) =>
    apiFetch<CertificateOfAnalysis[]>("/certificates/", { params: { sample } }),

  releaseCoa: (id: number, payload: { password: string; reason_text?: string }) =>
    apiFetch<CertificateOfAnalysis>(`/certificates/${id}/release/`, { method: "POST", body: payload }),
};

// The COA view returns HTML, not JSON, and needs the JWT header a plain
// `<a href>` new-tab navigation can't supply (this API has no session
// cookie). Fetch it authenticated and hand the caller a Blob URL to open
// instead — see pages/SampleDetail.tsx's "View certificate" button.
export async function fetchCoaHtml(id: number): Promise<string> {
  const resp = await fetch(buildUrl(`/certificates/${id}/html/`), {
    headers: accessToken ? { Authorization: `Bearer ${accessToken}` } : {},
  });
  if (!resp.ok) throw new ApiError(resp.status, await resp.text());
  return resp.text();
}
