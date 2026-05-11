import React, { useEffect, useRef, useState } from 'react';
import { Tour, Coverage, StageInfo, STAGE_LABELS, FRAMEWORK_STYLE, isRunning, isComplete } from './types';

/** stage 번호 → 라벨/설명 매핑. 백엔드 from_stage 와 정확히 일치.
 *  백엔드 순서: 1 → 2 → 3(walk) → 4(clean) → 6(ScreenMap build) → 5(LLM)
 *  드롭다운도 같은 순서로 노출 — "어디부터?" 의 자연스러운 선택. */
const RESUME_STAGES: { n: number; label: string; desc: string }[] = [
  { n: 1, label: 'Preprocess', desc: 'APK 검증·메타·프레임워크 감지부터' },
  { n: 2, label: 'Static',     desc: 'manifest + DEX 정적 분석부터' },
  { n: 3, label: 'Walk',    desc: '동적 탐색부터 (오래 걸림)' },
  { n: 4, label: 'Clean',      desc: '데이터 전처리부터' },
  { n: 6, label: 'ScreenMap',         desc: 'ScreenMap 빌드만 (빠름)' },
  { n: 5, label: 'LLM',        desc: 'LLM 라벨링만 (~5분, 자동 백업)' },
];

/* ─── Small presentational primitives ────────────────────────────── */

export function Btn({ children, onClick, primary, subtle, danger }: {
  children: React.ReactNode;
  onClick: () => void;
  primary?: boolean;
  subtle?: boolean;
  danger?: boolean;
}) {
  const bg = danger ? '#dc2626' : primary ? 'var(--color-black)' : 'var(--color-white)';
  const fg = danger ? '#ffffff' : primary ? 'var(--color-white)' : subtle ? 'var(--color-gray)' : 'var(--color-black)';
  const border = (primary || danger) ? 'none' : '1px solid var(--color-border)';
  return (
    <button
      onClick={onClick}
      style={{
        padding: '6px 14px',
        fontSize: '12px',
        fontWeight: 500,
        fontFamily: 'var(--font)',
        border,
        borderRadius: '6px',
        cursor: 'pointer',
        background: bg,
        color: fg,
        transition: 'opacity 0.15s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.7')}
      onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
    >
      {children}
    </button>
  );
}

function FrameworkBadge({ framework }: { framework?: string }) {
  if (!framework) return null;
  const style = FRAMEWORK_STYLE[framework] || { bg: '#f1f5f9', fg: '#64748b', label: framework };
  return (
    <span
      title={`Detected framework: ${framework}`}
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        fontSize: '10px',
        fontWeight: 600,
        fontFamily: 'var(--font-mono)',
        letterSpacing: '0.2px',
        background: style.bg,
        color: style.fg,
        borderRadius: '4px',
        lineHeight: '1.4',
      }}
    >
      {style.label}
    </span>
  );
}

/** 같은 앱을 두 디바이스에 동시에 올렸을 때 어느 디바이스 잡인지 구분.
 *  emulator-5554 → "emu-5554", 실기기 시리얼은 마지막 6자리. active 여부는
 *  녹색 도트로 표시 (메모리 슬롯에 잡혀 있으면 alive). */
function DeviceBadge({ serial, active }: { serial?: string; active?: boolean }) {
  if (!serial) return null;
  const short = serial.startsWith('emulator-')
    ? `emu-${serial.slice(9)}`
    : serial.length > 8 ? `…${serial.slice(-6)}` : serial;
  return (
    <span
      title={`Device: ${serial}${active ? ' (running)' : ' (last used)'}`}
      style={{
        display: 'inline-flex',
        alignItems: 'center',
        gap: '4px',
        padding: '2px 8px',
        fontSize: '10px',
        fontWeight: 600,
        fontFamily: 'var(--font-mono)',
        letterSpacing: '0.2px',
        background: active ? '#dcfce7' : '#f1f5f9',
        color: active ? '#166534' : '#64748b',
        borderRadius: '4px',
        lineHeight: '1.4',
      }}
    >
      {active && (
        <span style={{
          width: '6px', height: '6px', borderRadius: '50%',
          background: '#22c55e', display: 'inline-block',
        }} />
      )}
      {short}
    </span>
  );
}

