import React from 'react';
import { useAppState } from '../app/AppState';
import { shortSerial } from '../app/Shell';

/** 상단바 기기 선택 — 연결된 기기/에뮬레이터를 세그먼트로, 상태는 점으로. */
export function DevicePicker() {
  const { device, emuStatus, selectedSerial, setSelectedSerial } = useAppState();
  const targets = buildTargets(device.devices, emuStatus);
  const activeSerial = selectedSerial || (device.devices.length === 1 ? device.devices[0].serial : '');
  const needPick = device.devices.length > 1 && !activeSerial;

  if (targets.length === 0) {
    return (
      <span className="wf-chip outline"><span className="wf-dot" /> 연결된 기기 없음</span>
    );
  }
  return (
    <div className="wf-seg" style={needPick ? { borderColor: 'var(--wf-danger)' } : undefined} title={needPick ? '탐색할 기기를 선택하세요' : 'ADB 대상 기기'}>
      {targets.map((t) => {
        const on = activeSerial === t.serial;
        const tone = t.state === 'online_booting' ? 'warn' : ['offline', 'unauthorized'].includes(t.state) ? 'bad' : 'ok';
        return (
          <button key={t.serial} className={on ? 'on' : ''} disabled={!t.selectable} onClick={() => setSelectedSerial(t.serial)} title={t.title}>
            <span className={`wf-dot ${tone}`} style={{ width: 7, height: 7, marginRight: 6 }} />
            {t.label}{!t.selectable ? ` · ${t.state}` : ''}
          </button>
        );
      })}
    </div>
  );
}

export interface Target { serial: string; label: string; title: string; state: string; selectable: boolean; kind: 'emulator' | 'device' }

export function buildTargets(devices: { serial: string; info: string }[], emus: { serial: string; avd: string; state: string }[]): Target[] {
  const bySerial = new Map(devices.map((d) => [d.serial, d]));
  const seen = new Set<string>();
  const out: Target[] = emus.map((e) => {
    seen.add(e.serial);
    return { serial: e.serial, label: shortSerial(e.serial), title: `${e.serial}${e.avd ? ` (${e.avd})` : ''} — ${e.state}`, state: e.state, selectable: bySerial.has(e.serial), kind: 'emulator' as const };
  });
  for (const d of devices) {
    if (seen.has(d.serial)) continue;
    out.push({ serial: d.serial, label: shortSerial(d.serial), title: `${d.serial}${d.info ? ` ${d.info}` : ''}`, state: 'device', selectable: true, kind: d.serial.startsWith('emulator-') ? 'emulator' : 'device' });
  }
  return out;
}
