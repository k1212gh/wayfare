import React from 'react';
import { Handle, Position } from '@xyflow/react';
import { InstantTooltip } from './InstantTooltip';
import { NODE_SIZES, NodeSize } from './nodeSize';

export function CustomTextNode({ data }: { data: any }) {
  const { color, isEntry, status, tooltip, prioBadge, screen_purpose, label, screen_id, isSystem, isSystemTriggered, isFragment, isActivity, prioOpacity } = data;
  const nodeSize: NodeSize = (data.nodeSize as NodeSize) || 'md';
  const nodeW = NODE_SIZES[nodeSize].text.w;
  
  return (
    <div title={tooltip} style={{
        background: isSystem ? '#f5f3ff'
                  : isSystemTriggered ? '#fef3c7'
                  : isFragment ? '#eef2ff'
                  : isActivity ? '#f8fafc'
                  : '#fff',
        color: '#0a0a0a',
        border: '1px solid #d4d4d4',
        borderTop: `3px solid ${color}`,
        boxShadow: `0 2px 4px rgba(0,0,0,0.05)`,
        borderRadius: isFragment ? '12px' : '8px',
        padding: '10px 14px',
        fontSize: isFragment ? '11px' : '12px',
        fontFamily: "'Inter', sans-serif",
        fontWeight: 500,
        width: isFragment ? nodeW - 40 : nodeW,
        cursor: 'pointer',
        opacity: prioOpacity,
    }}>
      <Handle type="target" position={data.rankdir === 'LR' ? Position.Left : Position.Top} style={{ opacity: 0, pointerEvents: 'none' }} />
      <div style={{ display: 'flex', flexDirection: 'column', alignItems: 'flex-start', width: '100%', gap: '4px' }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '4px', width: '100%', justifyContent: 'space-between' }}>
          <div style={{ display: 'flex', alignItems: 'center', gap: '4px', overflow: 'hidden' }}>
            {prioBadge}
            <span style={{ fontWeight: 600, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>
              {label || screen_id}
            </span>
          </div>
        </div>
        {data.subLabel && (
          <span style={{ fontSize: '9px', color: '#9ca3af', fontFamily: "'JetBrains Mono', monospace", overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', maxWidth: '100%' }}>
            {data.subLabel}
          </span>
        )}
        {((status && !['probed', 'resolved', 'enriched'].includes(status)) || (data.functional_category && data.functional_category !== 'other')) && (
        <div style={{ display: 'flex', gap: '6px', marginTop: '2px', flexWrap: 'wrap', alignItems: 'center' }}>
          {status && !['probed', 'resolved', 'enriched'].includes(status) && (
            <div style={{
              padding: '2px 6px', fontSize: '9px', fontWeight: 600,
              background: `${data.STATUS_BORDER?.[status] || '#9ca3af'}1A`, 
              color: data.STATUS_BORDER?.[status] || '#9ca3af', borderRadius: '4px',
              display: 'inline-flex', alignItems: 'center', gap: '4px', maxWidth: '100%',
              overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap', 
              border: `1px solid ${data.STATUS_BORDER?.[status] || '#9ca3af'}4D`
            }} title={`Status: ${status}`}>
              <span style={{ width: 6, height: 6, borderRadius: '50%', background: data.STATUS_BORDER?.[status] || '#9ca3af' }} />
              {status.toUpperCase()}
            </div>
          )}
          {data.functional_category && data.functional_category !== 'other' && (
          <span style={{
            padding: '2px 6px', fontSize: '9px', fontWeight: 600, borderRadius: '4px',
            background: color, color: '#fff', flexShrink: 0,
            display: 'inline-flex', alignItems: 'center', border: `1px solid ${color}`
          }}>
            {data.functional_category.toUpperCase()}
          </span>
          )}
        </div>
        )}
      </div>
      <Handle type="source" position={data.rankdir === 'LR' ? Position.Right : Position.Bottom} style={{ opacity: 0, pointerEvents: 'none' }} />
    </div>
  );
}
