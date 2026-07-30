import { memo } from "react";
import type { Node, NodeProps } from "@xyflow/react";

export type TermColumnData = {
  label: string;
  sublabel: string | null;
  kind: "tray" | "done" | "term" | "past";
  isCurrent: boolean;
  isTarget: boolean;
  hover: boolean;
  overloaded: boolean;
  width: number;
  height: number;
};

export type TermColumnFlowNode = Node<TermColumnData, "termColumn">;

function TermColumnNodeInner({ data }: NodeProps<TermColumnFlowNode>) {
  const classes = [
    "term-column",
    `col-${data.kind}`,
    data.isCurrent ? "is-current" : "",
    data.isTarget ? "is-target" : "",
    data.hover ? "is-hover" : "",
    data.overloaded ? "is-overloaded" : "",
  ]
    .filter(Boolean)
    .join(" ");

  return (
    <div className={classes} style={{ width: data.width, height: data.height }}>
      <div className="tc-header">
        <span className="tc-label">
          {data.label}
          {data.isCurrent && <span className="tc-tag">atual</span>}
          {data.isTarget && <span className="tc-tag tc-tag-target">alvo</span>}
          {data.kind === "past" && <span className="tc-tag tc-tag-past">feito</span>}
        </span>
        {data.sublabel && <span className="tc-sublabel">{data.sublabel}</span>}
      </div>
    </div>
  );
}

export const TermColumnNode = memo(TermColumnNodeInner);
