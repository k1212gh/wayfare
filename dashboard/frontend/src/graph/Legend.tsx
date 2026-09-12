import React, { useEffect, useState } from 'react';
import { InstantTooltip } from './InstantTooltip';
import { EDGE_STYLE, STATUS_STYLE, PRIO_STYLE } from './colors';
import { IconChevron } from '../app/icons';

/** 캔버스 우하단 범례 — 간선/상태/우선순위. 접힘 상태는 localStorage 에 기억. */
const STORAGE_KEY = 'wayfare.flow.legend.collapsed';

export function Legend() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try { const v = window.localStorage.getItem(STORAGE_KEY); return v == null ? true : v === '1'; } catch { return true; }
  });
  useEffect(() => { try { window.localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0'); } catch {} }, [collapsed]);

  if (collapsed) {
    return (
      <button className="wf-btn wf-floating" style={{ position: 'absolute', bottom: 16, right: 16, zIndex: 10 }} onClick={() => setCollapsed(false)}>
        범례 <IconChevron size={13} />
      </button>
    );
  }
  const edgeKinds = ['navigate', 'two_hop', 'launcher', 'intent_filter', 'pending_intent', 'overlay', 'back', 'contains', 'global'];
  return (
    <div className="wf-floating wf-legend wf-fade-in" style={{ position: 'absolute', bottom: 16, right: 16, zIndex: 10, width: 260, maxHeight: 'calc(100% - 32px)', display: 'flex', flexDirection: 'column' }}>
      <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', padding: '8px 12px', borderBottom: '1px solid var(--wf-border)' }}>
        <span style={{ fontWeight: 700, color: 'var(--wf-ink)' }}>범례</span>
        <button className="wf-btn ghost icon sm" onClick={() => setCollapsed(true)} title="접기"><IconChevron size={13} /></button>
      </div>
      <div style={{ padding: '10px 12px 12px', overflowY: 'auto' }}>
        <div className="wf-eyebrow" style={{ marginBottom: 4 }}>간선</div>
        {edgeKinds.map((k) => {
          const e = EDGE_STYLE[k];
          return (
            <InstantTooltip key={k} text={e.desc}>
              <div className="row">
                <svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke={e.stroke} strokeWidth="2.2" strokeDasharray={e.dash} /></svg>
                <span>{e.label}</span>
              </div>
            </InstantTooltip>
          );
        })}
        <div className="wf-eyebrow" style={{ margin: '10px 0 4px' }}>화면 상태</div>
        {Object.entries(STATUS_STYLE).map(([k, s]) => (
          <InstantTooltip key={k} text={s.desc}>
            <div className="row"><span className="wf-dot" style={{ background: s.color }} /><span>{s.label}</span></div>
          </InstantTooltip>
        ))}
        <div className="wf-eyebrow" style={{ margin: '10px 0 4px' }}>캡처 우선순위</div>
        {Object.entries(PRIO_STYLE).map(([k, p]) => (
          <InstantTooltip key={k} text={p.desc}>
            <div className="row"><span className="wf-mini-badge" style={{ background: p.bg, color: '#FFFCF5', minWidth: 18, justifyContent: 'center' }}>{k}</span><span>{p.label}</span></div>
          </InstantTooltip>
        ))}
        <div className="wf-faint" style={{ marginTop: 10, fontSize: 10.5, lineHeight: 1.5 }}>
          카드 캡션 위 색선 = 화면 분류. 파란 테두리 = 진입 화면. 흐린 카드 = 내부 처리용.
        </div>
      </div>
    </div>
  );
}
