import React from 'react';
import { useAppState, useRunningTours } from '../app/AppState';
import { buildTargets } from '../dashboard/DevicePicker';
import { LiveDeviceMirror } from '../dashboard/LiveDeviceMirror';
import { tourTitle } from '../dashboard/tourTitle';
import { IconPhone, IconPlay, IconStop, IconRefresh } from '../app/icons';

export function DevicesPage() {
  const { device, emuStatus, selectedSerial, setSelectedSerial, startEmulator, killEmulator } = useAppState();
  const running = useRunningTours();
  const targets = buildTargets(device.devices, emuStatus);
  const activeSerial = selectedSerial || (device.devices.length === 1 ? device.devices[0].serial : '');
  const activeTarget = targets.find((t) => t.serial === activeSerial);
  const anyEmu = emuStatus[0];
  const stall = anyEmu && ['offline', 'unauthorized'].includes(anyEmu.state);
  const ready = anyEmu && anyEmu.state === 'online_boot_complete';
  const busyTour = running.find((t) => t.device_serial === activeSerial && t.device_active);
  const interactive = !!busyTour?.paused || !busyTour;

  return (
    <div className="wf-page">
      <div className="wf-page-head">
        <div>
          <h1>기기</h1>
          <p>ADB 로 연결된 실기기와 에뮬레이터. 탐색 중이 아닐 때는 화면을 직접 조작할 수 있습니다.</p>
        </div>
        <div style={{ display: 'flex', gap: 8 }}>
          {!anyEmu && <button className="wf-btn" onClick={() => startEmulator()}><IconPlay size={13} /> 에뮬레이터 시작</button>}
          {stall && <button className="wf-btn danger" onClick={() => killEmulator(anyEmu.serial)}><IconStop size={13} /> 강제 종료</button>}
          {stall && <button className="wf-btn" onClick={() => startEmulator({ safe: true })}><IconRefresh size={13} /> 안전 재시작</button>}
          {stall && <button className="wf-btn" onClick={() => startEmulator({ cold: true })}>콜드 재시작</button>}
          {ready && <button className="wf-btn quiet" onClick={() => killEmulator(anyEmu.serial)}><IconStop size={13} /> 에뮬레이터 종료</button>}
        </div>
      </div>

      <div style={{ display: 'grid', gridTemplateColumns: 'minmax(0, 1fr) 320px', gap: 20, alignItems: 'start' }}>
        <section className="wf-list">
          <div className="wf-section-title"><h2>연결된 기기</h2><span className="count">{targets.length}</span></div>
          {targets.length === 0 && (
            <div className="wf-empty">
              연결된 기기가 없습니다. USB 디버깅을 켠 폰을 연결하거나 에뮬레이터를 시작하세요.<br />
              <span className="wf-mono">adb devices</span> 로 인식되는지 확인할 수 있습니다.
            </div>
          )}
          {targets.map((t) => {
            const on = t.serial === activeSerial;
            const tone = t.state === 'online_booting' ? 'warn' : ['offline', 'unauthorized'].includes(t.state) ? 'bad' : 'ok';
            const info = device.devices.find((d) => d.serial === t.serial)?.info || '';
            const tour = running.find((r) => r.device_serial === t.serial && r.device_active);
            return (
              <div key={t.serial} className={`wf-card hover`} style={{ padding: 16, display: 'flex', alignItems: 'center', gap: 14, borderColor: on ? 'var(--wf-accent)' : undefined }}>
                <div className="wf-app-icon" style={{ background: t.kind === 'emulator' ? 'var(--wf-info)' : 'var(--wf-accent)' }}><IconPhone size={22} /></div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                    <span style={{ fontWeight: 700, fontSize: 15 }}>{t.kind === 'emulator' ? '에뮬레이터' : '실기기'}</span>
                    <span className="wf-chip mono outline">{t.serial}</span>
                    <span className={`wf-dot ${tone}`} />
                    <span className="wf-faint" style={{ fontSize: 12 }}>{t.state === 'device' ? '준비됨' : t.state}</span>
                  </div>
                  <div className="wf-faint wf-ellipsis" style={{ fontSize: 12, marginTop: 2 }}>{info || t.title}</div>
                  {tour && <div className="wf-chip amber" style={{ marginTop: 6 }}><span className="wf-dot live" style={{ width: 6, height: 6 }} /> {tourTitle(tour)} 탐색 중</div>}
                </div>
                <button className={`wf-btn${on ? ' on' : ''}`} disabled={!t.selectable} onClick={() => setSelectedSerial(t.serial)}>{on ? '선택됨' : '이 기기 사용'}</button>
              </div>
            );
          })}
        </section>

        <aside className="wf-card" style={{ padding: 14, position: 'sticky', top: 16 }}>
          <div className="wf-eyebrow" style={{ marginBottom: 8 }}>{activeTarget ? `${activeTarget.label} 화면` : '기기 화면'}</div>
          {activeTarget ? (
            <>
              <LiveDeviceMirror interactive={interactive} serial={activeSerial} compact />
              {!interactive && <div className="wf-callout amber" style={{ marginTop: 10, padding: '8px 10px' }}>탐색 중에는 읽기 전용입니다. 일시정지하면 직접 조작할 수 있습니다.</div>}
            </>
          ) : (
            <div className="wf-empty" style={{ padding: '40px 10px' }}>기기를 선택하면 화면이 표시됩니다</div>
          )}
        </aside>
      </div>
    </div>
  );
}
