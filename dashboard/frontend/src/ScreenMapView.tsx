import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
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
import { CATEGORY_COLOR, EDGE_KIND_DESC } from './graph/colors';
import { FloatingEdge } from './graph/FloatingEdge';
import { ScreenshotNode } from './graph/ScreenshotNode';
import { CustomTextNode } from './graph/CustomTextNode';
import { NODE_SIZES, NODE_SIZE_LABEL, NodeSize } from './graph/nodeSize';
import { displayLabel, subLabel } from './graph/displayLabel';

interface ScreenMapViewProps {
  graph: { entry_node: string; nodes: any[]; edges: any[] };
  onNodeSelect: (node: any) => void;
  filterCategory: string;
  searchQuery: string;
  tourId: string;
  appName?: string;
  /** 외부 (사이드 패널 / EdgeRow 클릭) 에서 노드를 선택했을 때 viewport 이동 */
  selectedNodeId?: string;
  /** Controlled mode: 외부에서 edge detail 패널을 열고 닫을 수 있도록. 미지정 시 내부 state 사용. */
  selectedEdgeData?: any | null;
  onSelectedEdgeDataChange?: (data: any | null) => void;
}


/** ReactFlow 안에서만 사용 가능한 useReactFlow hook 으로 selected 노드 → viewport 중앙. */
function PanToSelected({ selectedId, nodes }: { selectedId?: string; nodes: Node[] }) {
  const { setCenter } = useReactFlow();
  const lastPannedIdRef = useRef<string>('');
  useEffect(() => {
    if (!selectedId) return;
    if (lastPannedIdRef.current === selectedId) return;
    const n = nodes.find((x) => x.id === selectedId);
    if (!n || !n.position) return;
    const w = (n as any).width || (n as any).measured?.width || 220;
    const h = (n as any).height || (n as any).measured?.height || 80;
    lastPannedIdRef.current = selectedId;
    setCenter(n.position.x + w / 2, n.position.y + h / 2, { zoom: 1.1, duration: 600 });
  }, [selectedId, nodes, setCenter]);
  return null;
}

