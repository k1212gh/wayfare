import React, { useEffect, useState } from 'react';

/** LLM 공급자 설정 — Claude API / 로컬 OpenAI 호환 서버(다른 PC 의 GPU) / 사용 안 함.
 *  백엔드 /api/settings/llm 이 .env 와 프로세스 환경을 함께 갱신하므로 저장 즉시
 *  다음 파이프라인(Stage 5 라벨링, 비전 태퍼, 플래너)에 적용된다. */

type Mode = 'api' | 'openai' | 'off';

interface Settings {
  mode: Mode;
  base_url: string;
  model_screen: string;
  model_vision: string;
  api_key_masked: string;
  anthropic_api_key_masked: string;
  timeout: number | null;
  stage5_mode: string;
  stage5_vision: boolean;
  json_mode: boolean;
  configured: boolean;
  reason: string;
}

interface TestResult {
  ok: boolean;
  mode?: string;
  error?: string;
  models?: string[];
  models_error?: string;
  latency_ms?: number;
  json_ok?: boolean;
  vision_ok?: boolean;
  vision_latency_ms?: number;
  vision_error?: string;
  sample?: any;
  elapsed_ms?: number;
}

const PRESETS: Record<string, { base_url: string; model_screen: string; model_vision: string; hint: string }> = {
  ollama: {
    base_url: 'http://192.168.0.10:11434/v1',
    model_screen: 'qwen2.5:7b-instruct',
    model_vision: 'qwen2.5vl:7b',
    hint: 'Ollama: 다른 PC 에서 OLLAMA_HOST=0.0.0.0 ollama serve 후 ollama pull qwen2.5:7b-instruct',
  },
  lmstudio: {
    base_url: 'http://192.168.0.10:1234/v1',
    model_screen: 'qwen2.5-7b-instruct',
    model_vision: 'qwen2.5-vl-7b-instruct',
    hint: 'LM Studio: Developer 탭 → Server 시작, "Serve on Local Network" 켜기',
  },
};

const inputStyle: React.CSSProperties = {
  width: '100%', padding: '7px 10px', fontSize: 13, border: '1px solid var(--color-border)',
  borderRadius: 6, fontFamily: 'var(--font-mono)', background: '#fff',
};
const labelStyle: React.CSSProperties = { fontSize: 12, color: 'var(--color-gray)', marginBottom: 4, display: 'block' };

