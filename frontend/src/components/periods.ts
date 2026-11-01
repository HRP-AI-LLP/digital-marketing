/** Period presets shared by PDF export, the Reports page and the email dialog. */
const iso = (d: Date) => `${d.getFullYear()}-${String(d.getMonth() + 1).padStart(2, "0")}-${String(d.getDate()).padStart(2, "0")}`;
export function presetRange(id: string): { start: string; end: string } {
  const now = new Date();
  const back = (n: number) => { const d = new Date(now); d.setDate(d.getDate() - n + 1); return iso(d); };
  if (id === "7") return { start: back(7), end: iso(now) };
  if (id === "90") return { start: back(90), end: iso(now) };
  if (id === "month") return { start: iso(new Date(now.getFullYear(), now.getMonth(), 1)), end: iso(now) };
  if (id === "last-month") {
    return { start: iso(new Date(now.getFullYear(), now.getMonth() - 1, 1)), end: iso(new Date(now.getFullYear(), now.getMonth(), 0)) };
  }
  return { start: back(30), end: iso(now) };
}
export const PRESETS = [
  { id: "7", label: "Last 7 days" }, { id: "30", label: "Last 30 days" }, { id: "90", label: "Last 90 days" },
  { id: "month", label: "This month" }, { id: "last-month", label: "Last month" },
];