function buildLayout(
  nodes: any[],
  edges: any[],
  showScreenshots: boolean,
  showEdgeLabels: boolean,
  onOpenEdge: (edgeData: any) => void,
  tourId: string,
  spacingScale: number = 1.0,
  nodeSize: NodeSize = 'md',
  rankdir: 'TB' | 'LR' = 'LR',
) {
  const g = new dagre.graphlib.Graph();
  g.setDefaultEdgeLabel(() => ({}));
  const sizeDims = NODE_SIZES[nodeSize];
  const nodeW = showScreenshots ? sizeDims.screenshot.w : sizeDims.text.w;
  const nodeH = showScreenshots ? sizeDims.screenshot.sectionH : sizeDims.text.h;
  g.setGraph({
    // 2026-09-12: 기본 가로(LR). 세로로 긴 폰 카드를 세로 랭크로 쌓으면 곡선이 길어지고
    // 노드가 어긋나 보인다. 가로 흐름은 스토리보드처럼 읽힌다. 툴바에서 토글.
    rankdir,
    nodesep: sizeDims.dagre.nodesep * spacingScale,
    ranksep: sizeDims.dagre.ranksep * spacingScale,
  });

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
    const hasScreenshot = !!n.screenshot_ref;
    const hasWidgets = (n.widgets?.length ?? 0) > 0;
    const hasUICaptured = hasScreenshot && hasWidgets;
    const jitNeeded = status === 'probed' && !hasScreenshot;
    const uiMissing = hasScreenshot && !hasWidgets;
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
      uiMissing ? 'Screenshot captured, but no UI element list was extracted for this node.' : null,
      n.is_launcher ? 'Launcher: 예' : null,
      n.statically_reachable ? 'Static reachable: DEX 정적 참조 확인됨' : null,
      isSystemTriggered ? '⚡ System-triggered only: 알림/위젯/AlarmManager로만 진입' : null,
      (n.intent_filters && n.intent_filters.length)
        ? `Intent actions: ${(n.intent_filters[0].actions || []).slice(0, 2).join(', ')}`
        : null,
    ].filter(Boolean).join('\n');

    const isFragment = n.node_type === 'fragment';
    const isActivity = n.node_type === 'activity' || (!n.node_type && !isSystem && !isEntry);

    // Show a screenshot card whenever an actual screenshot exists. Some
    // dynamically discovered page_* nodes have screenshots but no extracted
    // widgets; hiding those made the graph look like only static A nodes
    // could expand.
    const useThumbnail = showScreenshots && hasScreenshot;

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
      data: {
        label: displayLabel(n),
        subLabel: subLabel(n, displayLabel(n)),
        rankdir,
        prioBadge,
        screen_purpose: n.screen_purpose,
        screen_id: n.screen_id,
        isSystem, isSystemTriggered, isFragment, isActivity,
        prioOpacity, STATUS_BORDER,
        showScreenshots, tourId, color, isEntry, status, tooltip,
        nodeSize,
        ...n
      },
      position: { x: (pos?.x || 0) - nodeW / 2, y: (pos?.y || 0) - nodeH / 2 },
      type: useThumbnail ? 'screenshotNode' : 'customTextNode',
    });
  }

  // Semantic-grouped palette. Same meaning class → same hue family:
  //  • Navigation (direct + helper) = cool deep (slate / indigo)
  //  • External entry (user/URL/system) = warm red-rose family (clearly "외부 진입")
  //  • Special transitions (overlay/back) = own accents
  //  • Structural = slate fade (300/400/500 — three clear tiers)
  const STYLE_BY_KIND: Record<string, { stroke: string; dash?: string; width: number; showLabel?: boolean }> = {
    // Navigation (cool deep)
    navigate:       { stroke: '#1e293b', width: 2.0, showLabel: true },  // slate-800 — primary
    two_hop:        { stroke: '#4338ca', width: 2.2, showLabel: true },  // indigo-700 — 진한 sibling
    // External entry (warm red-rose family — read as one group)
    launcher:       { stroke: '#dc2626', width: 2.5, showLabel: true },  // red-600 — 앱 직접 실행
    intent_filter:  { stroke: '#ef4444', width: 2.5, showLabel: true },  // red-500 — 딥링크
    pending_intent: { stroke: '#f43f5e', width: 2.5, showLabel: true },  // rose-500 — 시스템 트리거
    // Special transitions
    overlay:        { stroke: '#f59e0b', width: 2.0, showLabel: true },  // amber-500 — popup
    back:           { stroke: '#71717a', width: 1.5, showLabel: false }, // zinc-500 — system back
    // Structural — three-tier slate fade
    contains:       { stroke: '#64748b', width: 1.3, showLabel: false }, // slate-500
    static_ref:     { stroke: '#94a3b8', width: 1.2, showLabel: false }, // slate-400
    global:         { stroke: '#cbd5e1', width: 1.2, showLabel: false }, // slate-300
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
  const nodeMap: Record<string, any> = {};
  for (const n of nodes) {
    nodeLabelMap[n.screen_id] = n.label || '';
    nodeMap[n.screen_id] = n;
  }

  for (const e of edges) {
    const kind: string = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
    const confidence: string = e.confidence || (e.source === 'walk' ? 'observed' : 'static_intent');
    const s = STYLE_BY_KIND[kind] || STYLE_BY_KIND.navigate;

    const actionLabel = friendlyLabel(e, kind, nodeLabelMap[e.to]);
    const label = showEdgeLabels && s.showLabel ? actionLabel : '';
    const edgeId = e.edge_id || `${e.from}-${e.to}`;

    const baseOpacity = showEdgeLabels ? 0.82 : (kind === 'navigate' ? 0.44 : 0.56);
    const opacity = confidence === 'static_intent' && kind !== 'two_hop' && kind !== 'navigate'
      ? Math.min(baseOpacity, 0.42)
      : baseOpacity;
    const boost = showEdgeLabels && (e.frequency || 0) >= 3 ? 0.5 : 0;

    flowEdges.push({
      id: edgeId,
      source: e.from,
      target: e.to,
      type: 'floating',
      data: {
        edgeId,
        rankdir,
        kind,
        confidence,
        label,
        actionLabel,
        raw: e,
        sourceNode: nodeMap[e.from],
        targetNode: nodeMap[e.to],
        sourceLabel: nodeLabelMap[e.from] || e.from,
        targetLabel: nodeLabelMap[e.to] || e.to,
        onOpenEdge,
      },
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

const nodeTypes = { screenshotNode: ScreenshotNode, customTextNode: CustomTextNode };
const edgeTypes = { floating: FloatingEdge };
type GraphMode = 'flow' | 'structure' | 'diagnostics' | 'custom';
type ActionBounds = { left: number; top: number; right: number; bottom: number; label?: string };

function parseBoundsValue(value: any): ActionBounds | null {
  if (!value) return null;
  if (Array.isArray(value) && value.length >= 4) {
    const [left, top, right, bottom] = value.map(Number);
    if ([left, top, right, bottom].every(Number.isFinite) && right > left && bottom > top) {
      return { left, top, right, bottom };
    }
  }
  if (typeof value === 'object') {
    const left = Number(value.left ?? value.x1 ?? value.x);
    const top = Number(value.top ?? value.y1 ?? value.y);
    const right = Number(value.right ?? value.x2 ?? (Number.isFinite(left) ? left + Number(value.width) : NaN));
    const bottom = Number(value.bottom ?? value.y2 ?? (Number.isFinite(top) ? top + Number(value.height) : NaN));
    if ([left, top, right, bottom].every(Number.isFinite) && right > left && bottom > top) {
      return { left, top, right, bottom, label: value.label || value.text || value.content_desc };
    }
  }
  if (typeof value !== 'string') return null;
  const match = value.match(/^(.*?)@?\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]\[\s*(-?\d+(?:\.\d+)?)\s*,\s*(-?\d+(?:\.\d+)?)\s*\]/);
  if (!match) return null;
  const [, rawLabel, x1, y1, x2, y2] = match;
  const left = Number(x1);
  const top = Number(y1);
  const right = Number(x2);
  const bottom = Number(y2);
  if (![left, top, right, bottom].every(Number.isFinite) || right <= left || bottom <= top) return null;
  const label = rawLabel.replace(/^(click|tap|press)\s+/i, '').trim();
  return { left, top, right, bottom, label: label || undefined };
}

function extractActionBounds(raw: any): ActionBounds | null {
  return (
    parseBoundsValue(raw?.trigger_bounds) ||
    parseBoundsValue(raw?.widget_bounds) ||
    parseBoundsValue(raw?.bounds) ||
    parseBoundsValue(raw?.bbox) ||
    parseBoundsValue(raw?.trigger_widget)
  );
}

function readableTriggerLabel(value: any): string {
  if (typeof value !== 'string' || !value.trim()) return '';
  const parsed = parseBoundsValue(value);
  if (parsed?.label) return parsed.label;
  return value.replace(/@\[[^\]]+\]\[[^\]]+\]/, '').trim();
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

export function ScreenMapView({
  graph, onNodeSelect, filterCategory, searchQuery, tourId, appName, selectedNodeId,
  selectedEdgeData: externalEdgeData,
  onSelectedEdgeDataChange,
}: ScreenMapViewProps) {
  const [showScreenshots, setShowScreenshots] = useState(false);
  const [rankdir, setRankdir] = useState<'TB' | 'LR'>('LR');
  const [showEdgeLabels, setShowEdgeLabels] = useState(false);
  const [edgeFiltersOpen, setEdgeFiltersOpen] = useState(false);
  const [graphMode, setGraphMode] = useState<GraphMode>('flow');
  const [spacingScale, setSpacingScale] = useState(1.0);
  const [nodeSize, setNodeSize] = useState<NodeSize>('md');
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
  const [pathInfo, setPathInfo] = useState<string>('');
  const [taskPlannerOpen, setJourneyPlannerOpen] = useState(false);
  // Natural-language task planning (Claude-powered)
  const [taskInput, setTaskInput] = useState('');
  const [planBusy, setPlanBusy] = useState(false);
  const [planResult, setPlanResult] = useState<any | null>(null);

  const openEdgeDetail = useCallback((edgeData: any) => {
    setSelectedEdgeData(edgeData);
  }, []);

  const askTask = useCallback(async () => {
    const t = taskInput.trim();
    if (!t) return;
    setPlanBusy(true);
    setPlanResult(null);
    setPlanFocus(false);
    try {
      const res = await fetch(`/api/tours/${tourId}/plan?task=${encodeURIComponent(t)}`, {
        method: 'POST',
      });
      if (!res.ok) {
        const err = await res.json().catch(() => ({ detail: `HTTP ${res.status}` }));
        setPlanResult({ error: err.detail || `HTTP ${res.status}` });
        setHighlightedPath(null);
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
        setPlanFocus(true);
      } else {
        setHighlightedPath(null);
      }
    } catch (e: any) {
      setPlanResult({ error: String(e?.message || e) });
      setHighlightedPath(null);
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
        setPlanFocus(false);
        setPathInfo(`${best.hop_count} hops, cost ${best.total_cost}`);
      }
    } catch { setPathInfo('Path error'); }
  }, [tourId, graph.edges]);

  const filteredNodes = useMemo(() => {
    let nodes = graph.nodes;
    if (planFocus && highlightedPath) {
      nodes = nodes.filter((n: any) => highlightedPath.nodes.has(n.screen_id));
    } else if (filterCategory) {
      nodes = nodes.filter((n: any) => n.functional_category === filterCategory);
    }
    if (!planFocus && searchQuery) {
      const q = searchQuery.toLowerCase();
      nodes = nodes.filter((n: any) =>
        (n.label || '').toLowerCase().includes(q) ||
        (n.screen_purpose || '').toLowerCase().includes(q) ||
        (n.activity || '').toLowerCase().includes(q)
      );
    }
    return nodes;
  }, [graph.nodes, filterCategory, searchQuery, planFocus, highlightedPath]);

  const filteredNodeIds = useMemo(() => new Set(filteredNodes.map((n: any) => n.screen_id)), [filteredNodes]);
  // Edge kind filter — 사용자가 toolbar 에서 toggle 한 kind 들만 표시.
  // 2026-05-06 — default hide 'contains' / 'static_ref' / 'global':
  //   contains = fragment hierarchy 정적 분석. user click 이 아닌 "MainActivity
  //   contains FragmentX" 같은 포함 관계 → 메인 hub 가 모든 화면에 직접 연결된
  //   별모양 만들어 시각적 노이즈. toolbar 의 chip 클릭으로 보이게 가능.
  //   static_ref / global 도 정적 분석 부산물 — 같은 이유.
  const [hiddenKinds, setHiddenKinds] = useState<Set<string>>(
    // 2026-09-12: 주석의 의도대로 구조 엣지(contains/static_ref/global)는 기본 숨김.
    // 빈 Set 이던 초기값 탓에 메가커피 그래프에서 contains 45개가 hub star 를 그렸다.
    new Set(['contains', 'static_ref', 'global'])
  );
  const filteredEdges = useMemo(
    () => graph.edges.filter((e: any) => {
      if (!filteredNodeIds.has(e.from) || !filteredNodeIds.has(e.to)) return false;
      const edgeId = e.edge_id || `${e.from}-${e.to}`;
      if (planFocus && highlightedPath) return highlightedPath.edges.has(edgeId);
      const kind = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
      return !hiddenKinds.has(kind);
    }),
    [graph.edges, filteredNodeIds, hiddenKinds, planFocus, highlightedPath]
  );

  // 그래프에 실제 등장하는 kind 별 카운트 (toggle UI 에 표시)
  const edgeKindCounts = useMemo(() => {
    const c: Record<string, number> = {};
    const countEdges = planFocus && highlightedPath
      ? graph.edges.filter((e: any) => highlightedPath.edges.has(e.edge_id || `${e.from}-${e.to}`))
      : graph.edges;
    for (const e of countEdges) {
      if (!filteredNodeIds.has(e.from) || !filteredNodeIds.has(e.to)) continue;
      const k = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
      c[k] = (c[k] || 0) + 1;
    }
    return c;
  }, [graph.edges, filteredNodeIds, planFocus, highlightedPath]);

  const visibleEdgeCount = useMemo(
    () => planFocus && highlightedPath
      ? Object.values(edgeKindCounts).reduce((sum, count) => sum + count, 0)
      : Object.entries(edgeKindCounts)
      .reduce((sum, [kind, count]) => sum + (hiddenKinds.has(kind) ? 0 : count), 0),
    [edgeKindCounts, hiddenKinds, planFocus, highlightedPath]
  );
  const totalEdgeCount = useMemo(
    () => Object.values(edgeKindCounts).reduce((sum, count) => sum + count, 0),
    [edgeKindCounts]
  );
  const categoryLegendItems = useMemo(() => {
    const counts: Record<string, number> = {};
    for (const n of filteredNodes) {
      const cat = n.functional_category || 'other';
      counts[cat] = (counts[cat] || 0) + 1;
    }
    return Object.entries(counts)
      .sort((a, b) => b[1] - a[1])
      .slice(0, 10);
  }, [filteredNodes]);

  const layout = useMemo(() => {
    // Tag the nodes array with entry_node_id so buildLayout can highlight it
    const taggedNodes: any = filteredNodes.slice();
    taggedNodes.entry_node_id = graph.entry_node;
    return buildLayout(taggedNodes, filteredEdges, showScreenshots, showEdgeLabels, openEdgeDetail, tourId, spacingScale, nodeSize, rankdir);
  }, [filteredNodes, filteredEdges, showScreenshots, showEdgeLabels, openEdgeDetail, tourId, graph.entry_node, spacingScale, nodeSize, rankdir]);

  const [nodes, setNodes, onNodesChange] = useNodesState(layout.nodes);
  const [edges, setEdges, onEdgesChange] = useEdgesState(layout.edges);

  // Apply path highlighting to nodes and edges
  // Match the inner card's border-radius so boxShadow rings stay rounded.
  const radiusFor = (n: Node): number =>
    n.type === 'screenshotNode' ? 10 : ((n.data as any)?.isFragment ? 12 : 8);

  const styledNodes = useMemo(() => {
    if (highlightedPath) {
      return layout.nodes.map((n) => {
        const onPath = highlightedPath.nodes.has(n.id);
        if (!onPath) return { ...n, style: { ...n.style, opacity: 0.3 } };
        return {
          ...n,
          style: {
            ...n.style,
            boxShadow: '0 0 0 3px #dc2626',
            borderRadius: `${radiusFor(n)}px`,
            opacity: 1,
          },
        };
      });
    }
    if (selectedNodeId) {
      const connectedNodes = new Set<string>([selectedNodeId]);
      layout.edges.forEach(e => {
        if (e.source === selectedNodeId) connectedNodes.add(e.target);
        if (e.target === selectedNodeId) connectedNodes.add(e.source);
      });
      return layout.nodes.map((n) => {
        const isConnected = connectedNodes.has(n.id);
        if (!isConnected) return { ...n, style: { ...n.style, opacity: 0.3 } };
        const isSelected = n.id === selectedNodeId;
        return {
          ...n,
          style: {
            ...n.style,
            opacity: 1,
            boxShadow: isSelected ? '0 0 0 3px #3b82f6' : '0 0 0 2px rgba(59,130,246,0.5)',
            borderRadius: `${radiusFor(n)}px`,
          },
        };
      });
    }
    return layout.nodes;
  }, [layout.nodes, highlightedPath, layout.edges, selectedNodeId]);

  const styledEdges = useMemo(() => {
    const selectedEdgeId = selectedEdgeData?.edgeId || '';
    if (highlightedPath) {
      return layout.edges.map((e) => {
        const onPath = highlightedPath.edges.has(e.id);
        if (!onPath) return { ...e, style: { ...e.style, opacity: 0.12 } };
        // Preserve original kind color; emphasize via thicker stroke + flow animation.
        const baseW = (e.style?.strokeWidth as number) || 2;
        return {
          ...e,
          style: { ...e.style, strokeWidth: baseW + 2, opacity: 1 },
          animated: true,
        };
      });
    }
    if (selectedNodeId) {
      return layout.edges.map((e) => {
        const isConnected = e.source === selectedNodeId || e.target === selectedNodeId;
        if (!isConnected) return { ...e, style: { ...e.style, opacity: 0.12 } };
        const baseW = (e.style?.strokeWidth as number) || 2;
        return {
          ...e,
          style: { ...e.style, strokeWidth: baseW + 1.5, opacity: 1 },
        };
      });
    }
    return layout.edges.map((e) => {
      if (e.id !== selectedEdgeId) return e;
      return {
        ...e,
        style: { ...e.style, stroke: '#111827', strokeWidth: 3, opacity: 1 },
        markerEnd: { type: MarkerType.ArrowClosed, width: 15, height: 15, color: '#111827' },
      };
    });
  }, [layout.edges, highlightedPath, selectedEdgeData, selectedNodeId]);

  useEffect(() => { setNodes(styledNodes); }, [styledNodes, setNodes]);
  useEffect(() => { setEdges(styledEdges); }, [styledEdges, setEdges]);

  const onNodeClick: NodeMouseHandler = useCallback(
    (event, node) => {
      const nativeEvent = event as unknown as MouseEvent;
      if (nativeEvent.shiftKey && pathSource) {
        // Shift+click = set path target → find path
        findPath(pathSource, node.id);
      } else {
        if (selectedNodeId === node.id) {
          onNodeSelect(null);
          setPathSource(null);
        } else {
          onNodeSelect(node.data);
          setSelectedEdgeData(null);
          setPathSource(node.id);
          setHighlightedPath(null);
          setPlanFocus(false);
          setPathInfo('Shift+click another node for path');
        }
      }
    },
    [onNodeSelect, pathSource, findPath, selectedNodeId]
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
    {/* Task navigator sits below search/filter and stays closed by default. */}
    <div style={{ position: 'absolute', top: 220, left: 16, zIndex: 30 }}>
      {!taskPlannerOpen ? (
        <button
          type="button"
          onClick={() => setJourneyPlannerOpen(true)}
          style={{
            padding: '8px 12px',
            background: 'rgba(255,255,255,0.96)',
            border: '1px solid var(--color-border)',
            borderRadius: 8,
            boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
            fontSize: 12,
            fontWeight: 600,
            color: 'var(--color-black)',
            cursor: 'pointer',
            fontFamily: 'var(--font)',
          }}
        >
          Ask Claude
        </button>
      ) : (
        <div style={{
          background: 'rgba(255,255,255,0.96)', border: '1px solid var(--color-border)',
          borderRadius: 8, padding: '10px 12px', width: 340,
          boxShadow: '0 2px 8px rgba(0,0,0,0.08)',
        }}>
          <div style={{
            display: 'flex', alignItems: 'center', justifyContent: 'space-between',
            fontSize: 11, fontWeight: 600, color: 'var(--color-gray)',
            marginBottom: 6, textTransform: 'uppercase', letterSpacing: 0.5,
          }}>
            <span>Ask Claude</span>
            <button
              type="button"
              onClick={() => setJourneyPlannerOpen(false)}
              aria-label="Close task navigator"
              style={{
                background: 'transparent', border: 'none', color: 'var(--color-gray)',
                cursor: 'pointer', fontSize: 15, lineHeight: 1, padding: 0,
              }}
            >
              ×
            </button>
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
      )}
    </div>
    {selectedEdgeData && (
      <EdgeDetailPanel
        edgeData={selectedEdgeData}
        tourId={tourId}
        onClose={() => setSelectedEdgeData(null)}
        onSelectNode={(node: any) => {
          if (!node) return;
          onNodeSelect(node);
          setSelectedEdgeData(null);
        }}
      />
    )}
    <ReactFlow
      nodes={nodes} edges={edges}
      onNodesChange={onNodesChange} onEdgesChange={onEdgesChange}
      onNodeClick={onNodeClick} nodeTypes={nodeTypes} edgeTypes={edgeTypes}
      onPaneClick={() => {
        setSelectedEdgeData(null);
        onNodeSelect(null);
      }}
      fitView fitViewOptions={{ minZoom: 0.6, padding: 0.1 }} minZoom={0.15} maxZoom={3}
      proOptions={{ hideAttribution: true }}
    >
      <PanToSelected selectedId={selectedNodeId} nodes={nodes} />
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d4d4d4" />
      <Controls showInteractive={false} style={{ border: '1px solid #e5e5e5', borderRadius: '8px', overflow: 'hidden' }} />
      <MiniMap
        position="top-left"
        pannable
        zoomable
        nodeColor={(n) => CATEGORY_COLOR[(n.data as any)?.functional_category || 'other'] || '#9ca3af'}
        maskColor="rgba(0,0,0,0.06)"
        style={{ border: '1px solid #e5e5e5', borderRadius: '8px', overflow: 'hidden' }}
      />

      <Panel position="top-right">
        <div style={{
          display: 'flex', gap: '6px', alignItems: 'center', flexWrap: 'wrap',
          maxWidth: 'min(68vw, 960px)', justifyContent: 'flex-end',
        }}>
          <div style={{
            display: 'inline-flex', gap: 6, padding: '3px 8px',
            background: 'rgba(255,255,255,0.95)',
            border: '1px solid #e5e5e5', borderRadius: 8,
            alignItems: 'center'
          }}>
            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--color-gray)' }}>Spacing</span>
            <input
              type="range" min="0.5" max="2.5" step="0.1"
              value={spacingScale}
              onChange={(e) => setSpacingScale(parseFloat(e.target.value))}
              style={{ width: '80px', cursor: 'pointer' }}
            />
          </div>
          <div style={{
            display: 'inline-flex', gap: 4, padding: '3px 6px',
            background: 'rgba(255,255,255,0.95)',
            border: '1px solid #e5e5e5', borderRadius: 8,
            alignItems: 'center'
          }}>
            <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--color-gray)', marginRight: 2 }}>Size</span>
            {(['sm','md','lg'] as NodeSize[]).map((s) => (
              <button
                key={s}
                onClick={() => setNodeSize(s)}
                style={{
                  padding: '2px 8px',
                  fontSize: '11px',
                  fontWeight: 600,
                  background: nodeSize === s ? '#171717' : 'transparent',
                  color: nodeSize === s ? '#fff' : '#525252',
                  border: '1px solid ' + (nodeSize === s ? '#171717' : '#d4d4d4'),
                  borderRadius: 4,
                  cursor: 'pointer',
                }}
              >
                {NODE_SIZE_LABEL[s]}
              </button>
            ))}
          </div>
          <PanelBtn active={rankdir === 'LR'} onClick={() => setRankdir(rankdir === 'LR' ? 'TB' : 'LR')}>
            {rankdir === 'LR' ? '가로 흐름' : '세로 흐름'}
          </PanelBtn>
          <PanelBtn active={showScreenshots} onClick={() => setShowScreenshots(!showScreenshots)}>
            {showScreenshots ? 'Screenshots On' : 'Screenshots Off'}
          </PanelBtn>
          <PanelBtn active={showEdgeLabels} onClick={() => setShowEdgeLabels(!showEdgeLabels)}>
            Labels
          </PanelBtn>
          {highlightedPath && (
            <>
              <PanelBtn active={planFocus} onClick={() => setPlanFocus(!planFocus)}>
                Focus Plan
              </PanelBtn>
              <PanelBtn onClick={() => { setHighlightedPath(null); setPlanFocus(false); setPathSource(null); setPathInfo(''); }}>
                Clear Path
              </PanelBtn>
            </>
          )}
          <PanelBtn onClick={downloadKG}>Download ScreenMap</PanelBtn>
          {pathInfo && (
            <span style={{ fontSize: '10px', color: '#dc2626', fontFamily: 'var(--font-mono)', padding: '0 6px' }}>
              {pathInfo}
            </span>
          )}
          <PanelBtn active={edgeFiltersOpen} onClick={() => setEdgeFiltersOpen(!edgeFiltersOpen)}>
            Edges {visibleEdgeCount}/{totalEdgeCount}
          </PanelBtn>
          {/* Edge kind filter chips — opened only when needed to keep the canvas readable. */}
          {edgeFiltersOpen && Object.keys(edgeKindCounts).length > 0 && (
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
                        setGraphMode('custom');
                      }}
                      title={hidden ? `Show ${kind}` : `Hide ${kind}`}
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
          {categoryLegendItems.map(([cat, count]) => (
            <span key={cat} style={{ display: 'flex', alignItems: 'center', gap: '3px' }}>
              <span style={{
                width: 8, height: 8, borderRadius: '2px',
                background: CATEGORY_COLOR[cat] || CATEGORY_COLOR.other,
              }} />
              {cat} <span style={{ color: '#9ca3af', fontFamily: 'var(--font-mono)' }}>{count}</span>
            </span>
          ))}
        </div>
      </Panel>

      <Legend />
    </ReactFlow>
    </>
  );
}

