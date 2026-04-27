import React, { useCallback, useEffect, useRef, useState } from "react";
import type cytoscape from "cytoscape";
import {
  Info,
  Loader2,
  Maximize2,
  RefreshCw,
  ZoomIn,
  ZoomOut,
} from "lucide-react";
import clsx from "clsx";

import { api } from "@/api/client";
import type { ConceptMap, ConceptNode, ConceptNodeType } from "@/types";
import { DetailCard } from "./ConceptMap/DetailCard";

type ViewMode = "overview" | "focus" | "full";

const TYPE_COLOR: Record<ConceptNodeType, string> = {
  Problem: "#ef4444",
  Method: "#3b82f6",
  Component: "#6366f1",
  Baseline: "#64748b",
  Dataset: "#10b981",
  Metric: "#f59e0b",
  Finding: "#8b5cf6",
  Limitation: "#f97316",
};

const TYPE_PRIORITY: Record<ConceptNodeType, number> = {
  Problem: 80,
  Method: 70,
  Component: 55,
  Baseline: 30,
  Dataset: 40,
  Metric: 35,
  Finding: 65,
  Limitation: 45,
};

const MIN_OVERVIEW_ZOOM = 0.7;
const MIN_FOCUS_ZOOM = 0.95;
const MIN_FULL_ZOOM = 0.5;
const MAX_FOCUS_ZOOM = 1.48;
const DETAIL_PANEL_WIDTH = 320;

function makeDisplayLabel(label: string): string {
  if (label.length <= 24) return label;
  const truncated = label.slice(0, 22);
  const lastSpace = truncated.lastIndexOf(" ");
  return (lastSpace > 10 ? truncated.slice(0, lastSpace) : truncated) + "…";
}

function formatRelationChip(relation: string): string {
  return relation.replace(/_/g, " ");
}

function getPrimaryNode(conceptMap: ConceptMap): ConceptNode | null {
  if (conceptMap.nodes.length === 0) return null;
  const degree: Record<string, number> = {};
  for (const edge of conceptMap.edges) {
    degree[edge.source] = (degree[edge.source] ?? 0) + 1;
    degree[edge.target] = (degree[edge.target] ?? 0) + 1;
  }
  return conceptMap.nodes.reduce((best, node) => {
    const bestScore = (degree[best.id] ?? 0) * 10 + TYPE_PRIORITY[best.type];
    const nodeScore = (degree[node.id] ?? 0) * 10 + TYPE_PRIORITY[node.type];
    return nodeScore > bestScore ? node : best;
  });
}

type GraphIndex = {
  nodeById: Record<string, ConceptNode>;
  degree: Record<string, number>;
  neighborIds: Record<string, string[]>;
  incomingIds: Record<string, string[]>;
  outgoingIds: Record<string, string[]>;
};

function buildGraphIndex(conceptMap: ConceptMap): GraphIndex {
  const nodeById = Object.fromEntries(conceptMap.nodes.map((node) => [node.id, node]));
  const degree: Record<string, number> = {};
  const neighborSets: Record<string, Set<string>> = {};
  const incomingSets: Record<string, Set<string>> = {};
  const outgoingSets: Record<string, Set<string>> = {};

  for (const node of conceptMap.nodes) {
    neighborSets[node.id] = new Set();
    incomingSets[node.id] = new Set();
    outgoingSets[node.id] = new Set();
  }

  for (const edge of conceptMap.edges) {
    degree[edge.source] = (degree[edge.source] ?? 0) + 1;
    degree[edge.target] = (degree[edge.target] ?? 0) + 1;
    neighborSets[edge.source]?.add(edge.target);
    neighborSets[edge.target]?.add(edge.source);
    outgoingSets[edge.source]?.add(edge.target);
    incomingSets[edge.target]?.add(edge.source);
  }

  return {
    nodeById,
    degree,
    neighborIds: Object.fromEntries(Object.entries(neighborSets).map(([key, value]) => [key, [...value]])),
    incomingIds: Object.fromEntries(Object.entries(incomingSets).map(([key, value]) => [key, [...value]])),
    outgoingIds: Object.fromEntries(Object.entries(outgoingSets).map(([key, value]) => [key, [...value]])),
  };
}

