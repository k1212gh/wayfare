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
  Handle,
  Position,
  useNodesState,
  useEdgesState,
  useInternalNode,
  MarkerType,
  NodeMouseHandler,
  getBezierPath,
  EdgeProps,
  EdgeLabelRenderer,
} from '@xyflow/react';
import '@xyflow/react/dist/style.css';
import dagre from 'dagre';

// ───────────────────────────────────────────────────────────
//  InstantTooltip — custom tooltip that appears immediately on hover
//  (browser's native `title` attribute has ~1s delay).
// ───────────────────────────────────────────────────────────
function InstantTooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const [show, setShow] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  if (!text) return <>{children}</>;
  return (
    <span
      style={{ display: 'contents' }}
      onMouseEnter={(e) => {
        setPos({ x: e.clientX, y: e.clientY });
        setShow(true);
      }}
      onMouseMove={(e) => setPos({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <div style={{
          position: 'fixed',
          left: pos.x + 14,
          top: pos.y + 14,
          maxWidth: 320,
          background: 'rgba(17, 24, 39, 0.96)',
          color: '#f9fafb',
          padding: '8px 10px',
          borderRadius: 6,
          fontSize: 11,
          lineHeight: 1.45,
          fontFamily: "'Inter', sans-serif",
          zIndex: 100000,
          pointerEvents: 'none',
          whiteSpace: 'pre-wrap',
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          border: '1px solid rgba(255,255,255,0.1)',
        }}>
          {text}
        </div>
      )}
    </span>
  );
}

// ───────────────────────────────────────────────────────────
//  FloatingEdge — computes the attachment point on each node's
//  border based on the line from source center → target center,
//  so edges follow nodes as they are dragged.
// ───────────────────────────────────────────────────────────
function getNodeIntersection(sourceNode: any, targetNode: any) {
  // Source rect
  const sw = sourceNode.measured?.width  || sourceNode.width  || 180;
  const sh = sourceNode.measured?.height || sourceNode.height || 100;
  const sx = sourceNode.internals.positionAbsolute.x + sw / 2;
  const sy = sourceNode.internals.positionAbsolute.y + sh / 2;

  const tw = targetNode.measured?.width  || targetNode.width  || 180;
  const th = targetNode.measured?.height || targetNode.height || 100;
  const tx = targetNode.internals.positionAbsolute.x + tw / 2;
  const ty = targetNode.internals.positionAbsolute.y + th / 2;

  // Ray from source center to target center, clipped to source rectangle border
  const w = sw / 2, h = sh / 2;
  const dx = tx - sx, dy = ty - sy;
  if (dx === 0 && dy === 0) return { x: sx, y: sy };
  const scaleX = Math.abs(dx) / w;
  const scaleY = Math.abs(dy) / h;
  const scale = Math.max(scaleX, scaleY);
  return { x: sx + dx / scale, y: sy + dy / scale };
}

const EDGE_KIND_DESC: Record<string, string> = {
  navigate: '확인된 Activity 간 직접 startActivity 호출 (DEX 분석 또는 탐색에서 관찰됨)',
  two_hop: '난독화 helper class를 거쳐 startActivity 호출 (N-hop 역추적)',
  contains: '같은 Activity 내 Fragment 교체 (탭/ViewPager)',
  launcher: 'android.intent.action.MAIN + LAUNCHER/APP_* 카테고리',
  intent_filter: 'Manifest의 intent-filter로 선언된 deep link 진입점',
  pending_intent: '알림/위젯/AlarmManager에서 시스템 측이 실행',
  overlay: 'Dialog/BottomSheet 레이어 (화면 전환 아님)',
  static_ref: '난독화 helper에서 startActivity 참조 — 최종 호출 Activity 미확정',
  global: '다수 화면에서 공유되는 컴포넌트 (하단탭/드로어 등)',
  back: '시스템 뒤로가기 키 전환',
};

