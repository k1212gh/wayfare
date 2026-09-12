import React, { useEffect, useRef, useState } from 'react';
import { Tour, Coverage, StageInfo, STAGE_LABELS, FRAMEWORK_STYLE, isRunning, isComplete } from './types';
import { tourTitle, tourInitial, tourHue, formatStartedAt, formatElapsed } from './tourTitle';
import { IconPlay, IconStop, IconPause, IconTrash, IconRefresh, IconMap, IconChevron } from '../app/icons';

/** 백엔드 from_stage 번호와 1:1. 실행 순서: 1 → 2 → 3(탐색) → 4(정리) → 6(지도) → 5(LLM). */
const RESUME_STAGES: { n: number; label: string; desc: string }[] = [
  { n: 1, label: '준비',      desc: 'APK 검증·메타·프레임워크 감지부터' },
  { n: 2, label: '정적 분석', desc: 'manifest + DEX 분석부터' },
  { n: 3, label: '기기 탐색', desc: '동적 탐색부터 (오래 걸림)' },
  { n: 4, label: '화면 정리', desc: '캡처 정리·군집화부터' },
  { n: 6, label: '지도 빌드', desc: '흐름 지도만 다시 (빠름)' },
  { n: 5, label: '라벨링',    desc: 'LLM 라벨링만 (자동 백업)' },
];

const PIPELINE_STAGES = [
  { key: 'stage1', label: '준비' },
  { key: 'stage2', label: '정적' },
  { key: 'stage3', label: '탐색' },
  { key: 'stage4', label: '정리' },
  { key: 'stage6', label: '지도' },
  { key: 'stage5', label: '라벨' },
];

interface Props {
  tour: Tour;
  variant: 'active' | 'grid';
  onRun: () => void;
  onStop: () => void;
  onPause: () => void;
  onResume: () => void;
  onOpen: () => void;
  onDelete: () => void;
  onRetryFromStage: (n: number) => void;
}

