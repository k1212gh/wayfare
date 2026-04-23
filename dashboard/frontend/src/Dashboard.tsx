import React, { useCallback, useEffect, useRef, useState } from 'react';
import {
  Tour, Coverage, Device, StageInfo, EmulatorInfo,
  STAGE_LABELS, FRAMEWORK_STYLE, isRunning, isComplete,
} from './dashboard/types';
import { TourCard, Btn } from './dashboard/TourCard';
import { LiveDeviceMirror } from './dashboard/LiveDeviceMirror';

interface DashboardProps {
  onOpenGraph: (tourId: string) => void;
  onRunStart?: (tourId: string) => void;
}

export function Dashboard({ onOpenGraph, onRunStart }: DashboardProps) {
  const [tours, setTours] = useState<Tour[]>([]);
  const [device, setDevice] = useState<{ connected: boolean; devices: Device[] }>({ connected: false, devices: [] });
  const [emuStatus, setEmuStatus] = useState<EmulatorInfo[]>([]);
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
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
        return (data.tours || []).map((j: Tour) => ({
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
        `탐색을 실행할 대상을 상단 "Device" 드롭다운에서 먼저 선택해주세요.`,
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

      <div style={{ maxWidth: '1100px', margin: '0 auto', padding: '40px 24px' }}>
        {/* Upload zone */}
        <div
          onDragOver={(e) => { e.preventDefault(); setDragOver(true); }}
          onDragLeave={() => setDragOver(false)}
          onDrop={handleDrop}
          onClick={() => fileRef.current?.click()}
          style={{
            border: `2px dashed ${dragOver ? 'var(--color-primary)' : 'var(--color-border)'}`,
            borderRadius: '12px',
            padding: '40px',
            textAlign: 'center',
            cursor: 'pointer',
            transition: 'border-color 0.15s',
            marginBottom: '40px',
          }}
        >
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
          <div style={{ fontSize: '14px', fontWeight: 500, marginBottom: '4px' }}>
            {uploading ? 'Uploading...' : 'Drop APK here or click to upload'}
          </div>
          <div style={{ fontSize: '12px', color: 'var(--color-gray)' }}>
            Single .apk or multiple split APKs (base.apk + split_config.*.apk)
          </div>
        </div>

        {/* Tours list */}
        <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center', marginBottom: '16px' }}>
          <span style={{ fontSize: '13px', fontWeight: 600 }}>Tours</span>
          <span style={{ fontSize: '12px', color: 'var(--color-gray)' }}>{tours.length}</span>
        </div>

        {tours.length === 0 && (
          <div style={{ textAlign: 'center', padding: '60px 0', color: 'var(--color-gray)', fontSize: '13px' }}>
            No tours yet. Upload an APK to get started.
          </div>
        )}

        {(() => {
          // Is any tour currently in an analysis state (running or user-paused)?
          // If so, show LiveDeviceMirror as its own card to the right of tours list.
          const activeTour = tours.find((j) => isRunning(j.stage));
          const showMirror = !!activeTour;
          return (
            <div style={{ display: 'flex', gap: '16px', alignItems: 'flex-start' }}>
              <div style={{ flex: 1, minWidth: 0, display: 'flex', flexDirection: 'column', gap: '8px' }}>
                {tours.map((tour) => (
                  <TourCard
                    key={tour.tour_id}
                    tour={tour}
                    onRun={() => runTour(tour.tour_id)}
                    onStop={() => stopTour(tour.tour_id)}
                    onPause={() => pauseTour(tour.tour_id)}
                    onResume={() => resumeTour(tour.tour_id)}
                    onOpen={() => onOpenGraph(tour.tour_id)}
                    onDelete={() => deleteTour(tour.tour_id)}
                  />
                ))}
              </div>
              {showMirror && (
                <div style={{
                  border: '1px solid var(--color-border)',
                  borderRadius: '8px',
                  padding: '16px 20px',
                  flexShrink: 0,
                  position: 'sticky' as const, top: 16,
                }}>
                  <LiveDeviceMirror interactive={!!activeTour?.paused} serial={selectedSerial} />
                </div>
              )}
            </div>
          );
        })()}
      </div>
    </div>
  );
}


// FRAMEWORK_STYLE moved to dashboard/types.ts (re-exported above).

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

  let dotColor = '#d4d4d4';  // gray (no device)
  let label = 'No device';
  if (isReady || device.connected) {
    dotColor = '#22c55e';  // green
    label = anyEmu?.serial || device.devices[0]?.serial || 'Device';
  } else if (isBooting) {
    dotColor = '#f59e0b';  // amber
    label = `${anyEmu.serial} booting…`;
  } else if (isStall) {
    dotColor = '#dc2626';  // red
    label = `${anyEmu.serial} ${anyEmu.state}`;
  }

  // Device picker — only rendered when >= 2 ADB devices attached, since
  // picking the wrong one (e.g. user's real phone instead of emulator) is
  // destructive. Single-device case stays ambient.
  const multiDevice = device.devices.length >= 2;

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
    <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
      <span style={{
        display: 'inline-flex', alignItems: 'center', gap: '6px',
        fontSize: '12px', color: 'var(--color-gray)',
        fontFamily: 'var(--font-mono)',
      }}>
        <span style={{ width: '6px', height: '6px', borderRadius: '50%', background: dotColor }} />
        {label}
      </span>
      {multiDevice && (
        <select
          value={selectedSerial}
          onChange={(e) => onSelect(e.target.value)}
          title="탐색에 사용할 ADB 디바이스. 실기기·에뮬이 동시 연결되어 있으면 반드시 지정."
          style={{
            fontSize: '11px', padding: '3px 6px',
            fontFamily: 'var(--font-mono)',
            border: selectedSerial ? '1px solid var(--color-border)' : '1px solid #dc2626',
            borderRadius: '5px', background: 'var(--color-white)',
            cursor: 'pointer', maxWidth: 220,
          }}
        >
          <option value="">⚠ 디바이스 선택 필요…</option>
          {device.devices.map((d) => (
            <option key={d.serial} value={d.serial}>
              {d.serial.startsWith('emulator-') ? '🖥 ' : '📱 '}{d.serial}
            </option>
          ))}
        </select>
      )}
      {!anyEmu && btn(onStart, 'Start emulator', 'Launch default AVD')}
      {isStall && btn(() => onKill(anyEmu.serial), 'Kill', 'Stop stall emulator', true)}
      {isStall && btn(onStartSafe, 'Safe restart', '-gpu swiftshader_indirect + -no-snapshot')}
      {isStall && btn(onStartCold, 'Cold restart', '-no-snapshot (ignore saved state)')}
      {isReady && btn(() => onKill(anyEmu.serial), 'Stop', 'Stop this emulator')}
    </div>
  );
}
