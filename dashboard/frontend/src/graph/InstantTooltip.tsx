import React, { useState } from 'react';

/**
 * Native `title=` has a 500–700 ms OS-level delay before the browser shows
 * it, which makes hover-explain-everything UIs feel broken. This drops a
 * zero-delay tooltip beside the cursor. Use wherever the `<Legend>` does.
 *
 * Intentionally not a portal — just a fixed-position `<div>` rendered in
 * tree. React's event ordering is enough for our density (tens of items).
 */
export function InstantTooltip({ text, children }: { text: string; children: React.ReactNode }) {
  const [show, setShow] = useState(false);
  const [pos, setPos] = useState({ x: 0, y: 0 });
  if (!text) return <>{children}</>;
  return (
    <span
      style={{ display: 'contents' }}
      onMouseEnter={(e) => {
        setPos({ x: e.clientX, y: e.clientY });
        setShow(true);
      }}
      onMouseMove={(e) => setPos({ x: e.clientX, y: e.clientY })}
      onMouseLeave={() => setShow(false)}
    >
      {children}
      {show && (
        <div style={{
          position: 'fixed',
          left: pos.x + 14,
          top: pos.y + 14,
          maxWidth: 320,
          background: 'rgba(17, 24, 39, 0.96)',
          color: '#f9fafb',
          padding: '8px 10px',
          borderRadius: 6,
          fontSize: 11,
          lineHeight: 1.45,
          fontFamily: "'Inter', sans-serif",
          zIndex: 100000,
          pointerEvents: 'none',
          whiteSpace: 'pre-wrap',
          boxShadow: '0 4px 12px rgba(0,0,0,0.3)',
          border: '1px solid rgba(255,255,255,0.1)',
        }}>
          {text}
        </div>
      )}
    </span>
  );
}