export function ProjectCard({ tour, variant, onRun, onStop, onPause, onResume, onOpen, onDelete, onRetryFromStage }: Props) {
  const running = isRunning(tour.stage);
  const complete = isComplete(tour.stage);
  const failed = tour.stage === 'FAILED' || tour.stage === 'CANCELLED';
  const paused = !!tour.paused;
  const active = variant === 'active';
  const runningDetail = Object.values(tour.stages || {}).find((s) => s.status === 'running')?.detail || '';
  const fw = tour.framework ? (FRAMEWORK_STYLE[tour.framework] || { bg: 'var(--wf-surface-2)', fg: 'var(--wf-ink-2)', label: tour.framework }) : null;

  return (
    <div className={`wf-card${active ? ' raised' : ' hover'} wf-fade-in`} style={{ padding: active ? 20 : 16, display: 'flex', flexDirection: 'column', gap: 12 }}>
      {/* 헤더: 아이콘 + 이름 + 상태 */}
      <div style={{ display: 'flex', alignItems: 'flex-start', gap: 12 }}>
        <div className="wf-app-icon" style={{ background: tourHue(tour) }}>{tourInitial(tour)}</div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: 8, minWidth: 0 }}>
            <span className="wf-ellipsis" style={{ fontSize: active ? 17 : 15, fontWeight: 700, letterSpacing: '-0.01em' }}>{tourTitle(tour)}</span>
            {fw && <span className="wf-chip" style={{ background: fw.bg, color: fw.fg, height: 20 }}>{fw.label}</span>}
          </div>
          <div className="wf-ellipsis wf-mono wf-faint" style={{ marginTop: 2 }} title={tour.package_name}>{tour.package_name || tour.apk_filename}</div>
        </div>
        <StageBadge stage={tour.stage} paused={paused} />
      </div>

      {/* 메타 */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {tour.started_at > 0 && <span className="wf-chip outline mono" title={new Date(tour.started_at * 1000).toLocaleString()}>{formatStartedAt(tour.started_at)}</span>}
        {tour.apk_size_mb > 0 && <span className="wf-chip outline mono">{tour.apk_size_mb} MB</span>}
        {tour.device_serial && (
          <span className={`wf-chip mono${tour.device_active ? ' accent' : ' outline'}`} title={`기기: ${tour.device_serial}`}>
            {tour.device_active && <span className="wf-dot live" style={{ width: 6, height: 6 }} />}
            {tour.device_serial.startsWith('emulator-') ? `에뮬 ${tour.device_serial.slice(9)}` : `…${tour.device_serial.slice(-6)}`}
          </span>
        )}
      </div>

      {runningDetail && <div className="wf-callout plain" style={{ padding: '8px 12px' }}>{runningDetail}</div>}
      {failed && tour.error && <div className="wf-callout danger" style={{ padding: '8px 12px' }} title={tour.error}><span className="wf-ellipsis" style={{ display: 'block' }}>{tour.error}</span></div>}

      {tour.stage === 'WALKING' && tour.coverage && tour.coverage.expected > 0 && <CoverageBar coverage={tour.coverage} />}

      {paused && (
        <div className="wf-callout amber">
          <div style={{ display: 'flex', justifyContent: 'space-between', gap: 8 }}>
            <strong>⏸ 사용자 개입 대기 {tour.pause_auto ? '(자동 감지)' : ''}</strong>
            {tour.pause_since ? <span className="wf-mono">{Math.floor((Date.now() / 1000 - tour.pause_since) / 60)}m</span> : null}
          </div>
          <div style={{ marginTop: 4 }}>
            사유: <em>{tour.pause_reason || '사용자 대기'}</em> — 기기 화면에서 로그인/인증을 마친 뒤 <b>재개</b>를 누르세요.
            소셜 로그인·SMS 인증은 자동화되지 않습니다.
          </div>
        </div>
      )}

      {tour.stage === 'ANNOTATED' && tour.quality && <QualityStats quality={tour.quality} compact={!active} />}

      {(active || running || failed || complete) && <Stepper stages={tour.stages || {}} />}

      {/* 액션 */}
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', alignItems: 'center', marginTop: 2 }}>
        {(tour.stage === 'UPLOADED' || failed) && (
          <button className="wf-btn primary" onClick={onRun}><IconPlay size={14} /> 분석 시작</button>
        )}
        {running && !paused && <button className="wf-btn quiet" onClick={onPause}><IconPause size={13} /> 일시정지</button>}
        {running && paused && <button className="wf-btn primary" onClick={onResume}><IconPlay size={14} /> 재개</button>}
        {running && <button className="wf-btn danger" onClick={onStop}><IconStop size={13} /> 중지</button>}
        {complete && <button className="wf-btn primary" onClick={onOpen}><IconMap size={15} /> 지도 열기</button>}
        {complete && <ResumeMenu onRetryFromStage={onRetryFromStage} />}
        <span style={{ flex: 1 }} />
        {!running && <button className="wf-btn ghost icon" onClick={onDelete} title="삭제"><IconTrash size={15} /></button>}
      </div>
    </div>
  );
}

export function StageBadge({ stage, paused }: { stage: string; paused?: boolean }) {
  const running = isRunning(stage);
  const failed = stage === 'FAILED' || stage === 'CANCELLED';
  const cls = paused ? 'amber' : failed ? 'danger' : stage === 'ANNOTATED' ? 'accent' : stage === 'SCREENMAP_GENERATED' ? 'info' : running ? 'amber' : 'outline';
  return (
    <span className={`wf-chip ${cls}`} style={{ flexShrink: 0 }}>
      {running && !paused && <span className="wf-dot live" style={{ width: 6, height: 6, background: 'currentColor' }} />}
      {paused ? '일시정지' : STAGE_LABELS[stage] || stage}
    </span>
  );
}

function CoverageBar({ coverage }: { coverage: Coverage }) {
  const pct = Math.round(coverage.ratio * 100);
  return (
    <div title={coverage.missed_sample?.length ? `미방문: ${coverage.missed_sample.join(', ')}${coverage.missed_sample.length >= 5 ? ' …' : ''}` : undefined}>
      <div style={{ display: 'flex', justifyContent: 'space-between', fontSize: 11, color: 'var(--wf-ink-3)', marginBottom: 4 }}>
        <span>선언된 화면 방문</span>
        <span className="wf-mono"><b style={{ color: 'var(--wf-ink)' }}>{coverage.covered}</b> / {coverage.expected} · {pct}%</span>
      </div>
      <div className="wf-bar amber"><i style={{ width: `${Math.min(100, Math.max(2, pct))}%` }} /></div>
    </div>
  );
}

