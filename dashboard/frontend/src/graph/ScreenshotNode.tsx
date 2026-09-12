import React, { useState } from 'react';
import { Handle, Position } from '@xyflow/react';
import { InstantTooltip } from './InstantTooltip';
import { NODE_SIZES, NodeSize } from './nodeSize';

export function ScreenshotNode({ data }: { data: any }) {
  const color = data.color || '#9ca3af';
  const screenshotUrl = `/api/tours/${data.tourId}/screenshot/${data.screen_id}`;
  const [imgError, setImgError] = useState(false);
  const status: string = data.status || 'resolved';
  const nodeSize: NodeSize = (data.nodeSize as NodeSize) || 'md';
  const dims = NODE_SIZES[nodeSize].screenshot;
  const STATUS_DOT: Record<string, string> = {
    declared: '#cbd5e1',
    probed:   '#3b82f6',
    resolved: '#22c55e',
    enriched: '#22c55e',
    partial:  '#f59e0b',
    unknown:  '#ef4444',
    entry:    '#8b5cf6',
    system:   '#d97706',
  };
  const statusColor = STATUS_DOT[status] || '#9ca3af';
  const prio: string = data.capture_priority || '';
  const PRIO_BG: Record<string, string> = { A: '#059669', B: '#94a3b8', C: '#8b5cf6' };
  const prioBg = PRIO_BG[prio];
  return (
    <InstantTooltip text={data.tooltip || ''}>
    <div
      style={{
        width: dims.w, background: '#fff', border: '1px solid #d4d4d4',
        borderRadius: '10px', overflow: 'hidden', cursor: 'pointer',
        borderTop: `3px solid ${color}`,
        boxShadow: data.isEntry ? `0 0 0 2px ${STATUS_DOT.entry}` : undefined,
      }}
    >
      <Handle id="t" type="target" position={data.rankdir === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div style={{ height: dims.sectionH, background: '#f5f5f5', overflow: 'hidden', position: 'relative' }}>
        {!imgError ? (
          <img src={screenshotUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'contain', objectPosition: 'center', background: '#000' }}
            onError={() => setImgError(true)} />
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#d4d4d4', fontSize: '11px' }}>
            No screenshot
          </div>
        )}
        <span title={`Static status: ${status}`} style={{
          position: 'absolute', top: 6, left: 6, width: 10, height: 10,
          borderRadius: '50%', background: statusColor,
          border: '2px solid #fff', boxShadow: '0 0 2px rgba(0,0,0,0.3)',
        }} />
        {prioBg && (
          <span
            title={`Capture priority: ${prio}`}
            style={{
              position: 'absolute', top: 4, left: 22,
              padding: '1px 5px', borderRadius: 3,
              background: prioBg, color: '#fff',
              fontSize: '9px', fontWeight: 700,
              fontFamily: "'JetBrains Mono', monospace",
              boxShadow: '0 0 2px rgba(0,0,0,0.3)',
            }}
          >
            {prio}
          </span>
        )}
      </div>
      <div style={{ padding: '6px 8px' }}>
        <div style={{ fontSize: '11px', fontWeight: 600, lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
          {data.label || data.screen_id}
        </div>
        {data.subLabel && (
          <div style={{ fontSize: '9px', color: '#9ca3af', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
            {data.subLabel}
          </div>
        )}
        {/* 2026-09-12: probed/resolved 상태와 other 카테고리는 정보량이 없어 배지 생략 (상태는 좌상단 점) */}
        {((status && !['probed', 'resolved', 'enriched'].includes(status)) || (data.functional_category && data.functional_category !== 'other')) && (
        <div style={{ display: 'flex', gap: '6px', marginTop: '4px', flexWrap: 'wrap', alignItems: 'center' }}>
          {status && !['probed', 'resolved', 'enriched'].includes(status) && (
            <div style={{
              padding: '2px 6px', fontSize: '9px', fontWeight: 600,
              background: `${statusColor}1A`, color: statusColor, borderRadius: '4px',
              display: 'inline-flex', alignItems: 'center', gap: '4px', maxWidth: '100%',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', border: `1px solid ${statusColor}4D`
            }} title={`Status: ${status}`}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: statusColor }} />
              {status.toUpperCase()}
            </div>
          )}
          {data.functional_category && data.functional_category !== 'other' && (
          <span style={{
            padding: '2px 6px', fontSize: '9px', fontWeight: 600, borderRadius: '4px',
            background: color, color: '#fff', fontFamily: "'Inter', sans-serif",
            display: 'inline-flex', alignItems: 'center', border: `1px solid ${color}`
          }}>
            {data.functional_category.toUpperCase()}
          </span>
          )}
        </div>
        )}
      </div>
      <Handle id="s" type="source" position={data.rankdir === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </div>
    </InstantTooltip>
  );
}
