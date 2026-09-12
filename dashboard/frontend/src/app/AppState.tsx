import React, { createContext, useCallback, useContext, useEffect, useMemo, useState } from 'react';
import { Tour, Coverage, Device, EmulatorInfo, isRunning } from '../dashboard/types';

/* -------------------------------------------------------------------------
 * 앱 전역 상태 — 투어 목록·기기·에뮬레이터·선택 시리얼과 그에 대한 액션.
 * 프로젝트/기기/흐름 페이지가 같은 데이터를 보므로 한 곳에서 3초 폴링한다.
 * (이전 Dashboard.tsx 의 fetch/액션 로직을 그대로 옮김 — API 계약은 동일)
 * -----------------------------------------------------------------------*/

export interface AppState {
  tours: Tour[];
  device: { connected: boolean; devices: Device[] };
  emuStatus: EmulatorInfo[];
  selectedSerial: string;
  setSelectedSerial: (s: string) => void;
  uploading: boolean;
  refresh: () => Promise<void>;
  uploadFiles: (files: FileList | File[]) => Promise<string | null>;
  runTour: (tourId: string) => Promise<boolean>;
  stopTour: (tourId: string) => Promise<void>;
  pauseTour: (tourId: string) => Promise<void>;
  resumeTour: (tourId: string) => Promise<void>;
  retryFromStage: (tourId: string, stage: number) => Promise<boolean>;
  deleteTour: (tourId: string) => Promise<void>;
  startEmulator: (opts?: { safe?: boolean; cold?: boolean }) => Promise<void>;
  killEmulator: (serial?: string) => Promise<void>;
}

const Ctx = createContext<AppState | null>(null);

function sortToursNewestFirst(a: Tour, b: Tour): number {
  const byTime = (b.started_at || 0) - (a.started_at || 0);
  if (byTime !== 0) return byTime;
  return b.tour_id.localeCompare(a.tour_id);
}

