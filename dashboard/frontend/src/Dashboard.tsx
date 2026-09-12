import React, { useCallback, useEffect, useMemo, useRef, useState } from 'react';
import {
  Tour, Coverage, Device, StageInfo, EmulatorInfo,
  STAGE_LABELS, FRAMEWORK_STYLE, isRunning, isComplete,
} from './dashboard/types';
import { TourCard, Btn } from './dashboard/TourCard';
import { LiveDeviceMirror } from './dashboard/LiveDeviceMirror';
import { LLMSettings } from './dashboard/LLMSettings';

interface DashboardProps {
  onOpenGraph: (tourId: string) => void;
  onRunStart?: (tourId: string) => void;
}

function sortToursNewestFirst(a: Tour, b: Tour): number {
  const byTime = (b.started_at || 0) - (a.started_at || 0);
  if (byTime !== 0) return byTime;
  return b.tour_id.localeCompare(a.tour_id);
}

export function Dashboard({ onOpenGraph, onRunStart }: DashboardProps) {
  const [tours, setTours] = useState<Tour[]>([]);
  const [device, setDevice] = useState<{ connected: boolean; devices: Device[] }>({ connected: false, devices: [] });
  const [emuStatus, setEmuStatus] = useState<EmulatorInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const [tourFilter, setTourFilter] = useState<'all' | 'active' | 'ready' | 'complete' | 'failed'>('all');
  const [devicePanelOpen, setDevicePanelOpen] = useState(false);
  // Selected ADB serial for /run. Empty = auto (backend picks first). When 2+
  // devices are attached (real phone + emulator), the user MUST pick one to
  // avoid walking on the wrong device.
  const [selectedSerial, setSelectedSerial] = useState<string>('');

  // Keep selectedSerial consistent with the live device list:
  //   - If the currently-selected serial disappears from adb, reset to empty.
  //   - If exactly one device is attached, auto-select it (user doesn't need
  //     to pick manually in the common single-device case).
  useEffect(() => {
    const serials = device.devices.map((d) => d.serial);
    if (selectedSerial && !serials.includes(selectedSerial)) {
      setSelectedSerial('');
      return;
    }
    if (!selectedSerial && serials.length === 1) {
      setSelectedSerial(serials[0]);
    }
  }, [device.devices, selectedSerial]);
  const fileRef = useRef<HTMLInputElement>(null);

  const fetchTours = useCallback(async () => {
    try {
      const res = await fetch('/api/tours');
      const data = await res.json();
      // Preserve client-side `coverage` so the CoverageBar doesn't flash between polls
      setTours((prev) => {
        const covMap = new Map(prev.map((j) => [j.tour_id, j.coverage]));
        return ((data.tours || []) as Tour[])
          .slice()
          .sort(sortToursNewestFirst)
          .map((j: Tour) => ({
            ...j,
            coverage: j.coverage ?? covMap.get(j.tour_id),
          }));
      });
    } catch {}
  }, []);

  const fetchDevice = useCallback(async () => {
    try {
      const res = await fetch('/api/device');
      setDevice(await res.json());
    } catch {}
  }, []);

  const fetchEmuStatus = useCallback(async () => {
    try {
      const res = await fetch('/api/emulator/status');
      const data = await res.json();
      setEmuStatus(data.emulators || []);
    } catch {}
  }, []);

  useEffect(() => {
    fetchTours();
    fetchDevice();
    fetchEmuStatus();
    const interval = setInterval(() => {
      fetchTours();
      fetchDevice();
      fetchEmuStatus();
    }, 3000);
    return () => clearInterval(interval);
  }, [fetchTours, fetchDevice, fetchEmuStatus]);

  // For every WALKING tour, poll /walk-live and attach coverage.
  useEffect(() => {
    const walking = tours.filter((j) => j.stage === 'WALKING');
    if (walking.length === 0) return;

    let cancelled = false;
    const poll = async () => {
      const updates = await Promise.all(
        walking.map(async (j) => {
          try {
            const r = await fetch(`/api/tours/${j.tour_id}/walk-live`);
            const d = await r.json();
            return { id: j.tour_id, coverage: d.coverage as Coverage | undefined };
          } catch {
            return { id: j.tour_id, coverage: undefined };
          }
        }),
      );
      if (cancelled) return;
      setTours((prev) =>
        prev.map((j) => {
          const u = updates.find((x) => x.id === j.tour_id);
          // Only overwrite coverage if the new poll actually returned one.
          // This prevents flicker when /walk-live returns no coverage block.
          if (!u || u.coverage === undefined) return j;
          return { ...j, coverage: u.coverage };
        }),
      );
    };
    poll();
    const t = setInterval(poll, 2000);
    return () => {
      cancelled = true;
      clearInterval(t);
    };
  }, [tours.map((j) => `${j.tour_id}:${j.stage}`).join(',')]);

  const uploadFiles = async (files: FileList | File[]) => {
    const apks = Array.from(files).filter((f) => f.name.endsWith('.apk'));
    if (apks.length === 0) return;

    setUploading(true);
    try {
      if (apks.length === 1) {
        // Single APK
        const form = new FormData();
        form.append('file', apks[0]);
        await fetch('/api/upload', { method: 'POST', body: form });
      } else {
        // Split APKs (base.apk + splits)
        const form = new FormData();
        for (const f of apks) form.append('files', f);
        await fetch('/api/upload-multi', { method: 'POST', body: form });
      }
      await fetchTours();
    } finally {
      setUploading(false);
    }
  };

  const runTour = async (tourId: string) => {
    // If 2+ devices attached and user hasn't picked one, force explicit choice
    // — running on the wrong device (e.g. user's real phone) is destructive
    // enough that we refuse to guess.
    let serial = selectedSerial;
    if (device.devices.length > 1 && !serial) {
      alert(
        `여러 디바이스가 연결되어 있습니다 (${device.devices.length}개).\n` +
        `탐색을 실행할 대상을 상단 디바이스 탭에서 먼저 선택해주세요.`,
      );
      return;
    }
    const url = serial
      ? `/api/tours/${tourId}/run?device_serial=${encodeURIComponent(serial)}`
      : `/api/tours/${tourId}/run`;
    const res = await fetch(url, { method: 'POST' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(body.detail || `Run failed (${res.status})`);
      return;
    }
    // Tell parent so it can auto-open graph view when wireframe is ready
    onRunStart?.(tourId);
    await fetchTours();
  };

  const stopTour = async (tourId: string) => {
    if (!confirm('Stop walk and force-close the app on the device?')) return;
    await fetch(`/api/tours/${tourId}/stop`, { method: 'POST' });
    await fetchTours();
  };

  const pauseTour = async (tourId: string) => {
    await fetch(`/api/tours/${tourId}/pause`, { method: 'POST' });
    await fetchTours();
  };

  const resumeTour = async (tourId: string) => {
    await fetch(`/api/tours/${tourId}/resume`, { method: 'POST' });
    await fetchTours();
  };

  // 임의 stage 부터 재실행. 백엔드가 prerequisite output 검증 + Stage 5 진입 직전
  // .before_stage5.bak.json 자동 백업.  Stage 3 (Walk) 같은 case 는 디바이스 필요.
  const retryFromStage = async (tourId: string, stage: number) => {
    const labels: Record<number, string> = {
      1: 'Preprocess (메타·프레임워크 감지)',
      2: 'Static (manifest + DEX)',
      3: 'Walk (동적 탐색 — 오래 걸림)',
      4: 'Clean (데이터 전처리)',
      5: 'LLM (라벨링, ~5분, 자동 백업)',
      6: 'ScreenMap 빌드',
    };
    if (!confirm(
      `Stage ${stage} 부터 다시 실행합니다.\n→ ${labels[stage]}\n\n계속할까요?`
    )) return;
    const needsDevice = stage <= 3;
    let serial = selectedSerial;
    if (needsDevice && device.devices.length > 1 && !serial) {
      alert('디바이스가 여러 개입니다. 상단 디바이스 탭에서 먼저 선택해주세요.');
      return;
    }
    const params = new URLSearchParams({ from_stage: String(stage) });
    if (serial) params.set('device_serial', serial);
    const res = await fetch(`/api/tours/${tourId}/run?${params}`, { method: 'POST' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(body.detail || `Retry failed (${res.status})`);
      return;
    }
    onRunStart?.(tourId);
    await fetchTours();
  };

  const deleteTour = async (tourId: string) => {
    const res = await fetch(`/api/tours/${tourId}`, { method: 'DELETE' });
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      alert(body.detail || `Delete failed (${res.status})`);
    }
    await fetchTours();
  };

  const startEmulator = async (opts: { safe?: boolean; cold?: boolean } = {}) => {
    try {
      let res: Response;
      try {
        res = await fetch('/api/emulator/avds');
      } catch (netErr) {
        alert('백엔드에 연결할 수 없습니다 (127.0.0.1:8008). uvicorn이 실행 중인지 확인하세요.');
        return;
      }
      if (!res.ok) {
        const body = await res.json().catch(() => ({}));
        alert(body.detail || body.error || `AVD 목록 조회 실패 (HTTP ${res.status})`);
        return;
      }
      const text = await res.text();
      if (!text.trim()) {
        alert('백엔드가 빈 응답을 반환했습니다. 서버 로그를 확인하세요.');
        return;
      }
      let data: any;
      try {
        data = JSON.parse(text);
      } catch {
        alert('백엔드 응답 파싱 실패: ' + text.slice(0, 200));
        return;
      }
      if (!data.available || !data.avds || data.avds.length === 0) {
        alert(data.error || 'No AVDs found. Create one in Android Studio first.');
        return;
      }
      let avd = data.avds[0];
      if (data.avds.length > 1) {
        const picked = prompt(
          'Select AVD:\n' + data.avds.map((a: string, i: number) => `${i + 1}. ${a}`).join('\n'),
          '1',
        );
        if (!picked) return;
        const idx = parseInt(picked, 10) - 1;
        if (Number.isNaN(idx) || idx < 0 || idx >= data.avds.length) {
          alert('Invalid selection');
          return;
        }
        avd = data.avds[idx];
      }
      const params = new URLSearchParams({ avd });
      if (opts.safe) params.set('safe', 'true');
      if (opts.cold) params.set('cold', 'true');
      const startRes = await fetch(`/api/emulator/start?${params}`, { method: 'POST' });
      if (!startRes.ok) {
        const body = await startRes.json().catch(() => ({}));
        alert(body.detail || 'Failed to start emulator');
        return;
      }
      // No alert — the header badge will show boot state live.
    } catch (e) {
      alert('Failed to start emulator: ' + String(e));
    }
  };

  const killEmulator = async (serial: string = '') => {
    if (!confirm(serial ? `Stop emulator ${serial}?` : 'Stop ALL running emulators?')) return;
    await fetch(`/api/emulator/kill${serial ? `?serial=${serial}` : ''}`, { method: 'POST' });
    await fetchEmuStatus();
    await fetchDevice();
  };

  const handleDrop = (e: React.DragEvent) => {
    e.preventDefault();
    setDragOver(false);
    if (e.dataTransfer.files.length > 0) {
      uploadFiles(e.dataTransfer.files);
    }
  };

  const runningTours = useMemo(() => tours.filter((j) => isRunning(j.stage)), [tours]);
  const inactiveTours = useMemo(() => tours.filter((j) => !isRunning(j.stage)), [tours]);
  const readyTours = useMemo(() => tours.filter((j) => j.stage === 'UPLOADED'), [tours]);
  const completeTours = useMemo(() => tours.filter((j) => isComplete(j.stage)), [tours]);
  const failedTours = useMemo(() => tours.filter((j) => j.stage === 'FAILED' || j.stage === 'CANCELLED'), [tours]);
  const activeTour = runningTours[0];
  const visibleTours = useMemo(() => {
    if (tourFilter === 'active') return runningTours;
    if (tourFilter === 'ready') return readyTours;
    if (tourFilter === 'complete') return completeTours;
    if (tourFilter === 'failed') return failedTours;
    return activeTour ? inactiveTours : tours;
  }, [tourFilter, tours, runningTours, inactiveTours, readyTours, completeTours, failedTours, activeTour]);

  return (
    <div style={{ minHeight: '100vh', background: 'var(--color-white)', fontFamily: 'var(--font)' }}>
      {/* Header */}
      <header style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        padding: '0 32px 0 60px', height: '56px', borderBottom: '1px solid var(--color-border)',
      }}>
        <span style={{ fontSize: '15px', fontWeight: 600, letterSpacing: '-0.3px' }}>ScreenAtlas</span>
        <DeviceControls
          device={device}
          emuStatus={emuStatus}
          selectedSerial={selectedSerial}
          onSelect={setSelectedSerial}
          onStart={() => startEmulator()}
          onStartSafe={() => startEmulator({ safe: true })}
          onStartCold={() => startEmulator({ cold: true })}
          onKill={(serial) => killEmulator(serial)}
        />
      </header>

      <div style={{ maxWidth: '1180px', margin: '0 auto', padding: '24px 24px 48px' }}>
        <input
          ref={fileRef}
          type="file"
          accept=".apk"
          multiple
          style={{ display: 'none' }}
          onChange={(e) => {
            if (e.target.files && e.target.files.length > 0) {
              uploadFiles(e.target.files);
            }
          }}
        />

        <LLMSettings />

        <UploadStrip
          dragOver={dragOver}
          uploading={uploading}
          onClick={() => fileRef.current?.click()}
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
        />

        {activeTour && (
          <section style={{ marginBottom: 22 }}>
            <SectionHeader
              title="Active"
              count={runningTours.length}
              right={(
                <button
                  type="button"
                  onClick={() => setDevicePanelOpen(!devicePanelOpen)}
                  style={{
                    padding: '5px 10px',
                    border: '1px solid var(--color-border)',
                    borderRadius: 6,
                    background: devicePanelOpen ? 'var(--color-black)' : 'var(--color-white)',
                    color: devicePanelOpen ? 'var(--color-white)' : 'var(--color-black)',
                    fontFamily: 'var(--font)',
                    fontSize: 11,
                    cursor: 'pointer',
                  }}
                >
                  {devicePanelOpen ? 'Hide Device' : 'Show Device'}
                </button>
              )}
            />
            <div style={{ display: 'grid', gridTemplateColumns: devicePanelOpen || activeTour.paused ? 'minmax(0, 1fr) 300px' : '1fr', gap: 14, alignItems: 'start' }}>
              <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
                {runningTours.map((tour) => (
                  <TourCard
                    key={tour.tour_id}
                    tour={tour}
                    variant="active"
                    onRun={() => runTour(tour.tour_id)}
                    onStop={() => stopTour(tour.tour_id)}
                    onPause={() => pauseTour(tour.tour_id)}
                    onResume={() => resumeTour(tour.tour_id)}
                    onOpen={() => onOpenGraph(tour.tour_id)}
                    onDelete={() => deleteTour(tour.tour_id)}
                    onRetryFromStage={(n: number) => retryFromStage(tour.tour_id, n)}
                  />
                ))}
              </div>
              {(devicePanelOpen || activeTour.paused) && (
                <LiveDeviceRail
                  activeTour={activeTour}
                  serial={selectedSerial}
                  onClose={() => setDevicePanelOpen(false)}
                />
              )}
            </div>
          </section>
        )}

        <section>
          <SectionHeader
            title="Tours"
            count={visibleTours.length}
            right={(
              <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap' }}>
                <FilterChip active={tourFilter === 'all'} onClick={() => setTourFilter('all')}>
                  {activeTour ? `History ${inactiveTours.length}` : `All ${tours.length}`}
                </FilterChip>
                <FilterChip active={tourFilter === 'active'} onClick={() => setTourFilter('active')}>Active {runningTours.length}</FilterChip>
                <FilterChip active={tourFilter === 'ready'} onClick={() => setTourFilter('ready')}>Ready {readyTours.length}</FilterChip>
                <FilterChip active={tourFilter === 'complete'} onClick={() => setTourFilter('complete')}>Complete {completeTours.length}</FilterChip>
                <FilterChip active={tourFilter === 'failed'} onClick={() => setTourFilter('failed')}>Failed {failedTours.length}</FilterChip>
              </div>
            )}
          />

          {tours.length === 0 && (
            <div style={{
              textAlign: 'center',
              padding: '52px 0',
              color: 'var(--color-gray)',
              fontSize: 13,
              border: '1px dashed var(--color-border)',
              borderRadius: 8,
            }}>
              No tours yet. Upload an APK to get started.
            </div>
          )}

          {tours.length > 0 && visibleTours.length === 0 && (
            <div style={{ padding: '24px 0', color: 'var(--color-gray)', fontSize: 12 }}>
              No tours in this view.
            </div>
          )}

          {visibleTours.length > 0 && (
            <div style={{ display: 'flex', flexDirection: 'column', gap: 8 }}>
              {visibleTours.map((tour) => (
                <TourCard
                  key={tour.tour_id}
                  tour={tour}
                  variant={isRunning(tour.stage) ? 'active' : 'compact'}
                  onRun={() => runTour(tour.tour_id)}
                  onStop={() => stopTour(tour.tour_id)}
                  onPause={() => pauseTour(tour.tour_id)}
                  onResume={() => resumeTour(tour.tour_id)}
                  onOpen={() => onOpenGraph(tour.tour_id)}
                  onDelete={() => deleteTour(tour.tour_id)}
                  onRetryFromStage={(n: number) => retryFromStage(tour.tour_id, n)}
                />
              ))}
            </div>
          )}
        </section>
      </div>
    </div>
  );
}


