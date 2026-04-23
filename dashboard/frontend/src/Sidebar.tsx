import React, { useEffect, useState } from 'react';

/* -------------------------------------------------------------------------
 * Sidebar — overlay navigation drawer shared by Dashboard + ScreenMapView.
 *
 * Opens from a top-left hamburger (☰) button fixed to the viewport. Lists
 * completed tours as quick-switch entries; clicking one opens its graph
 * without a round-trip through the Dashboard.
 * -----------------------------------------------------------------------*/

interface SidebarTour {
  tour_id: string;
  stage: string;
  app_label?: string;
  apk_filename?: string;
  package_name?: string;
  framework?: string;
}

interface SidebarProps {
  currentPage: 'dashboard' | 'graph';
  activeTourId: string;
  onGoDashboard: () => void;
  onOpenGraph: (tourId: string) => void;
}

const COMPLETE_STAGES = new Set(['SCREENMAP_GENERATED', 'ANNOTATED']);
const RUNNING_STAGES = new Set([
  'PREPROCESSING', 'STATIC_ANALYZING', 'WALKING',
  'PREPROCESSING_DATA', 'LLM_ANALYZING', 'LLM_ANNOTATING', 'BUILDING_SCREENMAP',
]);

export function Sidebar({ currentPage, activeTourId, onGoDashboard, onOpenGraph }: SidebarProps) {
  const [open, setOpen] = useState(false);
  const [tours, setTours] = useState<SidebarTour[]>([]);

  // Fetch tours list when drawer opens (fresh each time so stages are current).
  useEffect(() => {
    if (!open) return;
    let cancelled = false;
    (async () => {
      try {
        const r = await fetch('/api/tours');
        const d = await r.json();
        if (!cancelled) setTours(d.tours || []);
      } catch {
        if (!cancelled) setTours([]);
      }
    })();
    return () => { cancelled = true; };
  }, [open]);

  // Close on Escape key
  useEffect(() => {
    if (!open) return;
    const handler = (e: KeyboardEvent) => {
      if (e.key === 'Escape') setOpen(false);
    };
    window.addEventListener('keydown', handler);
    return () => window.removeEventListener('keydown', handler);
  }, [open]);

  const tourLabel = (j: SidebarTour) =>
    j.app_label
    || (j.package_name ? j.package_name.split('.').slice(-1)[0] : '')
    || j.apk_filename
    || j.tour_id;

  const stageDot = (stage: string) => {
    if (COMPLETE_STAGES.has(stage)) return '#22c55e';   // green — viewable
    if (RUNNING_STAGES.has(stage)) return '#f59e0b';    // amber — in progress
    if (stage === 'FAILED') return '#ef4444';
    return '#94a3b8';                                    // gray — uploaded/cancelled
  };

  return (
    <>
      {/* Toggle — always-visible hamburger fixed to viewport top-left */}
      <button
        onClick={() => setOpen(true)}
        title="메뉴 열기 (ESC로 닫기)"
        aria-label="Open menu"
        style={{
          position: 'fixed', top: 10, left: 10, zIndex: 40,
          width: 36, height: 36,
          display: 'flex', alignItems: 'center', justifyContent: 'center',
          background: 'var(--color-white)',
          border: '1px solid var(--color-border)',
          borderRadius: 8, cursor: 'pointer',
          boxShadow: '0 1px 3px rgba(0,0,0,0.06)',
          fontSize: 18, lineHeight: 1, color: 'var(--color-black)',
        }}
      >
        ☰
      </button>

      {/* Drawer + backdrop */}
      {open && (
        <>
          {/* Backdrop — click closes */}
          <div
            onClick={() => setOpen(false)}
            style={{
              position: 'fixed', inset: 0, zIndex: 50,
              background: 'rgba(15, 23, 42, 0.35)',
              animation: 'sa-sidebar-fade 0.15s ease-out',
            }}
          />
          {/* Panel */}
          <aside
            style={{
              position: 'fixed', top: 0, left: 0, bottom: 0, zIndex: 51,
              width: 300, background: 'var(--color-white)',
              borderRight: '1px solid var(--color-border)',
              boxShadow: '2px 0 12px rgba(0,0,0,0.08)',
              display: 'flex', flexDirection: 'column',
              fontFamily: 'var(--font)',
              animation: 'sa-sidebar-slide 0.18s ease-out',
            }}
          >
            {/* Header */}
            <div style={{
              display: 'flex', alignItems: 'center', justifyContent: 'space-between',
              padding: '14px 16px', borderBottom: '1px solid var(--color-border)',
            }}>
              <span style={{ fontSize: 15, fontWeight: 600, letterSpacing: '-0.3px' }}>ScreenAtlas</span>
              <button
                onClick={() => setOpen(false)}
                aria-label="Close menu"
                style={{
                  background: 'none', border: 'none', cursor: 'pointer',
                  fontSize: 20, lineHeight: 1, color: 'var(--color-gray)',
                  padding: 4,
                }}
              >×</button>
            </div>

            {/* Nav links */}
            <nav style={{ padding: '8px 8px 12px' }}>
              <SidebarLink
                active={currentPage === 'dashboard'}
                onClick={() => { setOpen(false); onGoDashboard(); }}
                icon="🏠"
                label="대시보드"
                hint="업로드 · 실행 · 에뮬레이터"
              />
              <SidebarLink
                active={currentPage === 'graph'}
                disabled={!activeTourId}
                onClick={() => { setOpen(false); if (activeTourId) onOpenGraph(activeTourId); }}
                icon="🕸"
                label="그래프 뷰어"
                hint={activeTourId ? `현재: ${activeTourId.slice(0, 8)}` : '열린 그래프 없음'}
              />
            </nav>

            {/* Tours quick-switch */}
            <div style={{
              padding: '4px 16px 8px',
              fontSize: 10, fontWeight: 600, color: 'var(--color-gray)',
              textTransform: 'uppercase', letterSpacing: '0.5px',
              borderTop: '1px solid var(--color-border)',
            }}>
              Tours ({tours.length})
            </div>
            <div style={{ flex: 1, overflowY: 'auto', padding: '0 8px 12px' }}>
              {tours.length === 0 && (
                <div style={{
                  padding: '24px 12px', textAlign: 'center',
                  fontSize: 12, color: 'var(--color-gray)',
                }}>
                  업로드된 tour 없음
                </div>
              )}
              {tours.map((j) => {
                const viewable = COMPLETE_STAGES.has(j.stage);
                return (
                  <button
                    key={j.tour_id}
                    onClick={() => {
                      if (!viewable) return;
                      setOpen(false);
                      onOpenGraph(j.tour_id);
                    }}
                    disabled={!viewable}
                    title={viewable ? '이 tour 의 그래프 열기' : `아직 ${j.stage} 상태 — 그래프 없음`}
                    style={{
                      display: 'flex', alignItems: 'center', gap: 10,
                      width: '100%', padding: '10px 10px',
                      background: j.tour_id === activeTourId ? 'var(--color-surface)' : 'var(--color-white)',
                      border: 'none',
                      borderRadius: 6,
                      cursor: viewable ? 'pointer' : 'not-allowed',
                      textAlign: 'left' as const,
                      opacity: viewable ? 1 : 0.55,
                      fontFamily: 'inherit',
                      marginBottom: 2,
                    }}
                    onMouseEnter={(e) => {
                      if (viewable) e.currentTarget.style.background = 'var(--color-surface)';
                    }}
                    onMouseLeave={(e) => {
                      if (viewable && j.tour_id !== activeTourId) e.currentTarget.style.background = 'var(--color-white)';
                    }}
                  >
                    <span style={{
                      width: 8, height: 8, borderRadius: '50%',
                      background: stageDot(j.stage), flexShrink: 0,
                    }} />
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div style={{
                        fontSize: 12, fontWeight: 500,
                        overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const,
                      }}>
                        {tourLabel(j)}
                      </div>
                      <div style={{
                        fontSize: 10, color: 'var(--color-gray)',
                        fontFamily: 'var(--font-mono)',
                      }}>
                        {j.stage}
                      </div>
                    </div>
                  </button>
                );
              })}
            </div>
          </aside>

          {/* Keyframes — injected once */}
          <style>{`
            @keyframes sa-sidebar-slide {
              from { transform: translateX(-100%); }
              to { transform: translateX(0); }
            }
            @keyframes sa-sidebar-fade {
              from { opacity: 0; }
              to { opacity: 1; }
            }
          `}</style>
        </>
      )}
    </>
  );
}