function EdgeDetailPanel({
  edgeData,
  tourId,
  onClose,
  onSelectNode,
}: {
  edgeData: any;
  tourId: string;
  onClose: () => void;
  onSelectNode: (node: any) => void;
}) {
  const raw = edgeData.raw || {};
  const kind = edgeData.kind || raw.kind || 'navigate';
  const action = buildActionText(raw, edgeData.actionLabel || kind);
  const actionBounds = extractActionBounds(raw);
  const actionDetail = [
    raw.trigger_action ? `action=${raw.trigger_action}` : null,
    raw.trigger_widget ? `element=${raw.trigger_widget}` : null,
  ].filter(Boolean).join(' · ');
  const source = edgeData.sourceNode;
  const target = edgeData.targetNode;
  const transitionScreenshotUrl = edgeData.edgeId
    ? `/api/tours/${tourId}/transition-screenshot/${edgeData.edgeId}`
    : '';

  return (
    <div style={{
      position: 'absolute',
      top: 72,
      right: 16,
      zIndex: 36,
      width: 430,
      maxWidth: 'calc(100vw - 40px)',
      background: 'rgba(255,255,255,0.98)',
      border: '1px solid var(--color-border)',
      borderRadius: 8,
      boxShadow: '0 10px 30px rgba(15,23,42,0.16)',
      fontFamily: 'var(--font)',
      overflow: 'hidden',
    }}>
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '10px 12px', borderBottom: '1px solid var(--color-border)',
      }}>
        <div>
          <div style={{ fontSize: 11, fontWeight: 700, color: 'var(--color-gray)', textTransform: 'uppercase', letterSpacing: 0.5 }}>
            Transition
          </div>
          <div style={{ fontSize: 13, fontWeight: 700, marginTop: 2 }}>
            {edgeData.sourceLabel} → {edgeData.targetLabel}
          </div>
        </div>
        <button
          type="button"
          onClick={onClose}
          aria-label="Close transition detail"
          style={{
            background: 'transparent', border: 'none', cursor: 'pointer',
            color: 'var(--color-gray)', fontSize: 18, lineHeight: 1, padding: 4,
          }}
        >
          ×
        </button>
      </div>

      <div style={{ padding: 12 }}>
        <div style={{ display: 'grid', gridTemplateColumns: '1fr 1fr', gap: 10, marginBottom: 12 }}>
          <ScreenshotPreview
            tourId={tourId}
            node={source}
            label="출발 화면"
            overlay={`해야 할 동작: ${action}`}
            preferredUrl={transitionScreenshotUrl}
            highlightBounds={actionBounds}
            highlightLabel={action}
            onClick={() => onSelectNode(source)}
          />
          <ScreenshotPreview
            tourId={tourId}
            node={target}
            label="도착 화면"
            overlay="이 화면으로 이동"
            onClick={() => onSelectNode(target)}
          />
        </div>

        <div style={{
          padding: '9px 10px',
          background: '#f8fafc',
          border: '1px solid var(--color-border)',
          borderRadius: 6,
          fontSize: 12,
          lineHeight: 1.5,
        }}>
          <div style={{ display: 'flex', gap: 12, flexWrap: 'wrap', marginBottom: 8 }}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ color: 'var(--color-gray)', fontSize: 10, fontWeight: 600 }}>간선 타입:</span>
              <Badge>{kind}</Badge>
            </div>
            <div style={{ display: 'flex', alignItems: 'center', gap: 4 }}>
              <span style={{ color: 'var(--color-gray)', fontSize: 10, fontWeight: 600 }}>탐색 방식:</span>
              <Badge>{edgeData.confidence || 'unknown'}</Badge>
            </div>
          </div>
          <div><strong>동작:</strong> {action}</div>
          {actionDetail && (
            <div style={{ color: 'var(--color-gray)', fontFamily: 'var(--font-mono)', fontSize: 11, marginTop: 2 }}>
              {actionDetail}
            </div>
          )}
          {EDGE_KIND_DESC[kind] && (
            <div style={{ color: '#374151', marginTop: 6 }}>
              {EDGE_KIND_DESC[kind]}
            </div>
          )}
          {raw.condition && (
            <div style={{ color: 'var(--color-gray)', marginTop: 6 }}>
              condition: {String(raw.condition)}
            </div>
          )}
          {raw.outcome && (
            <div style={{ color: '#059669', marginTop: 6 }}>
              outcome: {String(raw.outcome)}
            </div>
          )}
          {!actionBounds && (
            <div style={{ color: 'var(--color-gray)', fontSize: 10.5, marginTop: 8 }}>
              이 간선에는 버튼 좌표가 없어 위치 박스 대신 trigger 라벨만 표시합니다.
            </div>
          )}
        </div>
      </div>
    </div>
  );
}

