import React, { useEffect, useState } from 'react';

interface ScreenPanelProps {
  node: any;
  tourId: string;
}

export function ScreenPanel({ node, tourId }: ScreenPanelProps) {
  const [screenshotUrl, setScreenshotUrl] = useState('');

  useEffect(() => {
    if (tourId && node.screen_id) {
      setScreenshotUrl(`/api/tours/${tourId}/screenshot/${node.screen_id}`);
    }
  }, [tourId, node.screen_id]);

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
      <div style={{ display: 'flex', gap: '6px', marginBottom: '20px' }}>
        <Tag>{node.functional_category || 'other'}</Tag>
        <Tag variant={node.confidence === 'high' ? 'default' : node.confidence === 'low' ? 'warn' : 'default'}>
          {node.confidence || 'N/A'}
        </Tag>
      </div>

      {/* Purpose */}
      <Section title="Purpose">
        <p style={{ color: 'var(--color-gray)', lineHeight: 1.6 }}>
          {node.screen_purpose || 'N/A'}
        </p>
      </Section>

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
