/** Per-functional-category node accent color (left border of each node).
 *  Keep in sync with the API's ScreenMap schema enum (see stage6_screenmap/screenmap_serializer). */
export const CATEGORY_COLOR: Record<string, string> = {
  home: '#2563eb',
  login: '#dc2626',
  settings: '#7c3aed',
  search: '#059669',
  list: '#0891b2',
  content_detail: '#d97706',
  form: '#e11d48',
  profile: '#4f46e5',
  navigation: '#6b7280',
  other: '#9ca3af',
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
