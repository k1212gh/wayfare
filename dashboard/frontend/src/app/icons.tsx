import React from 'react';

/* 선(stroke) 기반 단색 아이콘 — currentColor 를 따른다. */
type P = { size?: number; strokeWidth?: number; className?: string };
const base = (size = 18, sw = 1.8) => ({
  width: size, height: size, viewBox: '0 0 24 24', fill: 'none', stroke: 'currentColor',
  strokeWidth: sw, strokeLinecap: 'round' as const, strokeLinejoin: 'round' as const,
});

/** 브랜드 마크 — 나침반 바늘 + 경로 점. */
export function WayfareMark({ size = 20 }: P) {
  return (
    <svg width={size} height={size} viewBox="0 0 24 24" fill="none">
      <circle cx="12" cy="12" r="9.5" stroke="currentColor" strokeWidth="1.6" opacity=".55" />
      <path d="M14.8 9.2 12.9 14.6 9.2 14.8 11.1 9.4z" fill="currentColor" />
      <circle cx="12" cy="12" r="1.2" fill="var(--wf-accent)" />
    </svg>
  );
}
export const IconFolder = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M3 7a2 2 0 0 1 2-2h4l2 2h8a2 2 0 0 1 2 2v9a2 2 0 0 1-2 2H5a2 2 0 0 1-2-2z" /></svg>
);
export const IconMap = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M9 4 3 6v14l6-2 6 2 6-2V4l-6 2z" /><path d="M9 4v14M15 6v14" /></svg>
);
export const IconPhone = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><rect x="7" y="2.5" width="10" height="19" rx="2.5" /><path d="M11 18h2" /></svg>
);
export const IconChip = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><rect x="6" y="6" width="12" height="12" rx="2" /><path d="M9 2v4M15 2v4M9 18v4M15 18v4M2 9h4M2 15h4M18 9h4M18 15h4" /></svg>
);
export const IconUpload = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M12 16V4M7 9l5-5 5 5" /><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" /></svg>
);
export const IconPlay = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M7 5v14l11-7z" fill="currentColor" stroke="none" /></svg>
);
export const IconStop = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><rect x="6" y="6" width="12" height="12" rx="2" fill="currentColor" stroke="none" /></svg>
);
export const IconPause = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><rect x="6" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none" /><rect x="14" y="5" width="4" height="14" rx="1" fill="currentColor" stroke="none" /></svg>
);
export const IconTrash = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M4 7h16M10 11v6M14 11v6M6 7l1 13h10l1-13M9 7V4h6v3" /></svg>
);
export const IconRefresh = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M20 12a8 8 0 1 1-2.3-5.7" /><path d="M20 4v5h-5" /></svg>
);
export const IconChevron = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="m6 9 6 6 6-6" /></svg>
);
export const IconSearch = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><circle cx="11" cy="11" r="7" /><path d="m20 20-3.5-3.5" /></svg>
);
export const IconClose = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M6 6l12 12M18 6 6 18" /></svg>
);
export const IconArrowLeft = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M19 12H5M11 6l-6 6 6 6" /></svg>
);
export const IconDownload = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M12 4v12M7 11l5 5 5-5" /><path d="M4 16v2a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2v-2" /></svg>
);
export const IconSparkle = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M12 3v4M12 17v4M3 12h4M17 12h4M6 6l2.5 2.5M15.5 15.5 18 18M6 18l2.5-2.5M15.5 8.5 18 6" /></svg>
);
export const IconLayers = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="m12 3 9 5-9 5-9-5z" /><path d="m3 13 9 5 9-5" /></svg>
);
export const IconRoute = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><circle cx="6" cy="18" r="2.5" /><circle cx="18" cy="6" r="2.5" /><path d="M8.5 18H14a3 3 0 0 0 0-6h-4a3 3 0 0 1 0-6h5.5" /></svg>
);
export const IconCheck = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="m5 12 5 5L20 7" /></svg>
);
export const IconAlert = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M12 3 2.5 20h19z" /><path d="M12 9v5M12 17h.01" /></svg>
);
export const IconEye = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="M2 12s3.5-6 10-6 10 6 10 6-3.5 6-10 6S2 12 2 12z" /><circle cx="12" cy="12" r="3" /></svg>
);
export const IconGrid = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><rect x="3" y="3" width="7" height="7" rx="1.5" /><rect x="14" y="3" width="7" height="7" rx="1.5" /><rect x="3" y="14" width="7" height="7" rx="1.5" /><rect x="14" y="14" width="7" height="7" rx="1.5" /></svg>
);
export const IconWand = ({ size, strokeWidth }: P) => (
  <svg {...base(size, strokeWidth)}><path d="m4 20 10-10M14 4l1 2 2 1-2 1-1 2-1-2-2-1 2-1zM19 12l.7 1.3L21 14l-1.3.7L19 16l-.7-1.3L17 14l1.3-.7z" /></svg>
);
