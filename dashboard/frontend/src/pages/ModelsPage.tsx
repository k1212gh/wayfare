import React, { useEffect, useState } from 'react';
import { IconChip, IconSearch, IconCheck, IconAlert } from '../app/icons';

/** 모델 설정 — Claude API / 로컬 OpenAI 호환 서버(Ollama·LM Studio·Open WebUI) / 사용 안 함.
 *  백엔드 /api/settings/llm 이 .env 와 프로세스 환경을 함께 갱신하므로 저장 즉시
 *  다음 파이프라인(라벨링, 비전 탐색, 플래너)에 적용된다. */

type Mode = 'api' | 'openai' | 'off';

interface Settings {
  mode: Mode; base_url: string; model_screen: string; model_vision: string;
  api_key_masked: string; anthropic_api_key_masked: string; timeout: number | null;
  stage5_mode: string; stage5_vision: boolean; json_mode: boolean; configured: boolean; reason: string;
}
interface TestResult {
  ok: boolean; mode?: string; error?: string; models?: string[]; models_error?: string;
  latency_ms?: number; json_ok?: boolean; vision_ok?: boolean; vision_latency_ms?: number; vision_error?: string;
  sample?: any; elapsed_ms?: number; ollama_native?: boolean;
}
type Provider = 'ollama' | 'lmstudio' | 'openwebui' | 'custom';

const PROVIDERS: Record<Provider, { name: string; port: number; path: string; needsKey: boolean; model: string; vision: string; hint: string; desc: string }> = {
  ollama: { name: 'Ollama', port: 11434, path: '/v1', needsKey: false, model: 'gemma4:12b', vision: 'qwen3.5:9b',
    desc: '가장 간단. 권장: gemma4:12b(텍스트) + qwen3.5:9b(비전).',
    hint: '다른 PC: OLLAMA_HOST=0.0.0.0 으로 서버 실행 · ollama pull gemma4:12b qwen3.5:9b (12GB VRAM 권장, 8GB 는 qwen3.5:4b 하나로)' },
  lmstudio: { name: 'LM Studio', port: 1234, path: '/v1', needsKey: false, model: 'qwen2.5-7b-instruct', vision: 'qwen2.5-vl-7b-instruct',
    desc: 'GUI 로 모델 관리. AMD(Vulkan) 도 지원.',
    hint: 'Developer 탭 → Server 시작, "Serve on Local Network" 켜고 모델을 로드해 두세요' },
  openwebui: { name: 'Open WebUI', port: 3000, path: '/api', needsKey: true, model: 'gemma4:12b', vision: 'qwen3.5:9b',
    desc: 'Ollama 앞단 웹 UI. API 키 필요.',
    hint: 'Settings → Account → API Keys 에서 키 발급 후 아래 API 키에 입력 (Docker 기본 포트 3000, 직접 실행은 8080)' },
  custom: { name: '직접 입력', port: 8000, path: '/v1', needsKey: false, model: '', vision: '',
    desc: 'vLLM, llama.cpp 등 OpenAI 호환 서버.',
    hint: 'OpenAI 호환 chat/completions 를 제공하는 서버라면 무엇이든' },
};

interface Found { provider: string; port: number; base_url: string; needs_key: boolean; status: number; models: string[]; vision_models: string[]; recommended?: string; recommended_vision?: string; hint?: string }

