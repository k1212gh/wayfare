import React, { useEffect, useState } from 'react';
import { useAppState } from './AppState';
import { WayfareMark, IconFolder, IconMap, IconPhone, IconChip } from './icons';
import { isComplete, isRunning, Tour } from '../dashboard/types';
import { tourTitle } from '../dashboard/tourTitle';

export type Page = 'projects' | 'flow' | 'devices' | 'models';

interface ShellProps {
  page: Page;
  onNavigate: (p: Page) => void;
  activeTourId: string;
  onOpenFlow: (tourId: string) => void;
  crumbs: React.ReactNode;
  topRight?: React.ReactNode;
  /** 흐름 페이지처럼 캔버스가 꽉 차야 하면 true — 콘텐츠 스크롤을 끈다. */
  fill?: boolean;
  children: React.ReactNode;
}

interface LLMStatus { configured: boolean; mode: string; model_screen: string; reason: string }

export function Shell({ page, onNavigate, activeTourId, onOpenFlow, crumbs, topRight, fill, children }: ShellProps) {
  const { tours, device, selectedSerial } = useAppState();
  const [llm, setLlm] = useState<LLMStatus | null>(null);

  useEffect(() => {
    let alive = true;
    const load = async () => {
      try { const r = await fetch('/api/settings/llm'); if (r.ok && alive) setLlm(await r.json()); } catch {}
    };
    load();
    const h = setInterval(load, 15000);
    return () => { alive = false; clearInterval(h); };
  }, [page]);

  const running = tours.filter((t) => isRunning(t.stage)).length;
  const viewable = tours.filter((t) => isComplete(t.stage));
  const activeDevice = device.devices.find((d) => d.serial === selectedSerial) || device.devices[0];

  return (
    <div className="wf-shell">
      <nav className="wf-rail">
        <div className="brand">
          <div className="mark"><WayfareMark size={22} /></div>
          <div>
            <div className="name">Wayfare</div>
            <div className="tag">앱 화면 흐름 지도</div>
          </div>
        </div>

        <NavItem on={page === 'projects'} icon={<IconFolder />} label="프로젝트" sub={tours.length ? String(tours.length) : ''} onClick={() => onNavigate('projects')} />
        <NavItem on={page === 'flow'} icon={<IconMap />} label="흐름 지도" disabled={!activeTourId} sub={activeTourId ? activeTourId.slice(0, 6) : ''} onClick={() => activeTourId && onOpenFlow(activeTourId)} />
        <NavItem on={page === 'devices'} icon={<IconPhone />} label="기기" sub={running ? `● ${running}` : ''} onClick={() => onNavigate('devices')} />
        <NavItem on={page === 'models'} icon={<IconChip />} label="모델" onClick={() => onNavigate('models')} />

        <div className="group wf-eyebrow">지도 열기</div>
        <div className="wf-rail-list">
          {viewable.length === 0 && <div className="wf-faint" style={{ padding: '6px 10px', fontSize: 11 }}>완성된 지도가 없습니다</div>}
          {viewable.map((t: Tour) => (
            <button key={t.tour_id} className={`wf-rail-tour${t.tour_id === activeTourId ? ' on' : ''}`} onClick={() => onOpenFlow(t.tour_id)} title={t.package_name}>
              <span className={`wf-dot ${t.stage === 'ANNOTATED' ? 'ok' : ''}`} />
              <span className="wf-ellipsis" style={{ flex: 1 }}>{tourTitle(t)}</span>
            </button>
          ))}
        </div>

        <div className="wf-rail-foot">
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className={`wf-dot ${activeDevice ? 'ok' : ''}`} />
            <span className="wf-ellipsis">{activeDevice ? shortSerial(activeDevice.serial) : '기기 없음'}</span>
          </div>
          <div style={{ display: 'flex', alignItems: 'center', gap: 6 }}>
            <span className={`wf-dot ${llm?.configured ? 'ok' : 'warn'}`} />
            <span className="wf-ellipsis" title={llm?.reason}>
              {!llm ? '…' : llm.configured ? (llm.mode === 'api' ? 'Claude API' : llm.model_screen) : '모델 없음'}
            </span>
          </div>
        </div>
      </nav>

      <div className="wf-main">
        <header className="wf-topbar">
          <div className="wf-crumbs">{crumbs}</div>
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: 10 }}>{topRight}</div>
        </header>
        <div className="wf-content" style={fill ? { overflow: 'hidden', display: 'flex', flexDirection: 'column' } : undefined}>
          {children}
        </div>
      </div>
    </div>
  );
}

function NavItem({ on, icon, label, sub, disabled, onClick }: {
  on: boolean; icon: React.ReactNode; label: string; sub?: string; disabled?: boolean; onClick: () => void;
}) {
  return (
    <button className={`wf-nav-item${on ? ' on' : ''}`} disabled={disabled} onClick={onClick} title={label}>
      <span className="ic">{icon}</span>
      <span className="txt">{label}</span>
      {sub ? <span className="sub">{sub}</span> : null}
    </button>
  );
}

export function shortSerial(serial: string): string {
  if (serial.startsWith('emulator-')) return `에뮬 ${serial.slice(9)}`;
  return serial.length > 10 ? `…${serial.slice(-6)}` : serial;
}