function rankNode(index: GraphIndex, nodeId: string): number {
  const node = index.nodeById[nodeId];
  if (!node) return -1;
  return (index.degree[nodeId] ?? 0) * 10 + TYPE_PRIORITY[node.type];
}

type FocusContext = {
  anchorId: string;
  reason: "sibling" | "second-hop" | "global-anchor";
};

type FocusNeighborhood = {
  visibleIds: string[];
  directIds: string[];
  contextIds: string[];
  contextById: Record<string, FocusContext>;
};

function buildFocusNeighborhood(conceptMap: ConceptMap, selectedId: string): FocusNeighborhood {
  const index = buildGraphIndex(conceptMap);
  const visibleIds = new Set<string>([selectedId]);
  const contextById: Record<string, FocusContext> = {};

  const directIds = [...(index.neighborIds[selectedId] ?? [])].sort(
    (a, b) => rankNode(index, b) - rankNode(index, a)
  );
  for (const nodeId of directIds) visibleIds.add(nodeId);

  const targetMinimum = directIds.length <= 2 ? 5 : Math.min(7, directIds.length + 2);
  const addContextNode = (nodeId: string, anchorId: string, reason: FocusContext["reason"]) => {
    if (nodeId === selectedId || visibleIds.has(nodeId)) return;
    visibleIds.add(nodeId);
    contextById[nodeId] = { anchorId, reason };
  };

  const incomingIds = [...(index.incomingIds[selectedId] ?? [])].sort(
    (a, b) => rankNode(index, b) - rankNode(index, a)
  );
  for (const parentId of incomingIds) {
    const siblings = [...(index.neighborIds[parentId] ?? [])]
      .filter((candidate) => candidate !== selectedId && !visibleIds.has(candidate))
      .sort((a, b) => rankNode(index, b) - rankNode(index, a));
    for (const siblingId of siblings) {
      addContextNode(siblingId, parentId, "sibling");
      if (visibleIds.size >= targetMinimum) break;
    }
    if (visibleIds.size >= targetMinimum) break;
  }

  if (visibleIds.size < targetMinimum) {
    for (const directId of directIds) {
      const anchors = [...(index.neighborIds[directId] ?? [])]
        .filter((candidate) => candidate !== selectedId && !visibleIds.has(candidate))
        .sort((a, b) => rankNode(index, b) - rankNode(index, a));
      for (const anchorId of anchors) {
        addContextNode(anchorId, directId, "second-hop");
        if (visibleIds.size >= targetMinimum) break;
      }
      if (visibleIds.size >= targetMinimum) break;
    }
  }

  if (visibleIds.size < targetMinimum) {
    const primaryNode = getPrimaryNode(conceptMap);
    if (primaryNode && primaryNode.id !== selectedId) {
      addContextNode(primaryNode.id, selectedId, "global-anchor");
      const primaryNeighbors = [...(index.neighborIds[primaryNode.id] ?? [])]
        .filter((candidate) => candidate !== selectedId && !visibleIds.has(candidate))
        .sort((a, b) => rankNode(index, b) - rankNode(index, a));
      if (primaryNeighbors[0]) {
        addContextNode(primaryNeighbors[0], primaryNode.id, "global-anchor");
      }
    }
  }

  const contextIds = Object.keys(contextById);
  return {
    visibleIds: [...visibleIds],
    directIds,
    contextIds,
    contextById,
  };
}

