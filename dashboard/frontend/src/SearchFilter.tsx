import React from 'react';

interface SearchFilterProps {
  onSearch: (query: string) => void;
  onFilterCategory: (category: string) => void;
  categories: string[];
}

export function SearchFilter({ onSearch, onFilterCategory, categories }: SearchFilterProps) {
  return (
    <div style={{
      position: 'absolute',
      top: '16px',
      left: '16px',
      zIndex: 10,
      display: 'flex',
      gap: '8px',
      alignItems: 'center',
    }}>
      <input
        type="text"
        placeholder="Search..."
        onChange={(e) => onSearch(e.target.value)}
        style={{
          padding: '8px 12px',
          border: '1px solid var(--color-border)',
          borderRadius: '6px',
          fontSize: '13px',
          fontFamily: 'var(--font)',
          width: '180px',
          outline: 'none',
          background: 'var(--color-white)',
          color: 'var(--color-black)',
          transition: 'border-color 0.15s',
        }}
        onFocus={(e) => (e.target.style.borderColor = '#0a0a0a')}
        onBlur={(e) => (e.target.style.borderColor = '#e5e5e5')}
      />

      <select
        onChange={(e) => onFilterCategory(e.target.value)}
        style={{
          padding: '8px 10px',
          border: '1px solid var(--color-border)',
          borderRadius: '6px',
          fontSize: '13px',
          fontFamily: 'var(--font)',
          outline: 'none',
          background: 'var(--color-white)',
          color: 'var(--color-black)',
          cursor: 'pointer',
        }}
      >
        <option value="">All</option>
        {categories.map((cat) => (
          <option key={cat} value={cat}>{cat}</option>
        ))}
      </select>
    </div>
  );
}