/* P1.4 (2026-04-29) — Quality 4-card.
 * ANNOTATED 잡에만 표시. 백엔드 /api/tours 응답의 tour.quality 필드를 그대로 표시.
 * Reached / Actionable / Plannable / Validation 4개 — framework 무관 동일 schema. */
function QualityCards({ quality, compact = false }: { quality: NonNullable<Tour['quality']>; compact?: boolean }) {
  const reachPct = quality.total_nodes > 0
    ? Math.round((quality.reachable_count / quality.total_nodes) * 100)
    : 0;
  // 2026-05-03 (P1): user-facing 분모 우선. relevant_total 있으면 그 비율,
  // 없으면 (이전 잡) 기존 total_nodes 분모. 메가커피 같은 manifest 큰 앱에서
  // 외부 OAuth/Bridge 가 강제로 분모 부풀리던 회귀 정확화.
  const actBase = (quality.relevant_total && quality.relevant_total > 0)
    ? quality.relevant_total
    : quality.total_nodes;
  const actCount = (quality.relevant_actionable !== undefined)
    ? quality.relevant_actionable
    : quality.actionable_nodes;
  const actPct = actBase > 0 ? Math.round((actCount / actBase) * 100) : 0;
  const planPct = quality.actionable_nodes > 0
    ? Math.round((quality.plannable_nodes / quality.actionable_nodes) * 100)
    : 0;
  const valOk = quality.high_issues === 0;
  const cards = [
    {
      label: 'Reached',
      main: `${quality.reachable_count}`,
      sub: `/ ${quality.total_nodes} nodes  (${reachPct}%)`,
      tone: 'default' as const,
      title: 'entry 에서 BFS 도달 가능한 노드 수 / 전체 노드 수',
    },
    {
      label: 'Actionable',
      main: `${actCount}`,
      sub: `${actPct}% / ${actBase} user-facing — 조작 가능`,
      tone: 'default' as const,
      title: (quality.relevant_total && quality.relevant_total > 0)
        ? `user-facing (capture_priority='A') ${actBase} 중 actionable ${actCount} (${actPct}%). 외부 OAuth/Bridge/Hidden 류는 분모에서 제외.`
        : 'widgets / chip_groups / primary_affordances 가 있는 노드 / 전체 노드 수',
    },
    {
      label: 'Plannable',
      main: `${quality.plannable_nodes}`,
      sub: `${planPct}% of actionable — 라벨 + 액션`,
      tone: quality.plannable_nodes > 0 ? 'default' as const : 'warn' as const,
      title: 'actionable + status≥probed + label/purpose 있음 = LLM agent step 가능',
    },
    {
      label: 'Validation',
      main: valOk ? '✓ ok' : `${quality.high_issues}`,
      sub: valOk
        ? `${quality.validation_issues} issues (none high)`
        : `high · total ${quality.validation_issues}`,
      tone: valOk ? 'ok' as const : 'warn' as const,
      title: 'high severity issues (= orphan/critical). 0 이면 healthy',
    },
  ];
  if (compact) {
    return (
      <div style={{
        display: 'flex',
        gap: 6,
        flexWrap: 'wrap',
        marginTop: 10,
      }}>
        {cards.map((c) => {
          const fg = c.tone === 'warn' ? '#dc2626'
            : c.tone === 'ok' ? '#059669'
            : 'var(--color-black)';
          return (
            <span
              key={c.label}
              title={`${c.label}: ${c.main} ${c.sub}`}
              style={{
                display: 'inline-flex',
                alignItems: 'baseline',
                gap: 5,
                padding: '3px 8px',
                border: '1px solid var(--color-border)',
                borderRadius: 999,
                background: '#fff',
                fontSize: 10,
                color: 'var(--color-gray)',
              }}
            >
              <strong style={{ color: fg, fontFamily: 'var(--font-mono)', fontSize: 11 }}>{c.main}</strong>
              {c.label}
            </span>
          );
        })}
      </div>
    );
  }
  return (
    <div>
      <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: '8px', marginTop: '10px' }}>
        {cards.map((c) => {
          const fg = c.tone === 'warn' ? '#dc2626'
            : c.tone === 'ok' ? '#059669'
            : 'var(--color-black)';
          return (
            <div
              key={c.label}
              title={c.title}
              style={{
                padding: '8px 10px', border: '1px solid var(--color-border)',
                borderRadius: '6px', background: 'var(--color-white)',
              }}
            >
              <div style={{
                fontSize: '9px', fontWeight: 700, letterSpacing: '0.06em',
                textTransform: 'uppercase' as const, color: 'var(--color-gray)',
              }}>{c.label}</div>
              <div style={{
                fontSize: '17px', fontWeight: 700,
                fontFamily: 'var(--font-mono)', color: fg, marginTop: '2px',
              }}>{c.main}</div>
              <div style={{ fontSize: '10px', color: 'var(--color-gray)', marginTop: '2px' }}>
                {c.sub}
              </div>
            </div>
          );
        })}
      </div>
      <CompletenessRow completeness={quality.completeness} />
    </div>
  );
}


