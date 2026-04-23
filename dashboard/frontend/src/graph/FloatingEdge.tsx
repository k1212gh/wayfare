import React from 'react';
import {
  Position,
  useInternalNode,
  getBezierPath,
  EdgeProps,
  EdgeLabelRenderer,
} from '@xyflow/react';
import { EDGE_KIND_DESC } from './colors';

/** Compute the attachment point on a node's border along the line from
 *  source center → target center, so edges follow nodes as they're dragged. */
export function getNodeIntersection(sourceNode: any, targetNode: any) {
  const sw = sourceNode.measured?.width  || sourceNode.width  || 180;
  const sh = sourceNode.measured?.height || sourceNode.height || 100;
  const sx = sourceNode.internals.positionAbsolute.x + sw / 2;
  const sy = sourceNode.internals.positionAbsolute.y + sh / 2;

  const tw = targetNode.measured?.width  || targetNode.width  || 180;
  const th = targetNode.measured?.height || targetNode.height || 100;
  const tx = targetNode.internals.positionAbsolute.x + tw / 2;
  const ty = targetNode.internals.positionAbsolute.y + th / 2;

  const w = sw / 2, h = sh / 2;
  const dx = tx - sx, dy = ty - sy;
  if (dx === 0 && dy === 0) return { x: sx, y: sy };
  const scaleX = Math.abs(dx) / w;
  const scaleY = Math.abs(dy) / h;
  const scale = Math.max(scaleX, scaleY);
  return { x: sx + dx / scale, y: sy + dy / scale };
}

export function FloatingEdge({ id, source, target, style, markerEnd, data }: EdgeProps) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;
  const s = getNodeIntersection(sourceNode, targetNode);
  const t = getNodeIntersection(targetNode, sourceNode);
  const [path, labelX, labelY] = getBezierPath({
    sourceX: s.x, sourceY: s.y, targetX: t.x, targetY: t.y,
    sourcePosition: Position.Bottom, targetPosition: Position.Top,
  });
  const d: any = data || {};
  const label = d.label as string | undefined;
  const tooltip = [
    `Kind: ${d.kind || 'navigate'}`,
    EDGE_KIND_DESC[d.kind] || '',
    d.confidence ? `Confidence: ${d.confidence}` : '',
    label ? `Trigger: ${label}` : '',
  ].filter(Boolean).join('\n');
  return (
    <>
      <path id={id} d={path} style={{ ...(style || {}), pointerEvents: 'none' }} markerEnd={markerEnd} fill="none" />
      <g>
        <path
          d={path}
          stroke="rgba(0,0,0,0.001)"
          strokeWidth="18"
          fill="none"
          style={{ cursor: 'help' }}
          onMouseEnter={(e) => {
            const el = document.getElementById('sa-edge-tooltip');
            if (el) {
              el.style.display = 'block';
              el.textContent = tooltip;
              el.style.left = e.clientX + 14 + 'px';
              el.style.top = e.clientY + 14 + 'px';
            }
          }}
          onMouseMove={(e) => {
            const el = document.getElementById('sa-edge-tooltip');
            if (el) {
              el.style.left = e.clientX + 14 + 'px';
              el.style.top = e.clientY + 14 + 'px';
            }
          }}
          onMouseLeave={() => {
            const el = document.getElementById('sa-edge-tooltip');
            if (el) el.style.display = 'none';
          }}
        />
      </g>
      {label && (
        <EdgeLabelRenderer>
          <div
            style={{
              position: 'absolute', transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
              background: '#fff', padding: '1px 4px', borderRadius: 3,
              fontSize: 10, color: '#6b7280', fontFamily: "'JetBrains Mono', monospace",
              pointerEvents: 'none', opacity: 0.9,
            }}
          >
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
