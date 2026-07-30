import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import {
  Background,
  Controls,
  MarkerType,
  ReactFlow,
  ReactFlowProvider,
  applyNodeChanges,
  useReactFlow,
  type Edge,
  type Node,
  type NodeChange,
  type NodeTypes,
} from "@xyflow/react";
import "@xyflow/react/dist/style.css";

import type { CurriculumCourse, Diagnostic, PlanDetail, PlanItem } from "../../api/types";
import { buildGraph, collectAncestors, collectDescendants } from "../../lib/graph";
import { CourseNode, type CourseNodeData } from "../CourseNode/CourseNode";
import { TermColumnNode, type TermColumnData } from "./TermColumnNode";
import {
  COL_WIDTH,
  NODE_H,
  NODE_W,
  buildBoard,
  columnAtX,
  nodePosition,
  type ColumnKind,
} from "./layout";

const nodeTypes: NodeTypes = {
  course: CourseNode,
  termColumn: TermColumnNode,
};

export interface FocusToken {
  nonce: number;
  codes: string[];
}

/**
 * Where a card was dropped. The FlowBoard reports the target column and lets the
 * App decide what it means (status/completed_term/allocation) — the board never
 * owns domain semantics.
 */
export interface DropTarget {
  kind: ColumnKind;
  term: string | null;
}

export interface FlowBoardProps {
  courses: CurriculumCourse[];
  plan: PlanDetail;
  previewItems: PlanItem[] | null;
  blockedCodes: Set<string>;
  criticalPath: string[];
  showCriticalPath: boolean;
  diagnostics: Diagnostic[];
  selectedCode: string | null;
  highlight: { codes: string[]; related: string[] } | null;
  focusToken: FocusToken | null;
  onSelectCourse: (code: string | null) => void;
  onDropCourse: (code: string, target: DropTarget) => void;
  onToggleDone: (code: string, done: boolean) => void;
  onToggleLock: (code: string, lock: boolean) => void;
}

