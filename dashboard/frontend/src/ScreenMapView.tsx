import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  ReactFlow, Node, Edge, Background, BackgroundVariant, Controls, MiniMap, Panel,
  useNodesState, useEdgesState, useReactFlow, MarkerType, NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import { Legend } from './graph/Legend';
import { CATEGORY_COLOR, CATEGORY_LABEL, EDGE_STYLE, edgeStyle, STATUS_STYLE, PRIO_STYLE } from './graph/colors';
import { FloatingEdge } from './graph/FloatingEdge';
import { ScreenshotNode } from './graph/ScreenshotNode';
import { CustomTextNode } from './graph/CustomTextNode';
import { NODE_SIZES, NODE_SIZE_LABEL, NodeSize } from './graph/nodeSize';
import { displayLabel, subLabel } from './graph/displayLabel';
import { IconSearch, IconClose, IconDownload, IconWand, IconRoute } from './app/icons';

interface ScreenMapViewProps {
  graph: { entry_node: string; nodes: any[]; edges: any[] };
  onNodeSelect: (node: any) => void;
  tourId: string;
  appName?: string;
  selectedNodeId?: string;
  selectedEdgeData?: any | null;
  onSelectedEdgeDataChange?: (data: any | null) => void;
}

/** 외부(인스펙터 등)에서 노드를 고르면 뷰포트를 그리로 옮긴다. */
function PanToSelected({ selectedId, nodes }: { selectedId?: string; nodes: Node[] }) {
  const { setCenter } = useReactFlow();
  const last = useRef('');
  useEffect(() => {
    if (!selectedId || last.current === selectedId) return;
    const n = nodes.find((x) => x.id === selectedId);
    if (!n?.position) return;
    const w = (n as any).width || (n as any).measured?.width || 220;
    const h = (n as any).height || (n as any).measured?.height || 80;
    last.current = selectedId;
    setCenter(n.position.x + w / 2, n.position.y + h / 2, { zoom: 1.1, duration: 600 });
  }, [selectedId, nodes, setCenter]);
  return null;
}

