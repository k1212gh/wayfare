import React, { useMemo } from 'react';
import { ScreenMapView } from '../ScreenMapView';
import { ScreenPanel } from '../ScreenPanel';
import { IconClose, IconPlay, IconStop } from '../app/icons';
import { STAGE_LABELS } from '../dashboard/types';

interface Props {
  graphData: any;
  tourId: string;
  tourStage: string;
  selectedNode: any;
  onSelectNode: (n: any) => void;
  selectedEdgeData: any | null;
  onSelectedEdgeDataChange: (d: any | null) => void;
  onStop: () => void;
  onStartWalk: () => void;
}

const RUNNING = ['WALKING', 'PREPROCESSING_DATA', 'CARDS_READY', 'BUILDING_SCREENMAP', 'LLM_ANNOTATING', 'PREPROCESSING', 'STATIC_ANALYZING'];

/** 흐름 지도 페이지 — 캔버스 + 우측 인스펙터. 상단바 오른쪽 내용은 App 이 넣는다. */
export function FlowPage({ graphData, tourId, tourStage, selectedNode, onSelectNode, selectedEdgeData, onSelectedEdgeDataChange }: Props) {
  const graph = graphData?.screen_map?.graph;
  const nodes = graph?.nodes || [];
  const edges = graph?.edges || [];
  return (
    <div style={{ display: 'flex', flex: 1, minHeight: 0, position: 'relative' }}>
      <div style={{ flex: 1, position: 'relative', minWidth: 0 }}>
        {graph && (
          <ScreenMapView
            graph={graph}
            onNodeSelect={onSelectNode}
            tourId={tourId}
            appName={graphData.screen_map.app_name || graphData.screen_map.package_name}
            selectedNodeId={selectedNode?.screen_id}
            selectedEdgeData={selectedEdgeData}
            onSelectedEdgeDataChange={onSelectedEdgeDataChange}
          />
        )}
      </div>
      {selectedNode && (
        <aside className="wf-inspector wf-fade-in">
          <div className="head">
            <span className="wf-eyebrow" style={{ flex: 1 }}>화면 정보</span>
            <button className="wf-btn ghost icon sm" onClick={() => onSelectNode(null)}><IconClose size={14} /></button>
          </div>
          <div className="body">
            <ScreenPanel
              node={selectedNode}
              tourId={tourId}
              allNodes={nodes}
              allEdges={edges}
              onSelectNode={(nid: string) => { const f = nodes.find((n: any) => n.screen_id === nid); if (f) onSelectNode(f); }}
              onSelectEdge={(e: any) => {
                const src = nodes.find((n: any) => n.screen_id === e.from);
                const tgt = nodes.find((n: any) => n.screen_id === e.to);
                onSelectedEdgeDataChange({
                  edgeId: e.edge_id || `${e.from}-${e.to}`,
                  kind: e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate'),
                  confidence: e.confidence || (e.source === 'walk' ? 'observed' : 'static_intent'),
                  actionLabel: '', raw: e, sourceNode: src, targetNode: tgt,
                  sourceLabel: src?.label || e.from, targetLabel: tgt?.label || e.to,
                });
              }}
            />
          </div>
        </aside>
      )}
    </div>
  );
}

/** 상단바 오른쪽 — 노드/전이 수, 커버리지, 실행 상태. */
export function FlowTopRight({ graphData, tourStage, onStop, onStartWalk }: { graphData: any; tourStage: string; onStop: () => void; onStartWalk: () => void }) {
  const meta = graphData?.screen_map;
  const isRunning = RUNNING.includes(tourStage);
  const coverage = useMemo(() => {
    const nodes: any[] = meta?.graph?.nodes || [];
    let total = 0, reachable = 0, actionable = 0;
    for (const n of nodes) {
      if (n.screen_id === 'system:external_entry') continue;
      total++;
      if (['enriched', 'resolved', 'partial', 'unknown', 'entry', 'probed'].includes(n.status || '')) reachable++;
      if (n.screenshot_ref && (n.widgets?.length ?? 0) > 0) actionable++;
    }
    return total ? { total, reachable, actionable, actPct: Math.round((actionable / total) * 100), reachPct: Math.round((reachable / total) * 100) } : null;
  }, [meta]);
  return (
    <>
      {meta && <span className="wf-chip outline mono">{meta.metadata?.total_nodes ?? meta.graph?.nodes?.length} 화면 · {meta.metadata?.total_edges ?? meta.graph?.edges?.length} 전환</span>}
      {coverage && (
        <span className="wf-chip outline" title={`조작 가능(스크린샷+UI 요소) ${coverage.actionable} / 도달 확인 ${coverage.reachable} / 전체 ${coverage.total}`}>
          <span className="wf-bar" style={{ width: 70, height: 5, position: 'relative' }}>
            <i style={{ width: `${Math.max(2, coverage.reachPct)}%`, background: 'var(--wf-amber)', position: 'absolute', inset: 0 }} />
            <i style={{ width: `${Math.max(2, coverage.actPct)}%`, position: 'absolute', inset: 0 }} />
          </span>
          <span className="wf-mono"><b style={{ color: 'var(--wf-accent)' }}>{coverage.actionable}</b>/<span style={{ color: 'var(--wf-amber-ink)' }}>{coverage.reachable}</span>/{coverage.total}</span>
        </span>
      )}
      {isRunning ? (
        <>
          <span className="wf-chip amber"><span className="wf-dot live" style={{ width: 6, height: 6, background: 'currentColor' }} /> {STAGE_LABELS[tourStage] || tourStage}</span>
          <button className="wf-btn sm danger" onClick={onStop}><IconStop size={12} /> 중지</button>
        </>
      ) : (
        tourStage && (
          <>
            <span className="wf-chip outline">{STAGE_LABELS[tourStage] || tourStage}</span>
            {['UPLOADED', 'FAILED', 'CANCELLED', 'SCREENMAP_GENERATED', 'ANNOTATED', 'STATIC_DONE'].includes(tourStage) && (
              <button className="wf-btn sm" onClick={onStartWalk} title="기기에서 다시 탐색"><IconPlay size={12} /> {['SCREENMAP_GENERATED', 'ANNOTATED'].includes(tourStage) ? '다시 탐색' : '탐색 시작'}</button>
            )}
          </>
        )
      )}
    </>
  );
}
