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
  "accessioned",
  "in_storage",
  "in_testing",
  "aliquoted",
  "exhausted",
  "disposed",
  "rejected",
] as const;

export const VOLUME_UNITS = ["uL", "mL", "mg", "g", "count"] as const;

export type SampleType = (typeof SAMPLE_TYPES)[number];
export type SampleState = (typeof SAMPLE_STATES)[number];
export type VolumeUnit = (typeof VOLUME_UNITS)[number];

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

export interface Sample {
  id: string;
  barcode: string;
  accession_number: string | null;
  sample_type: SampleType;
  state: SampleState;
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
  metadata: Record<string, unknown>;
  created_by: string;
  created_at: string;
  updated_at: string;
  current_placement: SamplePlacement | null;
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
  can_adjust_inventory: boolean;
}
