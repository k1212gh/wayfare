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
  useReactFlow,
  MarkerType,
  NodeMouseHandler,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';
import { Legend } from './graph/Legend';
import { CATEGORY_COLOR } from './graph/colors';
import { FloatingEdge } from './graph/FloatingEdge';
import { ScreenshotNode } from './graph/ScreenshotNode';

interface ScreenMapViewProps {
  graph: { entry_node: string; nodes: any[]; edges: any[] };
  onNodeSelect: (node: any) => void;
  filterCategory: string;
  searchQuery: string;
  tourId: string;
  appName?: string;
  /** 외부 (사이드 패널 / EdgeRow 클릭) 에서 노드를 선택했을 때 viewport 이동 */
  selectedNodeId?: string;
}


/** ReactFlow 안에서만 사용 가능한 useReactFlow hook 으로 selected 노드 → viewport 중앙. */
function PanToSelected({ selectedId, nodes }: { selectedId?: string; nodes: Node[] }) {
  const { setCenter } = useReactFlow();
  useEffect(() => {
    if (!selectedId) return;
    const n = nodes.find((x) => x.id === selectedId);
    if (!n || !n.position) return;
    const w = (n as any).width || (n as any).measured?.width || 220;
    const h = (n as any).height || (n as any).measured?.height || 80;
    setCenter(n.position.x + w / 2, n.position.y + h / 2, { zoom: 1.1, duration: 600 });
  }, [selectedId, nodes, setCenter]);
  return null;
}