export function LLMSettings() {
  const [open, setOpen] = useState(false);
  const [cur, setCur] = useState<Settings | null>(null);
  const [mode, setMode] = useState<Mode>('api');
  const [baseUrl, setBaseUrl] = useState('');
  const [modelScreen, setModelScreen] = useState('');
  const [modelVision, setModelVision] = useState('');
  const [apiKey, setApiKey] = useState('');
  const [anthropicKey, setAnthropicKey] = useState('');
  const [timeout, setTimeoutS] = useState<number | ''>('');
  const [stage5Vision, setStage5Vision] = useState(false);
  const [jsonMode, setJsonMode] = useState(false);
  const [busy, setBusy] = useState<'save' | 'test' | null>(null);
  const [test, setTest] = useState<TestResult | null>(null);
  const [msg, setMsg] = useState('');

  const load = async () => {
    try {
      const r = await fetch('/api/settings/llm');
      if (!r.ok) return;
      const d: Settings = await r.json();
      setCur(d);
      setMode(((d.mode as string) === 'cli' ? 'api' : d.mode) as Mode);
      setBaseUrl(d.base_url || '');
      setModelScreen(d.model_screen || '');
      setModelVision(d.model_vision || '');
      setTimeoutS(d.timeout ?? '');
      setStage5Vision(!!d.stage5_vision);
      setJsonMode(!!d.json_mode);
    } catch { /* backend down — panel stays collapsed */ }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy('save'); setMsg('');
    try {
      const body: any = { mode, base_url: baseUrl, model_screen: modelScreen, model_vision: modelVision,
        stage5_vision: stage5Vision, json_mode: jsonMode };
      if (timeout !== '') body.timeout = Number(timeout);
      if (apiKey) body.api_key = apiKey;
      if (anthropicKey) body.anthropic_api_key = anthropicKey;
      const r = await fetch('/api/settings/llm', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) { setMsg(`저장 실패: ${d.detail || r.status}`); return; }
      setCur(d); setApiKey(''); setAnthropicKey('');
      setMsg(d.configured ? `저장됨 · ${d.reason}` : `저장됨 · LLM 비활성 (${d.reason})`);
    } catch (e: any) { setMsg(`저장 실패: ${e.message || e}`); }
    finally { setBusy(null); }
  };

  const runTest = async (withVision: boolean) => {
    setBusy('test'); setTest(null); setMsg('');
    try {
      const body: any = { mode, base_url: baseUrl, model_screen: modelScreen, model_vision: modelVision, with_vision: withVision };
      if (mode === 'openai' && apiKey) body.api_key = apiKey;
      if (mode === 'api' && anthropicKey) body.api_key = anthropicKey;
      const r = await fetch('/api/settings/llm/test', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      setTest(await r.json());
    } catch (e: any) { setTest({ ok: false, error: String(e.message || e) }); }
    finally { setBusy(null); }
  };

  const applyPreset = (k: string) => {
    const p = PRESETS[k];
    setMode('openai'); setBaseUrl(p.base_url); setModelScreen(p.model_screen); setModelVision(p.model_vision);
    setMsg(p.hint);
  };

  const statusDot = cur?.configured ? '#22c55e' : '#f59e0b';
  const statusText = !cur ? '…' : cur.configured
    ? (cur.mode === 'openai' ? `로컬 LLM · ${cur.model_screen}` : cur.mode === 'api' ? 'Claude API' : cur.reason)
    : `LLM 없음 (${cur.reason}) — 라벨은 화면 텍스트로 대체`;

  return (
    <section style={{ marginBottom: 18, border: '1px solid var(--color-border)', borderRadius: 10, overflow: 'hidden' }}>
      <button onClick={() => setOpen(!open)} style={{
        width: '100%', display: 'flex', alignItems: 'center', gap: 10, padding: '10px 14px',
        background: 'var(--color-surface)', border: 'none', cursor: 'pointer', fontFamily: 'var(--font)', fontSize: 13, textAlign: 'left',
      }}>
        <span style={{ width: 8, height: 8, borderRadius: '50%', background: statusDot, flexShrink: 0 }} />
        <span style={{ fontWeight: 600 }}>LLM 설정</span>
        <span style={{ color: 'var(--color-gray)', overflow: 'hidden', textOverflow: 'ellipsis', whiteSpace: 'nowrap' }}>{statusText}</span>
        <span style={{ marginLeft: 'auto', color: 'var(--color-gray)' }}>{open ? '▲' : '▼'}</span>
      </button>

      {open && (
        <div style={{ padding: 16, display: 'grid', gap: 14 }}>
          <div style={{ display: 'flex', gap: 8, flexWrap: 'wrap' }}>
            {([['api', 'Claude API'], ['openai', '로컬 LLM (OpenAI 호환)'], ['off', '사용 안 함']] as [Mode, string][]).map(([m, t]) => (
              <button key={m} onClick={() => setMode(m)} style={{
                padding: '6px 12px', fontSize: 13, borderRadius: 999, cursor: 'pointer', fontFamily: 'var(--font)',
                border: `1px solid ${mode === m ? 'var(--color-black)' : 'var(--color-border)'}`,
                background: mode === m ? 'var(--color-black)' : '#fff', color: mode === m ? '#fff' : 'var(--color-black)',
              }}>{t}</button>
            ))}
          </div>

          {mode === 'openai' && (
            <>
              <div style={{ display: 'flex', gap: 6, fontSize: 12, color: 'var(--color-gray)', alignItems: 'center', flexWrap: 'wrap' }}>
                프리셋:
                <button onClick={() => applyPreset('ollama')} style={presetBtn}>Ollama</button>
                <button onClick={() => applyPreset('lmstudio')} style={presetBtn}>LM Studio</button>
                <span>· 다른 PC 의 IP 로 base URL 을 바꾸세요. 이 PC 라면 127.0.0.1</span>
              </div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
                <div><label style={labelStyle}>Base URL (…/v1)</label>
                  <input style={inputStyle} value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://192.168.0.10:11434/v1" /></div>
                <div><label style={labelStyle}>텍스트 모델 (라벨 선택·플래너)</label>
                  <input style={inputStyle} value={modelScreen} onChange={(e) => setModelScreen(e.target.value)} placeholder="qwen2.5:7b-instruct" /></div>
                <div><label style={labelStyle}>비전 모델 (선택 · 스크린샷 라벨/탐색)</label>
                  <input style={inputStyle} value={modelVision} onChange={(e) => setModelVision(e.target.value)} placeholder="qwen2.5vl:7b" /></div>
                <div><label style={labelStyle}>API 키 (서버가 요구할 때만{cur?.api_key_masked ? ` · 현재 ${cur.api_key_masked}` : ''})</label>
                  <input style={inputStyle} type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="비우면 유지" /></div>
                <div><label style={labelStyle}>타임아웃(초) — 로컬 모델은 600 권장</label>
                  <input style={inputStyle} type="number" value={timeout} onChange={(e) => setTimeoutS(e.target.value === '' ? '' : Number(e.target.value))} placeholder="600" /></div>
              </div>
              <div style={{ display: 'flex', gap: 18, fontSize: 13, flexWrap: 'wrap' }}>
                <label><input type="checkbox" checked={stage5Vision} onChange={(e) => setStage5Vision(e.target.checked)} /> 스크린샷 라벨러도 실행 (비전 모델 필요, 느림)</label>
                <label><input type="checkbox" checked={jsonMode} onChange={(e) => setJsonMode(e.target.checked)} /> JSON 모드 요청 (response_format 지원 서버)</label>
              </div>
            </>
          )}

          {mode === 'api' && (
            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(240px, 1fr))', gap: 12 }}>
              <div><label style={labelStyle}>ANTHROPIC_API_KEY{cur?.anthropic_api_key_masked ? ` · 현재 ${cur.anthropic_api_key_masked}` : ' · 없음'}</label>
                <input style={inputStyle} type="password" value={anthropicKey} onChange={(e) => setAnthropicKey(e.target.value)} placeholder="sk-ant-… (비우면 유지)" /></div>
              <div><label style={labelStyle}>모델 (선택)</label>
                <input style={inputStyle} value={modelScreen} onChange={(e) => setModelScreen(e.target.value)} placeholder="claude-sonnet-4-5" /></div>
            </div>
          )}

          {mode === 'off' && (
            <div style={{ fontSize: 13, color: 'var(--color-gray)' }}>
              LLM 없이 동작합니다. 노드 이름은 화면 상단 텍스트, 카테고리는 휴리스틱으로 채워집니다.
            </div>
          )}

          <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
            <button onClick={save} disabled={busy !== null} style={primaryBtn}>{busy === 'save' ? '저장 중…' : '저장'}</button>
            {mode !== 'off' && (
              <>
                <button onClick={() => runTest(false)} disabled={busy !== null} style={ghostBtn}>{busy === 'test' ? '테스트 중…' : '연결 테스트'}</button>
                {mode === 'openai' && <button onClick={() => runTest(true)} disabled={busy !== null} style={ghostBtn}>비전 포함 테스트</button>}
              </>
            )}
            {msg && <span style={{ fontSize: 12, color: 'var(--color-gray)' }}>{msg}</span>}
          </div>

          {test && (
            <div style={{
              fontSize: 12, fontFamily: 'var(--font-mono)', padding: 10, borderRadius: 6,
              background: test.ok ? '#ecfdf5' : '#fef2f2', border: `1px solid ${test.ok ? '#a7f3d0' : '#fecaca'}`,
              whiteSpace: 'pre-wrap', wordBreak: 'break-all',
            }}>
              {test.ok ? '✓ 연결 성공' : '✗ 실패'}{test.latency_ms != null ? ` · JSON 응답 ${test.latency_ms}ms` : ''}
              {test.vision_ok != null ? ` · 비전 ${test.vision_ok ? `OK ${test.vision_latency_ms}ms` : `실패 (${test.vision_error || ''})`}` : ''}
              {test.error ? `\n${test.error}` : ''}
              {test.models && test.models.length ? `\n모델 ${test.models.length}개: ${test.models.slice(0, 8).join(', ')}${test.models.length > 8 ? ' …' : ''}` : ''}
              {test.models_error ? `\n(/models 조회 불가: ${test.models_error})` : ''}
            </div>
          )}

          <div style={{ fontSize: 12, color: 'var(--color-gray)', lineHeight: 1.6 }}>
            로컬 모드 기본 라벨링은 <b>후보 선택형</b>입니다. 화면에 실제로 보이는 텍스트 중 하나를 고르기만 하므로
            없는 이름을 지어내지 않고, 노드당 200토큰 안팎이라 7B 모델과 RX 6800 XT 급에서도 수십 초면 끝납니다.
          </div>
        </div>
      )}
    </section>
  );
}

const presetBtn: React.CSSProperties = {
  padding: '3px 9px', fontSize: 12, border: '1px solid var(--color-border)', borderRadius: 6, background: '#fff', cursor: 'pointer', fontFamily: 'var(--font)',
};
const primaryBtn: React.CSSProperties = {
  padding: '7px 16px', fontSize: 13, border: 'none', borderRadius: 6, background: 'var(--color-black)', color: '#fff', cursor: 'pointer', fontFamily: 'var(--font)',
};
const ghostBtn: React.CSSProperties = {
  padding: '7px 14px', fontSize: 13, border: '1px solid var(--color-border)', borderRadius: 6, background: '#fff', cursor: 'pointer', fontFamily: 'var(--font)',
};