function fitViewportToElements(
  cy: cytoscape.Core,
  elements: cytoscape.Collection,
  options: { padding: number; minZoom: number; maxZoom?: number }
) {
  const container = cy.container();
  if (!container || !elements?.length) return;

  const { padding, minZoom, maxZoom } = options;
  const bbox = elements.boundingBox({
    includeLabels: true,
    includeOverlays: false,
  });

  const width = Math.max(bbox.w, 1);
  const height = Math.max(bbox.h, 1);
  const viewportWidth = Math.max(container.clientWidth - padding * 2, 1);
  const viewportHeight = Math.max(container.clientHeight - padding * 2, 1);

  let zoom = Math.min(viewportWidth / width, viewportHeight / height);
  zoom = Math.max(zoom, minZoom);
  if (typeof maxZoom === "number") zoom = Math.min(zoom, maxZoom);

  const centerX = (bbox.x1 + bbox.x2) / 2;
  const centerY = (bbox.y1 + bbox.y2) / 2;
  const pan = {
    x: container.clientWidth / 2 - centerX * zoom,
    y: container.clientHeight / 2 - centerY * zoom,
  };

  cy.stop();
  cy.animate(
    { zoom, pan },
    { duration: 240, easing: "ease-out-cubic" }
  );
}

function runOverviewLayout(cy: cytoscape.Core, conceptMap: ConceptMap) {
  const roots = conceptMap.nodes
    .filter((node) => node.type === "Problem")
    .map((node) => node.id);

  cy.layout({
    name: "breadthfirst",
    directed: true,
    roots: roots.length > 0 ? roots : [conceptMap.nodes[0]?.id],
    spacingFactor: 1.4,
    padding: 18,
    nodeDimensionsIncludeLabels: true,
    animate: false,
    fit: false,
  }).run();
}

function buildFocusPositions(
  conceptMap: ConceptMap,
  selectedId: string,
  neighborhood: FocusNeighborhood
) {
  const index = buildGraphIndex(conceptMap);
  const positions: Record<string, { x: number; y: number }> = {
    [selectedId]: { x: -40, y: 0 },
  };

  const incomingOnly: string[] = [];
  const outgoingOnly: string[] = [];
  const peerNodes: string[] = [];

  for (const directId of neighborhood.directIds) {
    const isIncoming = (index.outgoingIds[directId] ?? []).includes(selectedId);
    const isOutgoing = (index.outgoingIds[selectedId] ?? []).includes(directId);

    if (isIncoming && !isOutgoing) {
      incomingOnly.push(directId);
    } else if (isOutgoing && !isIncoming) {
      outgoingOnly.push(directId);
    } else {
      peerNodes.push(directId);
    }
  }

  const placeColumn = (nodeIds: string[], x: number, step: number) => {
    const startY = -((nodeIds.length - 1) * step) / 2;
    nodeIds.forEach((nodeId, indexInColumn) => {
      positions[nodeId] = {
        x,
        y: startY + indexInColumn * step,
      };
    });
  };

  placeColumn(incomingOnly, -320, 122);
  placeColumn(outgoingOnly, 260, 122);

  const peerStep = 160;
  const peerStartX = -20 - ((peerNodes.length - 1) * peerStep) / 2;
  peerNodes.forEach((nodeId, peerIndex) => {
    positions[nodeId] = {
      x: peerStartX + peerIndex * peerStep,
      y: peerIndex % 2 === 0 ? 170 : -170,
    };
  });

  const anchorGroups: Record<string, string[]> = {};
  for (const contextId of neighborhood.contextIds) {
    const anchorId = neighborhood.contextById[contextId]?.anchorId ?? selectedId;
    if (!anchorGroups[anchorId]) anchorGroups[anchorId] = [];
    anchorGroups[anchorId].push(contextId);
  }

  Object.entries(anchorGroups).forEach(([anchorId, nodeIds]) => {
    const anchorPosition = positions[anchorId] ?? positions[selectedId];
    const anchorX = anchorPosition.x;
    const direction = anchorX <= positions[selectedId].x ? -1 : 1;
    const startY = anchorPosition.y - ((nodeIds.length - 1) * 92) / 2;

    nodeIds.forEach((nodeId, contextIndex) => {
      positions[nodeId] = {
        x: anchorPosition.x + direction * 190,
        y: startY + contextIndex * 92 + (anchorId === selectedId ? 110 : 0),
      };
    });
  });

  return positions;
}