// FRAMEWORK_STYLE moved to dashboard/types.ts (re-exported above).

function SectionHeader({
  title,
  count,
  right,
}: {
  title: string;
  count: number;
  right?: React.ReactNode;
}) {
  return (
    <div style={{
      display: 'flex',
      alignItems: 'center',
      justifyContent: 'space-between',
      gap: 12,
      marginBottom: 10,
    }}>
      <div style={{ display: 'flex', alignItems: 'baseline', gap: 8 }}>
        <span style={{ fontSize: 13, fontWeight: 700 }}>{title}</span>
        <span style={{ fontSize: 11, color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>{count}</span>
      </div>
      {right}
    </div>
  );
}

function FilterChip({ active, onClick, children }: {
  active: boolean;
  onClick: () => void;
  children: React.ReactNode;
}) {
  return (
    <button
      type="button"
      onClick={onClick}
      style={{
        padding: '4px 9px',
        border: active ? '1px solid var(--color-black)' : '1px solid var(--color-border)',
        borderRadius: 999,
        background: active ? 'var(--color-black)' : 'var(--color-white)',
        color: active ? 'var(--color-white)' : 'var(--color-gray)',
        fontSize: 11,
        fontWeight: 600,
        fontFamily: 'var(--font)',
        cursor: 'pointer',
      }}
    >
      {children}
    </button>
  );
}

function UploadStrip({
  dragOver,
  uploading,
  onClick,
  onDragOver,
  onDragLeave,
  onDrop,
}: {
  dragOver: boolean;
  uploading: boolean;
  onClick: () => void;
  onDragOver: (e: React.DragEvent) => void;
  onDragLeave: () => void;
  onDrop: (e: React.DragEvent) => void;
}) {
  return (
    <div
      onDragOver={onDragOver}
      onDragLeave={onDragLeave}
      onDrop={onDrop}
      onClick={onClick}
      style={{
        display: 'flex',
        flexDirection: 'column',
        alignItems: 'center',
        justifyContent: 'center',
        gap: 12,
        border: `2px dashed ${dragOver ? '#4338ca' : 'var(--color-border)'}`,
        borderRadius: 12,
        padding: '32px 16px',
        cursor: 'pointer',
        background: dragOver ? '#eef2ff' : '#fafafa',
        marginBottom: 24,
        transition: 'all 0.2s ease',
      }}
      onMouseEnter={(e) => {
        if (!dragOver) {
          e.currentTarget.style.borderColor = '#c7d2fe';
          e.currentTarget.style.background = '#f5f7ff';
        }
      }}
      onMouseLeave={(e) => {
        if (!dragOver) {
          e.currentTarget.style.borderColor = 'var(--color-border)';
          e.currentTarget.style.background = '#fafafa';
        }
      }}
    >
      <div style={{
        width: 48, height: 48, borderRadius: '50%', background: dragOver ? '#c7d2fe' : '#e5e5e5',
        display: 'flex', alignItems: 'center', justifyContent: 'center', marginBottom: 4, transition: 'all 0.2s'
      }}>
        <svg width="24" height="24" viewBox="0 0 24 24" fill="none" stroke={dragOver ? '#4338ca' : '#737373'} strokeWidth="2" strokeLinecap="round" strokeLinejoin="round">
          <path d="M21 15v4a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2v-4"></path>
          <polyline points="17 8 12 3 7 8"></polyline>
          <line x1="12" y1="3" x2="12" y2="15"></line>
        </svg>
      </div>
      <div style={{ textAlign: 'center' }}>
        <div style={{ fontSize: 14, fontWeight: 700, color: dragOver ? '#4338ca' : 'var(--color-black)' }}>
          {uploading ? 'Uploading APK...' : 'Click to Upload or Drag APKs here'}
        </div>
        <div style={{ fontSize: 12, color: 'var(--color-gray)', marginTop: 4 }}>
          Choose single base.apk or multiple split APK files.
        </div>
      </div>
      <span style={{
        marginTop: 4,
        padding: '8px 16px',
        borderRadius: 6,
        background: 'var(--color-black)',
        color: 'var(--color-white)',
        fontSize: 12,
        fontWeight: 600,
        boxShadow: '0 2px 4px rgba(0,0,0,0.1)'
      }}>
        Browse Files
      </span>
    </div>
  );
}

function LiveDeviceRail({
  activeTour,
  serial,
  onClose,
}: {
  activeTour: Tour;
  serial: string;
  onClose: () => void;
}) {
  return (
    <aside style={{
      position: 'sticky' as const,
      top: 72,
      border: '1px solid var(--color-border)',
      borderRadius: 8,
      background: '#fff',
      padding: 12,
    }}>
      <div style={{
        display: 'flex',
        justifyContent: 'space-between',
        alignItems: 'center',
        marginBottom: 8,
      }}>
        <div>
          <div style={{
            fontSize: 10,
            color: 'var(--color-gray)',
            textTransform: 'uppercase' as const,
            letterSpacing: 0.5,
            fontWeight: 700,
          }}>
            Live Device
          </div>
          <div style={{ fontSize: 11, color: 'var(--color-gray)', marginTop: 1 }}>
            {activeTour.paused ? 'Input enabled while paused' : 'Read-only preview'}
          </div>
        </div>
        {!activeTour.paused && (
          <button
            type="button"
            onClick={onClose}
            aria-label="Hide live device"
            style={{
              border: 'none',
              background: 'transparent',
              color: 'var(--color-gray)',
              cursor: 'pointer',
              fontSize: 18,
              lineHeight: 1,
            }}
          >
            ×
          </button>
        )}
      </div>
      <LiveDeviceMirror interactive={!!activeTour.paused} serial={serial} compact />
    </aside>
  );
}

function DeviceControls({
  device, emuStatus, selectedSerial, onSelect,
  onStart, onStartSafe, onStartCold, onKill,
}: {
  device: { connected: boolean; devices: Device[] };
  emuStatus: EmulatorInfo[];
  selectedSerial: string;
  onSelect: (serial: string) => void;
  onStart: () => void;
  onStartSafe: () => void;
  onStartCold: () => void;
  onKill: (serial?: string) => void;
}) {
  const anyEmu = emuStatus[0];
  const isStall = anyEmu && ['offline', 'unauthorized'].includes(anyEmu.state);
  const isBooting = anyEmu && anyEmu.state === 'online_booting';
  const isReady = anyEmu && anyEmu.state === 'online_boot_complete';

  const shortSerial = (serial: string) => {
    if (serial.startsWith('emulator-')) return serial.replace('emulator-', '');
    return serial.length > 10 ? `...${serial.slice(-6)}` : serial;
  };

  const deviceBySerial = new Map(device.devices.map((d) => [d.serial, d]));
  const seenSerials = new Set<string>();
  const targetOptions = [
    ...emuStatus.map((e) => {
      seenSerials.add(e.serial);
      return {
        serial: e.serial,
        label: `Emulator ${shortSerial(e.serial)}`,
        title: `${e.serial}${e.avd ? ` (${e.avd})` : ''} - ${e.state}`,
        state: e.state,
        selectable: deviceBySerial.has(e.serial),
        kind: 'emulator' as const,
      };
    }),
    ...device.devices
      .filter((d) => !seenSerials.has(d.serial))
      .map((d) => ({
        serial: d.serial,
        label: d.serial.startsWith('emulator-')
          ? `Emulator ${shortSerial(d.serial)}`
          : `Device ${shortSerial(d.serial)}`,
        title: `${d.serial}${d.info ? ` ${d.info}` : ''}`,
        state: 'device',
        selectable: true,
        kind: d.serial.startsWith('emulator-') ? 'emulator' as const : 'device' as const,
      })),
  ];
  const activeSerial =
    selectedSerial || (device.devices.length === 1 ? device.devices[0].serial : '');
  const activeTarget = targetOptions.find((t) => t.serial === activeSerial);

  let dotColor = '#d4d4d4';  // gray (no device)
  let label = 'No device';
  if (activeTarget) {
    label = activeTarget.label;
    if (activeTarget.state === 'online_booting') {
      dotColor = '#f59e0b';
    } else if (['offline', 'unauthorized'].includes(activeTarget.state)) {
      dotColor = '#dc2626';
    } else {
      dotColor = '#22c55e';
    }
  } else if (isBooting) {
    dotColor = '#f59e0b';  // amber
    label = `${anyEmu.serial} booting…`;
  } else if (isStall) {
    dotColor = '#dc2626';  // red
    label = `${anyEmu.serial} ${anyEmu.state}`;
  }

  const btn = (onClick: () => void, text: string, title: string, danger = false) => (
    <button
      onClick={onClick}
      title={title}
      style={{
        padding: '4px 10px', fontSize: '11px', fontWeight: 500,
        fontFamily: 'var(--font)',
        border: danger ? 'none' : '1px solid var(--color-border)',
        borderRadius: '6px',
        background: danger ? '#dc2626' : 'var(--color-white)',
        color: danger ? '#ffffff' : 'var(--color-black)',
        cursor: 'pointer',
      }}
    >
      {text}
    </button>
  );

  return (
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px', minWidth: 0 }}>
      <span style={{
        flexShrink: 0,
        display: 'inline-flex', alignItems: 'center', gap: '6px',
        fontSize: '12px', color: 'var(--color-gray)',
        fontFamily: 'var(--font-mono)',
      }}>
        <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: dotColor }} />
        {label}
      </span>
      {targetOptions.length > 0 && (
        <div
          role="tablist"
          aria-label="ADB target device"
          style={{
            display: 'inline-flex',
            alignItems: 'center',
            gap: 2,
            padding: 2,
            border: device.devices.length > 1 && !activeSerial
              ? '1px solid #dc2626'
              : '1px solid var(--color-border)',
            borderRadius: 6,
            background: '#f8fafc',
            overflowX: 'auto',
            maxWidth: 360,
          }}
        >
          {targetOptions.map((target) => {
            const active = activeSerial === target.serial;
            const needsAttention = !target.selectable && ['offline', 'unauthorized'].includes(target.state);
            return (
              <button
                key={target.serial}
                type="button"
                role="tab"
                aria-selected={active}
                disabled={!target.selectable}
                onClick={() => onSelect(target.serial)}
                title={target.selectable ? `탐색 대상: ${target.title}` : `연결 대기: ${target.title}`}
                style={{
                  flexShrink: 0,
                  padding: '3px 8px',
                  border: 'none',
                  borderRadius: 4,
                  background: active ? 'var(--color-black)' : 'transparent',
                  color: active
                    ? 'var(--color-white)'
                    : needsAttention ? '#dc2626' : 'var(--color-gray)',
                  fontFamily: 'var(--font)',
                  fontSize: 11,
                  fontWeight: 700,
                  cursor: target.selectable ? 'pointer' : 'not-allowed',
                  opacity: target.selectable ? 1 : 0.7,
                  whiteSpace: 'nowrap',
                }}
              >
                {target.label}{needsAttention ? ` ${target.state}` : ''}
              </button>
            );
          })}
        </div>
      )}
      {!anyEmu && btn(onStart, 'Start emulator', 'Launch default AVD')}
      {isStall && btn(() => onKill(anyEmu.serial), 'Kill', 'Stop stall emulator', true)}
      {isStall && btn(onStartSafe, 'Safe restart', '-gpu swiftshader_indirect + -no-snapshot')}
      {isStall && btn(onStartCold, 'Cold restart', '-no-snapshot (ignore saved state)')}
      {isReady && btn(() => onKill(anyEmu.serial), 'Stop', 'Stop this emulator')}
    </div>
  );
}