export function AppStateProvider({ children }: { children: React.ReactNode }) {
  const [tours, setTours] = useState<Tour[]>([]);
  const [device, setDevice] = useState<{ connected: boolean; devices: Device[] }>({ connected: false, devices: [] });
  const [emuStatus, setEmuStatus] = useState<EmulatorInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [selectedSerial, setSelectedSerial] = useState('');

  // 선택 시리얼을 실제 연결 목록과 맞춘다: 사라지면 비우고, 하나뿐이면 자동 선택.
  useEffect(() => {
    const serials = device.devices.map((d) => d.serial);
    if (selectedSerial && !serials.includes(selectedSerial)) { setSelectedSerial(''); return; }
    if (!selectedSerial && serials.length === 1) setSelectedSerial(serials[0]);
  }, [device.devices, selectedSerial]);

  const fetchTours = useCallback(async () => {
    try {
      const res = await fetch('/api/tours');
      const data = await res.json();
      setTours((prev) => {
        const covMap = new Map(prev.map((j) => [j.tour_id, j.coverage]));
        return ((data.tours || []) as Tour[]).slice().sort(sortToursNewestFirst)
          .map((j) => ({ ...j, coverage: j.coverage ?? covMap.get(j.tour_id) }));
      });
    } catch { /* backend down — keep last list */ }
  }, []);
  const fetchDevice = useCallback(async () => {
    try { const res = await fetch('/api/device'); setDevice(await res.json()); } catch {}
  }, []);
  const fetchEmu = useCallback(async () => {
    try { const res = await fetch('/api/emulator/status'); const d = await res.json(); setEmuStatus(d.emulators || []); } catch {}
  }, []);
  const refresh = useCallback(async () => { await Promise.all([fetchTours(), fetchDevice(), fetchEmu()]); }, [fetchTours, fetchDevice, fetchEmu]);

  useEffect(() => {
    refresh();
    const h = setInterval(refresh, 3000);
    return () => clearInterval(h);
  }, [refresh]);

  // 탐색 중인 투어는 /walk-live 로 커버리지를 2초마다 덧붙인다.
  const walkingKey = tours.filter((j) => j.stage === 'WALKING').map((j) => j.tour_id).join(',');
  useEffect(() => {
    if (!walkingKey) return;
    const ids = walkingKey.split(',');
    let cancelled = false;
    const poll = async () => {
      const updates = await Promise.all(ids.map(async (id) => {
        try { const r = await fetch(`/api/tours/${id}/walk-live`); const d = await r.json(); return { id, coverage: d.coverage as Coverage | undefined }; }
        catch { return { id, coverage: undefined }; }
      }));
      if (cancelled) return;
      setTours((prev) => prev.map((j) => {
        const u = updates.find((x) => x.id === j.tour_id);
        return !u || u.coverage === undefined ? j : { ...j, coverage: u.coverage };
      }));
    };
    poll();
    const t = setInterval(poll, 2000);
    return () => { cancelled = true; clearInterval(t); };
  }, [walkingKey]);

  const uploadFiles = useCallback(async (files: FileList | File[]) => {
    const apks = Array.from(files).filter((f) => f.name.endsWith('.apk'));
    if (apks.length === 0) return null;
    setUploading(true);
    try {
      const form = new FormData();
      let url = '/api/upload';
      if (apks.length === 1) form.append('file', apks[0]);
      else { for (const f of apks) form.append('files', f); url = '/api/upload-multi'; }
      const r = await fetch(url, { method: 'POST', body: form });
      const d = await r.json().catch(() => ({}));
      if (!r.ok) { alert(d.detail || `업로드 실패 (${r.status})`); return null; }
      await fetchTours();
      return d.tour_id || null;
    } finally { setUploading(false); }
  }, [fetchTours]);

  const requireSerial = useCallback((needsDevice: boolean): string | null => {
    if (needsDevice && device.devices.length > 1 && !selectedSerial) {
      alert(`기기가 ${device.devices.length}대 연결되어 있습니다.\n상단 기기 선택에서 탐색할 기기를 먼저 고르세요.`);
      return null;
    }
    return selectedSerial;
  }, [device.devices.length, selectedSerial]);

  const runTour = useCallback(async (tourId: string) => {
    const serial = requireSerial(true);
    if (serial === null) return false;
    const url = serial ? `/api/tours/${tourId}/run?device_serial=${encodeURIComponent(serial)}` : `/api/tours/${tourId}/run`;
    const res = await fetch(url, { method: 'POST' });
    if (!res.ok) { const b = await res.json().catch(() => ({})); alert(b.detail || `실행 실패 (${res.status})`); return false; }
    await fetchTours();
    return true;
  }, [requireSerial, fetchTours]);

  const stopTour = useCallback(async (tourId: string) => {
    if (!confirm('탐색을 중지하고 기기의 앱을 종료할까요?')) return;
    await fetch(`/api/tours/${tourId}/stop`, { method: 'POST' });
    await fetchTours();
  }, [fetchTours]);
  const pauseTour = useCallback(async (tourId: string) => { await fetch(`/api/tours/${tourId}/pause`, { method: 'POST' }); await fetchTours(); }, [fetchTours]);
  const resumeTour = useCallback(async (tourId: string) => { await fetch(`/api/tours/${tourId}/resume`, { method: 'POST' }); await fetchTours(); }, [fetchTours]);

  const retryFromStage = useCallback(async (tourId: string, stage: number) => {
    const labels: Record<number, string> = {
      1: '준비 (메타·프레임워크 감지)', 2: '정적 분석 (manifest + DEX)', 3: '기기 탐색 (오래 걸림)',
      4: '화면 정리', 5: 'LLM 라벨링 (자동 백업)', 6: '흐름 지도 빌드',
    };
    if (!confirm(`${stage}단계부터 다시 실행합니다.\n→ ${labels[stage]}\n\n계속할까요?`)) return false;
    const serial = requireSerial(stage <= 3);
    if (serial === null) return false;
    const params = new URLSearchParams({ from_stage: String(stage) });
    if (serial) params.set('device_serial', serial);
    const res = await fetch(`/api/tours/${tourId}/run?${params}`, { method: 'POST' });
    if (!res.ok) { const b = await res.json().catch(() => ({})); alert(b.detail || `재실행 실패 (${res.status})`); return false; }
    await fetchTours();
    return true;
  }, [requireSerial, fetchTours]);

  const deleteTour = useCallback(async (tourId: string) => {
    if (!confirm('이 프로젝트와 결과물을 삭제할까요? 되돌릴 수 없습니다.')) return;
    const res = await fetch(`/api/tours/${tourId}`, { method: 'DELETE' });
    if (!res.ok) { const b = await res.json().catch(() => ({})); alert(b.detail || `삭제 실패 (${res.status})`); }
    await fetchTours();
  }, [fetchTours]);

  const startEmulator = useCallback(async (opts: { safe?: boolean; cold?: boolean } = {}) => {
    try {
      let res: Response;
      try { res = await fetch('/api/emulator/avds'); }
      catch { alert('백엔드에 연결할 수 없습니다 (127.0.0.1:8008).'); return; }
      const data = await res.json().catch(() => ({}));
      if (!res.ok || !data.available || !data.avds?.length) { alert(data.detail || data.error || 'AVD 가 없습니다. Android Studio 에서 먼저 만드세요.'); return; }
      let avd = data.avds[0];
      if (data.avds.length > 1) {
        const picked = prompt('AVD 선택:\n' + data.avds.map((a: string, i: number) => `${i + 1}. ${a}`).join('\n'), '1');
        if (!picked) return;
        const idx = parseInt(picked, 10) - 1;
        if (Number.isNaN(idx) || idx < 0 || idx >= data.avds.length) { alert('잘못된 선택'); return; }
        avd = data.avds[idx];
      }
      const params = new URLSearchParams({ avd });
      if (opts.safe) params.set('safe', 'true');
      if (opts.cold) params.set('cold', 'true');
      const r = await fetch(`/api/emulator/start?${params}`, { method: 'POST' });
      if (!r.ok) { const b = await r.json().catch(() => ({})); alert(b.detail || '에뮬레이터 시작 실패'); }
    } catch (e) { alert('에뮬레이터 시작 실패: ' + String(e)); }
  }, []);
  const killEmulator = useCallback(async (serial = '') => {
    if (!confirm(serial ? `에뮬레이터 ${serial} 를 종료할까요?` : '모든 에뮬레이터를 종료할까요?')) return;
    await fetch(`/api/emulator/kill${serial ? `?serial=${serial}` : ''}`, { method: 'POST' });
    await fetchEmu(); await fetchDevice();
  }, [fetchEmu, fetchDevice]);

  const value = useMemo<AppState>(() => ({
    tours, device, emuStatus, selectedSerial, setSelectedSerial, uploading, refresh,
    uploadFiles, runTour, stopTour, pauseTour, resumeTour, retryFromStage, deleteTour, startEmulator, killEmulator,
  }), [tours, device, emuStatus, selectedSerial, uploading, refresh, uploadFiles, runTour, stopTour, pauseTour, resumeTour, retryFromStage, deleteTour, startEmulator, killEmulator]);

  return <Ctx.Provider value={value}>{children}</Ctx.Provider>;
}

export function useAppState(): AppState {
  const v = useContext(Ctx);
  if (!v) throw new Error('useAppState must be used inside AppStateProvider');
  return v;
}

export function useRunningTours(): Tour[] {
  const { tours } = useAppState();
  return useMemo(() => tours.filter((t) => isRunning(t.stage)), [tours]);
}