function buildLayout(nodes: any[], edges: any[], showScreenshots: boolean, tourId: string) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  const nodeW = showScreenshots ? 180 : 200;
  const nodeH = showScreenshots ? 260 : 56;
  g.setGraph({ rankdir: 'TB', nodesep: showScreenshots ? 30 : 40, ranksep: showScreenshots ? 60 : 70 });

  const flowNodes: Node[] = [];
  const flowEdges: Edge[] = [];

  // Identify system-triggered nodes: only incoming edge(s) are pending_intent
  // → these are reachable ONLY via notifications/widgets/AlarmManager.
  // Group them visually at the top of the graph so the user can see the
  // "external-trigger-only" boundary of the app.
  const incomingByKind: Record<string, string[]> = {};  // screen_id → list of kinds
  for (const e of edges) {
    const k = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
    if (!incomingByKind[e.to]) incomingByKind[e.to] = [];
    incomingByKind[e.to].push(k);
  }
  const isSystemOnly = (nid: string): boolean => {
    const kinds = incomingByKind[nid] || [];
    if (kinds.length === 0) return false;
    return kinds.every((k) => k === 'pending_intent');
  };

  for (const n of nodes) {
    const opts: any = { width: nodeW, height: nodeH };
    if (n.screen_id === 'system:external_entry' || isSystemOnly(n.screen_id)) {
      opts.rank = 'min';  // pin to top rank alongside external_entry
    }
    g.setNode(n.screen_id, opts);
  }
  for (const e of edges) g.setEdge(e.from, e.to);
  dagre.layout(g);

  // entry_node passed in from caller; fall back to first node for legacy graphs
  const entryId = (nodes as any).entry_node_id || nodes[0]?.screen_id;
  for (const n of nodes) {
    const pos = g.node(n.screen_id);
    const color = CATEGORY_COLOR[n.functional_category] || '#9ca3af';
    const isEntry = n.screen_id === entryId;
    const status: string = n.status || 'resolved';
    const STATUS_BORDER: Record<string, string> = {
      resolved: '#22c55e',
      partial:  '#f59e0b',
      unknown:  '#ef4444',
      enriched: '#22c55e',
      declared: '#cbd5e1',   // pale — not yet walked
      entry:    '#8b5cf6',
      probed:   '#3b82f6',   // blue — launched via am start -W, not UI-walked
    };
    const isDeclared = status === 'declared';
    const isSystem = n.screen_id === 'system:external_entry';
    const isSystemTriggered = isSystemOnly(n.screen_id);
    // Tooltip shown on hover (React Flow stores this on data and the node
    // custom component / default renders it via `title`).
    const statusDesc: Record<string, string> = {
      declared: '정적 선언만, 아직 동적 탐색에서 방문 안 됨',
      enriched: '동적 탐색에서 방문 완료 — 스크린샷/UI 요소 있음',
      resolved: '레이아웃 XML 분석만으로 완전 파악됨',
      partial: 'Fragment/ViewPager 포함 — 내부는 런타임 의존',
      unknown: 'RecyclerView/WebView 포함 — 동적 탐색 필요',
      entry: '외부/시스템 진입 가상 노드',
      probed: 'am start로 강제 런치 성공 — 런타임 도달 가능 확인 (UI 미캡처)',
    };
    const hasUICaptured = !!n.screenshot_ref && (n.widgets?.length ?? 0) > 0;
    const jitNeeded = status === 'probed' && !hasUICaptured;
    const prioDesc: Record<string, string> = {
      A: 'User screen (capture 필요) — scan 대상',
      B: 'Plumbing/Trampoline (UI 없음) — scan skip',
      C: 'Deep-link entry (intent_filter만 있으면 충분) — scan skip',
    };
    const prio = n.capture_priority || '';
    const nodeTypeLine = n.node_type === 'fragment'
      ? `Type: Fragment (host: ${(n.host_activity || n.activity || '?').rsplit ? '' : ''}${(n.host_activity || n.activity || '?').split('.').pop()})`
      : n.node_type === 'activity'
      ? 'Type: Activity host'
      : n.node_type === 'system'
      ? 'Type: System entry'
      : '';
    const tooltip = [
      `Activity: ${n.activity || n.screen_id}`,
      n.fragment ? `Fragment: ${n.fragment.split('.').pop()}` : null,
      nodeTypeLine || null,
      `Category: ${n.functional_category || 'other'}`,
      `Status: ${status} — ${statusDesc[status] || ''}`,
      prio && prio in prioDesc ? `Capture priority: ${prio} — ${prioDesc[prio]}` : null,
      n.capture_reason ? `  (reason: ${n.capture_reason})` : null,
      jitNeeded ? '⚡ JIT capture needed: MobileGPT 에이전트가 이 화면에 도달하면 런타임에 uiautomator로 스크린샷/UI 요소를 즉시 캡처 — 파이프라인에서는 am start가 로그인/파라미터 게이트로 막혀 UI 미확보' : null,
      n.is_launcher ? 'Launcher: 예' : null,
      n.statically_reachable ? 'Static reachable: DEX 정적 참조 확인됨' : null,
      isSystemTriggered ? '⚡ System-triggered only: 알림/위젯/AlarmManager로만 진입' : null,
      (n.intent_filters && n.intent_filters.length)
        ? `Intent actions: ${(n.intent_filters[0].actions || []).slice(0, 2).join(', ')}`
        : null,
    ].filter(Boolean).join('\n');

    const isFragment = n.node_type === 'fragment';
    const isActivity = n.node_type === 'activity' || (!n.node_type && !isSystem && !isEntry);

    // Show the screenshot card ONLY when this node actually has a captured
    // screenshot + UI elements. Otherwise every declared/probed activity
    // would show as a 180×200 empty frame, which makes the graph look like
    // the pipeline was interrupted mid-run. Those get a compact text card.
    const useThumbnail = showScreenshots && hasUICaptured;

    // Capture priority A/B/C badge — makes the "which of these is actually
    // an interactive user screen" distinction visible at a glance.
    const PRIO_STYLE: Record<string, { bg: string; fg: string; title: string }> = {
      A: { bg: '#059669', fg: '#fff', title: 'A — User screen (agent interacts here)' },
      B: { bg: '#94a3b8', fg: '#fff', title: 'B — Plumbing / trampoline (no UI, agent skips)' },
      C: { bg: '#8b5cf6', fg: '#fff', title: 'C — Deep-link entry (reachable via URI only)' },
    };
    const prioBadge = prio && PRIO_STYLE[prio] ? (
      <span
        title={PRIO_STYLE[prio].title}
        style={{
          display: 'inline-block',
          padding: '1px 5px',
          marginRight: 6,
          borderRadius: 3,
          background: PRIO_STYLE[prio].bg,
          color: PRIO_STYLE[prio].fg,
          fontSize: '9px',
          fontWeight: 700,
          fontFamily: "'JetBrains Mono', monospace",
          verticalAlign: 'middle',
        }}
      >
        {prio}
      </span>
    ) : null;
    // Priority B (plumbing) nodes are dimmed further so the user's eye skips them.
    const prioOpacity = prio === 'B' ? 0.5 : (isDeclared ? 0.55 : 1);

    flowNodes.push({
      id: n.screen_id,
      data: useThumbnail
        ? { label: n.label || n.screen_id, ...n, showScreenshots, tourId, color, isEntry, status, tooltip }
        : {
            // Compact text card — used both for "Hide Screenshots" mode and
            // for nodes that have no captured UI (they'd be empty frames otherwise).
            label: (
              <span title={tooltip} style={{ display: 'block', width: '100%' }}>
                {prioBadge}
                {n.label || n.screen_id}
              </span>
            ),
            ...n,
            showScreenshots, tourId, color, isEntry, status, tooltip,
          },
      position: { x: (pos?.x || 0) - nodeW / 2, y: (pos?.y || 0) - nodeH / 2 },
      type: useThumbnail ? 'screenshotNode' : 'default',
      style: useThumbnail ? undefined : {
        // Fragment nodes get a subtle indigo tint + rounded look inside their host
        background: isSystem ? '#f5f3ff'
                  : isSystemTriggered ? '#fef3c7'        // amber tint for system-only
                  : isFragment ? '#eef2ff'               // indigo tint for fragments
                  : isActivity ? '#f8fafc'               // very light gray for activity hosts
                  : '#fff',
        color: '#0a0a0a',
        border: isSystem ? '2px dashed #8b5cf6'
              : isEntry  ? '2px solid #8b5cf6'
              : isSystemTriggered ? '1.5px dashed #d97706' // amber dashed
              : isFragment ? `1px solid #6366f1`         // indigo border
              : isActivity && !isDeclared ? `2px solid ${STATUS_BORDER[status] || '#64748b'}` // thicker for activity host
              : isDeclared ? `1px dashed ${STATUS_BORDER[status]}`
              : `1px solid ${STATUS_BORDER[status] || '#d4d4d4'}`,
        borderRadius: isFragment ? '12px' : '8px',
        padding: '10px 14px',
        fontSize: isFragment ? '11px' : '12px',
        fontFamily: "'Inter', sans-serif",
        fontWeight: 500,
        width: isFragment ? nodeW - 40 : nodeW,   // fragments a bit smaller
        cursor: 'pointer',
        borderLeft: `4px solid ${color}`,
        opacity: prioOpacity,
      },
    });
  }

  // Simplified 4-group palette — easier to read at a glance
  const STYLE_BY_KIND: Record<string, { stroke: string; dash?: string; width: number; showLabel?: boolean }> = {
    // Group A: confirmed transitions (solid blue family)
    navigate:       { stroke: '#2563eb', width: 2, showLabel: true },
    two_hop:        { stroke: '#6d28d9', width: 1.8, showLabel: true },
    // contains 는 구조관계 (Activity ↔ Fragment) — 라벨 끄고 점선만으로 표현
    // (이전: 81/113 엣지가 contains 라 fragment_transaction 라벨 도배되던 문제 해소)
    contains:       { stroke: '#0ea5e9', width: 1.0, dash: '3,5', showLabel: false },
    // Group B: entry/external (green family)
    launcher:       { stroke: '#16a34a', width: 2.2, showLabel: true },
    intent_filter:  { stroke: '#16a34a', width: 1.3, dash: '4,2', showLabel: true },
    pending_intent: { stroke: '#65a30d', width: 1.3, dash: '5,3', showLabel: true },
    // Group C: weak / inferred (gray, no label)
    static_ref:     { stroke: '#cbd5e1', width: 0.8, dash: '2,4', showLabel: false },
    global:         { stroke: '#9ca3af', width: 1.0, dash: '2,3', showLabel: false },
    // Group D: reversals / overlays (amber/orange)
    overlay:        { stroke: '#f59e0b', width: 1.5, dash: '3,3', showLabel: true },
    back:           { stroke: '#d4d4d4', width: 1.0, dash: '6,4', showLabel: false },
  };

  // raw trigger 식별자를 사람이 읽기 좋게 매핑.
  // 예: "intent reflection/setClassName" → "정적 추론"
  //      "intent two_hop_/HelperFoo"     → "Helper 경유"
  //      "click btn_login"                → "btn_login" (그대로)
  function friendlyLabel(e: any, kind: string, targetLabel?: string): string {
    const action: string = e.trigger_action || '';
    const elem: string = e.trigger_widget || '';
    if (kind === 'contains') return '';
    if (kind === 'back' || action === 'press_back') return '뒤로';
    if (kind === 'launcher') return '런처';
    if (kind === 'intent_filter') return elem ? `딥링크: ${elem.split('/').pop()?.slice(0, 14) || ''}` : '딥링크';
    if (kind === 'overlay') return '오버레이';
    if (kind === 'global') return '';
    if (elem.startsWith('reflection/')) return '정적 추론';
    if (elem.startsWith('two_hop_')) return 'Helper 경유';
    if (elem === 'fragment_transaction') return '';
    if (action === 'intent' && !elem) return targetLabel ? `→ ${targetLabel}` : '인텐트';
    if (action === 'intent' && elem) {
      // FQN 같은 경우 마지막 segment 만
      const tail = elem.split('.').pop() || elem;
      return tail.length > 18 ? tail.slice(0, 18) + '…' : tail;
    }
    // click + 짧은 element text (button label 류) — 그대로
    if (elem) return elem.length > 18 ? elem.slice(0, 18) + '…' : elem;
    return action || '';
  }

  // 노드 → label 빠른 lookup (target label 사용을 위해)
  const nodeLabelMap: Record<string, string> = {};
  for (const n of nodes) nodeLabelMap[n.screen_id] = n.label || '';

  for (const e of edges) {
    const kind: string = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
    const confidence: string = e.confidence || (e.source === 'walk' ? 'observed' : 'static_intent');
    const s = STYLE_BY_KIND[kind] || STYLE_BY_KIND.navigate;

    const label = s.showLabel ? friendlyLabel(e, kind, nodeLabelMap[e.to]) : '';

    const opacity = confidence === 'static_intent' && kind !== 'two_hop' && kind !== 'navigate' ? 0.6 : 1;
    const boost = (e.frequency || 0) >= 3 ? 0.8 : 0;

    flowEdges.push({
      id: e.edge_id || `${e.from}-${e.to}`,
      source: e.from,
      target: e.to,
      type: 'floating',
      data: { kind, confidence, label },
      style: {
        stroke: s.stroke,
        strokeWidth: s.width + boost,
        strokeDasharray: s.dash,
        opacity,
      },
      markerEnd: {
        type: MarkerType.ArrowClosed,
        width: 12, height: 12,
        color: s.stroke,
      },
    });
  }

  return { nodes: flowNodes, edges: flowEdges };
}