function Stepper({ stages }: { stages: Record<string, StageInfo> }) {
  const [now, setNow] = useState(() => Date.now() / 1000);
  useEffect(() => {
    if (!Object.values(stages).some((s) => s.status === 'running')) return;
    const id = setInterval(() => setNow(Date.now() / 1000), 1000);
    return () => clearInterval(id);
  }, [stages]);
  if (!stages || Object.keys(stages).length === 0) return null;
  return (
    <div className="wf-stepper">
      {PIPELINE_STAGES.map(({ key, label }) => {
        const info = stages[key] || { status: 'pending' };
        const cls = info.status === 'done' ? 'done' : info.status === 'running' ? 'running' : info.status === 'failed' ? 'failed' : '';
        let time = '';
        if (info.duration_ms) time = formatElapsed(info.duration_ms / 1000);
        else if (info.status === 'running' && info.started_at) time = formatElapsed(Math.max(0, now - info.started_at));
        return (
          <div key={key} className={`wf-step ${cls}`} title={info.detail || label}>
            <div className="track"><i /></div>
            <span className="name">{label}</span>
            {time && <span className="time">{time}</span>}
            {info.status === 'running' && info.detail && <span className="detail">{info.detail}</span>}
          </div>
        );
      })}
    </div>
  );
}

function QualityStats({ quality, compact }: { quality: NonNullable<Tour['quality']>; compact: boolean }) {
  const actBase = quality.relevant_total && quality.relevant_total > 0 ? quality.relevant_total : quality.total_nodes;
  const actCount = quality.relevant_actionable !== undefined ? quality.relevant_actionable : quality.actionable_nodes;
  const actPct = actBase > 0 ? Math.round((actCount / actBase) * 100) : 0;
  const planPct = quality.actionable_nodes > 0 ? Math.round((quality.plannable_nodes / quality.actionable_nodes) * 100) : 0;
  const valOk = quality.high_issues === 0;
  const cards = [
    { k: '화면', v: String(quality.total_nodes), sub: `전이 ${quality.total_edges}`, cls: '' },
    { k: '조작 가능', v: `${actPct}%`, sub: `${actCount}/${actBase}`, cls: '' },
    { k: '계획 가능', v: `${planPct}%`, sub: `${quality.plannable_nodes}`, cls: quality.plannable_nodes > 0 ? '' : 'warn' },
    { k: '검증', v: valOk ? 'OK' : String(quality.high_issues), sub: valOk ? `${quality.validation_issues} 경미` : '심각 이슈', cls: valOk ? 'ok' : 'warn' },
  ];
  if (compact) {
    return (
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap' }}>
        {cards.map((c) => (
          <span key={c.k} className="wf-chip outline" title={`${c.k}: ${c.v} (${c.sub})`}>
            <b style={{ color: c.cls === 'warn' ? 'var(--wf-danger)' : c.cls === 'ok' ? 'var(--wf-accent)' : 'var(--wf-ink)' }}>{c.v}</b> {c.k}
          </span>
        ))}
      </div>
    );
  }
  return (
    <div style={{ display: 'grid', gridTemplateColumns: 'repeat(4, 1fr)', gap: 8 }}>
      {cards.map((c) => (
        <div key={c.k} className={`wf-stat ${c.cls}`}>
          <span className="v">{c.v}</span>
          <span className="k">{c.k}</span>
          <span className="wf-mono wf-faint">{c.sub}</span>
        </div>
      ))}
    </div>
  );
}

function ResumeMenu({ onRetryFromStage }: { onRetryFromStage: (n: number) => void }) {
  const [open, setOpen] = useState(false);
  const ref = useRef<HTMLDivElement>(null);
  useEffect(() => {
    if (!open) return;
    const onClick = (e: MouseEvent) => { if (ref.current && !ref.current.contains(e.target as Node)) setOpen(false); };
    document.addEventListener('mousedown', onClick);
    return () => document.removeEventListener('mousedown', onClick);
  }, [open]);
  return (
    <div ref={ref} style={{ position: 'relative' }}>
      <button className="wf-btn quiet" onClick={() => setOpen(!open)}><IconRefresh size={14} /> 다시 실행 <IconChevron size={13} /></button>
      {open && (
        <div className="wf-menu wf-fade-in" style={{ right: 0, top: 'calc(100% + 6px)' }}>
          <div className="wf-eyebrow" style={{ padding: '6px 10px 4px' }}>어느 단계부터?</div>
          {RESUME_STAGES.map((s) => (
            <button key={s.n} className="wf-menu-item" onClick={() => { setOpen(false); onRetryFromStage(s.n); }}>
              <div className="t">{s.n}단계 · {s.label}</div>
              <div className="d">{s.desc}</div>
            </button>
          ))}
        </div>
      )}
    </div>
  );
}