function buildLayout(nodes: any[], edges: any[], showScreenshots: boolean, showEdgeLabels: boolean,
  onOpenEdge: (d: any) => void, tourId: string, spacingScale: number, nodeSize: NodeSize, rankdir: 'TB' | 'LR') {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  const dims = NODE_SIZES[nodeSize];
  const nodeW = showScreenshots ? dims.screenshot.w : dims.text.w;
  const nodeH = showScreenshots ? dims.screenshot.sectionH : dims.text.h;
  g.setGraph({ rankdir, nodesep: dims.dagre.nodesep * spacingScale, ranksep: dims.dagre.ranksep * spacingScale });

  // 알림/위젯으로만 들어오는 화면은 맨 앞 랭크에 진입 노드와 나란히
  const incomingByKind: Record<string, string[]> = {};
  for (const e of edges) {
    const k = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
    (incomingByKind[e.to] ||= []).push(k);
  }
  const isSystemOnly = (nid: string) => { const ks = incomingByKind[nid] || []; return ks.length > 0 && ks.every((k) => k === 'pending_intent'); };

  for (const n of nodes) {
    const opts: any = { width: nodeW, height: nodeH };
    if (n.screen_id === 'system:external_entry' || isSystemOnly(n.screen_id)) opts.rank = 'min';
    g.setNode(n.screen_id, opts);
  }
  for (const e of edges) g.setEdge(e.from, e.to);
  dagre.layout(g);

  const entryId = (nodes as any).entry_node_id || nodes[0]?.screen_id;
  const flowNodes: Node[] = [];
  for (const n of nodes) {
    const pos = g.node(n.screen_id);
    const color = CATEGORY_COLOR[n.functional_category] || CATEGORY_COLOR.other;
    const status: string = n.status || 'resolved';
    const isSystem = n.screen_id === 'system:external_entry';
    const isSystemTriggered = isSystemOnly(n.screen_id);
    const hasShot = !!n.screenshot_ref;
    const hasWidgets = (n.widgets?.length ?? 0) > 0;
    const prio = n.capture_priority || '';
    const st = STATUS_STYLE[status];
    const tooltip = [
      `${n.activity || n.screen_id}`,
      n.fragment ? `Fragment: ${String(n.fragment).split('.').pop()}` : null,
      `분류: ${CATEGORY_LABEL[n.functional_category] || n.functional_category || '기타'}`,
      st ? `상태: ${st.label} — ${st.desc}` : null,
      prio && PRIO_STYLE[prio] ? `우선순위 ${prio}: ${PRIO_STYLE[prio].label}` : null,
      status === 'probed' && !hasShot ? '⚡ 파이프라인에서 UI 를 못 잡음 — 에이전트가 도달하면 즉시 캡처' : null,
      hasShot && !hasWidgets ? '스크린샷은 있으나 UI 요소 목록이 없음' : null,
      isSystemTriggered ? '⚡ 알림/위젯/AlarmManager 로만 진입' : null,
    ].filter(Boolean).join('\n');
    const isFragment = n.node_type === 'fragment';
    const isActivity = n.node_type === 'activity' || (!n.node_type && !isSystem);
    const useThumbnail = showScreenshots && hasShot;
    flowNodes.push({
      id: n.screen_id,
      data: {
        label: displayLabel(n), subLabel: subLabel(n, displayLabel(n)), rankdir,
        screen_id: n.screen_id, isSystem, isSystemTriggered, isFragment, isActivity,
        prioOpacity: prio === 'B' ? 0.55 : status === 'declared' ? 0.6 : 1,
        showScreenshots, tourId, color, isEntry: n.screen_id === entryId, status, tooltip, nodeSize,
        ...n,
      },
      position: { x: (pos?.x || 0) - nodeW / 2, y: (pos?.y || 0) - nodeH / 2 },
      type: useThumbnail ? 'screenshotNode' : 'customTextNode',
    });
  }

  function friendlyLabel(e: any, kind: string, targetLabel?: string): string {
    const action: string = e.trigger_action || '';
    const elem: string = e.trigger_widget || '';
    if (kind === 'contains' || kind === 'global') return '';
    if (kind === 'back' || action === 'press_back') return '뒤로';
    if (kind === 'launcher') return '런처';
    if (kind === 'intent_filter') return elem ? `딥링크 ${elem.split('/').pop()?.slice(0, 14) || ''}` : '딥링크';
    if (kind === 'overlay') return '오버레이';
    if (elem.startsWith('reflection/')) return '정적 추론';
    if (elem.startsWith('two_hop_')) return '헬퍼 경유';
    if (elem === 'fragment_transaction') return '';
    if (action === 'intent' && !elem) return targetLabel ? `→ ${targetLabel}` : '인텐트';
    if (action === 'intent' && elem) { const tail = elem.split('.').pop() || elem; return tail.length > 18 ? tail.slice(0, 18) + '…' : tail; }
    if (elem) { const t = readableTriggerLabel(elem) || elem; return t.length > 18 ? t.slice(0, 18) + '…' : t; }
    return action || '';
  }

  const nodeLabelMap: Record<string, string> = {};
  const nodeMap: Record<string, any> = {};
  for (const n of nodes) { nodeLabelMap[n.screen_id] = displayLabel(n); nodeMap[n.screen_id] = n; }

  const flowEdges: Edge[] = [];
  for (const e of edges) {
    const kind: string = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
    const confidence: string = e.confidence || (e.source === 'walk' ? 'observed' : 'static_intent');
    const s = edgeStyle(kind);
    const actionLabel = friendlyLabel(e, kind, nodeLabelMap[e.to]);
    const label = showEdgeLabels && s.showLabel ? actionLabel : '';
    const edgeId = e.edge_id || `${e.from}-${e.to}`;
    const baseOpacity = showEdgeLabels ? 0.85 : (kind === 'navigate' ? 0.5 : 0.6);
    const opacity = confidence === 'static_intent' && kind !== 'two_hop' && kind !== 'navigate' ? Math.min(baseOpacity, 0.45) : baseOpacity;
    const boost = showEdgeLabels && (e.frequency || 0) >= 3 ? 0.5 : 0;
    flowEdges.push({
      id: edgeId, source: e.from, target: e.to, type: 'floating',
      data: { edgeId, rankdir, kind, confidence, label, actionLabel, raw: e, sourceNode: nodeMap[e.from], targetNode: nodeMap[e.to],
        sourceLabel: nodeLabelMap[e.from] || e.from, targetLabel: nodeLabelMap[e.to] || e.to, onOpenEdge },
      style: { stroke: s.stroke, strokeWidth: s.width + boost, strokeDasharray: s.dash, opacity },
      markerEnd: { type: MarkerType.ArrowClosed, width: 12, height: 12, color: s.stroke },
    });
  }
  return { nodes: flowNodes, edges: flowEdges };
}

const nodeTypes = { screenshotNode: ScreenshotNode, customTextNode: CustomTextNode };
const edgeTypes = { floating: FloatingEdge };
type ActionBounds = { left: number; top: number; right: number; bottom: number; label?: string };

function parseBoundsValue(value: any): ActionBounds | null {
  if (!value) return null;
  if (Array.isArray(value) && value.length >= 4) {
    const [left, top, right, bottom] = value.map(Number);
    if ([left, top, right, bottom].every(Number.isFinite) && right > left && bottom > top) return { left, top, right, bottom };
  }
  if (typeof value === 'object') {
    const left = Number(value.left ?? value.x1 ?? value.x);
    const top = Number(value.top ?? value.y1 ?? value.y);
    const right = Number(value.right ?? value.x2 ?? (Number.isFinite(left) ? left + Number(value.width) : NaN));
    const bottom = Number(value.bottom ?? value.y2 ?? (Number.isFinite(top) ? top + Number(value.height) : NaN));
    if ([left, top, right, bottom].every(Number.isFinite) && right > left && bottom > top) return { left, top, right, bottom, label: value.label || value.text || value.content_desc };
  }
  if (typeof value !== 'string') return null;
  const m = value.match(/^(.*?)@?\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]/);
  if (!m) return null;
  const [, rawLabel, x1, y1, x2, y2] = m;
  const left = Number(x1), top = Number(y1), right = Number(x2), bottom = Number(y2);
  if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) return null;
  const label = rawLabel.replace(/^(click|tap|press)\s+/i, '').trim();
  return { left, top, right, bottom, label: label || undefined };
}
function extractActionBounds(raw: any): ActionBounds | null {
  return parseBoundsValue(raw?.trigger_bounds) || parseBoundsValue(raw?.widget_bounds) || parseBoundsValue(raw?.bounds) || parseBoundsValue(raw?.bbox) || parseBoundsValue(raw?.trigger_widget);
}
export function readableTriggerLabel(value: any): string {
  if (typeof value !== 'string' || !value.trim()) return '';
  const parsed = parseBoundsValue(value);
  if (parsed?.label) return parsed.label;
  return value.replace(/@\[[^\]]+\]\[[^\]]+\]/, '').replace(/^(click|tap|press)\s+/i, '').trim();
}
function buildActionText(raw: any, fallback: string): string {
  const action = raw?.trigger_action || '';
  const label = readableTriggerLabel(raw?.trigger_widget);
  if (action === 'click') return label ? `탭: ${label}` : '탭';
  if (action === 'press_back') return '뒤로가기';
  if (action === 'intent') return label ? `인텐트: ${label}` : '인텐트';
  if (action === 'contains') return '화면 포함 관계';
  return label || fallback || action || '동작';
}

