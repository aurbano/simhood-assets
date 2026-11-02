// The part of the validator's report the renderer reads (contract: "Validator CLI and report").
export interface ReportModel {
  id: string;
  pack: string;
  model_path: string;
  model_files?: string[];
  measure: string | null;
  measure_value: number | null;
  forward: string | null;
  factor: number | null;
  flat_shading: boolean | null;
  tint_key: string | null;
  tint_tolerance: number | null;
  triangles: number | null;
  change?: string;
}

export interface Report {
  version: number;
  models: ReportModel[];
  examples?: ReportModel[];
}
