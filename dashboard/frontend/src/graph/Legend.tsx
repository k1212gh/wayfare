import React from 'react';
import { InstantTooltip } from './InstantTooltip';

/**
 * Bottom-right overlay in the graph view explaining every edge type,
 * node status, and capture-priority letter. Every row uses InstantTooltip
 * so hovering shows the underlying reasoning without a click.
 *
 * Extracted from ScreenMapView.tsx (Step 6). Pure presentational component —
 * no props, no state; static content defined inline.
 */
export function Legend() {
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
