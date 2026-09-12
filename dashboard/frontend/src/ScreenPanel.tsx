import React, { useEffect, useState } from 'react';
import { CATEGORY_COLOR, CATEGORY_LABEL, STATUS_STYLE, edgeStyle } from './graph/colors';
import { displayLabel, subLabel } from './graph/displayLabel';
import { readableTriggerLabel } from './ScreenMapView';

interface ScreenPanelProps {
  node: any;
  tourId: string;
  allNodes?: any[];
  allEdges?: any[];
  onSelectNode?: (nid: string) => void;
  onSelectEdge?: (edge: any) => void;
}

function friendlyTrigger(e: any): string {
  const action: string = e.trigger_action || '';
  const elem: string = e.trigger_widget || '';
  const kind: string = e.kind || '';
  if (kind === 'back' || action === 'press_back') return '뒤로';
  if (kind === 'launcher') return '런처';
  if (kind === 'intent_filter') return elem ? `딥링크 ${elem.split('/').pop() || ''}` : '딥링크';
  if (kind === 'overlay') return '오버레이';
  if (elem === 'fragment_transaction') return '포함 (Fragment)';
  if (elem.startsWith('reflection/')) return '정적 추론';
  if (elem.startsWith('two_hop_')) return '헬퍼 경유';
  if (action === 'intent' && elem) return elem.split('.').pop() || elem;
  if (elem) { const t = readableTriggerLabel(elem) || elem; return t.length > 26 ? t.slice(0, 26) + '…' : t; }
  return action || kind || '?';
}

