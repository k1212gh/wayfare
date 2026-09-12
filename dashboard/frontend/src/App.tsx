import React, { useCallback, useEffect, useRef, useState } from 'react';
import { AppStateProvider, useAppState } from './app/AppState';
import { Shell, Page } from './app/Shell';
import { ProjectsPage } from './pages/ProjectsPage';
import { DevicesPage } from './pages/DevicesPage';
import { ModelsPage } from './pages/ModelsPage';
import { FlowPage, FlowTopRight } from './pages/FlowPage';
import { DevicePicker } from './dashboard/DevicePicker';
import { tourTitle } from './dashboard/tourTitle';
import { IconArrowLeft } from './app/icons';

const PROGRESSING = ['WALKING', 'PREPROCESSING_DATA', 'CARDS_READY', 'BUILDING_SCREENMAP', 'LLM_ANNOTATING'];

export default function App() {
  return (
    <AppStateProvider>
      <Wayfare />
    </AppStateProvider>
  );
}

function parseHash(h: string): { seg: string; id?: string } {
  const [seg, id] = h.replace(/^#\/?/, '').split('/');
  return { seg, id };
}

function Wayfare() {
  const { tours, runTour, stopTour } = useAppState();
  // 초기 라우트는 렌더 시점의 해시로 정한다 (StrictMode 의 이중 effect 가 해시를 먼저 덮어쓰는 문제 회피)
  const initial = useRef(parseHash(window.location.hash));
  const [page, setPage] = useState<Page>(() => {
    const s = initial.current.seg;
    return s === 'devices' || s === 'models' ? s : 'projects';
  });
  const pendingFlowRef = useRef<string>(initial.current.seg === 'flow' && initial.current.id ? initial.current.id : '');
  const [activeTourId, setActiveTourId] = useState('');
  const [graphData, setGraphData] = useState<any>(null);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [selectedEdgeData, setSelectedEdgeData] = useState<any | null>(null);
  const [pendingTourId, setPendingTourId] = useState('');
  const navigated = useRef<Set<string>>(new Set());

  const activeTour = tours.find((t) => t.tour_id === activeTourId);
  const tourStage = activeTour?.stage || '';

  const openFlow = useCallback(async (tourId: string) => {
    try {
      const res = await fetch(`/api/tours/${tourId}/graph`);
      if (!res.ok) { alert(`지도를 불러오지 못했습니다 (${res.status})`); return; }
      const data = await res.json();
      if (!data?.screen_map?.graph?.nodes) { alert('지도 데이터가 올바르지 않습니다'); return; }
      setGraphData(data); setActiveTourId(tourId); setSelectedNode(null); setSelectedEdgeData(null); setPage('flow');
    } catch (err) { alert(`오류: ${err}`); }
  }, []);

  const handleRunStart = useCallback((tourId: string) => { setPendingTourId(tourId); navigated.current.delete(tourId); }, []);

  // 해시 라우팅 — #/flow/<tourId>, #/devices, #/models. 새로고침해도 같은 화면으로.
  useEffect(() => {
    if (pendingFlowRef.current) { const id = pendingFlowRef.current; openFlow(id).finally(() => { pendingFlowRef.current = ''; }); }
    const onChange = () => {
      const { seg, id } = parseHash(window.location.hash);
      if (seg === 'flow' && id) openFlow(id);
      else if (seg === 'devices' || seg === 'models') setPage(seg);
      else setPage('projects');
    };
    window.addEventListener('hashchange', onChange);
    return () => window.removeEventListener('hashchange', onChange);
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, []);
  useEffect(() => {
    if (pendingFlowRef.current) return;   // 초기 지도 열기가 끝날 때까지 해시를 건드리지 않는다
    const want = page === 'flow' && activeTourId ? `#/flow/${activeTourId}` : page === 'projects' ? '#/' : `#/${page}`;
    if (window.location.hash !== want) window.history.replaceState(null, '', want);
  }, [page, activeTourId]);

  // 실행 직후 지도(와이어프레임)가 생기면 자동으로 열고, 진행 중이면 3초마다 다시 읽는다.
  useEffect(() => {
    const tick = async () => {
      if (pendingTourId && !navigated.current.has(pendingTourId)) {
        try {
          const r = await fetch(`/api/tours/${pendingTourId}/graph`);
          if (r.ok) { navigated.current.add(pendingTourId); await openFlow(pendingTourId); setPendingTourId(''); }
        } catch {}
      }
      if (page === 'flow' && activeTourId && PROGRESSING.includes(tourStage)) {
        try {
          const gr = await fetch(`/api/tours/${activeTourId}/graph`);
          if (gr.ok) { const gd = await gr.json(); if (gd?.screen_map?.graph?.nodes) setGraphData(gd); }
        } catch {}
      }
    };
    const h = setInterval(tick, 3000);
    tick();
    return () => clearInterval(h);
  }, [pendingTourId, page, activeTourId, tourStage, openFlow]);

  // 라벨링이 끝나면 한 번 더 읽어 최종 라벨을 반영
  const prevStage = useRef(tourStage);
  useEffect(() => {
    if (page === 'flow' && activeTourId && prevStage.current !== tourStage && ['ANNOTATED', 'SCREENMAP_GENERATED'].includes(tourStage)) {
      fetch(`/api/tours/${activeTourId}/graph`).then((r) => r.ok ? r.json() : null).then((gd) => { if (gd?.screen_map?.graph?.nodes) setGraphData(gd); }).catch(() => {});
    }
    prevStage.current = tourStage;
  }, [tourStage, page, activeTourId]);

  const crumbs = page === 'flow' && activeTour
    ? (<><button className="wf-btn ghost sm" onClick={() => setPage('projects')} style={{ marginLeft: -8 }}><IconArrowLeft size={14} /> 프로젝트</button><span className="sep">/</span><b className="wf-ellipsis">{graphData?.screen_map?.app_name || tourTitle(activeTour)}</b></>)
    : page === 'projects' ? <b>프로젝트</b> : page === 'devices' ? <b>기기</b> : <b>모델</b>;

  const topRight = page === 'flow'
    ? <><FlowTopRight graphData={graphData} tourStage={tourStage} onStop={() => activeTourId && stopTour(activeTourId)} onStartWalk={async () => { if (activeTourId && await runTour(activeTourId)) handleRunStart(activeTourId); }} /><DevicePicker /></>
    : <DevicePicker />;

  return (
    <Shell page={page} onNavigate={setPage} activeTourId={activeTourId} onOpenFlow={openFlow} crumbs={crumbs} topRight={topRight} fill={page === 'flow'}>
      {page === 'projects' && <ProjectsPage onOpenFlow={openFlow} onRunStart={handleRunStart} />}
      {page === 'devices' && <DevicesPage />}
      {page === 'models' && <ModelsPage />}
      {page === 'flow' && (
        <FlowPage
          graphData={graphData} tourId={activeTourId} tourStage={tourStage}
          selectedNode={selectedNode} onSelectNode={setSelectedNode}
          selectedEdgeData={selectedEdgeData} onSelectedEdgeDataChange={setSelectedEdgeData}
          onStop={() => activeTourId && stopTour(activeTourId)}
          onStartWalk={async () => { if (activeTourId && await runTour(activeTourId)) handleRunStart(activeTourId); }}
        />
      )}
    </Shell>
  );
}
