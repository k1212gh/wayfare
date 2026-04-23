/** Shared dashboard types — extracted so TourCard and sibling components
 *  don't have to reach back into Dashboard.tsx. Keep in sync with backend
 *  /api/tours payload shape. */

export interface StageInfo {
  status: string; // pending | running | done | failed
  detail?: string;
  duration_ms?: number;
}

export interface Coverage {
  covered: number;
  expected: number;
  ratio: number;
  missed_sample?: string[];
}

export interface Tour {
  tour_id: string;
  stage: string;
  package_name: string;
  apk_filename: string;
  apk_size_mb: number;
  started_at: number;
  error: string | null;
  stages?: Record<string, StageInfo>;
  framework?: string; // "xml" | "compose" | "flutter" | "react-native"
  paused?: boolean;
  pause_reason?: string;
  pause_auto?: boolean;
  pause_since?: number;
  coverage?: Coverage; // injected client-side from walk-live
  app_label?: string;
}

export interface Device {
  serial: string;
  info: string;
}

export interface EmulatorInfo {
  serial: string;
  avd: string;
  state: string;
}

export const STAGE_LABELS: Record<string, string> = {
  UPLOADED: 'Uploaded',
  PREPROCESSING: 'Preprocessing...',
  STATIC_ANALYZING: 'Static analysis...',
  STATIC_DONE: 'Static done',
  WALKING: 'Walking app...',
  WALK_DONE: 'Walk done',
  PREPROCESSING_DATA: 'Cleaning data...',
  CARDS_READY: 'Data ready',
  LLM_ANALYZING: 'LLM analyzing...',
  LLM_ANNOTATING: 'LLM enriching...',
  ANALYSIS_DONE: 'Analysis done',
  BUILDING_SCREENMAP: 'Building ScreenMap...',
  SCREENMAP_GENERATED: 'ScreenMap ready (wireframe)',
  ANNOTATED: 'Complete (with LLM)',
  CANCELLED: 'Cancelled',
  FAILED: 'Failed',
};

export const FRAMEWORK_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  'xml':          { bg: '#f1f5f9', fg: '#475569', label: 'XML' },
  'compose':      { bg: '#dbeafe', fg: '#1d4ed8', label: 'Compose' },
  'flutter':      { bg: '#cffafe', fg: '#0e7490', label: 'Flutter' },
  'react-native': { bg: '#ede9fe', fg: '#6d28d9', label: 'RN' },
};

export function isRunning(stage: string): boolean {
  return [
    'PREPROCESSING', 'STATIC_ANALYZING', 'WALKING',
    'PREPROCESSING_DATA', 'LLM_ANALYZING', 'LLM_ANNOTATING', 'BUILDING_SCREENMAP',
  ].includes(stage);
}

/** Terminal states with a viewable ScreenMap. Both wireframe (SCREENMAP_GENERATED) and
 *  LLM-enriched (ANNOTATED) have an screen_map.json worth opening. */
export function isComplete(stage: string): boolean {
  return stage === 'SCREENMAP_GENERATED' || stage === 'ANNOTATED';
}