function applyView(
  cy: cytoscape.Core,
  options: {
    viewMode: ViewMode;
    selectedId: string | null;
    conceptMap: ConceptMap | null;
  }
) {
  const { viewMode, selectedId, conceptMap } = options;
  if (!cy || !conceptMap) return;

  cy.batch(() => {
    cy.elements().removeClass(
      "hidden muted selected-node related-node focus-target focus-direct-node focus-context-node focus-direct-edge focus-context-edge"
    );
    cy.nodes().unselect();
  });

  const selected = selectedId ? cy.getElementById(selectedId) : null;

  if (viewMode === "full") {
    runOverviewLayout(cy, conceptMap);
    if (selected?.length) {
      selected.select().addClass("selected-node");
      selected.connectedEdges().addClass("related-node");
      selected.neighborhood("node").addClass("related-node");
    }
    fitViewportToElements(cy, cy.elements(), {
      padding: 48,
      minZoom: MIN_FULL_ZOOM,
      maxZoom: 0.9,
    });
    return;
  }

  if (viewMode === "overview") {
    runOverviewLayout(cy, conceptMap);
    if (selected?.length) {
      selected.select().addClass("selected-node");
      selected.neighborhood("node").addClass("related-node");
      selected.connectedEdges().addClass("related-node");
    }
    fitViewportToElements(cy, cy.elements(), {
      padding: 56,
      minZoom: MIN_OVERVIEW_ZOOM,
      maxZoom: 1.02,
    });
    return;
  }

  if (!selectedId || !selected?.length) {
    runOverviewLayout(cy, conceptMap);
    fitViewportToElements(cy, cy.elements(), {
      padding: 56,
      minZoom: MIN_OVERVIEW_ZOOM,
      maxZoom: 1.02,
    });
    return;
  }

  const neighborhood = buildFocusNeighborhood(conceptMap, selectedId);
  const visibleIdSet = new Set(neighborhood.visibleIds);

  cy.batch(() => {
    cy.nodes().forEach((node: cytoscape.NodeSingular) => {
      if (!visibleIdSet.has(node.id())) {
        node.addClass("hidden");
      }
    });

    cy.edges().forEach((edge: cytoscape.EdgeSingular) => {
      const sourceId = edge.source().id();
      const targetId = edge.target().id();
      if (!visibleIdSet.has(sourceId) || !visibleIdSet.has(targetId)) {
        edge.addClass("hidden");
      }
    });
  });

  const positions = buildFocusPositions(conceptMap, selectedId, neighborhood);
  cy.batch(() => {
    Object.entries(positions).forEach(([nodeId, position]) => {
      const node = cy.getElementById(nodeId);
      if (node?.length) node.position(position);
    });
  });

  selected.select().addClass("focus-target");

  neighborhood.directIds.forEach((nodeId) => {
    const node = cy.getElementById(nodeId);
    if (node?.length) node.addClass("focus-direct-node");
  });

  neighborhood.contextIds.forEach((nodeId) => {
    const node = cy.getElementById(nodeId);
    if (node?.length) node.addClass("focus-context-node");
  });

  cy.edges().forEach((edge: cytoscape.EdgeSingular) => {
    if (edge.hasClass("hidden")) return;
    const isDirectEdge = edge.source().id() === selectedId || edge.target().id() === selectedId;
    edge.addClass(isDirectEdge ? "focus-direct-edge" : "focus-context-edge");
  });

  const visibleElements = cy.elements().filter((element) => !element.hasClass("hidden"));
  fitViewportToElements(cy, visibleElements, {
    padding: 72,
    minZoom: MIN_FOCUS_ZOOM,
    maxZoom: MAX_FOCUS_ZOOM,
  });
}

interface Props {
  paperId: string;
  paperTitle: string;
  onExplainConcept: (label: string) => void;
  onShowInPaper: (page: number) => void;
}