/** Phase A 1번 (2026-04-29) — completeness 분리 표시.
 *  manifest / overlay / task 3 namespace 를 한 줄 pills 로. */
function CompletenessRow({ completeness }: { completeness?: import('./types').Completeness | null }) {
  if (!completeness) return null;
  const mr = completeness.manifest_reachability;
  const ov = completeness.overlay_count;
  const tc = completeness.task_coverage;

  const manifestText = mr.declared == null
    ? 'manifest n/a (RN/Compose)'
    : `manifest ${mr.captured ?? 0}/${mr.declared} captured · ${mr.enriched ?? 0} enriched`;
  const overlayText = ov.total > 0
    ? `overlay ${ov.total} (dlg ${ov.dialog} · sheet ${ov.bottom_sheet} · snack ${ov.snackbar})`
    : 'overlay 0 (감지 안 됨 — heuristic 한계)';
  const taskText = tc == null ? 'task n/a (fixture 미정의)' : `task ${tc}`;

  const Pill = ({ children, tone = 'default' }: { children: React.ReactNode; tone?: 'default' | 'warn' | 'note' }) => (
    <span style={{
      fontSize: '10px', fontFamily: 'var(--font-mono)',
      padding: '2px 8px', borderRadius: '10px',
      background: tone === 'warn' ? '#fef3c7' : tone === 'note' ? '#f3f4f6' : '#fff',
      border: '1px solid var(--color-border)',
      color: tone === 'warn' ? '#92400e' : 'var(--color-gray)',
    }}>{children}</span>
  );

  return (
    <div
      style={{ marginTop: '8px', display: 'flex', gap: '6px', flexWrap: 'wrap' }}
      title="completeness 3 분리 — '발견율' 한 단어 대신 namespace 별 측정 (manifest/overlay/task)"
    >
      <Pill tone={mr.declared == null ? 'note' : 'default'}>{manifestText}</Pill>
      <Pill tone={ov.total === 0 ? 'note' : 'default'}>{overlayText}</Pill>
      <Pill tone="note">{taskText}</Pill>
    </div>
  );
}


function CoverageBar({ coverage }: { coverage: Coverage }) {
  const pct = Math.round(coverage.ratio * 100);
  return (
    <div
      style={{ marginTop: '10px' }}
      title={
        coverage.missed_sample && coverage.missed_sample.length > 0
          ? `Missed: ${coverage.missed_sample.join(', ')}${coverage.missed_sample.length >= 5 ? ' …' : ''}`
          : undefined
      }
    >
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        fontSize: '11px', color: 'var(--color-gray)', marginBottom: '4px',
        fontFamily: 'var(--font-mono)',
      }}>
        <span>Activity coverage</span>
        <span>
          <strong style={{ color: 'var(--color-black)' }}>{coverage.covered}</strong>
          {' / '}{coverage.expected}
          <span style={{ marginLeft: '6px' }}>({pct}%)</span>
        </span>
      </div>
      <div style={{
        height: '6px', width: '100%', borderRadius: '3px',
        background: 'var(--color-border)', overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          width: `${Math.min(100, Math.max(2, pct))}%`,
          background: 'var(--color-black)',
          transition: 'width 0.4s ease',
        }} />
      </div>
    </div>
  );
}