export function ModelsPage() {
  const [cur, setCur] = useState<Settings | null>(null);
  const [mode, setMode] = useState<Mode>('openai');
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
  const [provider, setProvider] = useState<Provider>('ollama');
  const [host, setHost] = useState('127.0.0.1');
  const [found, setFound] = useState<Found[] | null>(null);
  const [models, setModels] = useState<string[]>([]);
  const [discovering, setDiscovering] = useState(false);

  const load = async () => {
    try {
      const r = await fetch('/api/settings/llm');
      if (!r.ok) return;
      const d: Settings = await r.json();
      setCur(d);
      setMode(((d.mode as string) === 'cli' ? 'api' : d.mode) as Mode);
      setBaseUrl(d.base_url || '');
      try {
        if (d.base_url) {
          const u = new URL(d.base_url);
          setHost(u.hostname);
          const hit = (Object.keys(PROVIDERS) as Provider[]).find((k) => k !== 'custom' && String(PROVIDERS[k].port) === u.port);
          setProvider(hit || 'custom');
        }
      } catch { /* keep defaults */ }
      setModelScreen(d.model_screen || '');
      setModelVision(d.model_vision || '');
      setTimeoutS(d.timeout ?? '');
      setStage5Vision(!!d.stage5_vision);
      setJsonMode(!!d.json_mode);
    } catch { /* backend down */ }
  };
  useEffect(() => { load(); }, []);

  const save = async () => {
    setBusy('save'); setMsg('');
    try {
      const body: any = { mode, base_url: baseUrl, model_screen: modelScreen, model_vision: modelVision, stage5_vision: stage5Vision, json_mode: jsonMode };
      if (timeout !== '') body.timeout = Number(timeout);
      if (apiKey) body.api_key = apiKey;
      if (anthropicKey) body.anthropic_api_key = anthropicKey;
      const r = await fetch('/api/settings/llm', { method: 'PUT', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body) });
      const d = await r.json();
      if (!r.ok) { setMsg(`저장 실패: ${d.detail || r.status}`); return; }
      setCur(d); setApiKey(''); setAnthropicKey('');
      setMsg(d.configured ? `저장됨 · ${d.reason}` : `저장됨 · 모델 없이 동작 (${d.reason})`);
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

  const applyProvider = (k: Provider) => {
    const p = PROVIDERS[k];
    setProvider(k); setMode('openai');
    setBaseUrl(`http://${host}:${p.port}${p.path}`);
    if (!modelScreen) setModelScreen(p.model);
    if (!modelVision) setModelVision(p.vision);
    setMsg(p.hint);
  };

  const discover = async () => {
    setDiscovering(true); setFound(null); setMsg('');
    try {
      const r = await fetch('/api/settings/llm/discover', { method: 'POST', headers: { 'Content-Type': 'application/json' }, body: JSON.stringify({ host, api_key: apiKey || undefined }) });
      const d = await r.json();
      const list: Found[] = d.found || [];
      setFound(list);
      if (!list.length) { setMsg(`${host} 에서 Ollama(11434) · LM Studio(1234) · Open WebUI(3000/8080) 를 찾지 못했습니다`); return; }
      applyFound(list.find((f) => f.models.length) || list[0]);
    } catch (e: any) { setMsg(`탐지 실패: ${e.message || e}`); }
    finally { setDiscovering(false); }
  };

  const applyFound = (f: Found) => {
    const k = (f.provider in PROVIDERS ? f.provider : 'custom') as Provider;
    setProvider(k); setMode('openai'); setBaseUrl(f.base_url); setModels(f.models);
    if (f.models.length) {
      if (f.recommended) {
        setModelScreen(f.recommended);
        setModelVision(f.recommended_vision || (f.vision_models.includes(f.recommended) ? f.recommended : (f.vision_models[0] || '')));
      } else {
        setModelScreen(f.models.find((m) => !f.vision_models.includes(m)) || f.models[0]);
        if (f.vision_models.length) setModelVision(f.vision_models[0]);
      }
    }
    setMsg(f.hint || `${PROVIDERS[k].name} 발견 · 모델 ${f.models.length}개${f.models.length ? '' : ' (모델을 먼저 받아 두세요)'}`);
  };

  const statusText = !cur ? '…' : cur.configured
    ? (cur.mode === 'openai' ? `로컬 모델 · ${cur.model_screen}` : cur.mode === 'api' ? 'Claude API' : cur.reason)
    : `모델 없음 (${cur.reason}) — 라벨은 화면 텍스트로 대체`;

  return (
    <div className="wf-page" style={{ maxWidth: 980 }}>
      <div className="wf-page-head">
        <div>
          <h1>모델</h1>
          <p>화면 이름 짓기·분류·비전 탐색에 쓰는 언어 모델. 로컬 모델이면 비용 없이 돌아갑니다.</p>
        </div>
        <span className={`wf-chip ${cur?.configured ? 'accent' : 'amber'}`} style={{ height: 28 }}>
          <span className={`wf-dot ${cur?.configured ? 'ok' : 'warn'}`} /> {statusText}
        </span>
      </div>

      <div className="wf-card" style={{ padding: 22, display: 'grid', gap: 22 }}>
        <div>
          <div className="wf-eyebrow" style={{ marginBottom: 10 }}>공급자</div>
          <div className="wf-seg" style={{ padding: 4 }}>
            {([['openai', '로컬 모델 (OpenAI 호환)'], ['api', 'Claude API'], ['off', '사용 안 함']] as [Mode, string][]).map(([m, t]) => (
              <button key={m} className={mode === m ? 'on' : ''} style={{ height: 30, padding: '0 14px' }} onClick={() => setMode(m)}>{t}</button>
            ))}
          </div>
        </div>

        {mode === 'openai' && (
          <>
            <div>
              <div className="wf-eyebrow" style={{ marginBottom: 10 }}>서버</div>
              <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(200px, 1fr))', gap: 10 }}>
                {(Object.keys(PROVIDERS) as Provider[]).map((k) => (
                  <button key={k} className="wf-card hover flat" onClick={() => applyProvider(k)} style={{
                    padding: '12px 14px', textAlign: 'left', borderColor: provider === k ? 'var(--wf-accent)' : undefined,
                    background: provider === k ? 'var(--wf-accent-soft)' : undefined,
                  }}>
                    <div style={{ display: 'flex', alignItems: 'center', gap: 8 }}>
                      <IconChip size={15} />
                      <span style={{ fontWeight: 700 }}>{PROVIDERS[k].name}</span>
                      <span className="wf-mono wf-faint" style={{ marginLeft: 'auto' }}>:{PROVIDERS[k].port}</span>
                    </div>
                    <div className="wf-muted" style={{ fontSize: 12, marginTop: 4 }}>{PROVIDERS[k].desc}</div>
                  </button>
                ))}
              </div>
            </div>

            <div style={{ display: 'flex', gap: 8, alignItems: 'flex-end', flexWrap: 'wrap' }}>
              <div style={{ width: 220 }}>
                <label className="wf-label">서버 PC 주소 (이 PC 는 127.0.0.1)</label>
                <input className="wf-input mono" value={host} onChange={(e) => setHost(e.target.value)} placeholder="192.168.0.10" />
              </div>
              <button className="wf-btn" onClick={discover} disabled={discovering}><IconSearch size={14} /> {discovering ? '찾는 중…' : '서버 찾기'}</button>
              {found && found.length > 0 && found.map((f) => (
                <button key={f.base_url} className={`wf-chip clickable ${f.base_url === baseUrl ? 'accent' : 'outline'}`} style={{ height: 34 }} onClick={() => applyFound(f)}>
                  {PROVIDERS[(f.provider in PROVIDERS ? f.provider : 'custom') as Provider].name} · :{f.port} · 모델 {f.models.length}개{f.needs_key && f.status !== 200 ? ' · 키 필요' : ''}
                </button>
              ))}
            </div>

            <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
              <div><label className="wf-label">Base URL (…/v1)</label>
                <input className="wf-input mono" value={baseUrl} onChange={(e) => setBaseUrl(e.target.value)} placeholder="http://127.0.0.1:11434/v1" /></div>
              <div><label className="wf-label">텍스트 모델 — 라벨 선택·플래너{models.length ? ` · 탐지 ${models.length}개` : ''}</label>
                <input className="wf-input mono" list="wf-models" value={modelScreen} onChange={(e) => setModelScreen(e.target.value)} placeholder="qwen3.5:9b" /></div>
              <div><label className="wf-label">비전 모델 — 스크린샷 라벨·탐색 (선택)</label>
                <input className="wf-input mono" list="wf-models" value={modelVision} onChange={(e) => setModelVision(e.target.value)} placeholder="qwen3.5:9b" /></div>
              <datalist id="wf-models">{models.map((m) => <option key={m} value={m} />)}</datalist>
              <div><label className="wf-label">API 키 (서버가 요구할 때만{cur?.api_key_masked ? ` · 현재 ${cur.api_key_masked}` : ''})</label>
                <input className="wf-input mono" type="password" value={apiKey} onChange={(e) => setApiKey(e.target.value)} placeholder="비우면 유지" /></div>
              <div><label className="wf-label">타임아웃(초) — 로컬 모델은 600 권장</label>
                <input className="wf-input mono" type="number" value={timeout} onChange={(e) => setTimeoutS(e.target.value === '' ? '' : Number(e.target.value))} placeholder="600" /></div>
            </div>
            <div style={{ display: 'flex', gap: 22, flexWrap: 'wrap' }}>
              <label className="wf-check"><input type="checkbox" checked={stage5Vision} onChange={(e) => setStage5Vision(e.target.checked)} /> 스크린샷 라벨러도 실행 (비전 모델 필요 · 화면당 5~10초)</label>
              <label className="wf-check"><input type="checkbox" checked={jsonMode} onChange={(e) => setJsonMode(e.target.checked)} /> JSON 모드 요청 (response_format 지원 서버)</label>
            </div>
          </>
        )}

        {mode === 'api' && (
          <div style={{ display: 'grid', gridTemplateColumns: 'repeat(auto-fit, minmax(260px, 1fr))', gap: 14 }}>
            <div><label className="wf-label">ANTHROPIC_API_KEY{cur?.anthropic_api_key_masked ? ` · 현재 ${cur.anthropic_api_key_masked}` : ' · 없음'}</label>
              <input className="wf-input mono" type="password" value={anthropicKey} onChange={(e) => setAnthropicKey(e.target.value)} placeholder="sk-ant-… (비우면 유지)" /></div>
            <div><label className="wf-label">모델 (선택)</label>
              <input className="wf-input mono" value={modelScreen} onChange={(e) => setModelScreen(e.target.value)} placeholder="claude-sonnet-4-5" /></div>
          </div>
        )}

        {mode === 'off' && (
          <div className="wf-callout plain">모델 없이 동작합니다. 화면 이름은 상단 텍스트, 분류는 휴리스틱으로 채워집니다.</div>
        )}

        <div className="wf-divider" />
        <div style={{ display: 'flex', gap: 8, alignItems: 'center', flexWrap: 'wrap' }}>
          <button className="wf-btn primary" onClick={save} disabled={busy !== null}>{busy === 'save' ? '저장 중…' : '저장'}</button>
          {mode !== 'off' && <button className="wf-btn" onClick={() => runTest(false)} disabled={busy !== null}>{busy === 'test' ? '테스트 중…' : '연결 테스트'}</button>}
          {mode === 'openai' && <button className="wf-btn" onClick={() => runTest(true)} disabled={busy !== null}>비전 포함 테스트</button>}
          {msg && <span className="wf-muted" style={{ fontSize: 12.5 }}>{msg}</span>}
        </div>

        {test && (
          <div className={`wf-callout ${test.ok ? 'accent' : 'danger'}`} style={{ fontFamily: 'var(--wf-font-mono)', fontSize: 12, whiteSpace: 'pre-wrap', wordBreak: 'break-all' }}>
            <span style={{ display: 'inline-flex', alignItems: 'center', gap: 6, fontWeight: 700 }}>
              {test.ok ? <IconCheck size={14} /> : <IconAlert size={14} />}{test.ok ? '연결 성공' : '실패'}
            </span>
            {test.latency_ms != null ? ` · JSON 응답 ${test.latency_ms}ms` : ''}
            {test.ollama_native ? ' · Ollama 네이티브' : ''}
            {test.vision_ok != null ? ` · 비전 ${test.vision_ok ? `OK ${test.vision_latency_ms}ms` : `실패 (${test.vision_error || ''})`}` : ''}
            {test.error ? `\n${test.error}` : ''}
            {test.models?.length ? `\n모델 ${test.models.length}개: ${test.models.slice(0, 8).join(', ')}${test.models.length > 8 ? ' …' : ''}` : ''}
            {test.models_error ? `\n(/models 조회 불가: ${test.models_error})` : ''}
          </div>
        )}
      </div>

      <div className="wf-callout plain" style={{ marginTop: 16 }}>
        <b>어떻게 쓰이나.</b> 로컬 모델의 기본 라벨링은 <b>후보 선택형</b>입니다 — 화면에 실제로 보이는 텍스트 중 하나를 고르기만 해서
        없는 이름을 지어내지 않고, 화면당 200토큰 안팎이라 9B 모델·12GB GPU 에서 수십 초면 끝납니다.
        RTX 4070 SUPER 실측(2회): 후보 선택은 <span className="wf-mono">gemma4:12b</span> 가 어려운 화면에서 더 정확(82%), 스크린샷 라벨은 <span className="wf-mono">qwen3.5:9b</span> 가 두 번 다 오답 0 — 그래서 기본값이 둘로 나뉩니다 (docs/local_llm_benchmark.md).
      </div>
    </div>
  );
}
