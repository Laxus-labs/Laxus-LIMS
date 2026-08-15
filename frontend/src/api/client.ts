import type {
  AcceptanceChecklist,
  AccessionBatchResult,
  Customer,
  Me,
  Paginated,
  RejectionReasonCode,
  Sample,
  SamplePlacement,
  SampleState,
  StorageLocation,
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
    payload: { rejection_reason_code: string; reason_text?: string; checklist?: AcceptanceChecklist }
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
    payload: { new_state: SampleState; reason_code?: string; reason_text?: string }
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
};
