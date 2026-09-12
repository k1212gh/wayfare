import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { InstantTooltip } from './InstantTooltip';
import { NODE_SIZES, NodeSize } from './nodeSize';
import { STATUS_STYLE, PRIO_STYLE, CATEGORY_LABEL } from './colors';

/** 스크린샷이 없는 화면(선언만 된 Activity, 시스템 진입 등) — 텍스트 카드. */
export function CustomTextNode({ data }: { data: any }) {
  const { color, isEntry, status, tooltip, label, screen_id, isSystem, isSystemTriggered, isFragment, prioOpacity } = data;
  const nodeW = NODE_SIZES[(data.nodeSize as NodeSize) || 'md'].text.w;
  const st = STATUS_STYLE[status];
  const showStatus = st && !['probed', 'resolved', 'enriched'].includes(status);
  const prio: string = data.capture_priority || '';
  const pr = PRIO_STYLE[prio];
  const cat: string = data.functional_category || '';
  const cls = ['wf-node-text', isFragment ? 'fragment' : '', isSystem || isSystemTriggered ? 'system' : ''].filter(Boolean).join(' ');

  return (
    <InstantTooltip text={tooltip || ''}>
      <div className={cls} style={{
        width: isFragment ? nodeW - 30 : nodeW, borderLeftColor: color || 'var(--wf-ink-3)', opacity: prioOpacity,
        boxShadow: isEntry ? '0 0 0 3px var(--wf-info-soft), 0 0 0 4px var(--wf-info)' : undefined,
      }}>
        <Handle type="target" position={data.rankdir === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
        <div style={{ display: 'flex', alignItems: 'center', gap: 6, minWidth: 0 }}>
          {pr && prio !== 'A' && <span className="wf-mini-badge" title={`${pr.label} — ${pr.desc}`} style={{ background: pr.bg, color: '#FFFCF5', flexShrink: 0 }}>{prio}</span>}
          <span className="t" style={{ flex: 1 }}>{label || screen_id}</span>
        </div>
        {data.subLabel && <div className="s">{data.subLabel}</div>}
        {(showStatus || (cat && cat !== 'other')) && (
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', marginTop: 5 }}>
            {showStatus && (
              <span className="wf-mini-badge" title={st.desc} style={{ background: `${st.color}22`, color: st.color }}>
                <span className="wf-dot" style={{ width: 5, height: 5, background: st.color }} />{st.label}
              </span>
            )}
            {cat && cat !== 'other' && <span className="wf-mini-badge" style={{ background: color, color: '#FFFCF5' }}>{CATEGORY_LABEL[cat] || cat}</span>}
          </div>
        )}
        <Handle type="source" position={data.rankdir === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
      </div>
    </InstantTooltip>
  );
}