/** 우측 인스펙터 — 선택한 화면의 스크린샷·설명·연결·UI 요소. */
export function ScreenPanel({ node, tourId, allNodes = [], allEdges = [], onSelectNode, onSelectEdge }: ScreenPanelProps) {
  const [screenshotUrl, setScreenshotUrl] = useState('');
  useEffect(() => {
    setScreenshotUrl(tourId && node.screen_id && node.screenshot_ref ? `/api/tours/${tourId}/screenshot/${node.screen_id}` : '');
  }, [tourId, node.screen_id, node.screenshot_ref]);

  const nodeById = (sid: string) => allNodes.find((x) => x.screen_id === sid);
  const labelFor = (sid: string) => { const n = nodeById(sid); return n ? displayLabel(n) : sid.slice(0, 14); };
  const thumbFor = (sid: string) => { const n = nodeById(sid); return n?.screenshot_ref ? `/api/tours/${tourId}/screenshot/${sid}` : undefined; };

  const incoming = allEdges.filter((e) => e.to === node.screen_id);
  const outgoing = allEdges.filter((e) => e.from === node.screen_id);
  const hasUI = !!node.screenshot_ref || (node.widgets?.length ?? 0) > 0;
  const status = node.status || '';
  const st = STATUS_STYLE[status];
  const cat = node.functional_category || 'other';
  const title = displayLabel(node);
  const sub = subLabel(node, title);

  return (
    <div>
      <div style={{ marginBottom: 14 }}>
        <div className="wf-display" style={{ fontSize: 20 }}>{title}</div>
        {sub && <div className="wf-mono wf-faint" style={{ marginTop: 2 }}>{sub}</div>}
      </div>
      <div style={{ display: 'flex', gap: 6, flexWrap: 'wrap', marginBottom: 18 }}>
        <span className="wf-chip" style={{ background: CATEGORY_COLOR[cat] || CATEGORY_COLOR.other, color: '#FFFCF5' }}>{CATEGORY_LABEL[cat] || cat}</span>
        {st && <span className="wf-chip outline" title={st.desc}><span className="wf-dot" style={{ background: st.color }} />{st.label}</span>}
        {node.confidence && <span className="wf-chip outline mono">신뢰도 {node.confidence}</span>}
        {node.label_source && <span className="wf-chip outline mono" title="라벨 출처">{{ vision: '스크린샷', vision_snapped: '스크린샷+화면 텍스트', picked: '텍스트 선택', llm: 'LLM', fallback: '자동', candidate: '후보' }[node.label_source as string] || node.label_source}</span>}
        {!hasUI && <span className="wf-chip amber">UI 없음</span>}
      </div>

      {screenshotUrl && (
        <Section title="스크린샷">
          <img src={screenshotUrl} alt="" style={{ width: '100%', borderRadius: 12, border: '1px solid var(--wf-border)', background: '#111' }}
            onError={(e) => { (e.target as HTMLImageElement).style.display = 'none'; }} />
        </Section>
      )}
      {node.screen_purpose && (
        <Section title="이 화면에서 하는 일">
          <div className="wf-callout accent" style={{ fontWeight: 600 }}>{node.screen_purpose}</div>
        </Section>
      )}
      {node.description && <Section title="설명"><p style={{ lineHeight: 1.6, fontSize: 13 }}>{node.description}</p></Section>}
      {node.entry_hint && <Section title="도달 방법"><p className="wf-muted" style={{ fontSize: 13, fontStyle: 'italic' }}>{node.entry_hint}</p></Section>}

      {node.data_displayed?.length > 0 && (
        <Section title="표시되는 데이터">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>{node.data_displayed.map((d: string, i: number) => <span key={i} className="wf-chip outline">{d}</span>)}</div>
        </Section>
      )}
      {node.primary_affordances?.length > 0 && (
        <Section title={`주요 동작 ${node.primary_affordances.length}`}>
          <ul style={{ paddingLeft: 18, lineHeight: 1.7, fontSize: 13 }}>{node.primary_affordances.map((a: string, i: number) => <li key={i}>{a}</li>)}</ul>
        </Section>
      )}
      {node.label_candidates?.length > 0 && (
        <Section title="화면 텍스트 후보">
          <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>{node.label_candidates.slice(0, 8).map((c: string, i: number) => <span key={i} className={`wf-chip ${c === node.label ? 'accent' : 'outline'}`}>{c}</span>)}</div>
        </Section>
      )}

      {outgoing.length > 0 && (
        <Section title={`→ 나가는 전환 ${outgoing.length}`}>
          <div className="wf-list" style={{ gap: 4 }}>
            {outgoing.map((e: any, i: number) => <EdgeRow key={i} trigger={friendlyTrigger(e)} target={labelFor(e.to)} kind={e.kind || 'navigate'} outcome={e.outcome} thumbnailUrl={thumbFor(e.to)} onClick={onSelectEdge ? () => onSelectEdge(e) : undefined} />)}
          </div>
        </Section>
      )}
      {incoming.length > 0 && (
        <Section title={`← 들어오는 전환 ${incoming.length}`}>
          <div className="wf-list" style={{ gap: 4 }}>
            {incoming.map((e: any, i: number) => <EdgeRow key={i} trigger={friendlyTrigger(e)} target={labelFor(e.from)} kind={e.kind || 'navigate'} direction="in" thumbnailUrl={thumbFor(e.from)} onClick={onSelectEdge ? () => onSelectEdge(e) : undefined} />)}
          </div>
        </Section>
      )}

      {node.primitives && Object.keys(node.primitives).length > 0 && (
        <Section title="입력 요소">
          <div className="wf-list" style={{ gap: 6 }}>
            {Object.entries(node.primitives).map(([type, items]: [string, any]) => (
              <div key={type} className="wf-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 4 }}>
                <div className="wf-eyebrow">{type} · {items.length}</div>
                <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                  {items.slice(0, 8).map((it: any, i: number) => (
                    <span key={i} className="wf-chip outline mono" title={JSON.stringify(it, null, 2)}>{it.label || it.id || it.value_hint || it.kind}{it.outcome_hint ? ` → ${it.outcome_hint}` : ''}</span>
                  ))}
                  {items.length > 8 && <span className="wf-faint">+{items.length - 8}</span>}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {node.chip_groups?.length > 0 && (
        <Section title={`선택 그룹 ${node.chip_groups.length}`}>
          <div className="wf-list" style={{ gap: 6 }}>
            {node.chip_groups.map((g: any, i: number) => (
              <div key={i} className="wf-row" style={{ flexDirection: 'column', alignItems: 'stretch', gap: 6 }}>
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'baseline' }}>
                  <b>{g.group_id || `group_${i}`}</b>
                  <span className="wf-mono wf-faint">{g.type}{g.required ? ' · 필수' : ''}</span>
                </div>
                {g.type === 'stepper' ? (
                  <div className="wf-mono" style={{ display: 'flex', gap: 6, alignItems: 'center' }}>
                    <span className="wf-chip outline">− {g.minus_widget}</span><span className="wf-chip">{g.display_widget || '…'}</span><span className="wf-chip outline">+ {g.plus_widget}</span>
                    <span className="wf-faint" style={{ marginLeft: 'auto' }}>[{g.min ?? 1}–{g.max ?? 99}, 기본 {g.default ?? 1}]</span>
                  </div>
                ) : g.type === 'dropdown' ? (
                  <div className="wf-mono wf-faint">trigger → {g.trigger_widget}</div>
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: 4 }}>
                    {(g.options || []).map((opt: any, j: number) => <span key={j} className={`wf-chip ${opt.selected_default ? 'accent' : 'outline'}`} title={opt.widget_id}>{opt.value}{opt.selected_default ? ' ✓' : ''}</span>)}
                  </div>
                )}
                {g.detection && <div className="wf-mono wf-faint">{g.detection.method} · conf={g.detection.confidence}</div>}
              </div>
            ))}
          </div>
        </Section>
      )}

      {node.infinite_scroll && (
        <Section title="무한 스크롤">
          <div className="wf-callout plain">피드/목록 화면 · 활동 노드 {node.scroll_metadata?.activity_node_count ?? '?'}</div>
        </Section>
      )}

      {node.widgets?.length > 0 && (
        <Section title={`UI 요소 ${node.widgets.length}`}>
          <div className="wf-list" style={{ gap: 4 }}>
            {node.widgets.map((el: any, i: number) => (
              <div key={i} className="wf-row" style={{ justifyContent: 'space-between' }}>
                <span className="wf-mono wf-ellipsis">{el.id || el.text || 'unnamed'}</span>
                <span className="wf-mono wf-faint" style={{ flexShrink: 0 }}>{el.type}{el.role ? ` · ${el.role}` : ''}</span>
              </div>
            ))}
          </div>
        </Section>
      )}

      <Section title="Activity">
        <code className="wf-mono wf-muted" style={{ wordBreak: 'break-all' }}>{node.activity || 'N/A'}</code>
        {node.params && (node.params.inputs?.length > 0 || node.params.outputs?.length > 0) && (
          <div className="wf-mono wf-faint" style={{ marginTop: 6 }}>
            {node.params.inputs?.length > 0 && <div>IN: {node.params.inputs.join(', ')}</div>}
            {node.params.outputs?.length > 0 && <div>OUT: {node.params.outputs.join(', ')}</div>}
          </div>
        )}
      </Section>
    </div>
  );
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return <div className="wf-insp-section"><div className="h">{title}</div>{children}</div>;
}

function EdgeRow({ trigger, target, kind, direction = 'out', outcome, onClick, thumbnailUrl }: {
  trigger: string; target: string; kind: string; direction?: 'in' | 'out'; outcome?: string; onClick?: () => void; thumbnailUrl?: string;
}) {
  const c = edgeStyle(kind).stroke;
  return (
    <div className={`wf-row${onClick ? ' clickable' : ''}`} onClick={onClick} style={{ borderLeft: `3px solid ${c}`, flexDirection: 'column', alignItems: 'stretch', gap: 2 }} title={`${edgeStyle(kind).label}${outcome ? '\n→ ' + outcome : ''}`}>
      <div style={{ display: 'grid', gridTemplateColumns: thumbnailUrl ? '36px 100px 1fr' : '100px 1fr', gap: 8, alignItems: 'center' }}>
        {thumbnailUrl && <img className="wf-thumb" src={thumbnailUrl} alt="" loading="lazy" onError={(e) => { (e.currentTarget as HTMLImageElement).style.visibility = 'hidden'; }} />}
        <span className="wf-ellipsis" style={{ color: c, fontWeight: 700, fontSize: 11.5 }}>{trigger}</span>
        <span className="wf-ellipsis">{direction === 'in' ? '← ' : '→ '}{target}</span>
      </div>
      {outcome && <div className="wf-faint" style={{ fontSize: 11, fontStyle: 'italic', paddingLeft: thumbnailUrl ? 44 : 0 }}>∵ {outcome}</div>}
    </div>
  );
}
