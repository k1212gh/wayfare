import React, { useState, useEffect } from 'react';
import { InstantTooltip } from './InstantTooltip';

/**
 * Bottom-right overlay in the graph view explaining every edge type,
 * node status, and capture-priority letter. Every row uses InstantTooltip
 * so hovering shows the underlying reasoning without a click.
 *
 * Collapsible — click the header chevron to fold/unfold. State persisted
 * in localStorage so the panel stays in the user's preferred mode across
 * sessions.
 */
const STORAGE_KEY = 'screenatlas.graph.legend.collapsed.v2';

export function Legend() {
  const [collapsed, setCollapsed] = useState<boolean>(() => {
    try {
      const stored = window.localStorage.getItem(STORAGE_KEY);
      return stored == null ? true : stored === '1';
    } catch {
      return true;
    }
  });

  useEffect(() => {
    try {
      window.localStorage.setItem(STORAGE_KEY, collapsed ? '1' : '0');
    } catch {
      // ignore (private mode etc.)
    }
  }, [collapsed]);

  const edgeItems = [
    { color: '#334155', label: 'Navigate — 일반 화면 전환',
      tip: '두 Activity 사이의 일반적인 화면 이동(startActivity). 가장 신뢰도 높고 기본이 되는 엣지입니다.' },
    { color: '#94a3b8', label: 'Contains — 구조 포함',
      tip: '같은 Activity 내에서 Fragment가 포함되거나 교체됨. 화면 전환이 아닌 내부 구조 관계입니다.' },
    { color: '#dc2626', label: 'Launcher — 앱 런처 진입',
      tip: '앱 아이콘을 터치해 실행할 때 들어오는 최초 진입점입니다.' },
    { color: '#059669', label: 'Intent-filter — 딥링크 진입',
      tip: '웹 브라우저나 외부 앱의 특정 URL 스킴/액션을 통해 직접 진입하는 경로입니다.' },
    { color: '#7c3aed', label: 'PendingIntent — 시스템 진입',
      tip: '알림(Notification), 위젯 등 시스템 측에서 실행되는 진입점입니다.' },
    { color: '#ea580c', label: 'Overlay — 다이얼로그',
      tip: '현재 화면 위에 덮이는 AlertDialog / BottomSheet. 배경 화면은 유지됩니다.' },
    { color: '#2563eb', label: 'Two-hop — 헬퍼 클래스 경유',
      tip: '난독화된 헬퍼 클래스를 한 번 거쳐서 실행되는 화면 전환 경로입니다.' },
    { color: '#cbd5e1', label: 'Static/Global — 공통 참조',
      tip: '드로어 메뉴, 하단 네비게이션 바 등 여러 곳에서 공통으로 참조되는 연결선입니다.' },
    { color: '#fca5a5', label: 'Back — 뒤로 가기',
      tip: '시스템 뒤로가기 키(KEYCODE_BACK) 동작으로 발생하는 전환입니다.' },
  ];

  const statusItems = [
    { color: '#cbd5e1', label: 'Declared — 정적 선언만 (미탐색)',
      tip: 'AndroidManifest에 선언돼 있지만 아직 동적 탐색에서 방문되지 않고 LLM 분석도 없는 상태.' },
    { color: '#3b82f6', label: 'Probed — 도달 확인 (JIT 캡처 대기)',
      tip: '정상 UI 네비게이션으로는 도달 못했지만 `adb shell am start -W`로 강제 런치했을 때 ActivityManager가 정상 응답한 경우.' },
    { color: '#22c55e', label: 'Resolved/Enriched — 완료',
      tip: 'Resolved: 레이아웃 XML 파악 완료.\nEnriched: 스크린샷과 UI 요소 데이터가 성공적으로 수집됨.' },
    { color: '#f59e0b', label: 'Partial — Fragment/ViewPager',
      tip: '구조는 파악했지만 내부 Fragment 목록과 현재 활성 페이지는 동적 탐색이 필요함.' },
    { color: '#ef4444', label: 'Unknown — 동적 탐색 필수',
      tip: 'WebView/RecyclerView/ListView를 포함해 정적 분석으로는 콘텐츠 파악이 불가한 상태.' },
  ];

  const categoryItems = [
    { color: '#2563eb', label: 'Activity — 일반 화면', tip: '일반적인 안드로이드 Activity 화면 (파란색 포인트)' },
    { color: '#7c3aed', label: 'Fragment — 부분 화면', tip: 'Activity 내부의 Fragment 화면 (보라색 포인트)' },
    { color: '#d97706', label: 'Dialog / Overlay — 팝업', tip: '현재 화면 위에 겹쳐서 표시되는 다이얼로그나 바텀시트 (주황색 포인트)' },
    { color: '#16a34a', label: 'System / Entry — 진입점', tip: '위젯, 알림, 딥링크 등을 통해 진입하는 외부 허브 노드 (초록색 포인트)' },
  ];

  const priorityItems = [
    { letter: 'A', bg: '#059669', label: 'User screen — 탐색/캡처 대상',
      tip: 'MobileGPT 같은 에이전트가 실제로 상호작용하는 화면. 기본값이며 scan에서 우선순위로 재방문한다.' },
    { letter: 'B', bg: '#94a3b8', label: 'Plumbing — UI 없음, skip',
      tip: 'HandleApiCalls, Proxy, Trampoline 류. onCreate에서 finish()를 불러 UI가 거의 없음. 에이전트는 건드릴 일이 없음. 그래프에 존재는 하되 투명도 낮춰 시야에서 빠지게 함.' },
    { letter: 'C', bg: '#8b5cf6', label: 'Deep-link entry — intent_filter로만 진입',
      tip: 'VIEW 액션 + scheme/host 조합의 intent_filter를 가진 activity. 앱 UI에서 탭으로 가는 게 아니라 외부 URL/다른 앱에서 불러 들어오는 진입점이라 Scan에서 건너뛰는 게 맞음.' },
  ];

  // Collapsed pill — minimal footprint
  if (collapsed) {
    return (
      <button
        type="button"
        onClick={() => setCollapsed(false)}
        title="Legend 펼치기"
        style={{
          position: 'absolute', bottom: 16, right: 16, zIndex: 10,
          padding: '8px 12px',
          background: 'rgba(30,30,40,0.92)', color: '#e5e7eb',
          border: '1px solid rgba(255,255,255,0.1)', borderRadius: 8,
          cursor: 'pointer', fontFamily: "'Inter', sans-serif",
          fontSize: 11, fontWeight: 600,
          display: 'flex', alignItems: 'center', gap: 6,
          boxShadow: '0 4px 12px rgba(0,0,0,0.25)',
        }}
      >
        <span style={{ fontSize: 13 }}>📋</span>
        <span>Legend</span>
        <span style={{ color: '#9ca3af', fontSize: 14, marginLeft: 2 }}>▾</span>
      </button>
    );
  }

  // Section header style — used for all 3 group headers
  const sectionHeaderStyle: React.CSSProperties = {
    fontWeight: 600, color: '#9ca3af', marginBottom: 4,
    fontSize: '10px', textTransform: 'uppercase', letterSpacing: '0.5px',
  };

  return (
    <div style={{
      position: 'absolute', bottom: 16, right: 16,
      background: 'rgba(30,30,40,0.92)', color: '#e5e7eb',
      borderRadius: '10px', fontSize: '11px', lineHeight: 1.6,
      fontFamily: "'Inter', sans-serif", zIndex: 10, maxWidth: 280,
      maxHeight: 'calc(100vh - 40px)', display: 'flex', flexDirection: 'column',
      boxShadow: '0 4px 12px rgba(0,0,0,0.25)',
    }}>
      {/* Header bar with collapse toggle */}
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
        padding: '8px 12px',
        borderBottom: '1px solid rgba(255,255,255,0.08)',
        flexShrink: 0,
      }}>
        <span style={{ fontWeight: 600, color: '#e5e7eb', fontSize: 11, display: 'flex', alignItems: 'center', gap: 6 }}>
          <span style={{ fontSize: 13 }}>📋</span>
          Legend
        </span>
        <button
          type="button"
          onClick={() => setCollapsed(true)}
          title="Legend 접기"
          style={{
            background: 'transparent', border: 'none', color: '#9ca3af',
            cursor: 'pointer', padding: '2px 6px', borderRadius: 4,
            fontSize: 14, lineHeight: 1, fontFamily: 'inherit',
          }}
          onMouseEnter={e => (e.currentTarget.style.background = 'rgba(255,255,255,0.08)')}
          onMouseLeave={e => (e.currentTarget.style.background = 'transparent')}
        >
          ▴
        </button>
      </div>

      {/* Scrollable body */}
      <div style={{ padding: '10px 14px 12px', overflowY: 'auto' }}>
        <div style={{ marginBottom: 10 }}>
          <div style={sectionHeaderStyle}>
            Edge types <span style={{ color: '#6b7280', textTransform: 'none' }}>(hover for detail)</span>
          </div>
          {edgeItems.map((it, i) => (
            <InstantTooltip key={i} text={it.tip}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'help' }}>
                <svg width="28" height="8"><line x1="0" y1="4" x2="28" y2="4" stroke={it.color} strokeWidth="2" /></svg>
                <span>{it.label}</span>
              </div>
            </InstantTooltip>
          ))}
        </div>
        <div>
          <div style={sectionHeaderStyle}>
            Node Category (Top border color)
          </div>
          {categoryItems.map((it, i) => (
            <InstantTooltip key={i} text={it.tip}>
              <div style={{ display: 'flex', alignItems: 'center', gap: 8, cursor: 'help' }}>
                <span style={{ width: 14, height: 4, background: it.color, borderRadius: '2px' }} />
                <span>{it.label}</span>
              </div>
            </InstantTooltip>
          ))}
        </div>
        <div style={{ marginTop: 10 }}>
          <div style={sectionHeaderStyle}>
            Node status (Inner badge)
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
          <div style={sectionHeaderStyle}>
            Capture priority <span style={{ color: '#6b7280', textTransform: 'none' }}>(hover for detail)</span>
          </div>
          {priorityItems.map((it, i) => (
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
    </div>
  );
}