function FloatingEdge({ id, source, target, style, markerEnd, data }: EdgeProps) {
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
      {/* Visible styled path (bottom) */}
      <path id={id} d={path} style={{ ...(style || {}), pointerEvents: 'none' }} markerEnd={markerEnd} fill="none" />
      {/* Wider transparent hit-test path on top of visible path */}
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
// ───────────────────────────────────────────────────────────

interface ScreenMapViewProps {
  graph: { entry_node: string; nodes: any[]; edges: any[] };
  onNodeSelect: (node: any) => void;
  filterCategory: string;
  searchQuery: string;
  tourId: string;
  appName?: string;
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
    two_hop:        { stroke: '#6d28d9', width: 1.8, showLabel: true },   // purple-ish blue
    contains:       { stroke: '#0ea5e9', width: 1.5, dash: '4,4', showLabel: true },
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

  for (const e of edges) {
    const kind: string = e.kind || (e.trigger_action === 'press_back' ? 'back' : 'navigate');
    const confidence: string = e.confidence || (e.source === 'walk' ? 'observed' : 'static_intent');
    const s = STYLE_BY_KIND[kind] || STYLE_BY_KIND.navigate;

    const rawLabel = e.trigger_widget
      ? `${e.trigger_action} ${e.trigger_widget}`
      : e.trigger_action || '';
    const label = s.showLabel && rawLabel
      ? (rawLabel.length > 22 ? rawLabel.slice(0, 22) + '…' : rawLabel)
      : '';

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

function ScreenshotNode({ data }: { data: any }) {
  const color = data.color || '#9ca3af';
  const screenshotUrl = `/api/tours/${data.tourId}/screenshot/${data.screen_id}`;
  const [imgError, setImgError] = useState(false);
  // Static-analysis completeness
  const status: string = data.status || 'resolved';
  const STATUS_DOT: Record<string, string> = {
    resolved: '#22c55e',    // green — fully understood from static XML
    partial:  '#f59e0b',    // amber — has Fragment/ViewPager
    unknown:  '#ef4444',    // red — RecyclerView/WebView/ListView present
    entry:    '#8b5cf6',    // violet — entry handler
  };
  const statusColor = STATUS_DOT[status] || '#9ca3af';
  // Capture priority badge (A/B/C) — only render if set
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
      {/* Single invisible target handle — floating edge computes attachment point */}
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
        {/* Status dot — top-left */}
        <span title={`Static status: ${status}`} style={{
          position: 'absolute', top: 6, left: 6, width: 10, height: 10,
          borderRadius: '50%', background: statusColor,
          border: '2px solid #fff', boxShadow: '0 0 2px rgba(0,0,0,0.3)',
        }} />
        {/* Capture-priority letter — next to status dot */}
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
        {/* Category tag — top-right */}
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


function Legend() {
  const edgeItems = [
    { color: '#2563eb', label: 'Navigate — 확인된 화면 전환',
      tip: '두 Activity 사이의 직접적인 startActivity 호출.\n정적 분석(DEX)에서 const-class Target + startActivity 패턴이 같은 메서드에서 발견되었거나, 동적 탐색에서 실제 전이가 관찰된 경우. ScreenMap에서 가장 신뢰도 높은 엣지.' },
    { color: '#6d28d9', label: 'Two-hop — 헬퍼 경유',
      tip: 'Activity A → 익명 헬퍼 클래스 → target.\nR8 난독화 앱에서 흔한 패턴. A가 헬퍼를 new-instance로 만들고, 그 헬퍼가 startActivity를 호출. 코드 체인 4-hop까지 역추적.' },
    { color: '#0ea5e9', label: 'Contains — Fragment 포함', dash: '4,4',
      tip: '같은 Activity 내에서 Fragment가 교체됨(탭 전환 등).\n화면 전환이 아닌 내부 구조 관계. Back stack에 쌓이지 않음.' },
    { color: '#16a34a', label: 'Launcher — 앱 진입',
      tip: 'android.intent.action.MAIN + LAUNCHER(또는 APP_*) 카테고리를 가진 Activity.\n앱 런처 아이콘 탭 시 실행되는 메인 화면.' },
    { color: '#16a34a', label: 'Intent-filter — Deep link', dash: '4,2',
      tip: 'Manifest의 intent-filter로 선언된 외부 진입점.\n다른 앱/브라우저가 특정 URL 스킴(spotify://...)이나 액션(ACTION_SEND 등)으로 직접 진입.' },
    { color: '#65a30d', label: 'PendingIntent — 시스템 진입', dash: '5,3',
      tip: '알림/위젯/AlarmManager가 시스템 측에서 실행하는 Activity.\n사용자가 알림을 탭하거나 위젯이 트리거될 때 직접 호출.' },
    { color: '#f59e0b', label: 'Overlay — Dialog', dash: '3,3',
      tip: '현재 Activity 위에 덮이는 AlertDialog / BottomSheet.\n화면이 전환되지 않고 현재 화면은 유지된 채 위에 레이어로 올라감.' },
    { color: '#cbd5e1', label: 'Static-ref — 정적 참조', dash: '2,4',
      tip: '난독화 helper 클래스에서 해당 Activity로 startActivity 호출이 발견됐지만,\n그 helper의 최종 호출 Activity(소유자)를 특정하지 못한 경우. 약한 시그널로 표시.' },
    { color: '#9ca3af', label: 'Global — 공통 컴포넌트', dash: '2,3',
      tip: '3개 이상의 서로 다른 소스에서 같은 target으로 같은 trigger를 공유.\n하단 네비게이션바, 드로어 메뉴 같은 전역 컴포넌트에서 나오는 엣지.' },
    { color: '#d4d4d4', label: 'Back', dash: '6,4',
      tip: '시스템 뒤로가기 키(KEYCODE_BACK)로 발생한 전환.' },
  ];

  const statusItems = [
    { color: '#cbd5e1', label: 'Declared — 정적 선언만 (미탐색)',
      tip: 'AndroidManifest에 선언돼 있지만 아직 동적 탐색에서 방문되지 않고 LLM 분석도 없는 상태. 점선 테두리 + 흐림 처리.' },
    { color: '#3b82f6', label: 'Probed — 도달 확인 (JIT 캡처 대기)',
      tip: '정상 UI 네비게이션으로는 도달 못했지만 `adb shell am start -W`로 강제 런치했을 때 ActivityManager가 정상 응답한 경우.\n로그인/파라미터 게이트 때문에 onCreate에서 finish()가 불리면 UI는 캡처 안 됨.\n\n⚡ JIT (Just-In-Time) 전략: MobileGPT 같은 AI 에이전트가 태스크 수행 중 이 화면에 실제 도달하면 그 시점에 uiautomator로 스크린샷/UI 요소를 즉시 캡처하고 노드를 resolved로 승격.\n파이프라인 단계에선 도달 가능성만 기록하고 데이터 수집은 런타임으로 위임.' },
    { color: '#d97706', label: 'System-triggered — 외부 진입만',
      tip: '알림·위젯·AlarmManager로만 진입하는 활동. 앱 UI로는 도달 불가. 그래프 상단에 모아둠. 앰버 테두리 + 배경.' },
    { color: '#22c55e', label: 'Resolved/Enriched — 완료',
      tip: 'Resolved: 레이아웃 XML에 동적 요소(WebView/RecyclerView)가 없어 정적 분석으로 완전히 이해됨.\nEnriched: 동적 탐색에서 방문되어 스크린샷과 UI 요소 데이터가 수집됨.' },
    { color: '#f59e0b', label: 'Partial — Fragment/ViewPager',
      tip: '화면이 Fragment/ViewPager를 포함해 런타임에 내용이 바뀜.\n구조는 파악했지만 내부 Fragment 목록과 현재 활성 페이지는 동적 탐색이 필요.' },
    { color: '#ef4444', label: 'Unknown — 동적 탐색 필수',
      tip: 'WebView/RecyclerView/ListView를 포함해 정적 분석으로는 콘텐츠 파악 불가.\n리스트 아이템 클릭 목적지는 Adapter 런타임 바인딩에 의존.' },
    { color: '#8b5cf6', label: 'Entry / External',
      tip: 'system:external_entry 가상 노드 또는 앱의 초기 진입점.\n외부(시스템/딥링크/위젯/알림)로부터 앱이 시작되는 경로들의 허브.' },
    { color: '#6366f1', label: 'Fragment (host 포함)',
      tip: 'Activity 내부의 Fragment 화면.\n얇은 인디고 테두리 + 인디고 배경.\nActivity host 노드와는 `contains` 엣지로 연결 (점선 하늘색).\n같은 Activity의 Fragment끼리 탭 전환이 일어나면 fragment_nav 엣지로 이어짐.' },
  ];

  const row = (it: any, idx: number) => (
    <div
      key={idx}
      title={it.tip}
      style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'help', padding: '1px 0' }}
    >
      {it.dash !== undefined || it.color === '#d4d4d4' || it.color === '#cbd5e1' || it.color === '#9ca3af' || it.color === '#2563eb' || it.color === '#6d28d9' || it.color === '#0ea5e9' || it.color === '#16a34a' || it.color === '#65a30d' || it.color === '#f59e0b' ? (
        // edge style preview (line)
        <svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke={it.color} strokeWidth="2" strokeDasharray={it.dash} /></svg>
      ) : (
        <span style={{ width: 10, height: 10, borderRadius: '50%', background: it.color, border: '2px solid #fff' }} />
      )}
      <span>{it.label}</span>
    </div>
  );

  return (
    <div style={{
      position: 'absolute', bottom: 16, right: 16, padding: '12px 14px',
      background: 'rgba(30,30,40,0.92)', color: '#e5e7eb',
      borderRadius: '10px', fontSize: '11px', lineHeight: 1.6,
      fontFamily: "'Inter', sans-serif", zIndex: 10, maxWidth: 280,
    }}>
      <div style={{ marginBottom: 10 }}>
        <div style={{ fontWeight: 600, color: '#9ca3af', marginBottom: 4, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Edge types <span style={{ color: '#6b7280', textTransform: 'none' }}>(hover for detail)</span>
        </div>
        {edgeItems.map((it, i) => (
          <InstantTooltip key={i} text={it.tip}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'help' }}>
              <svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke={it.color} strokeWidth="2" strokeDasharray={it.dash} /></svg>
              <span>{it.label}</span>
            </div>
          </InstantTooltip>
        ))}
      </div>
      <div>
        <div style={{ fontWeight: 600, color: '#9ca3af', marginBottom: 4, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Node status <span style={{ color: '#6b7280', textTransform: 'none' }}>(hover for detail)</span>
        </div>
        {statusItems.map((it, i) => (
          <InstantTooltip key={i} text={it.tip}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'help' }}>
              <span style={{ width: 10, height: 10, borderRadius: '50%', background: it.color, border: '2px solid #fff' }} />
              <span>{it.label}</span>
            </div>
          </InstantTooltip>
        ))}
      </div>
      <div style={{ marginTop: 10 }}>
        <div style={{ fontWeight: 600, color: '#9ca3af', marginBottom: 4, fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Capture priority <span style={{ color: '#6b7280', textTransform: 'none' }}>(hover for detail)</span>
        </div>
        {[
          { letter: 'A', bg: '#059669', label: 'User screen — 탐색/캡처 대상',
            tip: 'MobileGPT 같은 에이전트가 실제로 상호작용하는 화면. 기본값이며 scan에서 우선순위로 재방문한다.' },
          { letter: 'B', bg: '#94a3b8', label: 'Plumbing — UI 없음, skip',
            tip: 'HandleApiCalls, Proxy, Trampoline 류. onCreate에서 finish()를 불러 UI가 거의 없음. 에이전트는 건드릴 일이 없음. 그래프에 존재는 하되 투명도 낮춰 시야에서 빠지게 함.' },
          { letter: 'C', bg: '#8b5cf6', label: 'Deep-link entry — intent_filter로만 진입',
            tip: 'VIEW 액션 + scheme/host 조합의 intent_filter를 가진 activity. 앱 UI에서 탭으로 가는 게 아니라 외부 URL/다른 앱에서 불러 들어오는 진입점이라 Scan에서 건너뛰는 게 맞음.' },
        ].map((it, i) => (
          <InstantTooltip key={i} text={it.tip}>
            <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'help' }}>
              <span style={{
                display: 'inline-block',
                padding: '1px 6px',
                borderRadius: 3,
                background: it.bg, color: '#fff',
                fontSize: '10px', fontWeight: 700,
                fontFamily: "'JetBrains Mono', monospace",
                minWidth: 14, textAlign: 'center' as const,
              }}>{it.letter}</span>
              <span>{it.label}</span>
            </div>
          </InstantTooltip>
        ))}
      </div>
    </div>
  );
}

const nodeTypes = { screenshotNode: ScreenshotNode };
const edgeTypes = { floating: FloatingEdge };

export function ScreenMapView({ graph, onNodeSelect, filterCategory, searchQuery, tourId, appName }: ScreenMapViewProps) {
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
  const filteredEdges = useMemo(
    () => graph.edges.filter((e: any) => filteredNodeIds.has(e.from) && filteredNodeIds.has(e.to)),
    [graph.edges, filteredNodeIds]
  );

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
      <Background variant={BackgroundVariant.Dots} gap={20} size={1} color="#d4d4d4" />
      <Controls showInteractive={false} style={{ border: '1px solid #e5e5e5', borderRadius: '8px', overflow: 'hidden' }} />
      <MiniMap
        nodeColor={(n) => CATEGORY_COLOR[(n.data as any)?.functional_category || 'other'] || '#9ca3af'}
        maskColor="rgba(0,0,0,0.06)"
        style={{ border: '1px solid #e5e5e5', borderRadius: '8px', overflow: 'hidden' }}
      />

      <Panel position="top-right">
        <div style={{ display: 'flex', gap: '6px', alignItems: 'center' }}>
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
