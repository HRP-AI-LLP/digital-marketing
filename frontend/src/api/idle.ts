// Stop all background polling while nobody is using the app, so the database (billed while awake) can sleep.
// Polling pauses when the tab is hidden or after IDLE_MS without mouse, keyboard, touch or scroll, and resumes
// (with one refresh of what's on screen) as soon as the person comes back.
import { focusManager, type QueryClient } from "@tanstack/react-query";

export const IDLE_MS = 3 * 60_000;

export function pausePollingWhenIdle(qc: QueryClient): void {
  let active = true;
  let timer: number | undefined;

  const set = (on: boolean) => {
    if (on === active) return;
    active = on;
    focusManager.setFocused(on);
    if (on) void qc.invalidateQueries({ type: "active", stale: true });
  };
  const arm = () => {
    window.clearTimeout(timer);
    timer = window.setTimeout(() => set(false), IDLE_MS);
  };
  const activity = () => {
    if (document.visibilityState !== "visible") return;
    set(true);
    arm();
  };

  // React Query asks this whether the page is "focused"; intervals only run while it says yes.
  focusManager.setEventListener(() => () => undefined);
  focusManager.setFocused(true);
  for (const ev of ["pointerdown", "pointermove", "keydown", "wheel", "touchstart", "scroll"]) {
    window.addEventListener(ev, activity, { passive: true, capture: true });
  }
  document.addEventListener("visibilitychange", () => {
    if (document.visibilityState === "visible") activity();
    else { window.clearTimeout(timer); set(false); }
  });
  arm();
}
