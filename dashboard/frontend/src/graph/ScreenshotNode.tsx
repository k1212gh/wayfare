import React, { useState } from 'react';
import { Handle, Position } from '@xyflow/react';
import { InstantTooltip } from './InstantTooltip';

export function ScreenshotNode({ data }: { data: any }) {
  const color = data.color || '#9ca3af';
  const screenshotUrl = `/api/tours/${data.tourId}/screenshot/${data.screen_id}`;
  const [imgError, setImgError] = useState(false);
  const status: string = data.status || 'resolved';
  const STATUS_DOT: Record<string, string> = {
    resolved: '#22c55e',
    partial:  '#f59e0b',
    unknown:  '#ef4444',
    entry:    '#8b5cf6',
  };
  const statusColor = STATUS_DOT[status] || '#9ca3af';
  const prio: string = data.capture_priority || '';
  const PRIO_BG: Record<string, string> = { A: '#059669', B: '#94a3b8', C: '#8b5cf6' };
  const prioBg = PRIO_BG[prio];
  return (
    <InstantTooltip text={data.tooltip || ''}>
    <div
      style={{
        width: 180, background: '#fff', border: '1px solid #d4d4d4',
        borderRadius: '10px', overflow: 'hidden', cursor: 'pointer',
        borderTop: `3px solid ${color}`,
        boxShadow: data.isEntry ? `0 0 0 2px ${STATUS_DOT.entry}` : undefined,
      }}
    >
      <Handle id="t" type="target" position={Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div style={{ height: 200, background: '#f5f5f5', overflow: 'hidden', position: 'relative' }}>
        {!imgError ? (
          <img src={screenshotUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover', objectPosition: 'top' }}
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
        <span style={{
          position: 'absolute', top: 4, right: 4, padding: '1px 6px',
          fontSize: '9px', fontWeight: 600, borderRadius: '4px',
          background: color, color: '#fff', fontFamily: "'JetBrains Mono', monospace",
        }}>
          {data.functional_category || 'other'}
        </span>
      </div>
      <div style={{ padding: '6px 8px' }}>
        <div style={{ fontSize: '10px', fontWeight: 600, lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
          {data.label || data.screen_id}
        </div>
      </div>
      <Handle id="s" type="source" position={Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </div>
    </InstantTooltip>
  );
}