function SidebarLink({
  active, disabled, onClick, icon, label, hint,
}: {
  active: boolean; disabled?: boolean; onClick: () => void;
  icon: string; label: string; hint?: string;
}) {
  return (
    <button
      onClick={onClick}
      disabled={disabled}
      style={{
        display: 'flex', alignItems: 'center', gap: 12,
        width: '100%', padding: '10px 12px',
        background: active ? 'var(--color-black)' : 'transparent',
        color: active ? 'var(--color-white)' : (disabled ? 'var(--color-gray)' : 'var(--color-black)'),
        border: 'none', borderRadius: 6,
        cursor: disabled ? 'not-allowed' : 'pointer',
        textAlign: 'left' as const,
        opacity: disabled ? 0.5 : 1,
        marginBottom: 2,
        fontFamily: 'inherit',
      }}
      onMouseEnter={(e) => {
        if (!active && !disabled) e.currentTarget.style.background = 'var(--color-surface)';
      }}
      onMouseLeave={(e) => {
        if (!active && !disabled) e.currentTarget.style.background = 'transparent';
      }}
    >
      <span style={{ fontSize: 16 }}>{icon}</span>
      <div style={{ flex: 1, minWidth: 0 }}>
        <div style={{ fontSize: 13, fontWeight: 500 }}>{label}</div>
        {hint && (
          <div style={{
            fontSize: 10, marginTop: 1,
            color: active ? 'rgba(255,255,255,0.7)' : 'var(--color-gray)',
            fontFamily: 'var(--font-mono)',
            overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' as const,
          }}>
            {hint}
          </div>
        )}
      </div>
    </button>
  );
}