export function ConceptMap({
  paperId,
  paperTitle,
  onExplainConcept,
  onShowInPaper,
}: Props) {
  const [conceptMap, setConceptMap] = useState<ConceptMap | null>(null);
  const [loading, setLoading] = useState(true);
  const [error, setError] = useState<string | null>(null);
  const [regenerating, setRegenerating] = useState(false);
  const [autoPolling, setAutoPolling] = useState(false);
  const [selectedNode, setSelectedNode] = useState<ConceptNode | null>(null);
  const [viewMode, setViewMode] = useState<ViewMode>("overview");
  const [legendOpen, setLegendOpen] = useState(false);

  const containerRef = useRef<HTMLDivElement>(null);
  const legendRef = useRef<HTMLDivElement>(null);
  const cyRef = useRef<cytoscape.Core | null>(null);

  useEffect(() => {
    setLoading(true);
    setError(null);
    setLegendOpen(false);
    setViewMode("overview");
    setConceptMap(null);
    setSelectedNode(null);
    setAutoPolling(false);

    api.getConceptMap(paperId)
      .then((data) => {
        setConceptMap(data);
        setSelectedNode(getPrimaryNode(data));
        // Concept map is generated on-demand; show Generate button instead of polling.
      })
      .catch((nextError) => setError(String(nextError)))
      .finally(() => setLoading(false));
  }, [paperId]);

  useEffect(() => {
    if (!autoPolling) return;
    let stopped = false;
    const startedAt = Date.now();

    const poll = async () => {
      if (stopped) return;
      if (Date.now() - startedAt > 120_000) {
        setAutoPolling(false);
        return;
      }
      try {
        const data = await api.getConceptMap(paperId);
        if (stopped) return;
        setConceptMap(data);
        if (data.generated) {
          setSelectedNode(getPrimaryNode(data));
          setViewMode("overview");
          setAutoPolling(false);
          return;
        }
      } catch {
        // Keep polling; transient errors shouldn't force manual regenerate.
      }
      setTimeout(poll, 4000);
    };

    const t = setTimeout(poll, 2500);
    return () => {
      stopped = true;
      clearTimeout(t);
    };
  }, [autoPolling, paperId]);

  useEffect(() => {
    if (!legendOpen) return;

    const handlePointerDown = (event: MouseEvent) => {
      if (!legendRef.current?.contains(event.target as Node)) {
        setLegendOpen(false);
      }
    };

    document.addEventListener("mousedown", handlePointerDown);
    return () => document.removeEventListener("mousedown", handlePointerDown);
  }, [legendOpen]);

  useEffect(() => {
    if (!conceptMap || conceptMap.nodes.length === 0 || !containerRef.current) return;

    let mounted = true;

    (async () => {
      const cytoscapeModule = await import("cytoscape");
      if (!mounted || !containerRef.current) return;

      const cytoscape = cytoscapeModule.default;
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }

      const elements = [
        ...conceptMap.nodes.map((node) => ({
          data: {
            id: node.id,
            label: makeDisplayLabel(node.label),
            fullLabel: node.label,
            nodeType: node.type,
            color: TYPE_COLOR[node.type] ?? "#64748b",
          },
        })),
        ...conceptMap.edges.map((edge, index) => ({
          data: {
            id: `edge_${index}`,
            source: edge.source,
            target: edge.target,
            relation: edge.relation,
            relationLabel: formatRelationChip(edge.relation),
          },
        })),
      ];

      const cy = cytoscape({
        container: containerRef.current,
        elements,
        style: [
          {
            selector: "node",
            style: {
              label: "data(label)",
              "font-size": 10,
              "font-weight": 550,
              "font-family": "ui-sans-serif, system-ui, sans-serif",
              color: "#f8fafc",
              "text-valign": "center",
              "text-halign": "center",
              "text-wrap": "wrap",
              "text-max-width": "98px",
              width: 126,
              height: 50,
              shape: "round-rectangle",
              "background-color": "data(color)",
              "background-opacity": 0.72,
              "border-width": 1.5,
              "border-color": "data(color)",
              "border-opacity": 0.42,
              padding: "8px",
            },
          },
          {
            selector: "node.hidden",
            style: {
              display: "none",
            },
          },
          {
            selector: "node.selected-node",
            style: {
              "border-width": 2.5,
              "border-color": "#f8fafc",
              "border-opacity": 1,
              "background-opacity": 0.96,
            },
          },
          {
            selector: "node.related-node",
            style: {
              "background-opacity": 0.84,
              opacity: 1,
            },
          },
          {
            selector: "node.focus-target",
            style: {
              width: 148,
              height: 58,
              "font-size": 12,
              "text-max-width": "112px",
              "border-width": 3,
              "border-color": "#ffffff",
              "background-opacity": 1,
              "border-opacity": 1,
            },
          },
          {
            selector: "node.focus-direct-node",
            style: {
              "background-opacity": 0.88,
              opacity: 1,
            },
          },
          {
            selector: "node.focus-context-node",
            style: {
              "background-opacity": 0.42,
              "border-opacity": 0.3,
              opacity: 0.9,
            },
          },
          {
            selector: "edge",
            style: {
              width: 1.4,
              "line-color": "#4b6584",
              "target-arrow-color": "#4b6584",
              "target-arrow-shape": "triangle",
              "curve-style": "bezier",
              "arrow-scale": 0.85,
              opacity: 0.36,
              label: "",
            },
          },
          {
            selector: "edge.hidden",
            style: {
              display: "none",
            },
          },
          {
            selector: "edge.related-node",
            style: {
              opacity: 0.46,
            },
          },
          {
            selector: "edge.focus-direct-edge",
            style: {
              width: 2.2,
              "line-color": "#60a5fa",
              "target-arrow-color": "#60a5fa",
              opacity: 0.95,
              label: "data(relationLabel)",
              "font-size": 9,
              "font-weight": 600,
              color: "#1e40af",
              "text-rotation": "none",
              "text-margin-y": -10,
              "text-background-color": "#eff6ff",
              "text-background-opacity": 0.92,
              "text-background-shape": "roundrectangle",
              "text-background-padding": "4px",
              "text-border-width": 1,
              "text-border-color": "#93c5fd",
              "text-border-opacity": 0.6,
            },
          },
          {
            selector: "edge.focus-context-edge",
            style: {
              width: 1.2,
              opacity: 0.18,
              "line-color": "#64748b",
              "target-arrow-color": "#64748b",
            },
          },
        ],
        layout: { name: "preset" },
        userZoomingEnabled: true,
        userPanningEnabled: true,
        boxSelectionEnabled: false,
        wheelSensitivity: 0.6,
      });

      cyRef.current = cy;
      applyView(cy, {
        viewMode,
        selectedId: selectedNode?.id ?? null,
        conceptMap,
      });

      cy.on("tap", "node", (event: cytoscape.EventObject) => {
        const nodeId = event.target.id();
        const nextNode = conceptMap.nodes.find((node) => node.id === nodeId);
        if (!nextNode) return;
        setSelectedNode(nextNode);
        setViewMode("focus");
      });
    })();

    return () => {
      mounted = false;
      if (cyRef.current) {
        cyRef.current.destroy();
        cyRef.current = null;
      }
    };
  }, [conceptMap]);

  useEffect(() => {
    const cy = cyRef.current;
    if (!cy || !conceptMap) return;

    applyView(cy, {
      viewMode,
      selectedId: selectedNode?.id ?? null,
      conceptMap,
    });
  }, [conceptMap, selectedNode, viewMode]);

  useEffect(() => {
    if (!containerRef.current || !cyRef.current || !conceptMap) return;

    const observer = new ResizeObserver(() => {
      const cy = cyRef.current;
      if (!cy) return;
      applyView(cy, {
        viewMode,
        selectedId: selectedNode?.id ?? null,
        conceptMap,
      });
    });

    observer.observe(containerRef.current);
    return () => observer.disconnect();
  }, [conceptMap, selectedNode, viewMode]);

  const handleRegenerate = useCallback(async () => {
    setRegenerating(true);
    setError(null);

    try {
      await api.regenerateConceptMap(paperId);
      const deadline = Date.now() + 120_000;

      const poll = async (): Promise<void> => {
        if (Date.now() > deadline) {
          setError("Generation is taking longer than expected. Try again in a moment.");
          setRegenerating(false);
          return;
        }

        const data = await api.getConceptMap(paperId);
        if (data.generated) {
          setConceptMap(data);
          setSelectedNode(getPrimaryNode(data));
          setViewMode("overview");
          setRegenerating(false);
          return;
        }

        setTimeout(poll, 4000);
      };

      setTimeout(poll, 6000);
    } catch (nextError) {
      setError(String(nextError));
      setRegenerating(false);
    }
  }, [paperId]);

  const handleResetView = useCallback(() => {
    if (!conceptMap) return;
    setSelectedNode(getPrimaryNode(conceptMap));
    setViewMode("overview");
  }, [conceptMap]);

  const handleSelectNode = useCallback((nodeId: string) => {
    if (!conceptMap) return;
    const nextNode = conceptMap.nodes.find((node) => node.id === nodeId);
    if (!nextNode) return;
    setSelectedNode(nextNode);
    setViewMode("focus");
  }, [conceptMap]);

  if (loading) {
    return (
      <div className="flex h-full items-center justify-center gap-2 text-sm text-surface-500">
        <Loader2 className="h-4 w-4 animate-spin" />
        Loading concept map…
      </div>
    );
  }

  if (error) {
    return (
      <div className="flex h-full items-center justify-center">
        <p className="text-sm text-red-400">{error}</p>
      </div>
    );
  }

  if (!conceptMap?.generated || conceptMap.nodes.length === 0) {
    return (
      <div className="flex h-full flex-col items-center justify-center gap-3 px-8 text-center">
        <p className="text-sm text-surface-600">
          {autoPolling ? "Building concept map…" : "No concept map for this paper yet."}
        </p>
        <p className="max-w-xs text-xs text-surface-400">
          {autoPolling
            ? "You can start chatting now; this will appear automatically when ready."
            : "If this is an older paper or generation failed, you can regenerate."}
        </p>
        {autoPolling && (
          <div className="flex items-center gap-2 text-xs text-surface-500">
            <Loader2 className="h-3.5 w-3.5 animate-spin" />
            Waiting for background job…
          </div>
        )}
        {!autoPolling && (
          <button
            onClick={handleRegenerate}
            disabled={regenerating}
            className="flex items-center gap-2 rounded-lg border border-accent-200 bg-accent-50 px-4 py-2 text-xs font-medium text-accent-700 transition-colors hover:bg-accent-100 disabled:opacity-50"
          >
            {regenerating ? (
              <>
                <Loader2 className="h-3.5 w-3.5 animate-spin" />
                Generating…
              </>
            ) : (
              <>
                <RefreshCw className="h-3.5 w-3.5" />
                Generate concept map
              </>
            )}
          </button>
        )}
      </div>
    );
  }

  const legendTypes = (Object.keys(TYPE_COLOR) as ConceptNodeType[]).filter((type) =>
    conceptMap.nodes.some((node) => node.type === type)
  );

  return (
    <div className="flex h-full flex-col overflow-hidden">
      <div className="flex shrink-0 items-center gap-3 border-b border-surface-200 px-4 py-2">
        <div className="flex min-w-0 flex-[1.1] items-center gap-2">
          <span className="rounded-full border border-accent-200 bg-accent-50 px-2 py-0.5 text-[10px] font-medium uppercase tracking-[0.18em] text-accent-700">
            Concepts
          </span>
          <h2 className="truncate text-xs font-semibold text-surface-700 sm:text-sm">{paperTitle}</h2>
        </div>

        <div className="ml-auto flex items-center gap-2">
          <button
            onClick={() => {
              const cy = cyRef.current;
              if (!cy) return;
              const center = { x: cy.width() / 2, y: cy.height() / 2 };
              cy.zoom({ level: cy.zoom() * 1.3, renderedPosition: center });
            }}
            className="rounded-lg border border-surface-200 bg-surface-50 p-1.5 text-surface-600 transition-colors hover:bg-surface-100"
            title="Zoom in"
          >
            <ZoomIn className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => {
              const cy = cyRef.current;
              if (!cy) return;
              const center = { x: cy.width() / 2, y: cy.height() / 2 };
              cy.zoom({ level: cy.zoom() / 1.3, renderedPosition: center });
            }}
            className="rounded-lg border border-surface-200 bg-surface-50 p-1.5 text-surface-600 transition-colors hover:bg-surface-100"
            title="Zoom out"
          >
            <ZoomOut className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={() => {
              const cy = cyRef.current;
              if (!cy) return;
              cy.fit(undefined, 48);
            }}
            className="rounded-lg border border-surface-200 bg-surface-50 p-1.5 text-surface-600 transition-colors hover:bg-surface-100"
            title="Fit to view"
          >
            <Maximize2 className="h-3.5 w-3.5" />
          </button>
          <button
            onClick={handleResetView}
            className="rounded-lg border border-surface-200 bg-surface-50 px-3 py-1.5 text-xs font-medium text-surface-600 transition-colors hover:bg-surface-100"
          >
            Reset view
          </button>
          <button
            onClick={() => setViewMode("full")}
            className={clsx(
              "rounded-lg border px-3 py-1.5 text-xs font-medium transition-colors",
              viewMode === "full"
                ? "border-accent-200 bg-accent-50 text-accent-700"
                : "border-surface-200 bg-surface-50 text-surface-600 hover:bg-surface-100"
            )}
          >
            Full graph
          </button>

          <div ref={legendRef} className="relative">
            <button
              onClick={() => setLegendOpen((open) => !open)}
              className="flex items-center gap-1.5 rounded-lg border border-surface-200 bg-surface-50 px-3 py-1.5 text-xs font-medium text-surface-600 transition-colors hover:bg-surface-100"
            >
              <Info className="h-3.5 w-3.5" />
              Legend
            </button>
            {legendOpen && (
              <div className="absolute right-0 top-10 z-30 w-52 rounded-xl border border-surface-200 bg-white p-3 shadow-lg">
                <div className="space-y-2">
                  {legendTypes.map((type) => (
                    <div key={type} className="flex items-center gap-2 text-xs text-surface-600">
                      <span
                        className="inline-block h-2.5 w-2.5 rounded-sm"
                        style={{ backgroundColor: TYPE_COLOR[type] }}
                      />
                      {type}
                    </div>
                  ))}
                </div>
              </div>
            )}
          </div>
        </div>
      </div>

      <div className="flex min-h-0 flex-1 overflow-hidden">
        <div className="relative min-w-0 flex-1">
          <div ref={containerRef} className="h-full w-full" />

          <div className="pointer-events-none absolute bottom-3 left-3 rounded-full border border-surface-200 bg-white/90 px-3 py-1 text-[10px] text-surface-500 backdrop-blur">
            {viewMode === "overview" && "Overview"}
            {viewMode === "focus" && "Focus"}
            {viewMode === "full" && "Full graph"} · {conceptMap.nodes.length} concepts · {conceptMap.edges.length} relations
          </div>

          {viewMode === "full" && (
            <div className="pointer-events-none absolute bottom-3 right-3 rounded-full border border-surface-200 bg-white/90 px-3 py-1 text-[10px] text-surface-400 backdrop-blur">
              Click any node to jump back into a readable local focus
            </div>
          )}
        </div>

        <aside
          className="flex h-full flex-none flex-col border-l border-surface-200 bg-white"
          style={{ width: DETAIL_PANEL_WIDTH }}
        >
          {selectedNode ? (
            <DetailCard
              node={selectedNode}
              edges={conceptMap.edges}
              allNodes={conceptMap.nodes}
              onExplain={onExplainConcept}
              onShowInPaper={onShowInPaper}
              onSelectNode={handleSelectNode}
            />
          ) : (
            <div className="flex h-full items-center justify-center px-6 text-center text-sm text-surface-400">
              Select a concept to inspect its role in the paper.
            </div>
          )}
        </aside>
      </div>
    </div>
  );
}
