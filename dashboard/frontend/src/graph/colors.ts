/** Per-functional-category node accent color (left border of each node).
 *  Keep in sync with the API's ScreenMap schema enum (see stage6_screenmap/screenmap_serializer).
 *
 *  Palette philosophy: muted Tailwind 500-series base with strongest hues
 *  assigned to the highest-frequency categories observed in recent tours
 *  (list / detail / form / dialog). `other` stays slate-400 — it's a
 *  catch-all and shouldn't grab attention away from typed categories.
 */
export const CATEGORY_COLOR: Record<string, string> = {
  // High-frequency (40+ in recent 3 tours) — vivid, distinct hues
  list:           '#3b82f6',  // blue-500
  detail:         '#f59e0b',  // amber-500
  form:           '#ec4899',  // pink-500
  dialog:         '#f97316',  // orange-500

  // Mid-frequency
  settings:       '#8b5cf6',  // violet-500
  auth:           '#ef4444',  // red-500
  search:         '#06b6d4',  // cyan-500
  home:           '#10b981',  // emerald-500
  media:          '#14b8a6',  // teal-500

  // Less common / contextual
  login:          '#dc2626',  // red-600 — deeper than auth so the two coexist
  profile:        '#6366f1',  // indigo-500
  content_detail: '#d97706',  // amber-600 — sibling of detail
  navigation:     '#64748b',  // slate-500
  entry:          '#7c3aed',  // violet-600

  // Catch-all
  other:          '#94a3b8',  // slate-400 (muted)
};

/** Hover-tooltip copy for each edge `kind`. Rendered by FloatingEdge. */
export const EDGE_KIND_DESC: Record<string, string> = {
  navigate: '확인된 Activity 간 직접 startActivity 호출 (DEX 분석 또는 탐색에서 관찰됨)',
  two_hop: '난독화 helper class를 거쳐 startActivity 호출 (N-hop 역추적)',
  contains: '같은 Activity 내 Fragment 교체 (탭/ViewPager)',
  launcher: 'android.intent.action.MAIN + LAUNCHER/APP_* 카테고리',
  intent_filter: 'Manifest의 intent-filter로 선언된 deep link 진입점',
  pending_intent: '알림/위젯/AlarmManager에서 시스템 측이 실행',
  overlay: 'Dialog/BottomSheet 레이어 (화면 전환 아님)',
  static_ref: '난독화 helper에서 startActivity 참조 — 최종 호출 Activity 미확정',
  global: '다수 화면에서 공유되는 컴포넌트 (하단탭/드로어 등)',
  back: '시스템 뒤로가기 키 전환',
};
