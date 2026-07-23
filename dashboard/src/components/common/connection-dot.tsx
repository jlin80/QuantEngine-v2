import { cn } from "@/lib/utils";
import type { SocketStatus } from "@/lib/ws/client";

const TONE: Record<SocketStatus, string> = {
  open: "bg-bull",
  connecting: "bg-warn animate-pulse",
  closed: "bg-bear",
};

/** A small status dot with a soft glow, reused for WS + generic states. */
export function StatusDot({
  status,
  className,
}: {
  status: SocketStatus;
  className?: string;
}) {
  return (
    <span
      className={cn("inline-block size-2 rounded-full", TONE[status], className)}
      aria-hidden
    />
  );
}
