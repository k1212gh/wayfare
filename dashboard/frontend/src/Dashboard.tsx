import React, { useCallback, useEffect, useRef, useState } from 'react';

interface StageInfo {
  status: string; // pending | running | done | failed
  detail?: string;
  duration_ms?: number;
}

interface Coverage {
  covered: number;
  expected: number;
  ratio: number;
  missed_sample?: string[];
}

interface Tour {
  tour_id: string;
  stage: string;
  package_name: string;
  apk_filename: string;
  apk_size_mb: number;
  started_at: number;
  error: string | null;
  stages?: Record<string, StageInfo>;
  framework?: string; // "xml" | "compose" | "flutter" | "react-native"
  paused?: boolean;
  pause_reason?: string;
  pause_auto?: boolean;
  pause_since?: number;
  coverage?: Coverage; // injected client-side from walk-live
  app_label?: string; // 한글/영어 앱 이름 (aapt2 application-label)
}

interface Device {
  serial: string;
  info: string;
}

interface DashboardProps {
  onOpenGraph: (tourId: string) => void;
  onRunStart?: (tourId: string) => void;
}

const STAGE_LABELS: Record<string, string> = {
  UPLOADED: 'Uploaded',
  PREPROCESSING: 'Preprocessing...',
  STATIC_ANALYZING: 'Static analysis...',
  STATIC_DONE: 'Static done',
  WALKING: 'Walking app...',
  WALK_DONE: 'Walk done',
  PREPROCESSING_DATA: 'Cleaning data...',
  CARDS_READY: 'Data ready',
  LLM_ANALYZING: 'LLM analyzing...',
  LLM_ANNOTATING: 'LLM enriching...',
  ANALYSIS_DONE: 'Analysis done',
  BUILDING_SCREENMAP: 'Building ScreenMap...',
  SCREENMAP_GENERATED: 'ScreenMap ready (wireframe)',
  ANNOTATED: 'Complete (with LLM)',
  CANCELLED: 'Cancelled',
  FAILED: 'Failed',
};

function isRunning(stage: string) {
  return [
    'PREPROCESSING', 'STATIC_ANALYZING', 'WALKING',
    'PREPROCESSING_DATA', 'LLM_ANALYZING', 'LLM_ANNOTATING', 'BUILDING_SCREENMAP',
  ].includes(stage);
}

// Terminal states with a viewable ScreenMap — "View Graph" button is shown for these.
// Both wireframe (SCREENMAP_GENERATED) and LLM-enriched (ANNOTATED) have an
// screen_map.json worth opening.
function isComplete(stage: string) {
  return stage === 'SCREENMAP_GENERATED' || stage === 'ANNOTATED';
}

