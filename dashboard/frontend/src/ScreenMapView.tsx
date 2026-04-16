import React, { useCallback, useEffect, useMemo } from 'react';
import {
  ReactFlow,
  Node,
  Edge,
  Background,
  Controls,
  MiniMap,
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
}

/* No gradients, no shadows — border-only category indicators */
const CATEGORY_DOT: Record<string, string> = {
  home: '#0a0a0a',
  login: '#FF4D1C',
  settings: '#6b7280',
  search: '#0a0a0a',
  list: '#0a0a0a',
  content_detail: '#0a0a0a',
  form: '#FF4D1C',
  profile: '#6b7280',
  navigation: '#6b7280',
  other: '#6b7280',
};

function buildLayout(nodes: any[], edges: any[]) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  g.setGraph({ rankdir: 'TB', nodesep: 50, ranksep: 70 });

  const flowNodes: Node[] = [];
  const flowEdges: Edge[] = [];

  for (const n of nodes) g.setNode(n.screen_id, { width: 200, height: 52 });
  for (const e of edges) g.setEdge(e.from, e.to);
  dagre.layout(g);

  for (const n of nodes) {
    const pos = g.node(n.screen_id);
    const dot = CATEGORY_DOT[n.functional_category] || '#6b7280';
    const isEntry = n.functional_category === 'home' || n.functional_category === 'login';

    flowNodes.push({
      id: n.screen_id,
      data: { label: n.label || n.screen_id, ...n },
      position: { x: (pos?.x || 0) - 100, y: (pos?.y || 0) - 26 },
      style: {
        background: '#ffffff',
        color: '#0a0a0a',
        border: isEntry ? '2px solid #0a0a0a' : '1px solid #e5e5e5',
        borderRadius: '8px',
        padding: '10px 14px',
        fontSize: '12px',
        fontFamily: "'Inter', sans-serif",
        fontWeight: 500,
        width: 200,
        cursor: 'pointer',
        borderLeft: `3px solid ${dot}`,
        lineHeight: '1.3',
      },
    });
  }

  for (const e of edges) {
    const isBack = e.trigger_action === 'press_back';
    const isAuto = e.trigger_action === 'auto';
    const isConditional = !!e.condition;

    flowEdges.push({
      id: e.edge_id || `${e.from}-${e.to}`,
      source: e.from,
      target: e.to,
      label: e.trigger_widget
        ? `${e.trigger_action} ${e.trigger_widget}`
        : e.trigger_action || '',
      labelStyle: {
        fontSize: 10,
        fill: '#6b7280',
        fontFamily: "'JetBrains Mono', monospace",
      },
      labelBgStyle: {
        fill: '#f7f7f7',
        fillOpacity: 0.9,
      },
      labelBgPadding: [4, 2] as [number, number],
      style: {
        stroke: isBack ? '#d4d4d4' : '#0a0a0a',
        strokeWidth: isBack ? 1 : 1.5,
        strokeDasharray: isConditional || isAuto || isBack ? '4,4' : undefined,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 10,
        height: 10,
        color: isBack ? '#d4d4d4' : '#0a0a0a',
      },
    });
  }

  return { nodes: flowNodes, edges: flowEdges };
}

export function ScreenMapView({ graph, onNodeSelect, filterCategory, searchQuery }: ScreenMapViewProps) {
  const filteredNodes = useMemo(() => {
    let nodes = graph.nodes;
    if (filterCategory) {
      nodes = nodes.filter((n: any) => n.functional_category === filterCategory);
    }
    if (searchQuery) {
      const q = searchQuery.toLowerCase();
      nodes = nodes.filter(
        (n: any) =>
          (n.label || '').toLowerCase().includes(q) ||
          (n.screen_purpose || '').toLowerCase().includes(q) ||
          (n.activity || '').toLowerCase().includes(q)
      );
    }
    return nodes;
  }, [graph.nodes, filterCategory, searchQuery]);

  const filteredNodeIds = useMemo(
    () => new Set(filteredNodes.map((n: any) => n.screen_id)),
    [filteredNodes]
  );

  const filteredEdges = useMemo(
    () => graph.edges.filter((e: any) => filteredNodeIds.has(e.from) && filteredNodeIds.has(e.to)),
    [graph.edges, filteredNodeIds]
  );

  const layout = useMemo(
    () => buildLayout(filteredNodes, filteredEdges),
    [filteredNodes, filteredEdges]
  );

  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layout.edges);

  // Sync layout changes into state (e.g. when graph data changes after navigation)
  useEffect(() => { setNodes(layout.nodes); }, [layout.nodes, setNodes]);
  useEffect(() => { setEdges(layout.edges); }, [layout.edges, setEdges]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (_event, node) => onNodeSelect(node.data),
    [onNodeSelect]
  );

  return (
    <ReactFlow
      nodes={nodes}
      edges={edges}
      onNodesChange={onNodesChange}
      onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick}
      fitView
      minZoom={0.1}
      maxZoom={2}
      proOptions={{ hideAttribution: true }}
    >
      <Background color="#e5e5e5" gap={24} size={1} />
      <Controls
        showInteractive={false}
        style={{
          border: '1px solid #e5e5e5',
          borderRadius: '8px',
          overflow: 'hidden',
        }}
      />
      <MiniMap
        nodeColor={() => '#0a0a0a'}
        maskColor="rgba(0,0,0,0.04)"
        style={{
          border: '1px solid #e5e5e5',
          borderRadius: '8px',
          overflow: 'hidden',
        }}
      />
    </ReactFlow>
  );
}
