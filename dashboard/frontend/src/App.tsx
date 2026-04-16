import React, { useState } from 'react';
import { Dashboard } from './Dashboard';
import { ScreenMapView } from './ScreenMapView';
import { ScreenPanel } from './ScreenPanel';
import { SearchFilter } from './SearchFilter';

type Page = 'dashboard' | 'graph';

export default function App() {
  const [page, setPage] = useState<Page>('dashboard');
  const [activeTourId, setActiveTourId] = useState('');
  const [graphData, setGraphData] = useState<any>(null);
  const [selectedNode, setSelectedNode] = useState<any>(null);
  const [filterCategory, setFilterCategory] = useState('');
  const [searchQuery, setSearchQuery] = useState('');

  const openGraph = async (tourId: string) => {
    try {
      const res = await fetch(`/api/tours/${tourId}/graph`);
      if (!res.ok) {
        console.error('Graph fetch failed:', res.status, await res.text());
        alert(`Failed to load graph (${res.status})`);
        return;
      }
      const data = await res.json();
      if (!data?.screen_map?.graph?.nodes) {
        alert('Invalid graph data');
        return;
      }
      setGraphData(data);
      setActiveTourId(tourId);
      setSelectedNode(null);
      setPage('graph');
    } catch (err) {
      console.error('Graph fetch error:', err);
      alert(`Error: ${err}`);
    }
  };

  const categories = graphData
    ? [...new Set(graphData.screen_map.graph.nodes.map((n: any) => n.functional_category || 'other'))].sort() as string[]
    : [];

  if (page === 'dashboard') {
    return <Dashboard onOpenGraph={openGraph} />;
  }

  const meta = graphData?.screen_map;

  return (
    <div style={{ display: 'flex', flexDirection: 'column', height: '100vh', fontFamily: 'var(--font)' }}>
      {/* Navbar */}
      <header style={{
        display: 'flex', alignItems: 'center', gap: '16px',
        padding: '0 20px', height: '48px',
        borderBottom: '1px solid var(--color-border)', background: 'var(--color-white)', flexShrink: 0,
      }}>
        <button onClick={() => setPage('dashboard')} style={{
          background: 'none', border: 'none', cursor: 'pointer',
          fontSize: '13px', color: 'var(--color-gray)', padding: '4px 0',
        }}>
          &larr; Back
        </button>
        <div style={{ width: '1px', height: '20px', background: 'var(--color-border)' }} />
        <span style={{ fontSize: '13px', fontWeight: 600 }}>
          {meta?.app_name || 'Graph'}
        </span>
        {meta && (
          <span style={{ fontSize: '11px', color: 'var(--color-gray)', fontFamily: 'var(--font-mono)' }}>
            {meta.metadata.total_nodes}N / {meta.metadata.total_edges}E
          </span>
        )}
      </header>

      {/* Graph + Detail */}
      <div style={{ display: 'flex', flex: 1, overflow: 'hidden' }}>
        <div style={{ flex: 1, position: 'relative', background: 'var(--color-surface)' }}>
          <SearchFilter onSearch={setSearchQuery} onFilterCategory={setFilterCategory} categories={categories} />
          {graphData && (
            <ScreenMapView
              graph={graphData.screen_map.graph}
              onNodeSelect={setSelectedNode}
              filterCategory={filterCategory}
              searchQuery={searchQuery}
              tourId={activeTourId}
            />
          )}
        </div>
        {selectedNode && (
          <aside style={{
            width: '320px', borderLeft: '1px solid var(--color-border)',
            background: 'var(--color-white)', overflow: 'auto', flexShrink: 0,
          }}>
            <div style={{
              display: 'flex', justifyContent: 'space-between', alignItems: 'center',
              padding: '12px 16px', borderBottom: '1px solid var(--color-border)',
            }}>
              <span style={{ fontSize: '11px', fontWeight: 600, textTransform: 'uppercase' as const, color: 'var(--color-gray)', letterSpacing: '0.5px' }}>Detail</span>
              <button onClick={() => setSelectedNode(null)} style={{
                background: 'none', border: 'none', cursor: 'pointer', fontSize: '16px', color: 'var(--color-gray)',
              }}>&times;</button>
            </div>
            <ScreenPanel node={selectedNode} tourId={activeTourId} />
          </aside>
        )}
      </div>
    </div>
  );
}