export function ScreenMapView({ graph, onNodeSelect, tourId, appName, selectedNodeId, selectedEdgeData: externalEdgeData, onSelectedEdgeDataChange }: ScreenMapViewProps) {
  const [showScreenshots, setShowScreenshots] = useState(true);
  const [rankdir, setRankdir] = useState<'TB' | 'LR'>('LR');
  const [showEdgeLabels, setShowEdgeLabels] = useState(false);
  const [edgeFiltersOpen, setEdgeFiltersOpen] = useState(false);
  const [spacingScale, setSpacingScale] = useState(1.0);
  const [nodeSize, setNodeSize] = useState<NodeSize>('md');
  const [searchQuery, setSearchQuery] = useState('');
  const [filterCategory, setFilterCategory] = useState('');
  const [internalEdgeData, setInternalEdgeData] = useState<any | null>(null);
  const isControlled = externalEdgeData !== undefined;
  const selectedEdgeData = isControlled ? externalEdgeData : internalEdgeData;
  const setSelectedEdgeData = useCallback((data: any | null) => {
    if (onSelectedEdgeDataChange) onSelectedEdgeDataChange(data);
    if (!isControlled) setInternalEdgeData(data);
  }, [onSelectedEdgeDataChange, isControlled]);
  const [pathSource, setPathSource] = useState<string | null>(null);
  const [highlightedPath, setHighlightedPath] = useState<{ nodes: Set<string>; edges: Set<string> } | null>(null);
  const [planFocus, setPlanFocus] = useState(false);
  const [pathInfo, setPathInfo] = useState('');
  const [plannerOpen, setPlannerOpen] = useState(false);
  const [taskInput, setTaskInput] = useState('');
  const [planBusy, setPlanBusy] = useState(false);
  const [planResult, setPlanResult] = useState<any | null>(null);
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(new Set(['contains', 'static_ref', 'global']));
  // 보이는 간선이 하나도 없고 스크린샷도 없는 화면(선언만 된 Activity 등)은 기본으로 접는다 —
  // 그래프 왼쪽에 세로로 쌓여 실제 흐름을 가리기 때문. 툴바에서 토글.
  const [showIsolated, setShowIsolated] = useState(false);

  const openEdgeDetail = useCallback((edgeData: any) => setSelectedEdgeData(edgeData), [setSelectedEdgeData]);

  const highlightNodePath = useCallback((pathNodes: string[]) => {
    const nodeSet = new Set<string>(pathNodes);
    const edgeSet = new Set<string>();
    for (let i = 0; i < pathNodes.length - 1; i++) {
      for (const e of graph.edges) if (e.from === pathNodes[i] && e.to === pathNodes[i + 1]) edgeSet.add(e.edge_id || `${e.from}-${e.to}`);
    }
    setHighlightedPath({ nodes: nodeSet, edges: edgeSet });
  }, [graph.edges]);

  const askTask = useCallback(async () => {
    const t = taskInput.trim();
    if (!t) return;
    setPlanBusy(true); setPlanResult(null); setPlanFocus(false);
    try {
      const res = await fetch(`/api/tours/${tourId}/plan?task=${encodeURIComponent(t)}`, { method: 'POST' });
      if (!res.ok) { const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` })); setPlanResult({ error: err.detail || `HTTP ${res.status}` }); setHighlightedPath(null); return; }
      const data = await res.json();
      setPlanResult(data);
      const pathNodes = Array.isArray(data.path_nodes) ? data.path_nodes : [];
      if (pathNodes.length > 1) { highlightNodePath(pathNodes); setPlanFocus(true); } else setHighlightedPath(null);
    } catch (e: any) { setPlanResult({ error: String(e?.message || e) }); setHighlightedPath(null); }
    finally { setPlanBusy(false); }
  }, [taskInput, tourId, highlightNodePath]);

  const findPath = useCallback(async (source: string, target: string) => {
    try {
      const res = await fetch(`/api/tours/${tourId}/path?source=${source}&target=${target}`);
      if (!res.ok) { setPathInfo('경로 없음'); return; }
      const data = await res.json();
      if (data.best?.path) { highlightNodePath(data.best.path); setPlanFocus(false); setPathInfo(`${data.best.hop_count}단계 · 비용 ${data.best.total_cost}`); }
    } catch { setPathInfo('경로 오류'); }
  }, [tourId, highlightNodePath]);

  const categories = useMemo(() => [...new Set(graph.nodes.map((n: any) => n.functional_category || 'other'))].sort() as string[], [graph.nodes]);

  const isolatedIds = useMemo(() => {
    const degree: Record<string, number> = {};
    for (const e of graph.edges) {
      const kind = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
      if (hiddenKinds.has(kind)) continue;
      degree[e.from] = (degree[e.from] || 0) + 1;
      degree[e.to] = (degree[e.to] || 0) + 1;
    }
    return new Set<string>(graph.nodes.filter((n: any) => !degree[n.screen_id] && !n.screenshot_ref && n.screen_id !== graph.entry_node).map((n: any) => n.screen_id));
  }, [graph.nodes, graph.edges, graph.entry_node, hiddenKinds]);

  const filteredNodes = useMemo(() => {
    let nodes = graph.nodes;
    if (!showIsolated && !searchQuery) nodes = nodes.filter((n: any) => !isolatedIds.has(n.screen_id));
    if (planFocus && highlightedPath) nodes = nodes.filter((n: any) => highlightedPath.nodes.has(n.screen_id));
    else if (filterCategory) nodes = nodes.filter((n: any) => (n.functional_category || 'other') === filterCategory);
    if (!planFocus && searchQuery) {
      const q = searchQuery.toLowerCase();
      nodes = nodes.filter((n: any) => (displayLabel(n) || '').toLowerCase().includes(q) || (n.screen_purpose || '').toLowerCase().includes(q) || (n.activity || '').toLowerCase().includes(q));
    }
    return nodes;
  }, [graph.nodes, filterCategory, searchQuery, planFocus, highlightedPath, showIsolated, isolatedIds]);
  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map((n: any) => n.screen_id)), [filteredNodes]);
  const filteredEdges = useMemo(() => graph.edges.filter((e: any) => {
    if (!filteredNodeIds.has(e.from) || !filteredNodeIds.has(e.to)) return false;
    const edgeId = e.edge_id || `${e.from}-${e.to}`;
    if (planFocus && highlightedPath) return highlightedPath.edges.has(edgeId);
    return !hiddenKinds.has(e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate'));
  }), [graph.edges, filteredNodeIds, hiddenKinds, planFocus, highlightedPath]);

  const edgeKindCounts = useMemo(() => {
    const c: Record<string, number> = {};
    const list = planFocus && highlightedPath ? graph.edges.filter((e: any) => highlightedPath.edges.has(e.edge_id || `${e.from}-${e.to}`)) : graph.edges;
    for (const e of list) {
      if (!filteredNodeIds.has(e.from) || !filteredNodeIds.has(e.to)) continue;
      const k = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
      c[k] = (c[k] || 0) + 1;
    }
    return c;
  }, [graph.edges, filteredNodeIds, planFocus, highlightedPath]);
  const visibleEdgeCount = useMemo(() => planFocus && highlightedPath ? Object.values(edgeKindCounts).reduce((a, b) => a + b, 0)
    : Object.entries(edgeKindCounts).reduce((sum, [k, c]) => sum + (hiddenKinds.has(k) ? 0 : c), 0), [edgeKindCounts, hiddenKinds, planFocus, highlightedPath]);
  const totalEdgeCount = useMemo(() => Object.values(edgeKindCounts).reduce((a, b) => a + b, 0), [edgeKindCounts]);
  const categoryCounts = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of graph.nodes) { const c = n.functional_category || 'other'; counts[c] = (counts[c] || 0) + 1; }
    return counts;
  }, [graph.nodes]);

  const layout = useMemo(() => {
    const tagged: any = filteredNodes.slice();
    tagged.entry_node_id = graph.entry_node;
    return buildLayout(tagged, filteredEdges, showScreenshots, showEdgeLabels, openEdgeDetail, tourId, spacingScale, nodeSize, rankdir);
  }, [filteredNodes, filteredEdges, showScreenshots, showEdgeLabels, openEdgeDetail, tourId, graph.entry_node, spacingScale, nodeSize, rankdir]);

  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layout.edges);
  const radiusFor = (n: Node) => (n.type === 'screenshotNode' ? 14 : 12);

  const styledNodes = useMemo(() => {
    if (highlightedPath) {
      return layout.nodes.map((n) => highlightedPath.nodes.has(n.id)
        ? { ...n, style: { ...n.style, boxShadow: '0 0 0 3px var(--wf-amber)', borderRadius: `${radiusFor(n)}px`, opacity: 1 } }
        : { ...n, style: { ...n.style, opacity: 0.25 } });
    }
    if (selectedNodeId) {
      const connected = new Set<string>([selectedNodeId]);
      layout.edges.forEach((e) => { if (e.source === selectedNodeId) connected.add(e.target); if (e.target === selectedNodeId) connected.add(e.source); });
      return layout.nodes.map((n) => {
        if (!connected.has(n.id)) return { ...n, style: { ...n.style, opacity: 0.25 } };
        const sel = n.id === selectedNodeId;
        return { ...n, style: { ...n.style, opacity: 1, boxShadow: sel ? '0 0 0 3px var(--wf-accent)' : '0 0 0 2px rgba(31,111,91,0.45)', borderRadius: `${radiusFor(n)}px` } };
      });
    }
    return layout.nodes;
  }, [layout.nodes, layout.edges, highlightedPath, selectedNodeId]);

  const styledEdges = useMemo(() => {
    const selId = selectedEdgeData?.edgeId || '';
    if (highlightedPath) {
      return layout.edges.map((e) => highlightedPath.edges.has(e.id)
        ? { ...e, style: { ...e.style, strokeWidth: ((e.style?.strokeWidth as number) || 2) + 2, opacity: 1 }, animated: true }
        : { ...e, style: { ...e.style, opacity: 0.1 } });
    }
    if (selectedNodeId) {
      return layout.edges.map((e) => (e.source === selectedNodeId || e.target === selectedNodeId)
        ? { ...e, style: { ...e.style, strokeWidth: ((e.style?.strokeWidth as number) || 2) + 1.5, opacity: 1 } }
        : { ...e, style: { ...e.style, opacity: 0.1 } });
    }
    return layout.edges.map((e) => e.id !== selId ? e
      : { ...e, style: { ...e.style, stroke: 'var(--wf-accent)', strokeWidth: 3, opacity: 1 }, markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: '#1F6F5B' } });
  }, [layout.edges, highlightedPath, selectedEdgeData, selectedNodeId]);

  useEffect(() => { setNodes(styledNodes); }, [styledNodes, setNodes]);
  useEffect(() => { setEdges(styledEdges); }, [styledEdges, setEdges]);

  const onNodeClick: NodeMouseHandler = useCallback((event, node) => {
    if ((event as unknown as MouseEvent).shiftKey && pathSource) { findPath(pathSource, node.id); return; }
    if (selectedNodeId === node.id) { onNodeSelect(null); setPathSource(null); return; }
    onNodeSelect(node.data); setSelectedEdgeData(null); setPathSource(node.id);
    setHighlightedPath(null); setPlanFocus(false); setPathInfo('Shift+클릭으로 경로 찾기');
  }, [onNodeSelect, pathSource, findPath, selectedNodeId, setSelectedEdgeData]);

  const downloadMap = useCallback(() => {
    const blob = new Blob([JSON.stringify({ nodes: graph.nodes, edges: graph.edges }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    const safe = (appName || 'app').toString().replace(/[^A-Za-z0-9._-]/g, '_') || 'app';
    a.download = `${safe}_screen_map.json`; a.click();
  }, [graph, appName]);

  const clearPath = () => { setHighlightedPath(null); setPlanFocus(false); setPathSource(null); setPathInfo(''); };

  return (
    <div className="wf-canvas" style={{ position: 'absolute', inset: 0 }}>
      {/* 좌상단: 검색·분류 */}
      <div style={{ position: 'absolute', top: 14, left: 14, zIndex: 20, display: 'flex', flexDirection: 'column', gap: 8, width: 300 }}>
        <div className="wf-floating" style={{ display: 'flex', alignItems: 'center', gap: 6, padding: '4px 6px 4px 10px' }}>
          <IconSearch size={15} />
          <input value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="화면 이름·설명·Activity 검색" style={{ flex: 1, border: 'none', background: 'transparent', outline: 'none', fontSize: 12.5, height: 28 }} />
          {searchQuery && <button className="wf-btn ghost icon sm" onClick={() => setSearchQuery('')}><IconClose size={13} /></button>}
          <select className="wf-select" value={filterCategory} onChange={(e) => setFilterCategory(e.target.value)} style={{ width: 96, height: 28, fontSize: 12, padding: '0 8px' }}>
            <option value="">전체 분류</option>
            {categories.map((c) => <option key={c} value={c}>{CATEGORY_LABEL[c] || c} {categoryCounts[c] || 0}</option>)}
          </select>
        </div>
        {!plannerOpen ? (
          <button className="wf-btn wf-floating" style={{ alignSelf: 'flex-start' }} onClick={() => setPlannerOpen(true)}><IconWand size={14} /> 경로 묻기</button>
        ) : (
          <div className="wf-floating wf-fade-in" style={{ padding: 12 }}>
            <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', marginBottom: 8 }}>
              <span className="wf-eyebrow">경로 묻기</span>
              <button className="wf-btn ghost icon sm" onClick={() => setPlannerOpen(false)}><IconClose size={13} /></button>
            </div>
            <div style={{ display: 'flex', gap: 6 }}>
              <input className="wf-input" style={{ height: 32 }} value={taskInput} placeholder='예: "결제 화면까지 가는 길"' onChange={(e) => setTaskInput(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter' && !planBusy) askTask(); }} disabled={planBusy} />
              <button className="wf-btn sm primary" onClick={askTask} disabled={planBusy || !taskInput.trim()}>{planBusy ? '…' : '찾기'}</button>
            </div>
            {planResult && (
              <div style={{ marginTop: 10, fontSize: 12, maxHeight: 240, overflowY: 'auto' }}>
                {planResult.error ? <div className="wf-callout danger" style={{ padding: '6px 10px' }}>{planResult.error}</div> : (
                  <>
                    {Array.isArray(planResult.steps) && planResult.steps.length > 0 ? (
                      <ol style={{ paddingLeft: 18, display: 'flex', flexDirection: 'column', gap: 6 }}>
                        {planResult.steps.map((s: any, i: number) => (
                          <li key={i}>
                            <span className="wf-mono wf-muted">{s.from} → {s.to}</span>
                            {s.trigger && <span style={{ color: 'var(--wf-accent)' }}> [{s.trigger}]</span>}
                            {s.why && <div className="wf-faint" style={{ fontSize: 11 }}>{s.why}</div>}
                          </li>
                        ))}
                      </ol>
                    ) : <div className="wf-faint">경로를 찾지 못했습니다.</div>}
                    {planResult.notes && <div className="wf-callout amber" style={{ marginTop: 8, padding: '6px 10px', fontSize: 11 }}>{planResult.notes}</div>}
                  </>
                )}
              </div>
            )}
          </div>
        )}
      </div>

      {selectedEdgeData && (
        <EdgeDetailPanel edgeData={selectedEdgeData} tourId={tourId} onClose={() => setSelectedEdgeData(null)}
          onSelectNode={(node: any) => { if (!node) return; onNodeSelect(node); setSelectedEdgeData(null); }} />
      )}

      <ReactFlow
        nodes={nodes} edges={edges} onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
        onNodeClick={onNodeClick} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
        onPaneClick={() => { setSelectedEdgeData(null); onNodeSelect(null); }}
        fitView fitViewOptions={{ minZoom: 0.15, padding: 0.08 }} minZoom={0.1} maxZoom={3}
        proOptions={{ hideAttribution: true }}
      >
        <PanToSelected selectedId={selectedNodeId} nodes={nodes} />
        <Background variant={BackgroundVariant.Dots} gap={22} size={1.2} color="#CDBFA5" />
        <Controls showInteractive={false} position="bottom-left" />
        <MiniMap position="bottom-left" pannable zoomable style={{ marginLeft: 50, width: 170, height: 120 }}
          nodeColor={(n) => CATEGORY_COLOR[(n.data as any)?.functional_category || 'other'] || CATEGORY_COLOR.other}
          maskColor="rgba(60,45,20,0.08)" />

        <Panel position="top-right">
          <div className="wf-floating wf-toolbar" style={{ maxWidth: 'min(62vw, 900px)', justifyContent: 'flex-end' }}>
            <div className="wf-seg">
              <button className={rankdir === 'LR' ? 'on' : ''} onClick={() => setRankdir('LR')}>가로</button>
              <button className={rankdir === 'TB' ? 'on' : ''} onClick={() => setRankdir('TB')}>세로</button>
            </div>
            <div className="wf-seg">
              {(['sm', 'md', 'lg'] as NodeSize[]).map((s) => <button key={s} className={nodeSize === s ? 'on' : ''} onClick={() => setNodeSize(s)}>{NODE_SIZE_LABEL[s]}</button>)}
            </div>
            <label style={{ display: 'inline-flex', alignItems: 'center', gap: 6 }}>
              <span className="lbl">간격</span>
              <input className="wf-range" type="range" min="0.5" max="2.5" step="0.1" value={spacingScale} onChange={(e) => setSpacingScale(parseFloat(e.target.value))} style={{ width: 70 }} />
            </label>
            <button className={`wf-btn sm${showScreenshots ? ' on' : ''}`} onClick={() => setShowScreenshots(!showScreenshots)}>스크린샷</button>
            <button className={`wf-btn sm${showEdgeLabels ? ' on' : ''}`} onClick={() => setShowEdgeLabels(!showEdgeLabels)}>동작 라벨</button>
            <button className={`wf-btn sm${edgeFiltersOpen ? ' on' : ''}`} onClick={() => setEdgeFiltersOpen(!edgeFiltersOpen)}>간선 {visibleEdgeCount}/{totalEdgeCount}</button>
            {isolatedIds.size > 0 && (
              <button className={`wf-btn sm${showIsolated ? ' on' : ''}`} onClick={() => setShowIsolated(!showIsolated)} title="보이는 간선이 없고 스크린샷도 없는 화면 (선언만 된 Activity 등)">미연결 {isolatedIds.size}</button>
            )}
            {highlightedPath && (
              <>
                <button className={`wf-btn sm${planFocus ? ' on' : ''}`} onClick={() => setPlanFocus(!planFocus)}><IconRoute size={13} /> 경로만</button>
                <button className="wf-btn sm quiet" onClick={clearPath}>경로 지우기</button>
              </>
            )}
            {pathInfo && <span className="wf-chip amber">{pathInfo}</span>}
            <button className="wf-btn sm quiet icon" onClick={downloadMap} title="지도 JSON 내려받기"><IconDownload size={14} /></button>
          </div>
          {edgeFiltersOpen && Object.keys(edgeKindCounts).length > 0 && (
            <div className="wf-floating wf-toolbar wf-fade-in" style={{ marginTop: 6, justifyContent: 'flex-end' }}>
              {Object.entries(edgeKindCounts).sort((a, b) => b[1] - a[1]).map(([kind, count]) => {
                const hidden = hiddenKinds.has(kind);
                const s = EDGE_STYLE[kind];
                return (
                  <button key={kind} className={`wf-chip clickable ${hidden ? 'outline' : 'ink'}`} style={hidden ? { textDecoration: 'line-through' } : undefined}
                    onClick={() => { const next = new Set(hiddenKinds); if (hidden) next.delete(kind); else next.add(kind); setHiddenKinds(next); }}
                    title={s?.desc || kind}>
                    <span style={{ width: 10, height: 3, background: s?.stroke || 'currentColor', borderRadius: 2 }} />
                    {s?.label || kind} {count}
                  </button>
                );
              })}
            </div>
          )}
        </Panel>

        <Panel position="bottom-center">
          <div className="wf-floating" style={{ display: 'flex', gap: 10, flexWrap: 'wrap', padding: '6px 12px', fontSize: 11 }}>
            {Object.entries(categoryCounts).sort((a, b) => b[1] - a[1]).slice(0, 10).map(([cat, count]) => (
              <button key={cat} onClick={() => setFilterCategory(filterCategory === cat ? '' : cat)} style={{ display: 'flex', alignItems: 'center', gap: 5, fontWeight: filterCategory === cat ? 700 : 500, color: 'var(--wf-ink-2)' }}>
                <span style={{ width: 9, height: 9, borderRadius: 3, background: CATEGORY_COLOR[cat] || CATEGORY_COLOR.other }} />
                {CATEGORY_LABEL[cat] || cat} <span className="wf-mono wf-faint">{count}</span>
              </button>
            ))}
          </div>
        </Panel>
        <Legend />
      </ReactFlow>
    </div>
  );
}

function EdgeDetailPanel({ edgeData, tourId, onClose, onSelectNode }: { edgeData: any; tourId: string; onClose: () => void; onSelectNode: (node: any) => void }) {
  const raw = edgeData.raw || {};
  const kind = edgeData.kind || raw.kind || 'navigate';
  const action = buildActionText(raw, edgeData.actionLabel || kind);
  const bounds = extractActionBounds(raw);
  const detail = [raw.trigger_action ? `action=${raw.trigger_action}` : null, raw.trigger_widget ? `element=${raw.trigger_widget}` : null].filter(Boolean).join(' · ');
  const transitionShot = edgeData.edgeId ? `/api/tours/${tourId}/transition-screenshot/${edgeData.edgeId}` : '';
  const es = edgeStyle(kind);
  return (
    <div className="wf-floating wf-fade-in" style={{ position: 'absolute', top: 66, right: 14, zIndex: 36, width: 440, maxWidth: 'calc(100% - 28px)', overflow: 'hidden' }}>
      <div style={{ display: 'flex', alignItems: 'center', gap: 10, padding: '10px 12px', borderBottom: '1px solid var(--wf-border)' }}>
        <div style={{ flex: 1, minWidth: 0 }}>
          <div className="wf-eyebrow">화면 전환</div>
          <div className="wf-ellipsis" style={{ fontSize: 14, fontWeight: 700, marginTop: 2 }}>{edgeData.sourceLabel} → {edgeData.targetLabel}</div>
        </div>
        <button className="wf-btn ghost icon sm" onClick={onClose}><IconClose size={14} /></button>
      </div>
      <div style={{ padding: 12 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
          <ScreenshotPreview tourId={tourId} node={edgeData.sourceNode} label="출발" overlay={action} preferredUrl={transitionShot} highlightBounds={bounds} highlightLabel={action} onClick={() => onSelectNode(edgeData.sourceNode)} />
          <ScreenshotPreview tourId={tourId} node={edgeData.targetNode} label="도착" overlay="이 화면으로" onClick={() => onSelectNode(edgeData.targetNode)} />
        </div>
        <div className="wf-callout plain" style={{ fontSize: 12.5 }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap', marginBottom: 8 }}>
            <span className="wf-chip" style={{ background: es.stroke, color: '#FFFCF5' }}>{es.label}</span>
            <span className="wf-chip outline mono">{edgeData.confidence === 'observed' ? '탐색에서 관찰' : edgeData.confidence || 'unknown'}</span>
          </div>
          <div><b>동작:</b> {action}</div>
          {detail && <div className="wf-mono wf-faint" style={{ marginTop: 2 }}>{detail}</div>}
          <div className="wf-muted" style={{ marginTop: 6 }}>{es.desc}</div>
          {raw.condition && <div className="wf-faint" style={{ marginTop: 6 }}>조건: {String(raw.condition)}</div>}
          {raw.outcome && <div style={{ marginTop: 6, color: 'var(--wf-accent)' }}>결과: {String(raw.outcome)}</div>}
          {!bounds && <div className="wf-faint" style={{ fontSize: 11, marginTop: 8 }}>이 전환에는 버튼 좌표가 없어 위치 박스 대신 동작 라벨만 표시합니다.</div>}
        </div>
      </div>
    </div>
  );
}

function ScreenshotPreview({ tourId, node, label, overlay, preferredUrl, highlightBounds, highlightLabel, onClick }: {
  tourId: string; node: any; label: string; overlay: string; preferredUrl?: string; highlightBounds?: ActionBounds | null; highlightLabel?: string; onClick: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const mediaRef = useRef<HTMLDivElement | null>(null);
  const [urlIndex, setUrlIndex] = useState(0);
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const [mediaSize, setMediaSize] = useState({ width: 0, height: 0 });
  const nodeUrl = node?.screenshot_ref && node?.screen_id ? `/api/tours/${tourId}/screenshot/${node.screen_id}` : '';
  const urls = useMemo(() => Array.from(new Set([preferredUrl, nodeUrl].filter(Boolean) as string[])), [preferredUrl, nodeUrl]);
  const urlKey = urls.join('|');
  const url = urls[urlIndex] || '';
  const title = node ? displayLabel(node) : '알 수 없음';

  useEffect(() => { setFailed(false); setUrlIndex(0); setNaturalSize({ width: 0, height: 0 }); }, [urlKey]);
  useEffect(() => {
    const el = mediaRef.current; if (!el) return;
    const update = () => { const r = el.getBoundingClientRect(); setMediaSize({ width: r.width, height: r.height }); };
    update();
    if (typeof ResizeObserver === 'undefined') { window.addEventListener('resize', update); return () => window.removeEventListener('resize', update); }
    const ro = new ResizeObserver(update); ro.observe(el); return () => ro.disconnect();
  }, []);

  const box = useMemo(() => {
    if (!highlightBounds || !naturalSize.width || !naturalSize.height || !mediaSize.width || !mediaSize.height) return null;
    const likelyPhone = naturalSize.width / naturalSize.height > 0.35 && naturalSize.width / naturalSize.height < 0.65;
    const normalized = Math.max(highlightBounds.right, highlightBounds.bottom) <= 1;
    const cw = normalized ? 1 : Math.max(likelyPhone ? 1080 : naturalSize.width, highlightBounds.right);
    const ch = normalized ? 1 : Math.max(likelyPhone ? 2400 : naturalSize.height, highlightBounds.bottom);
    const ia = naturalSize.width / naturalSize.height, ma = mediaSize.width / mediaSize.height;
    const dw = ia > ma ? mediaSize.width : mediaSize.height * ia;
    const dh = ia > ma ? mediaSize.width / ia : mediaSize.height;
    const ox = (mediaSize.width - dw) / 2, oy = (mediaSize.height - dh) / 2;
    const l = Math.max(ox, Math.min(ox + dw, ox + (highlightBounds.left / cw) * dw));
    const t = Math.max(oy, Math.min(oy + dh, oy + (highlightBounds.top / ch) * dh));
    const r = Math.max(ox, Math.min(ox + dw, ox + (highlightBounds.right / cw) * dw));
    const b = Math.max(oy, Math.min(oy + dh, oy + (highlightBounds.bottom / ch) * dh));
    if (r <= l || b <= t) return null;
    return { left: l, top: t, width: r - l, height: b - t, labelLeft: Math.max(6, Math.min(l, mediaSize.width - 150)), labelTop: Math.max(6, t - 26) };
  }, [highlightBounds, naturalSize, mediaSize]);

  return (
    <button type="button" onClick={onClick} title={title} style={{ position: 'relative', minHeight: 220, border: '1px solid var(--wf-border)', borderRadius: 10, padding: 0, overflow: 'hidden', background: 'var(--wf-surface-2)', cursor: node ? 'pointer' : 'default', textAlign: 'left' }}>
      <div ref={mediaRef} style={{ position: 'relative', height: 220 }}>
        {url && !failed ? (
          <img src={url} alt={title} onLoad={(e) => setNaturalSize({ width: e.currentTarget.naturalWidth, height: e.currentTarget.naturalHeight })}
            onError={() => { if (urlIndex < urls.length - 1) setUrlIndex(urlIndex + 1); else setFailed(true); }}
            style={{ width: '100%', height: '100%', objectFit: 'contain', display: 'block', background: '#111' }} />
        ) : (
          <div style={{ height: 220, display: 'grid', placeItems: 'center', color: 'var(--wf-ink-3)', fontSize: 12, padding: 12, textAlign: 'center' }}>{title}</div>
        )}
        {box && (
          <>
            <div style={{ position: 'absolute', left: box.left, top: box.top, width: box.width, height: box.height, border: '2px solid var(--wf-amber)', boxShadow: '0 0 0 2px rgba(217,142,4,0.25), 0 0 16px rgba(217,142,4,0.6)', borderRadius: 5, background: 'rgba(217,142,4,0.12)', pointerEvents: 'none' }} />
            <span className="wf-ellipsis" style={{ position: 'absolute', left: box.labelLeft, top: box.labelTop, maxWidth: 'calc(100% - 12px)', padding: '3px 7px', background: 'var(--wf-amber)', color: '#1C1A17', borderRadius: 5, fontSize: 10, fontWeight: 800, pointerEvents: 'none' }}>{highlightLabel || '탭'}</span>
          </>
        )}
      </div>
      <span className="wf-chip ink" style={{ position: 'absolute', top: 8, left: 8 }}>{label}</span>
      <div className="wf-ellipsis" style={{ position: 'absolute', left: 8, right: 8, bottom: 8, padding: '5px 8px', background: 'rgba(255,252,245,0.94)', border: '1px solid var(--wf-border)', borderRadius: 7, fontSize: 11, fontWeight: 700 }}>{overlay}</div>
    </button>
  );
}
