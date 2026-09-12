/** 그래프 색 — Wayfare "종이 지도" 팔레트. 채도를 낮춘 흙·숲·바다 계열로
 *  카테고리마다 다른 색상(hue) 을 주되 배경(크림) 위에서 눈이 편하도록 맞췄다.
 *  functional_category enum 은 stage6_screenmap/screenmap_serializer 와 동일. */
export const CATEGORY_COLOR: Record<string, string> = {
  // 자주 나오는 것 — 서로 확실히 구분되는 색
  list:           '#2E5E8C',  // 바다
  detail:         '#B36A00',  // 호박
  form:           '#8A4F7D',  // 자두
  dialog:         '#C2552F',  // 테라코타

  // 중간 빈도
  settings:       '#4B5A8C',  // 남빛
  auth:           '#B4382D',  // 벽돌
  search:         '#1F7A8C',  // 청록
  home:           '#1F6F5B',  // 숲
  media:          '#4E6E31',  // 이끼

  // 드물게
  login:          '#8C2F2F',
  profile:        '#5B3F7A',
  content_detail: '#9B5B12',
  navigation:     '#6E675C',
  entry:          '#3F5A94',

  other:          '#A69B87',  // 모래 — 눈에 안 띄게
};

export const CATEGORY_LABEL: Record<string, string> = {
  list: '목록', detail: '상세', form: '입력', dialog: '대화상자', settings: '설정', auth: '인증',
  search: '검색', home: '홈', media: '미디어', login: '로그인', profile: '프로필',
  content_detail: '콘텐츠', navigation: '내비', entry: '진입', other: '기타',
};

/** 간선 종류별 선 스타일. 의미가 같은 것끼리 같은 색 계열. */
export const EDGE_STYLE: Record<string, { stroke: string; width: number; dash?: string; showLabel: boolean; label: string; desc: string }> = {
  navigate:       { stroke: '#3E3A33', width: 2.0, showLabel: true,  label: '화면 이동',     desc: '확인된 화면 간 직접 이동 (탐색에서 관찰되거나 DEX 에서 확인)' },
  two_hop:        { stroke: '#4B5A8C', width: 2.2, showLabel: true,  label: '헬퍼 경유',     desc: '난독화 헬퍼 클래스를 거쳐 실행되는 이동' },
  launcher:       { stroke: '#B4382D', width: 2.5, showLabel: true,  label: '런처 진입',     desc: '앱 아이콘으로 실행되는 최초 진입점' },
  intent_filter:  { stroke: '#C2552F', width: 2.5, showLabel: true,  label: '딥링크',        desc: 'URL 스킴/액션으로 외부에서 바로 들어오는 진입' },
  pending_intent: { stroke: '#D98E04', width: 2.5, showLabel: true,  label: '시스템 진입',   desc: '알림·위젯·AlarmManager 에서 시스템이 실행' },
  overlay:        { stroke: '#B36A00', width: 2.0, dash: '6 4', showLabel: true, label: '오버레이', desc: '현재 화면 위에 덮이는 대화상자/바텀시트 (화면 전환 아님)' },
  back:           { stroke: '#8F8776', width: 1.5, dash: '2 4', showLabel: false, label: '뒤로가기', desc: '시스템 뒤로가기 키로 발생하는 전환' },
  contains:       { stroke: '#A69B87', width: 1.3, showLabel: false, label: '구조 포함',     desc: '같은 Activity 안의 Fragment 교체 — 내부 구조 관계' },
  static_ref:     { stroke: '#C9BCA3', width: 1.2, showLabel: false, label: '정적 참조',     desc: '헬퍼에서 startActivity 참조 — 최종 화면 미확정' },
  global:         { stroke: '#D9CFBC', width: 1.2, showLabel: false, label: '공통 참조',     desc: '하단탭·드로어처럼 여러 화면이 공유하는 연결' },
};

export function edgeStyle(kind: string) { return EDGE_STYLE[kind] || EDGE_STYLE.navigate; }

/** 노드 상태(status) 점 색과 설명. */
export const STATUS_STYLE: Record<string, { color: string; label: string; desc: string }> = {
  enriched: { color: '#1F6F5B', label: '캡처 완료',   desc: '기기 탐색에서 방문 — 스크린샷과 UI 요소 있음' },
  resolved: { color: '#1F6F5B', label: '정적 파악',   desc: '레이아웃 XML 만으로 구조를 완전히 파악' },
  probed:   { color: '#2E5E8C', label: '도달 확인',   desc: 'am start 로 강제 실행에 성공 — UI 는 미캡처' },
  partial:  { color: '#D98E04', label: '부분 파악',   desc: 'Fragment/ViewPager 포함 — 내부는 런타임 의존' },
  unknown:  { color: '#B4382D', label: '탐색 필요',   desc: 'RecyclerView/WebView 포함 — 동적 탐색이 필요' },
  declared: { color: '#C9BCA3', label: '선언만',      desc: 'manifest 에 선언됐지만 아직 방문하지 못함' },
  entry:    { color: '#3F5A94', label: '진입 노드',   desc: '외부/시스템 진입을 나타내는 가상 노드' },
};

export const PRIO_STYLE: Record<string, { bg: string; label: string; desc: string }> = {
  A: { bg: '#1F6F5B', label: '사용자 화면', desc: '사용자가 실제로 조작하는 화면 — 탐색·캡처 대상' },
  B: { bg: '#A69B87', label: '내부 처리',   desc: 'UI 가 거의 없는 프록시/트램펄린 — 건너뜀' },
  C: { bg: '#5B3F7A', label: '딥링크 전용', desc: '외부 URL 로만 들어오는 진입점 — 탭으로는 못 감' },
};
