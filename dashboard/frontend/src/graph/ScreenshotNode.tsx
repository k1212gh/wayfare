import React, { useState } from 'react';
import { Handle, Position } from '@xyflow/react';
import { InstantTooltip } from './InstantTooltip';
import { NODE_SIZES, NodeSize } from './nodeSize';
import { STATUS_STYLE, PRIO_STYLE, CATEGORY_LABEL } from './colors';

/** 스크린샷이 있는 화면 카드 — 폰 화면 + 이름 캡션. 카테고리 색은 캡션 위 선. */
export function ScreenshotNode({ data }: { data: any }) {
  const color = data.color || 'var(--wf-ink-3)';
  const screenshotUrl = `/api/tours/${data.tourId}/screenshot/${data.screen_id}`;
  const [imgError, setImgError] = useState(false);
  const status: string = data.status || 'resolved';
  const dims = NODE_SIZES[(data.nodeSize as NodeSize) || 'md'].screenshot;
  const st = STATUS_STYLE[status];
  const prio: string = data.capture_priority || '';
  const pr = PRIO_STYLE[prio];
  const cat: string = data.functional_category || '';
  const showStatus = st && !['probed', 'resolved', 'enriched'].includes(status);
  const showBadges = showStatus || (cat && cat !== 'other') || data.is_dialog;

  return (
    <InstantTooltip text={data.tooltip || ''}>
      <div className="wf-node" style={{ width: dims.w, boxShadow: data.isEntry ? '0 0 0 3px var(--wf-info-soft), 0 0 0 4px var(--wf-info)' : undefined, opacity: data.prioOpacity }}>
        <Handle id="t" type="target" position={data.rankdir === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
        <div className="shot" style={{ height: dims.sectionH }}>
          {!imgError ? <img src={screenshotUrl} alt="" loading="lazy" onError={() => setImgError(true)} /> : <div className="empty">스크린샷 없음</div>}
          {pr && prio !== 'A' && (
            <span className="wf-mini-badge" title={`${pr.label} — ${pr.desc}`} style={{ position: 'absolute', top: 6, left: 6, background: pr.bg, color: '#FFFCF5' }}>{prio}</span>
          )}
        </div>
        <div className="cap" style={{ borderTopColor: color }}>
          <div className="t">{data.label || data.screen_id}</div>
          {data.subLabel && <div className="s">{data.subLabel}</div>}
          {showBadges && (
            <div className="badges">
              {showStatus && (
                <span className="wf-mini-badge" title={st.desc} style={{ background: `${st.color}22`, color: st.color }}>
                  <span className="wf-dot" style={{ width: 5, height: 5, background: st.color }} />{st.label}
                </span>
              )}
              {cat && cat !== 'other' && (
                <span className="wf-mini-badge" style={{ background: color, color: '#FFFCF5' }}>{CATEGORY_LABEL[cat] || cat}</span>
              )}
              {data.is_dialog && cat !== 'dialog' && <span className="wf-mini-badge" style={{ background: 'var(--wf-amber-soft)', color: 'var(--wf-amber-ink)' }}>오버레이</span>}
            </div>
          )}
        </div>
        <Handle id="s" type="source" position={data.rankdir === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
      </div>
    </InstantTooltip>
  );
}
