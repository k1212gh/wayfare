import React, { useCallback, useEffect, useMemo, useState } from 'react';
import {
  ReactFlow,
  Node,
  Edge,
  Background,
  BackgroundVariant,
  Controls,
  MiniMap,
  Panel,
  useNodesState,
  useEdgesState,
  MarkerType,
  NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';

interface ScreenMapViewProps {
  graph: { entry_node: string; nodes: any[]; edges: any[] };
  onNodeSelect: (node: any) => void;
  filterCategory: string;
  searchQuery: string;
  tourId: string;
}

const CATEGORY_COLOR: Record<string, string> = {
  home: '#2563eb',
  login: '#dc2626',
  settings: '#7c3aed',
  search: '#059669',
  list: '#0891b2',
  content_detail: '#d97706',
  form: '#e11d48',
  profile: '#4f46e5',
  navigation: '#6b7280',
  other: '#9ca3af',
};

function buildLayout(nodes: any[], edges: any[], showScreenshots: boolean, tourId: string) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  const nodeW = showScreenshots ? 240 : 200;
  const nodeH = showScreenshots ? 160 : 56;
  g.setGraph({ rankdir: 'TB', nodesep: 40, ranksep: showScreenshots ? 100 : 70 });

  const flowNodes: Node[] = [];
  const flowEdges: Edge[] = [];

  for (const n of nodes) g.setNode(n.screen_id, { width: nodeW, height: nodeH });
  for (const e of edges) g.setEdge(e.from, e.to);
  dagre.layout(g);

  for (const n of nodes) {
    const pos = g.node(n.screen_id);
    const color = CATEGORY_COLOR[n.functional_category] || '#9ca3af';
    const isEntry = n.screen_id === nodes[0]?.screen_id;

    flowNodes.push({
      id: n.screen_id,
      data: { label: n.label || n.screen_id, ...n, showScreenshots, tourId, color },
      position: { x: (pos?.x || 0) - nodeW / 2, y: (pos?.y || 0) - nodeH / 2 },
      type: showScreenshots ? 'screenshotNode' : 'default',
      style: showScreenshots ? undefined : {
        background: '#fff',
        color: '#0a0a0a',
        border: isEntry ? `2px solid ${color}` : '1px solid #d4d4d4',
        borderRadius: '8px',
        padding: '10px 14px',
        fontSize: '12px',
        fontFamily: "'Inter', sans-serif",
        fontWeight: 500,
        width: nodeW,
        cursor: 'pointer',
        borderLeft: `4px solid ${color}`,
      },
    });
  }

  for (const e of edges) {
    const isBack = e.trigger_action === 'press_back';
    const isAuto = e.trigger_action === 'auto';
    const isWalk = e.source === 'walk';
    const label = e.trigger_widget
      ? `${e.trigger_action} ${e.trigger_widget}`
      : e.trigger_action || '';

    flowEdges.push({
      id: e.edge_id || `${e.from}-${e.to}`,
      source: e.from,
      target: e.to,
      label: label.length > 25 ? label.slice(0, 25) + '...' : label,
      labelStyle: { fontSize: 10, fill: '#6b7280', fontFamily: "'JetBrains Mono', monospace" },
      labelBgStyle: { fill: '#fff', fillOpacity: 0.95, rx: 3, ry: 3 },
      labelBgPadding: [4, 2] as [number, number],
      style: {
        stroke: isBack ? '#d4d4d4' : isWalk ? '#2563eb' : '#404040',
        strokeWidth: isBack ? 1 : isWalk ? 2 : 1.5,
        strokeDasharray: isBack || isAuto ? '6,4' : undefined,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 12, height: 12,
        color: isBack ? '#d4d4d4' : isWalk ? '#2563eb' : '#404040',
      },
    });
  }

  return { nodes: flowNodes, edges: flowEdges };
}

function ScreenshotNode({ data }: { data: any }) {
  const color = data.color || '#9ca3af';
  const screenshotUrl = `/api/tours/${data.tourId}/screenshot/${data.screen_id}`;
  const [imgError, setImgError] = useState(false);

  return (
    <div style={{
      width: 240, background: '#fff', border: '1px solid #d4d4d4',
      borderRadius: '10px', overflow: 'hidden', cursor: 'pointer',
      borderTop: `3px solid ${color}`,
    }}>
      <div style={{ height: 100, background: '#f5f5f5', overflow: 'hidden', position: 'relative' }}>
        {!imgError ? (
          <img src={screenshotUrl} alt="" style={{ width: '100%', height: '100%', objectFit: 'cover' }}
            onError={() => setImgError(true)} />
        ) : (
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'center', height: '100%', color: '#d4d4d4', fontSize: '11px' }}>
            No screenshot
          </div>
        )}
        <span style={{
          position: 'absolute', top: 4, right: 4, padding: '1px 6px',
          fontSize: '9px', fontWeight: 600, borderRadius: '4px',
          background: color, color: '#fff', fontFamily: "'JetBrains Mono', monospace",
        }}>
          {data.functional_category || 'other'}
        </span>
      </div>
      <div style={{ padding: '8px 10px' }}>
        <div style={{ fontSize: '11px', fontWeight: 600, lineHeight: 1.3, overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
          {data.label || data.screen_id}
        </div>
        <div style={{ fontSize: '9px', color: '#9ca3af', marginTop: '2px', fontFamily: "'JetBrains Mono', monospace" }}>
          {(data.activity || '').split('.').pop()}
        </div>
      </div>
    </div>
  );
}

