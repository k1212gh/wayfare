import React, { useState } from 'react';

/**
 * 브라우저 기본 title 은 0.5초 넘게 기다려야 떠서 "올려 두면 설명이 보이는" UI 가
 * 느려 보인다. 커서 옆에 즉시 뜨는 툴팁. 포털 없이 고정 위치 div 하나.
 */
export function InstantTooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const [show, setShow] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  if (!text) return <>{children}</>;
  return (
    <span
      style={{ display: 'contents' }}
      onMouseEnter={(e) => { setPos({ x: e.clientX, y: e.clientY }); setShow(true); }}
      onMouseMove={(e) => setPos({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <div style={{
          position: 'fixed', left: pos.x + 14, top: pos.y + 14, maxWidth: 320,
          background: 'var(--wf-ink)', color: 'var(--wf-ink-inverse)',
          padding: '8px 10px', borderRadius: 8, fontSize: 11.5, lineHeight: 1.45,
          fontFamily: 'var(--wf-font)', zIndex: 100000, pointerEvents: 'none',
          whiteSpace: 'pre-wrap', boxShadow: 'var(--wf-shadow-lg)',
        }}>
          {text}
        </div>
      )}
    </span>
  );
}
