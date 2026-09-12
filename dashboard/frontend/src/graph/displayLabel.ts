/** 노드 표시 이름 — LLM 라벨 → 제목 텍스트 → 화면 첫 텍스트 → 액티비티 짧은 이름 → id.
 *  백엔드 fallback_label 과 같은 순서. 옛 tour(page_xxx 라벨) 도 프론트에서 구제. */
const PLACEHOLDER = /^(page|act|screen|state|node)_[0-9a-f]{6,}$/i;

export function shortActivity(activity?: string, fragment?: string): string {
  let name = (activity || '').split('.').pop() || '';
  if (name.endsWith('Activity') && name.length > 8) name = name.slice(0, -8);
  const frag = (fragment || '').split('.').pop() || '';
  if (frag && frag.toLowerCase() !== name.toLowerCase()) return `${name} · ${frag}`;
  return name;
}

export function isPlaceholderLabel(label: string | undefined, screenId: string): boolean {
  const l = (label || '').trim();
  return !l || l === screenId || PLACEHOLDER.test(l);
}

export function displayLabel(n: any): string {
  if (!isPlaceholderLabel(n.label, n.screen_id)) return n.label.trim();
  if (n.title_text && String(n.title_text).trim()) return String(n.title_text).trim();
  const c = n.label_candidates;
  if (Array.isArray(c) && c.length && String(c[0]).trim()) return String(c[0]).trim();
  return shortActivity(n.activity, n.fragment_class || n.fragment) || n.screen_id;
}

/** 카드 하단 보조 줄: 액티비티 짧은 이름 (라벨과 같으면 빈 문자열) */
export function subLabel(n: any, label: string): string {
  const s = shortActivity(n.activity, n.fragment_class || n.fragment);
  return s && s.toLowerCase() !== label.toLowerCase() ? s : '';
}