const PIPELINE_STAGES = [
  { key: 'stage1', label: 'Preprocess' },
  { key: 'stage2', label: 'Static' },
  { key: 'stage3', label: 'Walk' },
  { key: 'stage4', label: 'Clean' },
  { key: 'stage5', label: 'LLM' },
  { key: 'stage6', label: 'ScreenMap' },
];

function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m${s.toString().padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  return `${h}h${(m % 60).toString().padStart(2, '0')}m`;
}

function StageProgress({ stages }: { stages: Record<string, StageInfo> }) {
  // 2026-04-29: running stage 의 elapsed 가 매초 갱신되도록 1s tick.
  // backend 의 started_at 과 비교해서 (now - started_at) 표시.
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!stages) return;
    const hasRunning = Object.values(stages).some(s => s.status === 'running');
    if (!hasRunning) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [stages]);

  if (!stages || Object.keys(stages).length === 0) return null;
  return (
    <>
      <style>{`
        @keyframes sa-stage-pulse {
          0%   { opacity: 1;   box-shadow: 0 0 0 0 rgba(220,38,38,0.5); }
          50%  { opacity: 0.7; box-shadow: 0 0 0 4px rgba(220,38,38,0.0); }
          100% { opacity: 1;   box-shadow: 0 0 0 0 rgba(220,38,38,0.0); }
        }
      `}</style>
      <div style={{ display: 'flex', gap: '2px', marginTop: '10px', alignItems: 'flex-end' }}>
        {PIPELINE_STAGES.map(({ key, label }) => {
          const info = stages[key] || { status: 'pending' };
          const isRun = info.status === 'running';
          const color =
            info.status === 'done' ? 'var(--color-black)' :
            isRun ? '#dc2626' :
            info.status === 'failed' ? '#dc2626' :
            'var(--color-border)';
          // done: backend 가 setdone duration_ms · running: now - started_at
          let timeText = '';
          if (info.duration_ms) {
            timeText = formatElapsed(info.duration_ms / 1000);
          } else if (isRun && info.started_at) {
            const elapsed = Math.max(0, now - info.started_at);
            timeText = formatElapsed(elapsed);
          }
          return (
            <div key={key} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px', flex: 1 }}>
              <div style={{
                width: '100%',
                height: isRun ? '7px' : '4px',
                borderRadius: '2px',
                background: color,
                transition: 'background 0.3s, height 0.2s',
                animation: isRun ? 'sa-stage-pulse 1.4s ease-in-out infinite' : undefined,
              }} />
              <span style={{
                fontSize: isRun ? '10px' : '9px',
                fontWeight: isRun ? 700 : 400,
                color: isRun ? '#dc2626' : (info.status === 'failed' ? '#dc2626' : 'var(--color-gray)'),
                fontFamily: 'var(--font-mono)',
                whiteSpace: 'nowrap' as const,
              }}>
                {isRun ? '● ' : ''}{label}
              </span>
              {timeText && (
                <span
                  title={isRun ? `running · started ${formatElapsed(now - (info.started_at || now))} ago` : `done in ${timeText}`}
                  style={{
                    fontSize: '9px',
                    fontWeight: isRun ? 700 : 400,
                    color: isRun ? '#dc2626' : 'var(--color-gray)',
                    fontFamily: 'var(--font-mono)',
                  }}>
                  {timeText}
                </span>
              )}
              {isRun && info.detail && (
                <span title={info.detail} style={{
                  fontSize: '9px',
                  color: '#dc2626',
                  fontFamily: 'var(--font-mono)',
                  maxWidth: '100%',
                  overflow: 'hidden',
                  textOverflow: 'ellipsis' as const,
                  whiteSpace: 'nowrap' as const,
                }}>
                  {info.detail.length > 28 ? info.detail.slice(0, 28) + '…' : info.detail}
                </span>
              )}
            </div>
          );
        })}
      </div>
    </>
  );
}