const nodeTypes = { screenshotNode: ScreenshotNode };

export function ScreenMapView({ graph, onNodeSelect, filterCategory, searchQuery, tourId }: ScreenMapViewProps) {
  const [showScreenshots, setShowScreenshots] = useState(false);

  const filteredNodes = useMemo(() => {
    let nodes = graph.nodes;
    if (filterCategory) nodes = nodes.filter((n: any) => n.functional_category === filterCategory);
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      nodes = nodes.filter((n: any) =>
        (n.label || '').toLowerCase().includes(q) ||
        (n.screen_purpose || '').toLowerCase().includes(q) ||
        (n.activity || '').toLowerCase().includes(q)
      );
    }
    return nodes;
  }, [graph.nodes, filterCategory, searchQuery]);

  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map((n: any) => n.screen_id)), [filteredNodes]);
  const filteredEdges = useMemo(
    () => graph.edges.filter((e: any) => filteredNodeIds.has(e.from) && filteredNodeIds.has(e.to)),
    [graph.edges, filteredNodeIds]
  );

  const layout = useMemo(
    () => buildLayout(filteredNodes, filteredEdges, showScreenshots, tourId),
    [filteredNodes, filteredEdges, showScreenshots, tourId]
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layout.edges);

  useEffect(() => { setNodes(layout.nodes); }, [layout.nodes, setNodes]);
  useEffect(() => { setEdges(layout.edges); }, [layout.edges, setEdges]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => onNodeSelect(node.data),
    [onNodeSelect]
  );

  const downloadKG = useCallback(() => {
    const blob = new Blob([JSON.stringify({ nodes: graph.nodes, edges: graph.edges }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    a.download = 'knowledge_graph.json'; a.click();
  }, [graph]);

  return (
    <ReactFlow
      nodes={nodes} edges={edges}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick} nodeTypes={nodeTypes}
      fitView minZoom={0.05} maxZoom={2}
      proOptions={{ hideAttribution: true }}
    >
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d4d4d4" />
      <Controls showInteractive={false} style={{ border: '1px solid #e5e5e5', borderRadius: '8px', overflow: 'hidden' }} />
      <MiniMap
        nodeColor={(n) => CATEGORY_COLOR[(n.data as any)?.functional_category || 'other'] || '#9ca3af'}
        maskColor="rgba(0,0,0,0.06)"
        style={{ border: '1px solid #e5e5e5', borderRadius: '8px', overflow: 'hidden' }}
      />

      <Panel position="top-right">
        <div style={{ display: 'flex', gap: '6px' }}>
          <PanelBtn active={showScreenshots} onClick={() => setShowScreenshots(!showScreenshots)}>
            {showScreenshots ? 'Hide Screenshots' : 'Show Screenshots'}
          </PanelBtn>
          <PanelBtn onClick={downloadKG}>Download ScreenMap</PanelBtn>
        </div>
      </Panel>

      <Panel position="bottom-left">
        <div style={{
          display: 'flex', gap: '8px', flexWrap: 'wrap', padding: '6px 10px',
          background: 'rgba(255,255,255,0.95)', border: '1px solid #e5e5e5', borderRadius: '6px', fontSize: '10px',
        }}>
          {Object.entries(CATEGORY_COLOR).slice(0, 7).map(([cat, color]) => (
            <span key={cat} style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
              <span style={{ width: 8, height: 8, borderRadius: '2px', background: color }} /> {cat}
            </span>
          ))}
          <span style={{ color: '#2563eb' }}>── walked</span>
          <span style={{ color: '#d4d4d4' }}>- - back</span>
        </div>
      </Panel>
    </ReactFlow>
  );
}

function PanelBtn({ children, onClick, active }: { children: React.ReactNode; onClick: () => void; active?: boolean }) {
  return (
    <button onClick={onClick} style={{
      padding: '5px 10px', fontSize: '11px', fontWeight: 500, fontFamily: 'var(--font)',
      border: '1px solid #e5e5e5', borderRadius: '6px', cursor: 'pointer',
      background: active ? '#0a0a0a' : '#fff', color: active ? '#fff' : '#404040',
    }}>{children}</button>
  );
}
