import type { PropsWithChildren } from "react";
import { cn } from "../../lib/cn";

export type StatusTone = "ready" | "loading" | "error";

export function StatusBadge({ children, tone }: PropsWithChildren<{ tone: StatusTone }>) {
  return (
    <span className={cn("status-badge", `status-badge--${tone}`)}>
      <span className="status-badge__dot" aria-hidden="true" />
      {children}
    </span>
  );
}