/* ─── Resume dropdown ─────────────────────────────────────────────── */

function ResumeMenu({ onRetryFromStage }: { onRetryFromStage: (n: number) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);

  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => {
      if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);

  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <Btn onClick={() => setOpen(!open)} subtle>↻ Resume ▾</Btn>
      {open && (
        <div style={{
          position: 'absolute', right: 0, top: 'calc(100% + 4px)',
          background: '#fff', border: '1px solid var(--color-border)',
          borderRadius: '6px', boxShadow: '0 4px 12px rgba(0,0,0,0.08)',
          padding: '4px 0', minWidth: '240px', zIndex: 20,
        }}>
          <div style={{
            padding: '4px 12px', fontSize: '10px', color: 'var(--color-gray)',
            fontFamily: 'var(--font-mono)', textTransform: 'uppercase' as const,
            letterSpacing: '0.05em',
          }}>
            어느 단계부터 다시?
          </div>
          {RESUME_STAGES.map((s) => (
            <button
              key={s.n}
              onClick={() => { onRetryFromStage(s.n); setOpen(false); }}
              style={{
                display: 'block', width: '100%', textAlign: 'left' as const,
                padding: '7px 12px', border: 'none', background: 'transparent',
                cursor: 'pointer', fontFamily: 'var(--font)',
              }}
              onMouseEnter={(e) => (e.currentTarget.style.background = '#f5f5f5')}
              onMouseLeave={(e) => (e.currentTarget.style.background = 'transparent')}
            >
              <div style={{ fontSize: '12px', fontWeight: 600, color: 'var(--color-black)' }}>
                Stage {s.n} · {s.label}
              </div>
              <div style={{ fontSize: '10px', color: 'var(--color-gray)', marginTop: '1px' }}>
                {s.desc}
              </div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}


/* started_at (unix sec) → "YY.MM.DD HH:MM" 형식. 사용자가 잡 시작 시점을
 * 빠르게 식별하도록 카드에 표시. */
function formatStartedAt(ts: number): string {
  if (!ts || ts <= 0) return '';
  const d = new Date(ts * 1000);
  const yy = String(d.getFullYear()).slice(-2);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${yy}.${mm}.${dd} ${hh}:${min}`;
}


/* ─── TourCard ─────────────────────────────────────────────────────── */

interface TourCardProps {
  tour: Tour;
  variant?: 'normal' | 'active' | 'compact';
  onRun: () => void;
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
  onOpen: () => void;
  onDelete: () => void;
  onRetryFromStage?: (n: number) => void;  // 1..6 stage 번호로 재실행
}

export function TourCard({ tour, variant = 'normal', onRun, onStop, onPause, onResume, onOpen, onDelete, onRetryFromStage }: TourCardProps) {
  const running = isRunning(tour.stage);
  const complete = isComplete(tour.stage);
  const failed = tour.stage === 'FAILED' || tour.stage === 'CANCELLED';
  const paused = !!tour.paused;
  const compact = variant === 'compact';
  const active = variant === 'active';

  const runningDetail = Object.values(tour.stages || {}).find(s => s.status === 'running')?.detail || '';

  return (
    <div style={{
      border: active ? '1px solid #fecaca' : '1px solid var(--color-border)',
      borderRadius: '8px',
      padding: compact ? '12px 16px' : active ? '18px 20px' : '16px 20px',
      background: active ? '#fffafa' : 'var(--color-white)',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {/* Left */}
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
            <span style={{ fontSize: active ? '14px' : '13px', fontWeight: 700 }}>
              {tour.app_label
                || (tour.package_name ? tour.package_name.split('.').slice(-1)[0] : '')
                || tour.apk_filename
                || tour.tour_id}
            </span>
            {tour.apk_size_mb > 0 && (
              <span style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>
                {tour.apk_size_mb}MB
              </span>
            )}
            <FrameworkBadge framework={tour.framework} />
            <DeviceBadge serial={tour.device_serial} active={tour.device_active} />
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
            <span style={{
              fontSize: '11px', fontFamily: 'var(--font-mono)',
              color: failed ? 'var(--color-primary)' : running ? 'var(--color-black)' : 'var(--color-gray)',
            }}>
              {running && <span style={{ marginRight: '4px' }}>&#9679;</span>}
              {STAGE_LABELS[tour.stage] || tour.stage}
            </span>
            {tour.package_name && (
              <span style={{ fontSize: '11px', color: 'var(--color-gray)' }}>{tour.package_name}</span>
            )}
            {tour.started_at > 0 && (
              <span
                title={`Started: ${new Date(tour.started_at * 1000).toLocaleString()}`}
                style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}
              >
                {formatStartedAt(tour.started_at)}
              </span>
            )}
          </div>
          {runningDetail && (
            <div style={{ fontSize: '11px', color: 'var(--color-gray)', marginTop: '2px' }}>{runningDetail}</div>
          )}
          {failed && tour.error && (
            <div style={{ fontSize: '11px', color: 'var(--color-primary)', marginTop: '4px', maxWidth: '400px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
              {tour.error}
            </div>
          )}
        </div>

        {/* Right — actions */}
        <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
          {(tour.stage === 'UPLOADED' || failed) && (
            <Btn onClick={onRun}>Run</Btn>
          )}
          {running && !paused && (
            <Btn onClick={onPause} subtle>Pause</Btn>
          )}
          {running && paused && (
            <Btn onClick={onResume} primary>Resume</Btn>
          )}
          {running && (
            <Btn onClick={onStop} danger>Stop</Btn>
          )}
          {complete && onRetryFromStage && (
            <ResumeMenu onRetryFromStage={onRetryFromStage} />
          )}
          {complete && (
            <Btn onClick={onOpen} primary>View Graph</Btn>
          )}
          {!running && (
            <Btn onClick={onDelete} subtle>Delete</Btn>
          )}
        </div>
      </div>
      {/* Coverage bar (only during WALKING) */}
      {tour.stage === 'WALKING' && tour.coverage && tour.coverage.expected > 0 && (
        <CoverageBar coverage={tour.coverage} />
      )}
      {/* Pause banner — auto-detect login or manual pause */}
      {paused && (
        <div style={{
          marginTop: '10px',
          padding: '10px 12px',
          background: '#fef3c7',
          border: '1px solid #fbbf24',
          borderRadius: '6px',
          fontSize: '12px',
          color: '#78350f',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
            <strong>⏸ 사용자 개입 대기 중 {tour.pause_auto ? '(자동 감지)' : ''}</strong>
            {tour.pause_since ? (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: '#92400e' }}>
                {Math.floor((Date.now() / 1000 - tour.pause_since) / 60)}m
              </span>
            ) : null}
          </div>
          <div style={{ marginTop: '6px', lineHeight: '1.5' }}>
            사유: <em>{tour.pause_reason || 'waiting for user'}</em>
            <br />
            <strong>에뮬레이터 화면에서 직접 로그인</strong>을 완료한 뒤 아래 버튼으로 탐색을 재개하세요.
            소셜 로그인(카카오·네이버)이나 SMS 인증은 자동화가 불가능하므로 수동으로 처리해 주세요.
            로그인이 불필요한 경우 건너뛰기를 누르면 현재 화면에서 다른 경로를 계속 탐색합니다.
          </div>
        </div>
      )}
      {/* P1.4: Quality 4-card — ANNOTATED 잡에만 (SCREENMAP_GENERATED 는 LLM 전이라 plannable 0) */}
      {tour.stage === 'ANNOTATED' && tour.quality && (
        <QualityCards quality={tour.quality} compact={compact} />
      )}
      {/* Progress bar */}
      {(!compact || running || failed) && (
        <StageProgress stages={tour.stages || {}} />
      )}
    </div>
  );
}
