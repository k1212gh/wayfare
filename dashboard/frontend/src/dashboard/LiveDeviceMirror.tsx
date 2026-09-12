import React, { useEffect, useRef, useState } from 'react';

/**
 * 연결된 ADB 기기 화면 미러 — /api/device/screenshot 을 0.8~1.5초마다 갱신하고
 * interactive 면 클릭/키/텍스트를 기기로 보낸다. `serial` 은 여러 기기가 붙었을 때
 * 대상을 고정한다 (없으면 adb exec-out 이 모호해서 실패).
 */
interface LiveDeviceMirrorProps {
  interactive: boolean;
  serial: string;
  compact?: boolean;
}

export function LiveDeviceMirror({ interactive, serial, compact = false }: LiveDeviceMirrorProps) {
  const [tick, setTick] = useState<number>(() => Date.now());
  const [err, setErr] = useState(false);
  const serialQs = serial ? `&serial=${encodeURIComponent(serial)}` : '';
  const [devW, setDevW] = useState(1080);
  const [devH, setDevH] = useState(2400);
  const imgRef = useRef<HTMLImageElement | null>(null);
  const [text, setText] = useState('');

  useEffect(() => {
    const id = setInterval(() => setTick(Date.now()), interactive ? 800 : 1500);
    return () => clearInterval(id);
  }, [interactive]);

  const handleClick = async (e: React.MouseEvent<HTMLImageElement>) => {
    if (!interactive) return;
    const rect = e.currentTarget.getBoundingClientRect();
    const dx = Math.round(((e.clientX - rect.left) / rect.width) * devW);
    const dy = Math.round(((e.clientY - rect.top) / rect.height) * devH);
    try { await fetch(`/api/device/tap?x=${dx}&y=${dy}${serialQs}`, { method: 'POST' }); setTick(Date.now()); } catch {}
  };
  const sendKey = async (code: string) => {
    try { await fetch(`/api/device/key?code=${code}${serialQs}`, { method: 'POST' }); setTick(Date.now()); } catch {}
  };
  const sendText = async () => {
    if (!text) return;
    try { await fetch(`/api/device/text?s=${encodeURIComponent(text)}${serialQs}`, { method: 'POST' }); setText(''); setTick(Date.now()); } catch {}
  };

  return (
    <div style={{ display: 'flex', flexDirection: 'column', gap: 10, width: compact ? '100%' : 260 }}>
      {interactive && (
        <div style={{ display: 'flex', justifyContent: 'flex-end' }}>
          <span className="wf-chip amber">입력 켜짐</span>
        </div>
      )}
      <div style={{ display: 'flex', justifyContent: 'center' }}>
        {err ? (
          <div className="wf-empty" style={{ width: '100%', padding: '60px 10px' }}>기기 화면을 가져올 수 없습니다</div>
        ) : (
          <img
            ref={imgRef}
            className="wf-phone"
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
              maxHeight: compact ? 380 : 460,
              width: compact ? '100%' : 220,
              maxWidth: compact ? 250 : undefined,
              cursor: interactive ? 'crosshair' : 'default',
            }}
          />
        )}
      </div>
      {interactive && (
        <>
          <div style={{ display: 'flex', gap: 4, flexWrap: 'wrap', justifyContent: 'center' }}>
            {['BACK', 'HOME', 'ENTER', 'DEL', 'MENU'].map((k) => (
              <button key={k} className="wf-btn sm quiet" onClick={() => sendKey(k)}><span className="wf-mono">{k}</span></button>
            ))}
          </div>
          <div style={{ display: 'flex', gap: 6 }}>
            <input className="wf-input" style={{ height: 32 }} value={text} placeholder="입력할 텍스트…" onChange={(e) => setText(e.target.value)} onKeyDown={(e) => { if (e.key === 'Enter') sendText(); }} />
            <button className="wf-btn sm primary" onClick={sendText}>입력</button>
          </div>
          <div className="wf-faint" style={{ fontSize: 11, lineHeight: 1.5 }}>
            화면을 클릭하면 그 좌표를 탭합니다 · 키 버튼으로 BACK/HOME 전송 · 텍스트는 Enter
          </div>
        </>
      )}
    </div>
  );
}
