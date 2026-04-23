import React from 'react';
import { Tour, Coverage, StageInfo, STAGE_LABELS, FRAMEWORK_STYLE, isRunning, isComplete } from './types';

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

function StageProgress({ stages }: { stages: Record<string, StageInfo> }) {
  if (!stages || Object.keys(stages).length === 0) return null;
  return (
    <div style={{ display: 'flex', gap: '2px', marginTop: '10px', alignItems: 'flex-end' }}>
      {PIPELINE_STAGES.map(({ key, label }) => {
        const info = stages[key] || { status: 'pending' };
        const color =
          info.status === 'done' ? 'var(--color-black)' :
          info.status === 'running' ? 'var(--color-primary)' :
          info.status === 'failed' ? '#dc2626' :
          'var(--color-border)';
        const dur = info.duration_ms ? `${(info.duration_ms / 1000).toFixed(1)}s` : '';
        return (
          <div key={key} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px', flex: 1 }}>
            <div style={{
              width: '100%', height: '4px', borderRadius: '2px', background: color,
              transition: 'background 0.3s',
            }} />
            <span style={{ fontSize: '9px', color: info.status === 'running' ? 'var(--color-primary)' : 'var(--color-gray)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' as const }}>
              {label}
            </span>
            {dur && <span style={{ fontSize: '8px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>{dur}</span>}
          </div>
        );
      })}
    </div>
  );
}


/* ─── TourCard ─────────────────────────────────────────────────────── */

interface TourCardProps {
  tour: Tour;
  onRun: () => void;
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
  onOpen: () => void;
  onDelete: () => void;
}

export function TourCard({ tour, onRun, onStop, onPause, onResume, onOpen, onDelete }: TourCardProps) {
  const running = isRunning(tour.stage);
  const complete = isComplete(tour.stage);
  const failed = tour.stage === 'FAILED' || tour.stage === 'CANCELLED';
  const paused = !!tour.paused;

  const runningDetail = Object.values(tour.stages || {}).find(s => s.status === 'running')?.detail || '';

  return (
    <div style={{
      border: '1px solid var(--color-border)',
      borderRadius: '8px',
      padding: '16px 20px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
        {/* Left */}
        <div style={{ flex: 1 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
            <span style={{ fontSize: '13px', fontWeight: 600 }}>
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
      {/* Progress bar */}
      <StageProgress stages={tour.stages || {}} />
    </div>
  );
}