interface EmulatorInfo {
  serial: string;
  avd: string;
  state: string; // "online_boot_complete" | "online_booting" | "offline" | "unauthorized"
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


const FRAMEWORK_STYLE: Record<string, { bg: string; fg: string; label: string }> = {
  'xml':          { bg: '#f1f5f9', fg: '#475569', label: 'XML' },
  'compose':      { bg: '#dbeafe', fg: '#1d4ed8', label: 'Compose' },
  'flutter':      { bg: '#cffafe', fg: '#0e7490', label: 'Flutter' },
  'react-native': { bg: '#ede9fe', fg: '#6d28d9', label: 'RN' },
};

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


function CoverageBar({ coverage }: { coverage: Coverage }) {
  const pct = Math.round(coverage.ratio * 100);
  return (
    <div
      style={{ marginTop: '10px' }}
      title={
        coverage.missed_sample && coverage.missed_sample.length > 0
          ? `Missed: ${coverage.missed_sample.join(', ')}${coverage.missed_sample.length >= 5 ? ' …' : ''}`
          : undefined
      }
    >
      <div style={{
        display: 'flex', justifyContent: 'space-between', alignItems: 'center',
        fontSize: '11px', color: 'var(--color-gray)', marginBottom: '4px',
        fontFamily: 'var(--font-mono)',
      }}>
        <span>Activity coverage</span>
        <span>
          <strong style={{ color: 'var(--color-black)' }}>{coverage.covered}</strong>
          {' / '}{coverage.expected}
          <span style={{ marginLeft: '6px' }}>({pct}%)</span>
        </span>
      </div>
      <div style={{
        height: '6px', width: '100%', borderRadius: '3px',
        background: 'var(--color-border)', overflow: 'hidden',
      }}>
        <div style={{
          height: '100%',
          width: `${Math.min(100, Math.max(2, pct))}%`,
          background: 'var(--color-black)',
          transition: 'width 0.4s ease',
        }} />
      </div>
    </div>
  );
}


function FrameworkBadge({ framework }: { framework?: string }) {
  if (!framework) return null;
  const style = FRAMEWORK_STYLE[framework] || { bg: '#f1f5f9', fg: '#64748b', label: framework };
  return (
    <span
      title={`Detected framework: ${framework}`}
      style={{
        display: 'inline-block',
        padding: '2px 8px',
        fontSize: '10px',
        fontWeight: 600,
        fontFamily: 'var(--font-mono)',
        letterSpacing: '0.2px',
        background: style.bg,
        color: style.fg,
        borderRadius: '4px',
        lineHeight: '1.4',
      }}
    >
      {style.label}
    </span>
  );
}


const PIPELINE_STAGES = [
  { key: 'stage1', label: 'Preprocess' },
  { key: 'stage2', label: 'Static' },
  { key: 'stage3', label: 'Walk' },
  { key: 'stage4', label: 'Clean' },
  { key: 'stage5', label: 'LLM' },
  { key: 'stage6', label: 'ScreenMap' },
];

function StageProgress({ stages }: { stages: Record<string, StageInfo> }) {
  if (!stages || Object.keys(stages).length === 0) return null;
  return (
    <div style={{ display: 'flex', gap: '2px', marginTop: '10px', alignItems: 'flex-end' }}>
      {PIPELINE_STAGES.map(({ key, label }) => {
        const info = stages[key] || { status: 'pending' };
        const color =
          info.status === 'done' ? 'var(--color-black)' :
          info.status === 'running' ? 'var(--color-primary)' :
          info.status === 'failed' ? '#dc2626' :
          'var(--color-border)';
        const dur = info.duration_ms ? `${(info.duration_ms / 1000).toFixed(1)}s` : '';
        return (
          <div key={key} style={{ display: 'flex', flexDirection: 'column', alignItems: 'center', gap: '4px', flex: 1 }}>
            <div style={{
              width: '100%', height: '4px', borderRadius: '2px', background: color,
              transition: 'background 0.3s',
            }} />
            <span style={{ fontSize: '9px', color: info.status === 'running' ? 'var(--color-primary)' : 'var(--color-gray)', fontFamily: 'var(--font-mono)', whiteSpace: 'nowrap' as const }}>
              {label}
            </span>
            {dur && <span style={{ fontSize: '8px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>{dur}</span>}
          </div>
        );
      })}
    </div>
  );
}


function TourCard({ tour, onRun, onStop, onPause, onResume, onOpen, onDelete }: {
  tour: Tour; onRun: () => void; onStop: () => void; onPause: () => void; onResume: () => void;
  onOpen: () => void; onDelete: () => void;
}) {
  const running = isRunning(tour.stage);
  const complete = isComplete(tour.stage);
  const failed = tour.stage === 'FAILED' || tour.stage === 'CANCELLED';
  const paused = !!tour.paused;

  // Find current running stage detail
  const runningDetail = Object.values(tour.stages || {}).find(s => s.status === 'running')?.detail || '';

  return (
    <div style={{
      border: '1px solid var(--color-border)',
      borderRadius: '8px',
      padding: '16px 20px',
    }}>
      <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
      {/* Left */}
      <div style={{ flex: 1 }}>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px', marginBottom: '4px' }}>
          <span style={{ fontSize: '13px', fontWeight: 600 }}>
            {tour.app_label
              || (tour.package_name ? tour.package_name.split('.').slice(-1)[0] : '')
              || tour.apk_filename
              || tour.tour_id}
          </span>
          {tour.apk_size_mb > 0 && (
            <span style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>
              {tour.apk_size_mb}MB
            </span>
          )}
          <FrameworkBadge framework={tour.framework} />
        </div>
        <div style={{ display: 'flex', alignItems: 'center', gap: '8px' }}>
          <span style={{
            fontSize: '11px', fontFamily: 'var(--font-mono)',
            color: failed ? 'var(--color-primary)' : running ? 'var(--color-black)' : 'var(--color-gray)',
          }}>
            {running && <span style={{ marginRight: '4px' }}>&#9679;</span>}
            {STAGE_LABELS[tour.stage] || tour.stage}
          </span>
          {tour.package_name && (
            <span style={{ fontSize: '11px', color: 'var(--color-gray)' }}>{tour.package_name}</span>
          )}
        </div>
        {runningDetail && (
          <div style={{ fontSize: '11px', color: 'var(--color-gray)', marginTop: '2px' }}>{runningDetail}</div>
        )}
        {failed && tour.error && (
          <div style={{ fontSize: '11px', color: 'var(--color-primary)', marginTop: '4px', maxWidth: '400px', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const }}>
            {tour.error}
          </div>
        )}
      </div>

      {/* Right — actions */}
      <div style={{ display: 'flex', gap: '8px', flexShrink: 0 }}>
        {(tour.stage === 'UPLOADED' || failed) && (
          <Btn onClick={onRun}>Run</Btn>
        )}
        {running && !paused && (
          <Btn onClick={onPause} subtle>Pause</Btn>
        )}
        {running && paused && (
          <Btn onClick={onResume} primary>Resume</Btn>
        )}
        {running && (
          <Btn onClick={onStop} danger>Stop</Btn>
        )}
        {complete && (
          <Btn onClick={onOpen} primary>View Graph</Btn>
        )}
        {!running && (
          <Btn onClick={onDelete} subtle>Delete</Btn>
        )}
      </div>
      </div>
      {/* Coverage bar (only during WALKING) */}
      {tour.stage === 'WALKING' && tour.coverage && tour.coverage.expected > 0 && (
        <CoverageBar coverage={tour.coverage} />
      )}
      {/* Pause banner — auto-detect login or manual pause */}
      {paused && (
        <div style={{
          marginTop: '10px',
          padding: '10px 12px',
          background: '#fef3c7',
          border: '1px solid #fbbf24',
          borderRadius: '6px',
          fontSize: '12px',
          color: '#78350f',
        }}>
          <div style={{ display: 'flex', alignItems: 'center', justifyContent: 'space-between', gap: '8px' }}>
            <strong>⏸ 사용자 개입 대기 중 {tour.pause_auto ? '(자동 감지)' : ''}</strong>
            {tour.pause_since ? (
              <span style={{ fontFamily: 'var(--font-mono)', fontSize: '11px', color: '#92400e' }}>
                {Math.floor((Date.now() / 1000 - tour.pause_since) / 60)}m
              </span>
            ) : null}
          </div>
          <div style={{ marginTop: '6px', lineHeight: '1.5' }}>
            사유: <em>{tour.pause_reason || 'waiting for user'}</em>
            <br />
            <strong>에뮬레이터 화면에서 직접 로그인</strong>을 완료한 뒤 아래 버튼으로 탐색을 재개하세요.
            소셜 로그인(카카오·네이버)이나 SMS 인증은 자동화가 불가능하므로 수동으로 처리해 주세요.
            로그인이 불필요한 경우 건너뛰기를 누르면 현재 화면에서 다른 경로를 계속 탐색합니다.
          </div>
        </div>
      )}
      {/* Progress bar */}
      <StageProgress stages={tour.stages || {}} />
    </div>
  );
}


function LiveDeviceMirror({ interactive, serial }: { interactive: boolean; serial: string }) {
  const [tick, setTick] = useState<number>(() => Date.now());
  const [err, setErr] = useState<boolean>(false);
  // URL suffix carrying the ADB serial — without this, `adb exec-out screencap`
  // fails when >1 device is attached (ambiguous target).
  const serialQs = serial ? `&serial=${encodeURIComponent(serial)}` : '';
  // Device resolution (read from the image's natural dimensions after load).
  const [devW, setDevW] = useState<number>(1080);
  const [devH, setDevH] = useState<number>(2400);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [text, setText] = useState('');

  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), interactive ? 800 : 1500);
    return () => clearInterval(id);
  }, [interactive]);

  const handleClick = async (e: React.MouseEvent<HTMLImageElement>) => {
    if (!interactive) return;
    const img = e.currentTarget;
    const rect = img.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    // Map DOM click to device pixel coords
    const dx = Math.round((cx / rect.width) * devW);
    const dy = Math.round((cy / rect.height) * devH);
    try {
      await fetch(`/api/device/tap?x=${dx}&y=${dy}${serialQs}`, { method: 'POST' });
      setTick(Date.now());
    } catch {}
  };

  const sendKey = async (code: string) => {
    try {
      await fetch(`/api/device/key?code=${code}${serialQs}`, { method: 'POST' });
      setTick(Date.now());
    } catch {}
  };

  const sendText = async () => {
    if (!text) return;
    try {
      await fetch(`/api/device/text?s=${encodeURIComponent(text)}${serialQs}`, { method: 'POST' });
      setText('');
      setTick(Date.now());
    } catch {}
  };

  return (
    <div style={{
      width: '260px', flexShrink: 0,
      padding: '10px 12px',
      background: '#f9fafb', border: '1px solid var(--color-border)',
      borderRadius: '6px',
      display: 'flex', flexDirection: 'column', gap: '8px',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--color-gray)',
                      textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Live Device
        </span>
        {interactive && (
          <span style={{ fontSize: '10px', color: '#d97706', fontWeight: 600,
                         padding: '2px 6px', background: '#fef3c7', borderRadius: '4px' }}>
            INPUT ON
          </span>
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        {err ? (
          <div style={{ fontSize: '11px', color: 'var(--color-gray)', padding: '60px 0' }}>
            Device not reachable
          </div>
        ) : (
          <img
            ref={imgRef}
            src={`/api/device/screenshot?t=${tick}${serialQs}`}
            alt="device"
            onError={() => setErr(true)}
            onLoad={(e) => {
              setErr(false);
              const img = e.currentTarget;
              if (img.naturalWidth) setDevW(img.naturalWidth);
              if (img.naturalHeight) setDevH(img.naturalHeight);
            }}
            onClick={handleClick}
            style={{
              maxHeight: '460px', width: '220px',
              border: '4px solid #0a0a0a', borderRadius: '22px',
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
              objectFit: 'contain', background: '#000',
              cursor: interactive ? 'crosshair' : 'default',
            }}
          />
        )}
      </div>
      {interactive && (
        <>
          <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' as const }}>
            {['BACK', 'HOME', 'ENTER', 'DEL', 'MENU'].map(k => (
              <button key={k} onClick={() => sendKey(k)} style={{
                fontSize: '10px', padding: '3px 8px', cursor: 'pointer',
                background: 'var(--color-white)', border: '1px solid var(--color-border)',
                borderRadius: '4px', color: 'var(--color-black)', fontFamily: 'var(--font-mono)',
              }}>{k}</button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '4px' }}>
            <input
              type="text"
              value={text}
              placeholder="text to type..."
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') sendText(); }}
              style={{
                flex: 1, fontSize: '11px', padding: '4px 8px',
                border: '1px solid var(--color-border)', borderRadius: '4px',
                fontFamily: 'var(--font)',
              }}
            />
            <button onClick={sendText} style={{
              fontSize: '10px', padding: '4px 10px', cursor: 'pointer',
              background: 'var(--color-black)', color: 'var(--color-white)',
              border: 'none', borderRadius: '4px', fontFamily: 'var(--font)',
            }}>Type</button>
          </div>
          <div style={{ fontSize: '10px', color: 'var(--color-gray)', lineHeight: 1.4 }}>
            • 폰 화면 클릭 → 그 좌표로 tap<br/>
            • 키 버튼으로 BACK/HOME 등 전송<br/>
            • text 입력 → Enter 또는 Type 버튼
          </div>
        </>
      )}
    </div>
  );
}


function Btn({ children, onClick, primary, subtle, danger }: {
  children: React.ReactNode; onClick: () => void; primary?: boolean; subtle?: boolean; danger?: boolean;
}) {
  const bg = danger ? '#dc2626' : primary ? 'var(--color-black)' : 'var(--color-white)';
  const fg = danger ? '#ffffff' : primary ? 'var(--color-white)' : subtle ? 'var(--color-gray)' : 'var(--color-black)';
  const border = (primary || danger) ? 'none' : '1px solid var(--color-border)';
  return (
    <button
      onClick={onClick}
      style={{
        padding: '6px 14px',
        fontSize: '12px',
        fontWeight: 500,
        fontFamily: 'var(--font)',
        border,
        borderRadius: '6px',
        cursor: 'pointer',
        background: bg,
        color: fg,
        transition: 'opacity 0.15s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.7')}
      onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
    >
      {children}
    </button>
  );
}
