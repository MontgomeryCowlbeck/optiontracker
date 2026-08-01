/* Static at load — pointer type doesn't change mid-session in practice.
   Used to gate desktop-only affordances (Enter-to-send, autoFocus). */
export const COARSE_POINTER =
  typeof window !== "undefined" && window.matchMedia?.("(pointer: coarse)").matches === true;

import { useEffect, useState } from "react";

/* Live viewport check, for layouts that must RESTRUCTURE on phones (tables →
   stacked cards) rather than just restyle. */
export function useNarrow(maxWidth = 640): boolean {
  const q = `(max-width: ${maxWidth}px)`;
  const [narrow, setNarrow] = useState(
    () => typeof window !== "undefined" && window.matchMedia(q).matches,
  );
  useEffect(() => {
    const mq = window.matchMedia(q);
    const on = () => setNarrow(mq.matches);
    mq.addEventListener("change", on);
    return () => mq.removeEventListener("change", on);
  }, [q]);
  return narrow;
}
