import { Tour } from './types';

/** 프로젝트 표시 이름 — 앱 라벨 → 패키지 마지막 세그먼트 → APK 파일명 → id. */
export function tourTitle(t: Pick<Tour, 'app_label' | 'package_name' | 'apk_filename' | 'tour_id'>): string {
  return t.app_label
    || (t.package_name ? t.package_name.split('.').slice(-1)[0] : '')
    || (t.apk_filename ? t.apk_filename.replace(/\.apk$/i, '') : '')
    || t.tour_id;
}

/** 앱 아이콘 대용 — 이름 첫 글자와 패키지 해시 기반 색. */
export function tourInitial(t: Pick<Tour, 'app_label' | 'package_name' | 'apk_filename' | 'tour_id'>): string {
  const s = tourTitle(t).trim();
  return s ? s[0].toUpperCase() : '?';
}

const ICON_HUES = ['#1F6F5B', '#2E5E8C', '#8A4F7D', '#B4382D', '#B36A00', '#4E6E31', '#4B5A8C', '#8C3F5A'];
export function tourHue(t: Pick<Tour, 'package_name' | 'tour_id'>): string {
  const key = t.package_name || t.tour_id;
  let h = 0;
  for (let i = 0; i < key.length; i++) h = (h * 31 + key.charCodeAt(i)) >>> 0;
  return ICON_HUES[h % ICON_HUES.length];
}

export function formatStartedAt(ts: number): string {
  if (!ts || ts <= 0) return '';
  const d = new Date(ts * 1000);
  const mm = String(d.getMonth() + 1).padStart(2, '0');
  const dd = String(d.getDate()).padStart(2, '0');
  const hh = String(d.getHours()).padStart(2, '0');
  const min = String(d.getMinutes()).padStart(2, '0');
  return `${mm}.${dd} ${hh}:${min}`;
}

export function formatElapsed(seconds: number): string {
  if (seconds < 60) return `${seconds.toFixed(0)}s`;
  const m = Math.floor(seconds / 60);
  const s = Math.round(seconds % 60);
  if (m < 60) return `${m}m${s.toString().padStart(2, '0')}s`;
  const h = Math.floor(m / 60);
  return `${h}h${(m % 60).toString().padStart(2, '0')}m`;
}