function ScreenshotPreview({
  tourId,
  node,
  label,
  overlay,
  preferredUrl,
  highlightBounds,
  highlightLabel,
  onClick,
}: {
  tourId: string;
  node: any;
  label: string;
  overlay: string;
  preferredUrl?: string;
  highlightBounds?: ActionBounds | null;
  highlightLabel?: string;
  onClick: () => void;
}) {
  const [failed, setFailed] = useState(false);
  const mediaRef = useRef<HTMLDivElement | null>(null);
  const [urlIndex, setUrlIndex] = useState(0);
  const [naturalSize, setNaturalSize] = useState({ width: 0, height: 0 });
  const [mediaSize, setMediaSize] = useState({ width: 0, height: 0 });
  const nodeScreenshotUrl = node?.screenshot_ref && node?.screen_id
    ? `/api/tours/${tourId}/screenshot/${node.screen_id}`
    : '';
  const urls = useMemo(
    () => Array.from(new Set([preferredUrl, nodeScreenshotUrl].filter(Boolean) as string[])),
    [preferredUrl, nodeScreenshotUrl],
  );
  const urlKey = urls.join('|');
  const screenshotUrl = urls[urlIndex] || '';
  const title = node?.label || node?.activity?.split('.').pop() || node?.screen_id || 'Unknown';

  useEffect(() => {
    setFailed(false);
    setUrlIndex(0);
    setNaturalSize({ width: 0, height: 0 });
  }, [urlKey]);

  useEffect(() => {
    const el = mediaRef.current;
    if (!el) return;
    const update = () => {
      const rect = el.getBoundingClientRect();
      setMediaSize({ width: rect.width, height: rect.height });
    };
    update();
    if (typeof ResizeObserver === 'undefined') {
      window.addEventListener('resize', update);
      return () => window.removeEventListener('resize', update);
    }
    const observer = new ResizeObserver(update);
    observer.observe(el);
    return () => observer.disconnect();
  }, []);

  const highlightBox = useMemo(() => {
    if (!highlightBounds || !naturalSize.width || !naturalSize.height || !mediaSize.width || !mediaSize.height) {
      return null;
    }
    const likelyPhone = naturalSize.width / naturalSize.height > 0.35 && naturalSize.width / naturalSize.height < 0.65;
    const normalized = Math.max(highlightBounds.right, highlightBounds.bottom) <= 1;
    const coordWidth = normalized ? 1 : Math.max(likelyPhone ? 1080 : naturalSize.width, highlightBounds.right);
    const coordHeight = normalized ? 1 : Math.max(likelyPhone ? 2400 : naturalSize.height, highlightBounds.bottom);
    const imageAspect = naturalSize.width / naturalSize.height;
    const mediaAspect = mediaSize.width / mediaSize.height;
    const displayWidth = imageAspect > mediaAspect ? mediaSize.width : mediaSize.height * imageAspect;
    const displayHeight = imageAspect > mediaAspect ? mediaSize.width / imageAspect : mediaSize.height;
    const offsetX = (mediaSize.width - displayWidth) / 2;
    const offsetY = (mediaSize.height - displayHeight) / 2;

    const left = offsetX + (highlightBounds.left / coordWidth) * displayWidth;
    const top = offsetY + (highlightBounds.top / coordHeight) * displayHeight;
    const right = offsetX + (highlightBounds.right / coordWidth) * displayWidth;
    const bottom = offsetY + (highlightBounds.bottom / coordHeight) * displayHeight;
    const clampedLeft = Math.max(offsetX, Math.min(offsetX + displayWidth, left));
    const clampedTop = Math.max(offsetY, Math.min(offsetY + displayHeight, top));
    const clampedRight = Math.max(offsetX, Math.min(offsetX + displayWidth, right));
    const clampedBottom = Math.max(offsetY, Math.min(offsetY + displayHeight, bottom));
    if (clampedRight <= clampedLeft || clampedBottom <= clampedTop) return null;
    return {
      left: clampedLeft,
      top: clampedTop,
      width: clampedRight - clampedLeft,
      height: clampedBottom - clampedTop,
      labelLeft: Math.max(6, Math.min(clampedLeft, mediaSize.width - 150)),
      labelTop: Math.max(6, clampedTop - 26),
    };
  }, [highlightBounds, naturalSize, mediaSize]);

  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        position: 'relative',
        minHeight: 210,
        border: '1px solid var(--color-border)',
        borderRadius: 8,
        padding: 0,
        overflow: 'hidden',
        background: '#f5f5f5',
        cursor: node ? 'pointer' : 'default',
        fontFamily: 'var(--font)',
      }}
      title={title}
    >
      <div ref={mediaRef} style={{ position: 'relative', height: 210, background: '#f1f5f9' }}>
        {screenshotUrl && !failed ? (
          <img
            src={screenshotUrl}
            alt={title}
            onLoad={(e) => {
              setNaturalSize({
                width: e.currentTarget.naturalWidth,
                height: e.currentTarget.naturalHeight,
              });
            }}
            onError={() => {
              if (urlIndex < urls.length - 1) {
                setUrlIndex(urlIndex + 1);
              } else {
                setFailed(true);
              }
            }}
            style={{ width: '100%', height: '100%', objectFit: 'contain', objectPosition: 'center', display: 'block' }}
          />
        ) : (
          <div style={{
            height: 210,
            display: 'flex',
            alignItems: 'center',
            justifyContent: 'center',
            color: 'var(--color-gray)',
            fontSize: 12,
            padding: 12,
            textAlign: 'center',
          }}>
            {title}
          </div>
        )}
        {highlightBox && (
          <>
            <div style={{
              position: 'absolute',
              left: highlightBox.left,
              top: highlightBox.top,
              width: highlightBox.width,
              height: highlightBox.height,
              border: '2px solid #ef4444',
              boxShadow: '0 0 0 2px rgba(239,68,68,0.2), 0 0 18px rgba(239,68,68,0.55)',
              borderRadius: 5,
              background: 'rgba(239,68,68,0.10)',
              pointerEvents: 'none',
            }} />
            <span style={{
              position: 'absolute',
              left: highlightBox.labelLeft,
              top: highlightBox.labelTop,
              maxWidth: 'calc(100% - 12px)',
              padding: '3px 6px',
              background: '#ef4444',
              color: '#fff',
              borderRadius: 5,
              fontSize: 10,
              fontWeight: 800,
              lineHeight: 1.2,
              boxShadow: '0 2px 8px rgba(15,23,42,0.24)',
              pointerEvents: 'none',
              whiteSpace: 'nowrap',
              overflow: 'hidden',
              textOverflow: 'ellipsis',
            }}>
              {highlightLabel || '탭'}
            </span>
          </>
        )}
      </div>
      <div style={{
        position: 'absolute', top: 8, left: 8, right: 8,
        display: 'flex', justifyContent: 'space-between', gap: 6,
      }}>
        <span style={{
          padding: '3px 7px',
          background: 'rgba(17,24,39,0.82)',
          color: '#fff',
          borderRadius: 5,
          fontSize: 10,
          fontWeight: 700,
        }}>
          {label}
        </span>
      </div>
      <div style={{
        position: 'absolute',
        left: 8,
        right: 8,
        bottom: 8,
        padding: '6px 8px',
        background: 'rgba(255,255,255,0.94)',
        border: '1px solid rgba(15,23,42,0.12)',
        borderRadius: 6,
        color: '#111827',
        fontSize: 11,
        fontWeight: 700,
        lineHeight: 1.35,
        textAlign: 'left',
      }}>
        {overlay}
      </div>
    </button>
  );
}

function Badge({ children }: { children: React.ReactNode }) {
  return (
    <span style={{
      padding: '2px 7px',
      background: '#111827',
      color: '#fff',
      borderRadius: 999,
      fontSize: 10,
      fontWeight: 700,
      fontFamily: 'var(--font-mono)',
    }}>
      {children}
    </span>
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

function ModeBtn({ children, onClick, active }: { children: React.ReactNode; onClick: () => void; active: boolean }) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '4px 8px',
        fontSize: '10px',
        fontWeight: 600,
        fontFamily: 'var(--font)',
        border: 'none',
        borderRadius: 6,
        cursor: 'pointer',
        background: active ? '#0a0a0a' : 'transparent',
        color: active ? '#fff' : '#525252',
      }}
    >
      {children}
    </button>
  );
}