function FocusController({ focusToken }: { focusToken: FocusToken | null }) {
  const rf = useReactFlow();
  useEffect(() => {
    if (!focusToken || focusToken.codes.length === 0) return;
    const nodes = focusToken.codes.map((c) => rf.getNode(c)).filter((n): n is Node => !!n);
    if (nodes.length > 0) {
      void rf.fitView({ nodes, padding: 0.5, duration: 450, maxZoom: 1.1 });
    }
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [focusToken?.nonce]);
  return null;
}

function FlowBoardInner(props: FlowBoardProps) {
  const {
    courses, plan, previewItems, blockedCodes, criticalPath, showCriticalPath,
    diagnostics, selectedCode, highlight, focusToken,
    onSelectCourse, onDropCourse, onToggleDone, onToggleLock,
  } = props;

  const isPreview = previewItems !== null;
  const graph = useMemo(() => buildGraph(courses), [courses]);
  const board = useMemo(() => buildBoard(courses, plan, previewItems), [courses, plan, previewItems]);

  const itemByCode = useMemo(
    () => new Map((previewItems ?? plan.items).map((i) => [i.course_code, i])),
    [previewItems, plan.items],
  );
  const realItemByCode = useMemo(() => new Map(plan.items.map((i) => [i.course_code, i])), [plan.items]);

  const criticalSet = useMemo(() => new Set(showCriticalPath ? criticalPath : []), [criticalPath, showCriticalPath]);
  const chainDown = useMemo(() => (selectedCode ? collectDescendants(graph, selectedCode) : null), [graph, selectedCode]);
  const chainUp = useMemo(() => (selectedCode ? collectAncestors(graph, selectedCode) : null), [graph, selectedCode]);
  const diagCodes = useMemo(() => new Set(highlight?.codes ?? []), [highlight]);
  const diagRelated = useMemo(() => new Set(highlight?.related ?? []), [highlight]);

  const overloadedTerms = useMemo(
    () => new Set(diagnostics.filter((d) => d.type === "TERM_OVERLOADED" && d.term).map((d) => d.term as string)),
    [diagnostics],
  );

  const byCode = useMemo(() => new Map(courses.map((c) => [c.code, c])), [courses]);

  const computedNodes = useMemo<Node[]>(() => {
    const nodes: Node[] = [];
    const isTermLike = (k: string) => k === "term" || k === "past";

    for (const col of board.columns) {
      let credits = 0;
      let diff = 0;
      for (const code of col.codes) {
        const c = byCode.get(code);
        if (c) { credits += c.credits; diff += c.difficulty; }
      }
      const sublabel =
        isTermLike(col.kind) && col.codes.length > 0 ? `${credits}cr · dif ${diff}` : isTermLike(col.kind) ? "—" : null;

      const data: TermColumnData = {
        label: col.label,
        sublabel,
        kind: col.kind,
        isCurrent: col.term === plan.current_term,
        isTarget: !!plan.target_term && col.term === plan.target_term,
        hover: false,
        overloaded: !!col.term && overloadedTerms.has(col.term),
        width: COL_WIDTH,
        height: board.height,
      };
      nodes.push({
        id: `col:${col.id}`, type: "termColumn", position: { x: col.x, y: 0 },
        data, draggable: false, selectable: false, focusable: false, zIndex: 0,
      });
    }

    const anyFocus = !!selectedCode || diagCodes.size > 0;

    for (const col of board.columns) {
      col.codes.forEach((code, row) => {
        const course = byCode.get(code);
        if (!course) return;
        const effItem = itemByCode.get(code);
        const alloc = effItem ? { term: effItem.term, locked: effItem.locked } : null;

        let hl: CourseNodeData["highlight"] = null;
        if (code === selectedCode) hl = "selected";
        else if (diagCodes.has(code)) hl = "diagnostic";
        else if (diagRelated.has(code)) hl = "related";
        else if (chainDown?.has(code)) hl = "dependent";
        else if (chainUp?.has(code)) hl = "ancestor";

        const involved = hl !== null;
        const realItem = realItemByCode.get(code);
        const changedByPreview = isPreview && effItem !== undefined && (!realItem || realItem.term !== effItem.term);

        // Every card is always draggable: the user decides when a course happens.
        // Locked cards drag too (the drop unlocks — App decides); preview cards drag
        // too (the drop materializes the preview first).
        const canDrag = true;

        const data: CourseNodeData = {
          course, alloc,
          blocked: blockedCodes.has(code),
          onCritical: criticalSet.has(code),
          highlight: hl,
          dimmed: anyFocus && !involved,
          isPreview: changedByPreview,
          canDrag, onToggleDone, onToggleLock,
        };
        nodes.push({ id: code, type: "course", position: nodePosition(col, row), data, draggable: canDrag, zIndex: 1 });
      });
    }
    return nodes;
  }, [
    board, byCode, plan.current_term, plan.target_term, overloadedTerms, itemByCode,
    realItemByCode, isPreview, selectedCode, diagCodes, diagRelated, chainDown, chainUp,
    criticalSet, blockedCodes, onToggleDone, onToggleLock,
  ]);

  const [nodes, setNodes] = useState<Node[]>(computedNodes);
  useEffect(() => setNodes(computedNodes), [computedNodes]);

  const onNodesChange = useCallback(
    (changes: NodeChange[]) => setNodes((nds) => applyNodeChanges(changes, nds)),
    [],
  );

  const edges = useMemo<Edge[]>(() => {
    const critEdges = new Set<string>();
    if (showCriticalPath) {
      for (let i = 0; i < criticalPath.length - 1; i++) critEdges.add(`${criticalPath[i]}->${criticalPath[i + 1]}`);
    }
    const anyFocus = !!selectedCode || diagCodes.size > 0;
    const out: Edge[] = [];
    for (const c of courses) {
      for (const p of c.prereqs) {
        if (!byCode.has(p)) continue;
        const key = `${p}->${c.code}`;
        let cls = "edge-default";
        let animated = false;
        if (selectedCode && (p === selectedCode || chainDown?.has(p)) && chainDown?.has(c.code)) { cls = "edge-dep"; animated = true; }
        else if (selectedCode && (c.code === selectedCode || chainUp?.has(c.code)) && chainUp?.has(p)) cls = "edge-anc";
        else if (critEdges.has(key)) cls = "edge-critical";
        else if (anyFocus) cls = "edge-dim";
        out.push({
          id: `e:${key}`, source: p, target: c.code, className: cls, animated,
          markerEnd: { type: MarkerType.ArrowClosed, width: 14, height: 14 },
        });
      }
    }
    return out;
  }, [courses, byCode, selectedCode, chainDown, chainUp, criticalPath, showCriticalPath, diagCodes]);

  const dragOriginCol = useRef<string | null>(null);

  const setHover = useCallback((hoverColId: string | null) => {
    setNodes((nds) =>
      nds.map((n) => {
        if (n.type !== "termColumn") return n;
        const want = n.id === hoverColId;
        if ((n.data as TermColumnData).hover === want) return n;
        return { ...n, data: { ...n.data, hover: want } };
      }),
    );
  }, []);

  const handleNodeDragStart = useCallback(
    (_: unknown, node: Node) => { dragOriginCol.current = board.colOfCode.get(node.id) ?? null; },
    [board],
  );

  const handleNodeDrag = useCallback(
    (_: unknown, node: Node) => {
      if (node.type !== "course") return;
      const col = columnAtX(board.columns, node.position.x + NODE_W / 2);
      // Every column is a valid drop target now — the meaning of the drop is
      // resolved by the App (see handleDropCourse).
      const valid = !!col && col.id !== dragOriginCol.current;
      setHover(valid ? `col:${col!.id}` : null);
    },
    [board, setHover],
  );

  const handleNodeDragStop = useCallback(
    (_: unknown, node: Node) => {
      setHover(null);
      if (node.type !== "course") return;
      const col = columnAtX(board.columns, node.position.x + NODE_W / 2);
      const origin = dragOriginCol.current;
      dragOriginCol.current = null;
      const reset = () => setNodes(computedNodes);

      // Dropped outside any column, or back where it started -> snap back.
      if (!col || col.id === origin) { reset(); return; }

      onDropCourse(node.id, { kind: col.kind, term: col.term });

      const row = col.codes.length;
      const pos = nodePosition(col, Math.min(row, Math.max(0, row)));
      setNodes((nds) => nds.map((n) => (n.id === node.id ? { ...n, position: { x: pos.x, y: Math.max(node.position.y, 8) } } : n)));
    },
    [board, computedNodes, onDropCourse, setHover],
  );

  return (
    <div className="flowboard" style={{ minHeight: 0 }}>
      <ReactFlow
        nodes={nodes}
        edges={edges}
        nodeTypes={nodeTypes}
        onNodesChange={onNodesChange}
        onNodeClick={(_, node) => { if (node.type === "course") onSelectCourse(node.id); }}
        onPaneClick={() => onSelectCourse(null)}
        onNodeDragStart={handleNodeDragStart}
        onNodeDrag={handleNodeDrag}
        onNodeDragStop={handleNodeDragStop}
        nodesConnectable={false}
        nodesFocusable
        elementsSelectable
        fitView
        fitViewOptions={{ padding: 0.15, maxZoom: 0.9 }}
        minZoom={0.2}
        maxZoom={1.75}
        defaultEdgeOptions={{ type: "default" }}
      >
        <Background gap={24} size={1.5} />
        <Controls showInteractive={false} />
      </ReactFlow>
      <FocusController focusToken={focusToken} />
      <style>{`.course-node{height:${NODE_H - 8}px;width:${NODE_W}px}`}</style>
    </div>
  );
}

export function FlowBoard(props: FlowBoardProps) {
  return (
    <ReactFlowProvider>
      <FlowBoardInner {...props} />
    </ReactFlowProvider>
  );
}
