import React from 'react';
import { Position, useInternalNode, getBezierPath, EdgeProps, EdgeLabelRenderer } from '@xyflow/react';

/** 노드 중심을 잇는 직선이 테두리와 만나는 점 — 노드를 끌어도 간선이 따라온다. */
export function getNodeIntersection(sourceNode: any, targetNode: any) {
  const sw = sourceNode.measured?.width || sourceNode.width || 180;
  const sh = sourceNode.measured?.height || sourceNode.height || 100;
  const sx = sourceNode.internals.positionAbsolute.x + sw / 2;
  const sy = sourceNode.internals.positionAbsolute.y + sh / 2;
  const tw = targetNode.measured?.width || targetNode.width || 180;
  const th = targetNode.measured?.height || targetNode.height || 100;
  const tx = targetNode.internals.positionAbsolute.x + tw / 2;
  const ty = targetNode.internals.positionAbsolute.y + th / 2;
  const w = sw / 2, h = sh / 2;
  const dx = tx - sx, dy = ty - sy;
  if (dx === 0 && dy === 0) return { x: sx, y: sy };
  const scale = Math.max(Math.abs(dx) / w, Math.abs(dy) / h);
  return { x: sx + dx / scale, y: sy + dy / scale };
}

export function FloatingEdge({ id, source, target, style, markerEnd, data }: EdgeProps) {
  const sourceNode = useInternalNode(source);
  const targetNode = useInternalNode(target);
  if (!sourceNode || !targetNode) return null;
  const s = getNodeIntersection(sourceNode, targetNode);
  const t = getNodeIntersection(targetNode, sourceNode);
  const d: any = data || {};
  const lr = d.rankdir === 'LR';
  const [path, labelX, labelY] = getBezierPath({
    sourceX: s.x, sourceY: s.y, targetX: t.x, targetY: t.y,
    sourcePosition: lr ? Position.Right : Position.Bottom,
    targetPosition: lr ? Position.Left : Position.Top,
  });
  const label = d.label as string | undefined;
  return (
    <>
      <path id={id} d={path} style={{ ...(style || {}), pointerEvents: 'none' }} markerEnd={markerEnd} fill="none" />
      <path d={path} stroke="rgba(0,0,0,0.001)" strokeWidth="18" fill="none" style={{ cursor: 'pointer' }}
        onClick={(e) => { e.stopPropagation(); d.onOpenEdge?.(d); }} />
      {label && (
        <EdgeLabelRenderer>
          <div style={{
            position: 'absolute', transform: `translate(-50%, -50%) translate(${labelX}px, ${labelY}px)`,
            background: 'var(--wf-surface)', padding: '2px 8px', borderRadius: 999,
            fontSize: 10.5, fontWeight: 600, color: 'var(--wf-ink-2)', fontFamily: 'var(--wf-font)',
            border: '1px solid var(--wf-border)', boxShadow: 'var(--wf-shadow-sm)', pointerEvents: 'none', whiteSpace: 'nowrap',
          }}>
            {label}
          </div>
        </EdgeLabelRenderer>
      )}
    </>
  );
}
