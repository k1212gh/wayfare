import React, { useCallback, useEffect, useRef, useState } from 'react';
import { Dashboard } from './Dashboard';
import { ScreenMapView } from './ScreenMapView';
import { ScreenPanel } from './ScreenPanel';
import { SearchFilter } from './SearchFilter';
import { Sidebar } from './Sidebar';

type Page = 'dashboard' | 'graph';

export default function App() {
  const [page, setPage] = useState<Page>('dashboard');
  const [activeTourId, setActiveTourId] = useState('');
  const [graphData, setGraphData] = useState<any>(null);
  const [tourStage, setTourStage] = useState<string>('');
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [filterCategory, setFilterCategory] = useState('');
  const [searchQuery, setSearchQuery] = useState('');
  // Track a tour the user just Ran — once its wireframe is ready, auto-navigate
  const [pendingTourId, setPendingTourId] = useState('');
  const alreadyNavigatedRef = useRef<Set<string>>(new Set());

  const openGraph = useCallback(async (tourId: string) => {
    try {
      const res = await fetch(`/api/tours/${tourId}/graph`);
      if (!res.ok) {
        console.error('Graph fetch failed:', res.status, await res.text());
        alert(`Failed to load graph (${res.status})`);
        return;
      }
      const data = await res.json();
      if (!data?.screen_map?.graph?.nodes) {
        alert('Invalid graph data');
        return;
      }
      setGraphData(data);
      setActiveTourId(tourId);
      setSelectedNode(null);
      setPage('graph');
    } catch (err) {
      console.error('Graph fetch error:', err);
      alert(`Error: ${err}`);
    }
  }, []);

  // When the user clicks Run on a tour card, register it.  Background poll
  // then auto-navigates once the wireframe ScreenMap exists.
  const handleRunStart = useCallback((tourId: string) => {
    setPendingTourId(tourId);
    alreadyNavigatedRef.current.delete(tourId);
  }, []);

  // Background poll: auto-navigate when wireframe is ready for `pendingTourId`,
  // and live-refresh the currently-displayed graph while walk is running.
  useEffect(() => {
    const tick = async () => {
      // Auto-navigate pending tour
      if (pendingTourId && !alreadyNavigatedRef.current.has(pendingTourId)) {
        try {
          const r = await fetch(`/api/tours/${pendingTourId}/graph`);
          if (r.ok) {
            // Graph exists (either wireframe from Stage 2.5 or full ScreenMap)
            alreadyNavigatedRef.current.add(pendingTourId);
            await openGraph(pendingTourId);
            setPendingTourId('');
          }
        } catch {}
      }

      // Live-refresh while on the graph page: fetch tour state + re-fetch graph
      if (page === 'graph' && activeTourId) {
        try {
          const jr = await fetch('/api/tours');
          const jd = await jr.json();
          const tour = (jd.tours || []).find((x: any) => x.tour_id === activeTourId);
          if (tour) {
            setTourStage(tour.stage || '');
            // Re-fetch graph if tour is still progressing
            if (['WALKING', 'PREPROCESSING_DATA', 'CARDS_READY', 'BUILDING_SCREENMAP',
                 'LLM_ANNOTATING'].includes(tour.stage)) {
              const gr = await fetch(`/api/tours/${activeTourId}/graph`);
              if (gr.ok) {
                const gd = await gr.json();
                if (gd?.screen_map?.graph?.nodes) {
                  setGraphData(gd);
                }
              }
            }
          }
        } catch {}
      }
    };
    const h = setInterval(tick, 3000);
    tick();  // immediate
    return () => clearInterval(h);
  }, [pendingTourId, page, activeTourId, openGraph]);

  const stopActiveTour = useCallback(async () => {
    if (!activeTourId) return;
    if (!confirm('Stop walk and force-close the app on the device?')) return;
    await fetch(`/api/tours/${activeTourId}/stop`, { method: 'POST' });
  }, [activeTourId]);

  const startWalkFromGraph = useCallback(async () => {
    if (!activeTourId) return;
    const res = await fetch(`/api/tours/${activeTourId}/run`, { method: 'POST' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(body.detail || `Run failed (${res.status})`);
      return;
    }
    // Stage will switch to WALKING on next poll; live refresh already set up
  }, [activeTourId]);

  const categories = graphData
    ? [...new Set(graphData.screen_map.graph.nodes.map((n: any) => n.functional_category || 'other'))].sort() as string[]
    : [];

  if (page === 'dashboard') {
    return (
      <>
        <Sidebar
          currentPage="dashboard"
          activeTourId={activeTourId}
          onGoDashboard={() => setPage('dashboard')}
          onOpenGraph={openGraph}
        />
        <Dashboard onOpenGraph={openGraph} onRunStart={handleRunStart} />
      </>
    );
  }

  const isRunning = ['WALKING', 'PREPROCESSING_DATA', 'CARDS_READY',
                     'BUILDING_SCREENMAP', 'LLM_ANNOTATING', 'PREPROCESSING',
                     'STATIC_ANALYZING'].includes(tourStage);

  // Two-tier coverage — separate "reachable" (exists) from "actionable"
  // (has screenshot + UI elements, usable by MobileGPT-style agent). The
  // gap between them = activities that need runtime JIT capture.
  const coverageStats = (() => {
    if (!graphData) return null;
    const nodes = graphData.screen_map?.graph?.nodes || [];
    let total = 0;
    let reachable = 0;
    let actionable = 0;
    for (const n of nodes) {
      if (n.screen_id === 'system:external_entry') continue;
      total += 1;
      const s = n.status || '';
      if (s === 'enriched' || s === 'resolved' || s === 'partial' || s === 'unknown' || s === 'entry' || s === 'probed') reachable += 1;
      if (n.screenshot_ref && (n.widgets?.length ?? 0) > 0) actionable += 1;
    }
    if (total === 0) return null;
    return {
      reachable, actionable, total,
      reachPct: Math.round((reachable / total) * 100),
      actPct: Math.round((actionable / total) * 100),
    };
  })();

  const meta = graphData?.screen_map;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', fontFamily: 'var(--font)' }}>
      <Sidebar
        currentPage="graph"
        activeTourId={activeTourId}
        onGoDashboard={() => setPage('dashboard')}
        onOpenGraph={openGraph}
      />
      {/* Navbar */}
      <header style={{
        display: 'flex', alignItems: 'center', gap: '16px',
        padding: '0 20px 0 60px', height: '48px',
        borderBottom: '1px solid var(--color-border)', background: 'var(--color-white)', flexShrink: 0,
      }}>
        <button onClick={() => setPage('dashboard')} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          fontSize: '13px', color: 'var(--color-gray)', padding: '4px 0',
        }}>
          &larr; Back
        </button>
        <div style={{ width: '1px', height: '20px', background: 'var(--color-border)' }} />
        <span style={{ fontSize: '13px', fontWeight: 600 }}>
          {meta?.app_name || 'Graph'}
        </span>
        {meta && (
          <span style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>
            {meta.metadata.total_nodes}N / {meta.metadata.total_edges}E
          </span>
        )}
        {/* Two-tier coverage: actionable (UI captured) vs reachable (exists) */}
        {coverageStats && (
          <div
            title={
              `Actionable: ${coverageStats.actionable} / ${coverageStats.total} (${coverageStats.actPct}%)\n` +
              `  — MobileGPT 같은 AI 에이전트가 바로 사용 가능한 화면 (screenshot + UI elements 모두 확보)\n\n` +
              `Reachable: ${coverageStats.reachable} / ${coverageStats.total} (${coverageStats.reachPct}%)\n` +
              `  — adb am start로 확인된 도달 가능 activity (probed 포함)\n` +
              `  — UI 캡처 없는 probed 노드는 에이전트 런타임에 JIT 캡처로 승격`
            }
            style={{ display: 'flex', alignItems: 'center', gap: 6, cursor: 'help' }}
          >
            <div style={{
              width: 90, height: 10, background: 'var(--color-border)',
              borderRadius: 2, overflow: 'hidden', position: 'relative',
            }}>
              {/* Reachable (amber, background layer) */}
              <div style={{
                position: 'absolute', top: 0, left: 0, bottom: 0,
                width: `${Math.max(2, coverageStats.reachPct)}%`,
                background: '#fbbf24',
                transition: 'width 0.3s ease',
              }} />
              {/* Actionable (green, overlay) */}
              <div style={{
                position: 'absolute', top: 0, left: 0, bottom: 0,
                width: `${Math.max(2, coverageStats.actPct)}%`,
                background: '#22c55e',
                transition: 'width 0.3s ease',
              }} />
            </div>
            <span style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>
              <strong style={{ color: '#22c55e' }}>{coverageStats.actionable}</strong>
              {' / '}
              <span style={{ color: '#b45309' }}>{coverageStats.reachable}</span>
              {' / '}{coverageStats.total}
              <span style={{ marginLeft: 4 }}>({coverageStats.actPct}% · {coverageStats.reachPct}%)</span>
            </span>
          </div>
        )}
        {/* Live tour status + Stop button — shown while walk is active */}
        {isRunning && (
          <div style={{ display: 'flex', alignItems: 'center', gap: '10px', marginLeft: 'auto' }}>
            <span style={{ fontSize: '11px', color: '#f97316', fontFamily: 'var(--font-mono)' }}>
              <span style={{
                display: 'inline-block', width: 7, height: 7, borderRadius: '50%',
                background: '#f97316', marginRight: 6, animation: 'pulse 1.2s ease-in-out infinite',
              }} />
              {tourStage} — live
            </span>
            <button
              onClick={stopActiveTour}
              style={{
                padding: '5px 12px', fontSize: '11px', fontWeight: 600,
                background: '#dc2626', color: '#fff', border: 'none',
                borderRadius: '5px', cursor: 'pointer', fontFamily: 'var(--font)',
              }}
            >
              Stop
            </button>
          </div>
        )}
        {!isRunning && tourStage && (
          <div style={{ marginLeft: 'auto', display: 'flex', alignItems: 'center', gap: '10px' }}>
            <span style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>
              {tourStage}
            </span>
            {/* Start/Re-run walk from inside the graph view */}
            {['UPLOADED', 'FAILED', 'CANCELLED', 'SCREENMAP_GENERATED', 'ANNOTATED', 'STATIC_DONE'].includes(tourStage) && (
              <button
                onClick={startWalkFromGraph}
                title={tourStage === 'SCREENMAP_GENERATED' || tourStage === 'ANNOTATED'
                  ? '동적 탐색 재실행 — wireframe에 선언된 활동을 다시 방문합니다'
                  : '동적 탐색 시작'}
                style={{
                  padding: '5px 12px', fontSize: '11px', fontWeight: 600,
                  background: '#0a0a0a', color: '#fff', border: 'none',
                  borderRadius: '5px', cursor: 'pointer', fontFamily: 'var(--font)',
                }}
              >
                {tourStage === 'SCREENMAP_GENERATED' || tourStage === 'ANNOTATED' ? '▶ Re-walk' : '▶ Start walk'}
              </button>
            )}
          </div>
        )}
      </header>

      {/* Graph + Detail */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <div style={{ flex: 1, position: 'relative', background: 'var(--color-surface)' }}>
          <SearchFilter onSearch={setSearchQuery} onFilterCategory={setFilterCategory} categories={categories} />
          {graphData && (
            <ScreenMapView
              graph={graphData.screen_map.graph}
              onNodeSelect={setSelectedNode}
              filterCategory={filterCategory}
              searchQuery={searchQuery}
              tourId={activeTourId}
              appName={graphData.screen_map.app_name || graphData.screen_map.package_name}
            />
          )}
        </div>
        {selectedNode && (
          <aside style={{
            width: '320px', borderLeft: '1px solid var(--color-border)',
            background: 'var(--color-white)', overflow: 'auto', flexShrink: 0,
          }}>
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '12px 16px', borderBottom: '1px solid var(--color-border)',
            }}>
              <span style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase' as const, color: 'var(--color-gray)', letterSpacing: '0.5px' }}>Detail</span>
              <button onClick={() => setSelectedNode(null)} style={{
                background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: 'var(--color-gray)',
              }}>&times;</button>
            </div>
            <ScreenPanel node={selectedNode} tourId={activeTourId} />
          </aside>
        )}
      </div>
    </div>
  );
}
