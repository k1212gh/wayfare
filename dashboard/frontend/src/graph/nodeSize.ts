export type NodeSize = 'sm' | 'md' | 'lg';

export interface NodeSizeDims {
  screenshot: { w: number; sectionH: number };
  text: { w: number; h: number };
  dagre: { nodesep: number; ranksep: number };
}

export const NODE_SIZES: Record<NodeSize, NodeSizeDims> = {
  sm: {
    screenshot: { w: 120, sectionH: 200 },
    text: { w: 150, h: 50 },
    dagre: { nodesep: 28, ranksep: 70 },
  },
  md: {
    screenshot: { w: 180, sectionH: 320 },
    text: { w: 200, h: 56 },
    dagre: { nodesep: 40, ranksep: 100 },
  },
  lg: {
    screenshot: { w: 280, sectionH: 500 },
    text: { w: 280, h: 80 },
    dagre: { nodesep: 70, ranksep: 160 },
  },
};

export const NODE_SIZE_LABEL: Record<NodeSize, string> = {
  sm: '작음',
  md: '중간',
  lg: '큼',
};
