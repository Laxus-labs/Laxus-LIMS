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
  committed_report_date: string | null;
  actual_completion_date: string | null;
  delay_reason: string;
  retention_until: string | null;
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

export interface SlaStatus {
  expected_completion_date: string | null;
  actual_completion_date: string | null;
  committed_report_date: string | null;
  tat_status: "complete" | "no_orders" | "breached" | "due_soon" | "on_time";
}

// --- testing --------------------------------------------------------------

export const TEST_ORDER_STATUSES = [
  "requested",
  "scheduled",
  "in_progress",
  "complete",
  "cancelled",
] as const;
export type TestOrderStatus = (typeof TEST_ORDER_STATUSES)[number];

export const RESULT_STATUSES = ["draft", "submitted", "reviewed", "approved", "rejected"] as const;
export type ResultStatus = (typeof RESULT_STATUSES)[number];

export const COA_STATUSES = ["draft", "released", "superseded"] as const;
export type CoaStatus = (typeof COA_STATUSES)[number];

export interface TestDefinition {
  id: number;
  code: string;
  name: string;
  method_reference: string;
  unit: string;
  is_quantitative: boolean;
  allowed_values: string[];
  turnaround_hours: number;
  is_active: boolean;
}

export interface TestDefinitionMini {
  id: number;
  code: string;
  name: string;
  unit: string;
}

export interface Specification {
  id: number;
  test_definition: TestDefinitionMini;
  sample_type: string;
  version: number;
  lower_limit: string | null;
  upper_limit: string | null;
  expected_values: string[];
  effective_from: string;
  superseded_at: string | null;
  created_by: string;
}

export interface SampleMini {
  id: string;
  barcode: string;
  accession_number: string | null;
  sample_type: SampleType;
}

export interface TestOrder {
  id: number;
  sample: SampleMini;
  test_definition: TestDefinitionMini;
  status: TestOrderStatus;
  priority: number;
  assigned_to: string | null;
  requested_by: string;
  requested_at: string;
  due_at: string | null;
  completed_at: string | null;
  cancellation_reason: string;
  has_result: boolean;
}

export interface TestResult {
  id: number;
  order: TestOrder;
  specification: number | null;
  numeric_value: string | null;
  text_value: string;
  is_out_of_spec: boolean;
  status: ResultStatus;
  entered_by: string;
  entered_at: string;
  reviewed_by: string | null;
  reviewed_at: string | null;
  approved_by: string | null;
  approved_at: string | null;
  instrument_ref: string;
  comments: string;
  oos_closed_by: string | null;
  oos_closed_at: string | null;
  oos_resolution: string;
  updated_at: string;
}

export interface CertificateOfAnalysis {
  id: number;
  coa_number: string;
  sample: SampleMini;
  status: CoaStatus;
  results_snapshot: Record<string, unknown>[];
  generated_by: string;
  generated_at: string;
  released_by: string | null;
  released_at: string | null;
  superseded_at: string | null;
  superseded_by: number | null;
}

export interface TatSummary {
  total_completed: number;
  average_tat_hours: number | null;
  tat_compliance_pct: number | null;
}
