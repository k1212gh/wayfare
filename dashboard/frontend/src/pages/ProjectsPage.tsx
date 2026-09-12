import React, { useMemo, useRef, useState } from 'react';
import { useAppState } from '../app/AppState';
import { ProjectCard } from '../dashboard/ProjectCard';
import { LiveDeviceMirror } from '../dashboard/LiveDeviceMirror';
import { isRunning, isComplete } from '../dashboard/types';
import { IconUpload, IconPhone, IconClose } from '../app/icons';

interface Props {
  onOpenFlow: (tourId: string) => void;
  onRunStart: (tourId: string) => void;
}

type Filter = 'all' | 'ready' | 'complete' | 'failed';

export function ProjectsPage({ onOpenFlow, onRunStart }: Props) {
  const s = useAppState();
  const [dragOver, setDragOver] = useState(false);
  const [filter, setFilter] = useState<Filter>('all');
  const [mirrorOpen, setMirrorOpen] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const running = useMemo(() => s.tours.filter((t) => isRunning(t.stage)), [s.tours]);
  const idle = useMemo(() => s.tours.filter((t) => !isRunning(t.stage)), [s.tours]);
  const ready = useMemo(() => idle.filter((t) => t.stage === 'UPLOADED'), [idle]);
  const complete = useMemo(() => idle.filter((t) => isComplete(t.stage)), [idle]);
  const failed = useMemo(() => idle.filter((t) => t.stage === 'FAILED' || t.stage === 'CANCELLED'), [idle]);
  const visible = filter === 'ready' ? ready : filter === 'complete' ? complete : filter === 'failed' ? failed : idle;
  const active = running[0];

  const run = async (id: string) => { if (await s.runTour(id)) onRunStart(id); };
  const retry = async (id: string, n: number) => { if (await s.retryFromStage(id, n)) onRunStart(id); };
  const cardProps = (t: typeof s.tours[number]) => ({
    tour: t,
    onRun: () => run(t.tour_id),
    onStop: () => s.stopTour(t.tour_id),
    onPause: () => s.pauseTour(t.tour_id),
    onResume: () => s.resumeTour(t.tour_id),
    onOpen: () => onOpenFlow(t.tour_id),
    onDelete: () => s.deleteTour(t.tour_id),
    onRetryFromStage: (n: number) => retry(t.tour_id, n),
  });

  return (
    <div className="wf-page">
      <div className="wf-page-head">
        <div>
          <h1>프로젝트</h1>
          <p>APK 를 올리면 기기에서 앱을 직접 돌아다니며 화면과 전환을 지도로 만듭니다.</p>
        </div>
      </div>

      <input ref={fileRef} type="file" accept=".apk" multiple style={{ display: 'none' }}
        onChange={(e) => { if (e.target.files?.length) s.uploadFiles(e.target.files); e.currentTarget.value = ''; }} />
      <div
        className={`wf-drop${dragOver ? ' over' : ''}`}
        onClick={() => fileRef.current?.click()}
        onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
        onDragLeave={() => setDragOver(false)}
        onDrop={(e) => { e.preventDefault(); setDragOver(false); if (e.dataTransfer.files.length) s.uploadFiles(e.dataTransfer.files); }}
        style={{ marginBottom: 28 }}
      >
        <div className="glyph"><IconUpload size={26} /></div>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div style={{ fontSize: 16, fontWeight: 700 }}>{s.uploading ? 'APK 올리는 중…' : '새 앱 분석 — APK 를 여기에 놓거나 클릭해서 선택'}</div>
          <div className="wf-muted" style={{ fontSize: 12.5, marginTop: 3 }}>base.apk 하나, 또는 split APK 여러 개(base + split_config.*)를 함께 올릴 수 있습니다.</div>
        </div>
        <span className="wf-btn primary lg" style={{ pointerEvents: 'none' }}>파일 선택</span>
      </div>

      {active && (
        <section style={{ marginBottom: 32 }}>
          <div className="wf-section-title" style={{ justifyContent: 'space-between' }}>
            <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
              <h2>진행 중</h2><span className="count">{running.length}</span>
            </div>
            <button className={`wf-btn sm${mirrorOpen ? ' on' : ''}`} onClick={() => setMirrorOpen(!mirrorOpen)}>
              <IconPhone size={13} /> {mirrorOpen ? '기기 화면 닫기' : '기기 화면 보기'}
            </button>
          </div>
          <div style={{ display: 'grid', gridTemplateColumns: mirrorOpen || active.paused ? 'minmax(0, 1fr) 300px' : '1fr', gap: 16, alignItems: 'start' }}>
            <div className="wf-list">
              {running.map((t) => <ProjectCard key={t.tour_id} variant="active" {...cardProps(t)} />)}
            </div>
            {(mirrorOpen || active.paused) && (
              <aside className="wf-card" style={{ padding: 14, position: 'sticky', top: 16 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: 10 }}>
                  <div>
                    <div className="wf-eyebrow">기기 화면</div>
                    <div className="wf-faint" style={{ fontSize: 11, marginTop: 2 }}>{active.paused ? '일시정지 중 — 직접 조작 가능' : '읽기 전용 미리보기'}</div>
                  </div>
                  {!active.paused && <button className="wf-btn ghost icon sm" onClick={() => setMirrorOpen(false)}><IconClose size={14} /></button>}
                </div>
                <LiveDeviceMirror interactive={!!active.paused} serial={s.selectedSerial} compact />
              </aside>
            )}
          </div>
        </section>
      )}

      <section>
        <div className="wf-section-title" style={{ justifyContent: 'space-between', flexWrap: 'wrap' }}>
          <div style={{ display: 'flex', alignItems: 'baseline', gap: 10 }}>
            <h2>{active ? '지난 분석' : '모든 프로젝트'}</h2><span className="count">{visible.length}</span>
          </div>
          <div className="wf-seg">
            <button className={filter === 'all' ? 'on' : ''} onClick={() => setFilter('all')}>전체 {idle.length}</button>
            <button className={filter === 'ready' ? 'on' : ''} onClick={() => setFilter('ready')}>대기 {ready.length}</button>
            <button className={filter === 'complete' ? 'on' : ''} onClick={() => setFilter('complete')}>완성 {complete.length}</button>
            <button className={filter === 'failed' ? 'on' : ''} onClick={() => setFilter('failed')}>실패 {failed.length}</button>
          </div>
        </div>

        {s.tours.length === 0 && (
          <div className="wf-empty">아직 프로젝트가 없습니다. 위에서 APK 를 올려 시작하세요.</div>
        )}
        {s.tours.length > 0 && visible.length === 0 && (
          <div className="wf-empty">이 조건에 맞는 프로젝트가 없습니다.</div>
        )}
        {visible.length > 0 && (
          <div className="wf-grid">
            {visible.map((t) => <ProjectCard key={t.tour_id} variant="grid" {...cardProps(t)} />)}
          </div>
        )}
      </section>
    </div>
  );
}
