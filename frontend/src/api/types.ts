export const SAMPLE_TYPES = [
  "blood",
  "serum",
  "plasma",
  "tissue",
  "swab",
  "urine",
  "dna",
  "rna",
  "other",
] as const;

export const SAMPLE_STATES = [
  "received",
  "on_hold",
  "accessioned",
  "in_storage",
  "in_testing",
  "aliquoted",
  "exhausted",
  "disposed",
  "rejected",
] as const;

export const VOLUME_UNITS = ["uL", "mL", "mg", "g", "count"] as const;

export const CUSTOMER_CATEGORIES = [
  "pharma_manufacturer",
  "hospital",
  "cro",
  "research_institute",
  "government",
  "individual",
  "other",
] as const;

export type SampleType = (typeof SAMPLE_TYPES)[number];
export type SampleState = (typeof SAMPLE_STATES)[number];
export type VolumeUnit = (typeof VOLUME_UNITS)[number];
export type CustomerCategory = (typeof CUSTOMER_CATEGORIES)[number];

export interface Customer {
  id: number;
  name: string;
  code: string;
  category: CustomerCategory;
  contact_person: string;
  phone: string;
  email: string;
  billing_address: string;
  shipping_address: string;
  gst_number: string;
  is_active: boolean;
  created_by: string;
  created_at: string;
  updated_at: string;
}

export interface CustomerMini {
  id: number;
  name: string;
  code: string;
}

export interface RejectionReasonCode {
  code: string;
  label: string;
  requires_details: boolean;
}

export interface LocationMini {
  id: number;
  code: string;
  name: string;
  location_type: string;
}

export interface StorageLocation extends LocationMini {
  parent: number | null;
  path: string;
  grid_rows: number | null;
  grid_cols: number | null;
  temperature_c: string | null;
  capacity: number | null;
  is_active: boolean;
  notes: string;
}

export interface SamplePlacement {
  id: number;
  sample: string;
  location: LocationMini;
  row_label: string;
  column_number: number | null;
  placed_at: string;
  placed_by: string | null;
  removed_at: string | null;
  removed_by: string | null;
  reason_code: string;
}

export type AcceptanceDecision =
  | "pending"
  | "accepted"
  | "accepted_with_deviation"
  | "on_hold"
  | "rejected";

export interface Sample {
  id: string;
  barcode: string;
  accession_number: string | null;
  sample_type: SampleType;
  state: SampleState;
  customer: CustomerMini | null;
  parent: string | null;
  root: string | null;
  generation: number;
  volume: string;
  volume_unit: VolumeUnit;
  subject_ref: string;
  collected_at: string | null;
  received_at: string | null;
  accessioned_at: string | null;
  disposed_at: string | null;
  expires_at: string | null;
  acceptance_decision: AcceptanceDecision;
  deviation_notes: string;
  container_integrity_ok: boolean | null;
  seal_integrity_ok: boolean | null;
  quantity_ok: boolean | null;
  label_ok: boolean | null;
  documentation_ok: boolean | null;
  transport_conditions_ok: boolean | null;
  rejection_reason: RejectionReasonCode | null;
  rejection_reason_other: string;
  qa_approved_by: string | null;
  qa_approved_at: string | null;
  client_notified_at: string | null;
  metadata: Record<string, unknown>;
  created_by: string;
  created_at: string;
  updated_at: string;
  current_placement: SamplePlacement | null;
}

export interface AcceptanceChecklist {
  container_integrity_ok?: boolean;
  seal_integrity_ok?: boolean;
  quantity_ok?: boolean;
  label_ok?: boolean;
  documentation_ok?: boolean;
  transport_conditions_ok?: boolean;
}

export interface AccessionBatchResult {
  created: Sample[];
  duplicates: Sample[];
  total_scanned: number;
}

export interface Paginated<T> {
  next: string | null;
  previous: string | null;
  results: T[];
}

export interface Me {
  id: number;
  username: string;
  role: string;
  can_review_results: boolean;
  can_approve_results: boolean;
  can_dispose_samples: boolean;
  can_approve_qa: boolean;
  can_adjust_inventory: boolean;
}
