import React, { useEffect, useRef, useState } from 'react';

/**
 * Mirror of the connected ADB device — polls /api/device/screenshot every
 * 0.8–1.5s and optionally forwards clicks / keys / text back to the device.
 *
 * `serial` pins the mirror to one device when multiple are attached
 * (real phone + emulator). Without it, adb rejects `exec-out` as ambiguous
 * and the panel shows "Device not reachable".
 */
interface LiveDeviceMirrorProps {
  interactive: boolean;
  serial: string;
}

export function LiveDeviceMirror({ interactive, serial }: LiveDeviceMirrorProps) {
  const [tick, setTick] = useState<number>(() => Date.now());
  const [err, setErr] = useState<boolean>(false);
  // URL suffix carrying the ADB serial — every /api/device/* call needs it
  // when multiple devices are attached.
  const serialQs = serial ? `&serial=${encodeURIComponent(serial)}` : '';
  // Device resolution (read from the image's natural dimensions after load).
  const [devW, setDevW] = useState<number>(1080);
  const [devH, setDevH] = useState<number>(2400);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [text, setText] = useState('');

  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), interactive ? 800 : 1500);
    return () => clearInterval(id);
  }, [interactive]);

  const handleClick = async (e: React.MouseEvent<HTMLImageElement>) => {
    if (!interactive) return;
    const img = e.currentTarget;
    const rect = img.getBoundingClientRect();
    const cx = e.clientX - rect.left;
    const cy = e.clientY - rect.top;
    // Map DOM click to device pixel coords
    const dx = Math.round((cx / rect.width) * devW);
    const dy = Math.round((cy / rect.height) * devH);
    try {
      await fetch(`/api/device/tap?x=${dx}&y=${dy}${serialQs}`, { method: 'POST' });
      setTick(Date.now());
    } catch {}
  };

  const sendKey = async (code: string) => {
    try {
      await fetch(`/api/device/key?code=${code}${serialQs}`, { method: 'POST' });
      setTick(Date.now());
    } catch {}
  };

  const sendText = async () => {
    if (!text) return;
    try {
      await fetch(`/api/device/text?s=${encodeURIComponent(text)}${serialQs}`, { method: 'POST' });
      setText('');
      setTick(Date.now());
    } catch {}
  };

  return (
    <div style={{
      width: '260px', flexShrink: 0,
      padding: '10px 12px',
      background: '#f9fafb', border: '1px solid var(--color-border)',
      borderRadius: '6px',
      display: 'flex', flexDirection: 'column', gap: '8px',
    }}>
      <div style={{
        display: 'flex', alignItems: 'center', justifyContent: 'space-between',
      }}>
        <span style={{ fontSize: '11px', fontWeight: 600, color: 'var(--color-gray)',
                      textTransform: 'uppercase', letterSpacing: '0.5px' }}>
          Live Device
        </span>
        {interactive && (
          <span style={{ fontSize: '10px', color: '#d97706', fontWeight: 600,
                         padding: '2px 6px', background: '#fef3c7', borderRadius: '4px' }}>
            INPUT ON
          </span>
        )}
      </div>
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        {err ? (
          <div style={{ fontSize: '11px', color: 'var(--color-gray)', padding: '60px 0' }}>
            Device not reachable
          </div>
        ) : (
          <img
            ref={imgRef}
            src={`/api/device/screenshot?t=${tick}${serialQs}`}
            alt="device"
            onError={() => setErr(true)}
            onLoad={(e) => {
              setErr(false);
              const img = e.currentTarget;
              if (img.naturalWidth) setDevW(img.naturalWidth);
              if (img.naturalHeight) setDevH(img.naturalHeight);
            }}
            onClick={handleClick}
            style={{
              maxHeight: '460px', width: '220px',
              border: '4px solid #0a0a0a', borderRadius: '22px',
              boxShadow: '0 4px 12px rgba(0,0,0,0.15)',
              objectFit: 'contain', background: '#000',
              cursor: interactive ? 'crosshair' : 'default',
            }}
          />
        )}
      </div>
      {interactive && (
        <>
          <div style={{ display: 'flex', gap: '4px', flexWrap: 'wrap' as const }}>
            {['BACK', 'HOME', 'ENTER', 'DEL', 'MENU'].map(k => (
              <button key={k} onClick={() => sendKey(k)} style={{
                fontSize: '10px', padding: '3px 8px', cursor: 'pointer',
                background: 'var(--color-white)', border: '1px solid var(--color-border)',
                borderRadius: '4px', color: 'var(--color-black)', fontFamily: 'var(--font-mono)',
              }}>{k}</button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: '4px' }}>
            <input
              type="text"
              value={text}
              placeholder="text to type..."
              onChange={(e) => setText(e.target.value)}
              onKeyDown={(e) => { if (e.key === 'Enter') sendText(); }}
              style={{
                flex: 1, fontSize: '11px', padding: '4px 8px',
                border: '1px solid var(--color-border)', borderRadius: '4px',
                fontFamily: 'var(--font)',
              }}
            />
            <button onClick={sendText} style={{
              fontSize: '10px', padding: '4px 10px', cursor: 'pointer',
              background: 'var(--color-black)', color: 'var(--color-white)',
              border: 'none', borderRadius: '4px', fontFamily: 'var(--font)',
            }}>Type</button>
          </div>
          <div style={{ fontSize: '10px', color: 'var(--color-gray)', lineHeight: 1.4 }}>
            • 폰 화면 클릭 → 그 좌표로 tap<br/>
            • 키 버튼으로 BACK/HOME 등 전송<br/>
            • text 입력 → Enter 또는 Type 버튼
          </div>
        </>
      )}
    </div>
  );
}
