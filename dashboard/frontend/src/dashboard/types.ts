/** Shared dashboard types — extracted so TourCard and sibling components
 *  don't have to reach back into Dashboard.tsx. Keep in sync with backend
 *  /api/tours payload shape. */

export interface StageInfo {
  status: string; // pending | running | done | failed
  detail?: string;
  duration_ms?: number;
  started_at?: number;  // running 중 elapsed 표시용 (epoch seconds, server)
}

export interface Coverage {
  covered: number;
  expected: number;
  ratio: number;
  missed_sample?: string[];
}

/** Phase A 1번 (2026-04-29) — completeness 분리.
 *  manifest_reachability: declared activity 중 launched/captured/enriched 단계별 카운트.
 *    declared 가 null 이면 RN/Compose 처럼 manifest 가 무용한 케이스 (분모 없음).
 *  overlay_count: dialog/bottom_sheet/snackbar/popup/toast 카운트. ground truth 없어 lower bound.
 *  task_coverage: tests/task_fixtures 도입 후 채움. 지금은 null. */
export interface ManifestReachability {
  declared: number | null;
  launched: number | null;
  captured: number | null;
  enriched: number | null;
  launched_ratio: number | null;
  captured_ratio: number | null;
  enriched_ratio: number | null;
}

export interface OverlayCount {
  dialog: number;
  bottom_sheet: number;
  snackbar: number;
  popup: number;
  toast: number;
  total: number;
}

export interface Completeness {
  manifest_reachability: ManifestReachability;
  overlay_count: OverlayCount;
  task_coverage: number | null;
}

/** P1.3 (2026-04-29) — ScreenMap quality 요약. ANNOTATED 잡에만 채워짐. */
export interface TourQuality {
  total_nodes: number;
  total_edges: number;
  actionable_nodes: number;     // 사용자가 탭/입력 가능한 노드 (전체 분모)
  plannable_nodes: number;      // actionable + 라벨링 완료
  reachable_count: number;      // entry 에서 도달 가능
  validation_issues: number;
  high_issues: number;          // severity=high (orphan / 심각한 issue)
  activity_coverage: number | null;
  completeness?: Completeness | null;
  // 2026-05-03 (P1) — user-facing 만 분모 (외부 OAuth/Bridge/Hidden 제외)
  relevant_total?: number;
  relevant_actionable?: number;
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
  device_serial?: string;       // 잡이 마지막으로 시작된 ADB 시리얼 (idle 잡도 보존)
  device_active?: boolean;      // true = 현재 메모리 슬롯 점유 중
  quality?: TourQuality | null; // P1.3
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