const nodeTypes = { screenshotNode: ScreenshotNode };
const edgeTypes = { floating: FloatingEdge };

export function ScreenMapView({ graph, onNodeSelect, filterCategory, searchQuery, tourId, appName, selectedNodeId }: ScreenMapViewProps) {
  const [showScreenshots, setShowScreenshots] = useState(false);
  const [pathSource, setPathSource] = useState<string | null>(null);
  const [highlightedPath, setHighlightedPath] = useState<{ nodes: Set<string>; edges: Set<string> } | null>(null);
  const [pathInfo, setPathInfo] = useState<string>('');
  // Natural-language task planning (Claude-powered)
  const [taskInput, setTaskInput] = useState('');
  const [planBusy, setPlanBusy] = useState(false);
  const [planResult, setPlanResult] = useState<any | null>(null);

  const askTask = useCallback(async () => {
    const t = taskInput.trim();
    if (!t) return;
    setPlanBusy(true);
    setPlanResult(null);
    try {
      const res = await fetch(`/api/tours/${tourId}/plan?task=${encodeURIComponent(t)}`, {
        method: 'POST',
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        setPlanResult({ error: err.detail || `HTTP ${res.status}` });
        return;
      }
      const data = await res.json();
      setPlanResult(data);
      // Highlight path nodes on the graph
      const pathNodes = Array.isArray(data.path_nodes) ? data.path_nodes : [];
      if (pathNodes.length > 1) {
        const nodeSet = new Set<string>(pathNodes);
        const edgeSet = new Set<string>();
        for (let i = 0; i < pathNodes.length - 1; i++) {
          for (const e of graph.edges) {
            if (e.from === pathNodes[i] && e.to === pathNodes[i + 1]) {
              edgeSet.add(e.edge_id || `${e.from}-${e.to}`);
            }
          }
        }
        setHighlightedPath({ nodes: nodeSet, edges: edgeSet });
      }
    } catch (e: any) {
      setPlanResult({ error: String(e?.message || e) });
    } finally {
      setPlanBusy(false);
    }
  }, [taskInput, tourId, graph.edges]);

  const findPath = useCallback(async (source: string, target: string) => {
    try {
      const res = await fetch(`/api/tours/${tourId}/path?source=${source}&target=${target}`);
      if (!res.ok) { setPathInfo('No path found'); return; }
      const data = await res.json();
      const best = data.best;
      if (best?.path) {
        const pathNodeSet = new Set(best.path as string[]);
        const pathEdgeSet = new Set<string>();
        for (let i = 0; i < best.path.length - 1; i++) {
          // Match edge by from+to
          for (const e of graph.edges) {
            if (e.from === best.path[i] && e.to === best.path[i + 1]) {
              pathEdgeSet.add(e.edge_id || `${e.from}-${e.to}`);
            }
          }
        }
        setHighlightedPath({ nodes: pathNodeSet, edges: pathEdgeSet });
        setPathInfo(`${best.hop_count} hops, cost ${best.total_cost}`);
      }
    } catch { setPathInfo('Path error'); }
  }, [tourId, graph.edges]);

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
  // Edge kind filter — 사용자가 toolbar 에서 toggle 한 kind 들만 표시.
  // 2026-05-06 — default hide 'contains' / 'static_ref' / 'global':
  //   contains = fragment hierarchy 정적 분석. user click 이 아닌 "MainActivity
  //   contains FragmentX" 같은 포함 관계 → 메인 hub 가 모든 화면에 직접 연결된
  //   별모양 만들어 시각적 노이즈. toolbar 의 chip 클릭으로 보이게 가능.
  //   static_ref / global 도 정적 분석 부산물 — 같은 이유.
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(
    new Set(['contains', 'static_ref', 'global'])
  );
  const filteredEdges = useMemo(
    () => graph.edges.filter((e: any) => {
      if (!filteredNodeIds.has(e.from) || !filteredNodeIds.has(e.to)) return false;
      const kind = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
      return !hiddenKinds.has(kind);
    }),
    [graph.edges, filteredNodeIds, hiddenKinds]
  );

  // 그래프에 실제 등장하는 kind 별 카운트 (toggle UI 에 표시)
  const edgeKindCounts = useMemo(() => {
    const c: Record<string, number> = {};
    for (const e of graph.edges) {
      if (!filteredNodeIds.has(e.from) || !filteredNodeIds.has(e.to)) continue;
      const k = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
      c[k] = (c[k] || 0) + 1;
    }
    return c;
  }, [graph.edges, filteredNodeIds]);

  const layout = useMemo(() => {
    // Tag the nodes array with entry_node_id so buildLayout can highlight it
    const taggedNodes: any = filteredNodes.slice();
    taggedNodes.entry_node_id = graph.entry_node;
    return buildLayout(taggedNodes, filteredEdges, showScreenshots, tourId);
  }, [filteredNodes, filteredEdges, showScreenshots, tourId, graph.entry_node]);

  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layout.edges);

  // Apply path highlighting to nodes and edges
  const styledNodes = useMemo(() => {
    if (!highlightedPath) return layout.nodes;
    return layout.nodes.map((n) => {
      const onPath = highlightedPath.nodes.has(n.id);
      if (!onPath) return { ...n, style: { ...n.style, opacity: 0.3 } };
      return { ...n, style: { ...n.style, border: '3px solid #dc2626', opacity: 1 } };
    });
  }, [layout.nodes, highlightedPath]);

  const styledEdges = useMemo(() => {
    if (!highlightedPath) return layout.edges;
    return layout.edges.map((e) => {
      const onPath = highlightedPath.edges.has(e.id);
      if (!onPath) return { ...e, style: { ...e.style, opacity: 0.15 } };
      return {
        ...e,
        style: { ...e.style, stroke: '#dc2626', strokeWidth: 3, opacity: 1 },
        markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14, color: '#dc2626' },
        animated: true,
      };
    });
  }, [layout.edges, highlightedPath]);

  useEffect(() => { setNodes(styledNodes); }, [styledNodes, setNodes]);
  useEffect(() => { setEdges(styledEdges); }, [styledEdges, setEdges]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (event, node) => {
      const nativeEvent = event as unknown as MouseEvent;
      if (nativeEvent.shiftKey && pathSource) {
        // Shift+click = set path target → find path
        findPath(pathSource, node.id);
      } else {
        // Normal click = select node + set as path source
        onNodeSelect(node.data);
        setPathSource(node.id);
        setHighlightedPath(null);
        setPathInfo('Shift+click another node for path');
      }
    },
    [onNodeSelect, pathSource, findPath]
  );

  const downloadKG = useCallback(() => {
    const blob = new Blob([JSON.stringify({ nodes: graph.nodes, edges: graph.edges }, null, 2)], { type: 'application/json' });
    const a = document.createElement('a'); a.href = URL.createObjectURL(blob);
    // Filename: result/<app_name>/knowledge_graph.json — browsers that
    // honor subpath in download attribute will place it there; others flatten
    // to `<app_name>_knowledge_graph.json`.
    const rawName = (appName || 'app').toString();
    const safeName = rawName.replace(/[^A-Za-z0-9._-]/g, '_') || 'app';
    a.download = `result/${safeName}/knowledge_graph.json`;
    a.click();
  }, [graph, appName]);

  return (
    <>
    {/* Edge-hover tooltip element (imperatively positioned to avoid re-renders) */}
    <div id="sa-edge-tooltip" style={{
      display: 'none', position: 'fixed', zIndex: 100000,
      maxWidth: 320, background: 'rgba(17,24,39,0.96)', color: '#f9fafb',
      padding: '8px 10px', borderRadius: 6, fontSize: 11, lineHeight: 1.45,
      fontFamily: "'Inter', sans-serif", pointerEvents: 'none',
      whiteSpace: 'pre-wrap', boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
      border: '1px solid rgba(255,255,255,0.1)',
    }} />
    {/* Task navigator overlay — natural-language task → Claude plan */}
    <div style={{
      position: 'absolute', top: 16, left: 16, zIndex: 30,
      background: 'rgba(255,255,255,0.96)', border: '1px solid var(--color-border)',
      borderRadius: 8, padding: '10px 12px', maxWidth: 360,
      boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
    }}>
      <div style={{ fontSize: 11, fontWeight: 600, color: 'var(--color-gray)',
                    marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5 }}>
        Ask Claude — 자연어 Task로 경로 계획
      </div>
      <div style={{ display: 'flex', gap: 6 }}>
        <input
          type="text"
          value={taskInput}
          placeholder={'예: "가사 화면 보여줘", "계정 전환"'}
          onChange={(e) => setTaskInput(e.target.value)}
          onKeyDown={(e) => { if (e.key === 'Enter' && !planBusy) askTask(); }}
          disabled={planBusy}
          style={{
            flex: 1, fontSize: 12, padding: '5px 8px',
            border: '1px solid var(--color-border)', borderRadius: 4,
            fontFamily: 'var(--font)',
          }}
        />
        <button onClick={askTask} disabled={planBusy || !taskInput.trim()} style={{
          fontSize: 11, padding: '5px 12px', cursor: planBusy ? 'wait' : 'pointer',
          background: 'var(--color-black)', color: 'var(--color-white)',
          border: 'none', borderRadius: 4, fontFamily: 'var(--font)',
          opacity: planBusy || !taskInput.trim() ? 0.5 : 1,
        }}>
          {planBusy ? '...' : 'Plan'}
        </button>
      </div>
      {planResult && (
        <div style={{ marginTop: 8, fontSize: 11, maxHeight: 240, overflowY: 'auto' }}>
          {planResult.error ? (
            <div style={{ color: '#dc2626' }}>error: {planResult.error}</div>
          ) : (
            <>
              {Array.isArray(planResult.steps) && planResult.steps.length > 0 ? (
                <ol style={{ paddingLeft: 16, margin: '4px 0' }}>
                  {planResult.steps.map((s: any, i: number) => (
                    <li key={i} style={{ marginBottom: 4, lineHeight: 1.4 }}>
                      <span style={{ fontFamily: 'var(--font-mono)', color: '#6b7280' }}>
                        {s.from} → {s.to}
                      </span>
                      {s.trigger && <span style={{ color: '#059669' }}> [{s.trigger}]</span>}
                      {s.why && <div style={{ color: '#374151', fontSize: 10, marginTop: 2 }}>{s.why}</div>}
                    </li>
                  ))}
                </ol>
              ) : (
                <div style={{ color: 'var(--color-gray)' }}>No steps returned.</div>
              )}
              {planResult.notes && (
                <div style={{ marginTop: 4, padding: '4px 6px', background: '#fef3c7',
                              color: '#92400e', borderRadius: 3, fontSize: 10 }}>
                  {planResult.notes}
                </div>
              )}
            </>
          )}
        </div>
      )}
    </div>
    <ReactFlow
      nodes={nodes} edges={edges}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      fitView minZoom={0.05} maxZoom={2}
      proOptions={{ hideAttribution: true }}
    >
      <PanToSelected selectedId={selectedNodeId} nodes={nodes} />
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d4d4d4" />
      <Controls showInteractive={false} style={{ border: '1px solid #e5e5e5', borderRadius: '8px', overflow: 'hidden' }} />
      <MiniMap
        nodeColor={(n) => CATEGORY_COLOR[(n.data as any)?.functional_category || 'other'] || '#9ca3af'}
        maskColor="rgba(0,0,0,0.06)"
        style={{ border: '1px solid #e5e5e5', borderRadius: '8px', overflow: 'hidden' }}
      />

      <Panel position="top-right">
        <div style={{ display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap', maxWidth: '70vw', justifyContent: 'flex-end' }}>
          <PanelBtn active={showScreenshots} onClick={() => setShowScreenshots(!showScreenshots)}>
            {showScreenshots ? 'Hide Screenshots' : 'Show Screenshots'}
          </PanelBtn>
          {highlightedPath && (
            <PanelBtn onClick={() => { setHighlightedPath(null); setPathSource(null); setPathInfo(''); }}>
              Clear Path
            </PanelBtn>
          )}
          <PanelBtn onClick={downloadKG}>Download ScreenMap</PanelBtn>
          {pathInfo && (
            <span style={{ fontSize: '10px', color: '#dc2626', fontFamily: 'var(--font-mono)', padding: '0 6px' }}>
              {pathInfo}
            </span>
          )}
          {/* Edge kind filter chips — 클릭으로 해당 kind 엣지 숨김 */}
          {Object.keys(edgeKindCounts).length > 0 && (
            <div style={{
              display: 'flex', gap: '3px', alignItems: 'center',
              padding: '4px 8px', background: 'rgba(255,255,255,0.95)',
              border: '1px solid #e5e5e5', borderRadius: '6px',
            }}>
              <span style={{ fontSize: '10px', color: '#6b7280', marginRight: '2px',
                             fontFamily: 'var(--font-mono)' }}>edges:</span>
              {Object.entries(edgeKindCounts)
                .sort((a, b) => b[1] - a[1])
                .map(([kind, count]) => {
                  const hidden = hiddenKinds.has(kind);
                  return (
                    <span
                      key={kind}
                      onClick={() => {
                        const next = new Set(hiddenKinds);
                        if (hidden) next.delete(kind); else next.add(kind);
                        setHiddenKinds(next);
                      }}
                      title={hidden ? `${kind} 보이기` : `${kind} 숨기기`}
                      style={{
                        cursor: 'pointer', fontSize: '10px',
                        padding: '2px 7px', borderRadius: '10px',
                        fontFamily: 'var(--font-mono)',
                        background: hidden ? 'transparent' : '#1a1a1a',
                        color: hidden ? '#9ca3af' : '#fff',
                        border: hidden ? '1px solid #d4d4d4' : '1px solid #1a1a1a',
                        textDecoration: hidden ? 'line-through' : 'none',
                        userSelect: 'none' as const,
                      }}
                    >
                      {kind} {count}
                    </span>
                  );
                })}
            </div>
          )}
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
        </div>
      </Panel>

      <Legend />
    </ReactFlow>
    </>
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
