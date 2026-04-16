import React, { useCallback, useEffect, useRef, useState } from 'react';

interface StageInfo {
  status: string; // pending | running | done | failed
  detail?: string;
  duration_ms?: number;
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
}

interface Device {
  serial: string;
  info: string;
}

interface DashboardProps {
  onOpenGraph: (tourId: string) => void;
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
  ANALYSIS_DONE: 'Analysis done',
  BUILDING_SCREENMAP: 'Building ScreenMap...',
  SCREENMAP_GENERATED: 'Complete',
  FAILED: 'Failed',
};

function isRunning(stage: string) {
  return ['PREPROCESSING', 'STATIC_ANALYZING', 'WALKING', 'PREPROCESSING_DATA', 'LLM_ANALYZING', 'BUILDING_SCREENMAP'].includes(stage);
}

export function Dashboard({ onOpenGraph }: DashboardProps) {
  const [tours, setTours] = useState<Tour[]>([]);
  const [device, setDevice] = useState<{ connected: boolean; devices: Device[] }>({ connected: false, devices: [] });
  const [uploading, setUploading] = useState(false);
  const [dragOver, setDragOver] = useState(false);
  const fileRef = useRef<HTMLInputElement>(null);

  const fetchTours = useCallback(async () => {
    try {
      const res = await fetch('/api/tours');
      const data = await res.json();
      setTours(data.tours || []);
    } catch {}
  }, []);

  const fetchDevice = useCallback(async () => {
    try {
      const res = await fetch('/api/device');
      setDevice(await res.json());
    } catch {}
  }, []);

  useEffect(() => {
    fetchTours();
    fetchDevice();
    const interval = setInterval(fetchTours, 3000);
    return () => clearInterval(interval);
  }, [fetchTours, fetchDevice]);

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
    await fetch(`/api/tours/${tourId}/run`, { method: 'POST' });
    await fetchTours();
  };

  const deleteTour = async (tourId: string) => {
    await fetch(`/api/tours/${tourId}`, { method: 'DELETE' });
    await fetchTours();
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
        padding: '0 32px', height: '56px', borderBottom: '1px solid var(--color-border)',
      }}>
        <span style={{ fontSize: '15px', fontWeight: 600, letterSpacing: '-0.3px' }}>ScreenAtlas</span>
        <div style={{ display: 'flex', alignItems: 'center', gap: '12px' }}>
          <span style={{
            display: 'inline-flex', alignItems: 'center', gap: '6px',
            fontSize: '12px', color: 'var(--color-gray)',
          }}>
            <span style={{
              width: '6px', height: '6px', borderRadius: '50%',
              background: device.connected ? '#22c55e' : '#d4d4d4',
            }} />
            {device.connected ? device.devices[0]?.serial || 'Device' : 'No device'}
          </span>
        </div>
      </header>

      <div style={{ maxWidth: '720px', margin: '0 auto', padding: '40px 24px' }}>
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

        <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
          {tours.map((tour) => (
            <TourCard
              key={tour.tour_id}
              tour={tour}
              onRun={() => runTour(tour.tour_id)}
              onOpen={() => onOpenGraph(tour.tour_id)}
              onDelete={() => deleteTour(tour.tour_id)}
            />
          ))}
        </div>
      </div>
    </div>
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


function TourCard({ tour, onRun, onOpen, onDelete }: {
  tour: Tour; onRun: () => void; onOpen: () => void; onDelete: () => void;
}) {
  const running = isRunning(tour.stage);
  const complete = tour.stage === 'SCREENMAP_GENERATED';
  const failed = tour.stage === 'FAILED';

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
            {tour.apk_filename || tour.tour_id}
          </span>
          {tour.apk_size_mb > 0 && (
            <span style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>
              {tour.apk_size_mb}MB
            </span>
          )}
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
        {complete && (
          <Btn onClick={onOpen} primary>View Graph</Btn>
        )}
        {!running && (
          <Btn onClick={onDelete} subtle>Delete</Btn>
        )}
      </div>
      </div>
      {/* Progress bar */}
      <StageProgress stages={tour.stages || {}} />
    </div>
  );
}


function Btn({ children, onClick, primary, subtle }: {
  children: React.ReactNode; onClick: () => void; primary?: boolean; subtle?: boolean;
}) {
  return (
    <button
      onClick={onClick}
      style={{
        padding: '6px 14px',
        fontSize: '12px',
        fontWeight: 500,
        fontFamily: 'var(--font)',
        border: primary ? 'none' : '1px solid var(--color-border)',
        borderRadius: '6px',
        cursor: 'pointer',
        background: primary ? 'var(--color-black)' : 'var(--color-white)',
        color: primary ? 'var(--color-white)' : subtle ? 'var(--color-gray)' : 'var(--color-black)',
        transition: 'opacity 0.15s',
      }}
      onMouseEnter={(e) => (e.currentTarget.style.opacity = '0.7')}
      onMouseLeave={(e) => (e.currentTarget.style.opacity = '1')}
    >
      {children}
    </button>
  );
}
