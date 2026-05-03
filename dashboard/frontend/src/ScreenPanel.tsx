import React, { useEffect, useState } from 'react';

interface ScreenPanelProps {
  node: any;
  tourId: string;
  allNodes?: any[];
  allEdges?: any[];
  onSelectNode?: (nid: string) => void;
}

// 엣지 trigger 를 사람이 읽기 좋은 짧은 문구로
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
  if (elem.startsWith('two_hop_')) return 'Helper 경유';
  if (action === 'intent' && elem) return elem.split('.').pop() || elem;
  if (elem) return elem.length > 28 ? elem.slice(0, 28) + '…' : elem;
  return action || kind || '?';
}

export function ScreenPanel({ node, tourId, allNodes = [], allEdges = [], onSelectNode }: ScreenPanelProps) {
  const [screenshotUrl, setScreenshotUrl] = useState('');

  useEffect(() => {
    if (tourId && node.screen_id) {
      setScreenshotUrl(`/api/tours/${tourId}/screenshot/${node.screen_id}`);
    }
  }, [tourId, node.screen_id]);

  // 노드 → 라벨 lookup
  const labelFor = (sid: string): string => {
    const n = allNodes.find((x) => x.screen_id === sid);
    if (!n) return sid.slice(0, 14);
    const lbl = n.label || '';
    const act = (n.activity || '').split('.').pop();
    return (lbl || act || sid).slice(0, 36);
  };

  const incoming = allEdges.filter((e) => e.to === node.screen_id);
  const outgoing = allEdges.filter((e) => e.from === node.screen_id);
  const hasUI = !!node.screenshot_ref || (node.widgets?.length ?? 0) > 0;
  const status = node.status || '?';

  return (
    <div style={{ padding: '20px', fontSize: '13px' }}>
      {/* Title */}
      <h3 style={{
        fontSize: '16px',
        fontWeight: 600,
        letterSpacing: '-0.3px',
        marginBottom: '8px',
        lineHeight: 1.3,
      }}>
        {node.label || node.screen_id}
      </h3>

      {/* Tags */}
      <div style={{ display: 'flex', gap: '6px', marginBottom: '20px', flexWrap: 'wrap' }}>
        <Tag>{node.functional_category || 'other'}</Tag>
        <Tag variant={node.confidence === 'high' ? 'default' : node.confidence === 'low' ? 'warn' : 'default'}>
          {node.confidence || 'N/A'}
        </Tag>
        <Tag>{status}</Tag>
        {!hasUI && <Tag variant="warn">no UI</Tag>}
      </div>

      {/* Purpose (한 줄 요약) */}
      {node.screen_purpose && (
        <Section title="Purpose">
          <p style={{ color: 'var(--color-gray)', lineHeight: 1.6, margin: 0 }}>
            {node.screen_purpose}
          </p>
        </Section>
      )}

      {/* Description (LLM 풍부 설명) */}
      {node.description && (
        <Section title="Description">
          <p style={{ color: 'var(--color-black)', lineHeight: 1.6, margin: 0 }}>
            {node.description}
          </p>
        </Section>
      )}

      {/* Entry hint — 어떻게 도달하나 */}
      {node.entry_hint && (
        <Section title="Entry hint">
          <p style={{ color: 'var(--color-gray)', lineHeight: 1.5, margin: 0, fontStyle: 'italic' }}>
            {node.entry_hint}
          </p>
        </Section>
      )}

      {/* Data displayed */}
      {node.data_displayed?.length > 0 && (
        <Section title="Data displayed">
          <div style={{ display: 'flex', flexWrap: 'wrap' as const, gap: '4px' }}>
            {node.data_displayed.map((d: string, i: number) => (
              <span key={i} style={{
                padding: '2px 8px', fontSize: '11px',
                background: 'var(--color-bg-elev, #f5f5f5)',
                border: '1px solid var(--color-border)',
                borderRadius: '4px',
              }}>{d}</span>
            ))}
          </div>
        </Section>
      )}

      {/* Primary affordances — 사용자가 누를 만한 것 */}
      {node.primary_affordances?.length > 0 && (
        <Section title={`Primary actions (${node.primary_affordances.length})`}>
          <ul style={{ margin: 0, paddingLeft: '18px', lineHeight: 1.6 }}>
            {node.primary_affordances.map((a: string, i: number) => (
              <li key={i} style={{ fontSize: '12px' }}>{a}</li>
            ))}
          </ul>
        </Section>
      )}

      {/* Phase 2: Universal Primitives (input/toggle/selector/...) */}
      {node.primitives && Object.keys(node.primitives).length > 0 && (
        <Section title="Primitives (Phase 2)">
          <div style={{ display: 'flex', flexDirection: 'column', gap: '6px' }}>
            {Object.entries(node.primitives).map(([type, items]: [string, any]) => (
              <div key={type} style={{
                padding: '6px 10px',
                border: '1px solid var(--color-border)',
                borderRadius: '6px',
                fontSize: '11px',
              }}>
                <div style={{
                  fontSize: '10px', fontWeight: 700, color: 'var(--color-gray)',
                  textTransform: 'uppercase' as const, letterSpacing: '0.05em',
                  marginBottom: '3px',
                }}>{type} ({items.length})</div>
                <div style={{ display: 'flex', flexDirection: 'column' as const, gap: '3px' }}>
                  {items.slice(0, 8).map((it: any, i: number) => (
                    <div key={i} style={{ fontFamily: 'var(--font-mono)', fontSize: '10px' }}>
                      <span style={{
                        padding: '1px 6px', background: 'var(--color-bg-elev, #f5f5f5)',
                        borderRadius: '3px',
                      }} title={JSON.stringify(it, null, 2)}>
                        {it.label || it.id || it.value_hint || it.kind}
                      </span>
                      {it.outcome_hint && (
                        <span style={{
                          marginLeft: '6px', fontSize: '10px',
                          color: 'var(--color-gray)', fontStyle: 'italic',
                          fontFamily: 'var(--font)',
                        }}>
                          → {it.outcome_hint}
                        </span>
                      )}
                    </div>
                  ))}
                  {items.length > 8 && (
                    <span style={{ fontSize: '10px', color: 'var(--color-gray)' }}>
                      +{items.length - 8}
                    </span>
                  )}
                </div>
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Outgoing — 어디로 갈 수 있나 (이 노드의 핵심 기능 정보) */}
      {outgoing.length > 0 && (
        <Section title={`→ 나가는 엣지 (${outgoing.length})`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {outgoing.map((e: any, i: number) => (
              <EdgeRow
                key={i}
                trigger={friendlyTrigger(e)}
                target={labelFor(e.to)}
                kind={e.kind || 'navigate'}
                outcome={e.outcome}
                onClick={onSelectNode ? () => onSelectNode(e.to) : undefined}
              />
            ))}
          </div>
        </Section>
      )}

      {/* Incoming — 어디서 도착하나 */}
      {incoming.length > 0 && (
        <Section title={`← 들어오는 엣지 (${incoming.length})`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {incoming.map((e: any, i: number) => (
              <EdgeRow
                key={i}
                trigger={friendlyTrigger(e)}
                target={labelFor(e.from)}
                kind={e.kind || 'navigate'}
                direction="in"
                onClick={onSelectNode ? () => onSelectNode(e.from) : undefined}
              />
            ))}
          </div>
        </Section>
      )}

      {/* Activity */}
      <Section title="Activity">
        <code style={{
          fontSize: '11px',
          fontFamily: 'var(--font-mono)',
          color: 'var(--color-gray)',
          wordBreak: 'break-all' as const,
        }}>
          {node.activity || 'N/A'}
        </code>
      </Section>

      {/* Params */}
      {node.params && (node.params.inputs?.length > 0 || node.params.outputs?.length > 0) && (
        <Section title="Parameters">
          {node.params.inputs?.length > 0 && (
            <ParamRow label="IN" items={node.params.inputs} />
          )}
          {node.params.outputs?.length > 0 && (
            <ParamRow label="OUT" items={node.params.outputs} />
          )}
        </Section>
      )}

      {/* Elements */}
      {node.widgets?.length > 0 && (
        <Section title={`Elements (${node.widgets.length})`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '4px' }}>
            {node.widgets.map((elem: any, i: number) => (
              <div
                key={i}
                style={{
                  padding: '8px 10px',
                  border: '1px solid var(--color-border)',
                  borderRadius: '6px',
                  fontSize: '12px',
                }}
              >
                <div style={{ display: 'flex', justifyContent: 'space-between', alignItems: 'center' }}>
                  <span style={{
                    fontFamily: 'var(--font-mono)',
                    fontWeight: 500,
                    fontSize: '11px',
                  }}>
                    {elem.id || 'unnamed'}
                  </span>
                  <span style={{
                    fontSize: '10px',
                    color: 'var(--color-gray)',
                    fontFamily: 'var(--font-mono)',
                  }}>
                    {elem.type}
                  </span>
                </div>
                {elem.role && (
                  <div style={{ color: 'var(--color-gray)', fontSize: '11px', marginTop: '2px' }}>
                    {elem.role}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Option Groups (sprint 2026-04-27) — radio/checkbox/stepper/dropdown */}
      {node.chip_groups?.length > 0 && (
        <Section title={`Option Groups (${node.chip_groups.length})`}>
          <div style={{ display: 'flex', flexDirection: 'column', gap: '8px' }}>
            {node.chip_groups.map((g: any, i: number) => (
              <div
                key={i}
                style={{
                  padding: '10px 12px',
                  border: '1px solid var(--color-border)',
                  borderRadius: '8px',
                  background: 'var(--color-bg-elev)',
                  fontSize: '12px',
                }}
              >
                <div style={{
                  display: 'flex', justifyContent: 'space-between',
                  alignItems: 'baseline', marginBottom: '6px',
                }}>
                  <span style={{ fontWeight: 600 }}>
                    {g.group_id || `group_${i}`}
                  </span>
                  <span style={{
                    fontSize: '10px', color: 'var(--color-gray)',
                    fontFamily: 'var(--font-mono)',
                  }}>
                    {g.type}{g.required ? ' · required' : ''}
                  </span>
                </div>
                {g.type === 'stepper' ? (
                  <div style={{
                    display: 'flex', alignItems: 'center', gap: '6px',
                    fontFamily: 'var(--font-mono)', fontSize: '11px',
                  }}>
                    <span style={{ padding: '2px 8px', border: '1px solid var(--color-border)', borderRadius: '4px' }}>
                      − {g.minus_widget}
                    </span>
                    <span style={{ padding: '2px 8px', background: 'var(--color-border)', borderRadius: '4px' }}>
                      {g.display_widget || '…'}
                    </span>
                    <span style={{ padding: '2px 8px', border: '1px solid var(--color-border)', borderRadius: '4px' }}>
                      + {g.plus_widget}
                    </span>
                    <span style={{ marginLeft: 'auto', color: 'var(--color-gray)' }}>
                      [{g.min ?? 1} – {g.max ?? 99}, default {g.default ?? 1}]
                    </span>
                  </div>
                ) : g.type === 'dropdown' ? (
                  <div style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>
                    trigger → {g.trigger_widget}
                  </div>
                ) : (
                  <div style={{ display: 'flex', flexWrap: 'wrap', gap: '4px' }}>
                    {(g.options || []).map((opt: any, j: number) => (
                      <span
                        key={j}
                        style={{
                          padding: '3px 9px',
                          border: '1px solid var(--color-border)',
                          borderRadius: '12px',
                          background: opt.selected_default
                            ? 'var(--color-accent-bg, rgba(88,166,255,0.18))'
                            : 'transparent',
                          fontSize: '11px',
                        }}
                        title={opt.widget_id}
                      >
                        {opt.value}{opt.selected_default ? ' ✓' : ''}
                      </span>
                    ))}
                  </div>
                )}
                {g.detection && (
                  <div style={{
                    fontSize: '10px', color: 'var(--color-gray)',
                    marginTop: '6px', fontFamily: 'var(--font-mono)',
                  }}>
                    {g.detection.method} · conf={g.detection.confidence}
                  </div>
                )}
              </div>
            ))}
          </div>
        </Section>
      )}

      {/* Infinite-scroll feed badge (sprint 2026-04-27) */}
      {node.infinite_scroll && (
        <Section title="Infinite Scroll">
          <div style={{
            padding: '8px 12px', border: '1px solid var(--color-border)',
            borderRadius: '6px', fontSize: '12px', background: 'var(--color-bg-elev)',
          }}>
            <span style={{ fontWeight: 600 }}>♾️ Feed/list screen</span>
            <div style={{ fontSize: '11px', color: 'var(--color-gray)', marginTop: '4px' }}>
              Activity nodes:{' '}
              {node.scroll_metadata?.activity_node_count ?? '?'} ·
              category match: {String(node.scroll_metadata?.category_match ?? false)}
            </div>
          </div>
        </Section>
      )}

      {/* Screenshot */}
      {screenshotUrl && (
        <Section title="Screenshot">
          <img
            src={screenshotUrl}
            alt="Screen capture"
            style={{
              width: '100%',
              borderRadius: '8px',
              border: '1px solid var(--color-border)',
            }}
            onError={(e) => {
              (e.target as HTMLImageElement).style.display = 'none';
            }}
          />
        </Section>
      )}
    </div>
  );
}

/* --- Sub-components --- */

function Section({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <div style={{ marginBottom: '20px' }}>
      <div style={{
        fontSize: '11px',
        fontWeight: 600,
        color: 'var(--color-gray)',
        letterSpacing: '0.4px',
        textTransform: 'uppercase' as const,
        marginBottom: '8px',
      }}>
        {title}
      </div>
      {children}
    </div>
  );
}

function Tag({ children, variant = 'default' }: { children: React.ReactNode; variant?: 'default' | 'warn' }) {
  return (
    <span style={{
      display: 'inline-block',
      padding: '2px 8px',
      fontSize: '11px',
      fontWeight: 500,
      fontFamily: 'var(--font-mono)',
      border: `1px solid ${variant === 'warn' ? 'var(--color-primary)' : 'var(--color-border)'}`,
      borderRadius: '4px',
      color: variant === 'warn' ? 'var(--color-primary)' : 'var(--color-gray)',
    }}>
      {children}
    </span>
  );
}

function EdgeRow({ trigger, target, kind, direction = 'out', outcome, onClick }: {
  trigger: string;
  target: string;
  kind: string;
  direction?: 'in' | 'out';
  outcome?: string;
  onClick?: () => void;
}) {
  // kind 별 색상 — ScreenMapView 의 STYLE_BY_KIND 와 일치
  const kindColor: Record<string, string> = {
    navigate: '#2563eb', two_hop: '#6d28d9', contains: '#0ea5e9',
    launcher: '#16a34a', intent_filter: '#16a34a', pending_intent: '#65a30d',
    static_ref: '#cbd5e1', global: '#9ca3af',
    overlay: '#f59e0b', back: '#94a3b8',
  };
  const c = kindColor[kind] || '#64748b';
  return (
    <div
      onClick={onClick}
      style={{
        display: 'flex', flexDirection: 'column', gap: '2px',
        padding: '6px 9px',
        border: '1px solid var(--color-border)',
        borderRadius: '6px',
        borderLeft: `3px solid ${c}`,
        fontSize: '12px',
        cursor: onClick ? 'pointer' : 'default',
        background: 'var(--color-white)',
      }}
      onMouseEnter={(e) => onClick && (e.currentTarget.style.background = 'var(--color-bg-elev, #f8fafc)')}
      onMouseLeave={(e) => onClick && (e.currentTarget.style.background = 'var(--color-white)')}
      title={`kind: ${kind}${outcome ? '\n→ ' + outcome : ''}`}
    >
      <div style={{
        display: 'grid', gridTemplateColumns: '90px 1fr', gap: '8px',
      }}>
        <span style={{
          color: c, fontWeight: 600, fontSize: '11px',
          fontFamily: 'var(--font-mono)',
          whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' as const,
        }}>
          {trigger}
        </span>
        <span style={{
          color: 'var(--color-black)',
          whiteSpace: 'nowrap' as const, overflow: 'hidden', textOverflow: 'ellipsis' as const,
        }}>
          {direction === 'in' ? '← ' : '→ '}{target}
        </span>
      </div>
      {/* P2.2: edge.outcome — LLM 추정 결과 */}
      {outcome && (
        <div style={{
          fontSize: '10.5px', color: 'var(--color-gray)',
          paddingLeft: '98px', fontStyle: 'italic',
          whiteSpace: 'normal' as const, lineHeight: 1.4,
        }}>
          ∵ {outcome}
        </div>
      )}
    </div>
  );
}

function ParamRow({ label, items }: { label: string; items: string[] }) {
  return (
    <div style={{ display: 'flex', gap: '8px', alignItems: 'baseline', marginBottom: '4px' }}>
      <span style={{
        fontSize: '10px',
        fontWeight: 600,
        fontFamily: 'var(--font-mono)',
        color: 'var(--color-gray)',
        width: '28px',
        flexShrink: 0,
      }}>
        {label}
      </span>
      <span style={{ fontSize: '12px', fontFamily: 'var(--font-mono)' }}>
        {items.join(', ')}
      </span>
    </div>
  );
}
