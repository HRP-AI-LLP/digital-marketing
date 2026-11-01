// AI replies to comments: settings (off / drafts to approve / fully automatic), emoji style, which platforms,
// which kinds of comments, delay and daily limit. Negative or urgent comments are always left for a person.
import { useEffect, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Bot, ShieldCheck } from "lucide-react";
import { api } from "../../api/client";
import { Alert, Button, Modal, Textarea } from "../../components/ui";

export type AutoReplyCfg = {
  enabled: boolean; mode: "draft" | "auto"; platforms: string[]; emoji: "none" | "light" | "rich" | "brand";
  sentiments: ("positive" | "neutral" | "question")[]; delay_minutes: number; max_per_day: number; instructions: string;
  started_at: string | null; available_platforms: { id: string; label: string }[];
};

export function useAutoReply() {
  return useQuery({ queryKey: ["inbox-autoreply"], queryFn: () => api<AutoReplyCfg>("/inbox/autoreply"), staleTime: 60_000 });
}

const EMOJI: { id: AutoReplyCfg["emoji"]; label: string; sample: string }[] = [
  { id: "rich", label: "Expressive", sample: "Thank you so much, Priya! 🙏 Glad it helped 🚀" },
  { id: "light", label: "Subtle", sample: "Thanks, Priya, glad it helped 🙂" },
  { id: "none", label: "None", sample: "Thanks, Priya, glad it helped." },
  { id: "brand", label: "Brand setting", sample: "Follows the emoji rule in Brand settings" },
];
const KINDS: { id: AutoReplyCfg["sentiments"][number]; label: string }[] = [
  { id: "positive", label: "Praise and thanks" }, { id: "neutral", label: "General comments" }, { id: "question", label: "Questions" },
];

export default function AutoReplySettings({ onClose }: { onClose: () => void }) {
  const qc = useQueryClient();
  const q = useAutoReply();
  const [f, setF] = useState<AutoReplyCfg | null>(null);
  useEffect(() => { if (q.data && !f) setF(q.data); }, [q.data, f]);
  const save = useMutation({
    mutationFn: (body: Partial<AutoReplyCfg>) => api<AutoReplyCfg>("/inbox/autoreply", { method: "PUT", json: body }),
    onSuccess: (d) => { qc.setQueryData(["inbox-autoreply"], d); onClose(); },
  });
  if (!f) return <Modal title="AI replies" onClose={onClose} wide><p className="text-sm text-muted">Loading…</p></Modal>;
  const set = (p: Partial<AutoReplyCfg>) => setF({ ...f, ...p });
  const mode = !f.enabled ? "off" : f.mode;
  const card = (on: boolean) => `flex-1 rounded-xl border px-3 py-2.5 text-left text-sm transition-colors ${on ? "border-primary-500 bg-primary-50 ring-2 ring-primary-500/15" : "border-line hover:border-primary-300"}`;
  const chip = (on: boolean) => `rounded-full border px-2.5 py-1 text-xs ${on ? "border-primary-500 bg-primary-50 font-semibold text-primary-800" : "border-line text-muted hover:text-ink"}`;
  const allPlatforms = f.platforms.length === 0;

  return (
    <Modal title="AI replies to comments" onClose={onClose} wide>
      <div className="space-y-5 text-sm">
        {save.error && <Alert>{(save.error as Error).message}</Alert>}
        <div>
          <div className="mb-2 font-medium text-ink">How should the AI help?</div>
          <div className="flex flex-col gap-2 sm:flex-row">
            <button type="button" className={card(mode === "off")} onClick={() => set({ enabled: false })}>
              <b className="block">Off</b><span className="text-xs text-muted">Suggestions only when you ask in a conversation.</span>
            </button>
            <button type="button" className={card(mode === "draft")} onClick={() => set({ enabled: true, mode: "draft" })}>
              <b className="block">Draft, I approve</b><span className="text-xs text-muted">Writes a reply for each new comment; you check it and press Send.</span>
            </button>
            <button type="button" className={card(mode === "auto")} onClick={() => set({ enabled: true, mode: "auto" })}>
              <b className="block">Reply automatically</b><span className="text-xs text-muted">Sends the reply itself, within the limits below.</span>
            </button>
          </div>
        </div>

        <div>
          <div className="mb-2 font-medium text-ink">Emojis</div>
          <div className="grid gap-2 sm:grid-cols-2">
            {EMOJI.map((e) => (
              <button key={e.id} type="button" onClick={() => set({ emoji: e.id })} className={card(f.emoji === e.id)}>
                <b className="block">{e.label}</b><span className="text-xs text-muted">{e.sample}</span>
              </button>
            ))}
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <div>
            <div className="mb-2 font-medium text-ink">Reply to</div>
            <div className="flex flex-wrap gap-1.5">
              {KINDS.map((k) => {
                const on = f.sentiments.includes(k.id);
                return <button key={k.id} type="button" className={chip(on)} onClick={() => set({ sentiments: on ? f.sentiments.filter((x) => x !== k.id) : [...f.sentiments, k.id] })}>{k.label}</button>;
              })}
            </div>
            <p className="mt-1.5 text-xs text-muted">Complaints and urgent comments are never answered by the AI; they wait for you.</p>
          </div>
          <div>
            <div className="mb-2 font-medium text-ink">On</div>
            <div className="flex flex-wrap gap-1.5">
              <button type="button" className={chip(allPlatforms)} onClick={() => set({ platforms: [] })}>Every channel</button>
              {f.available_platforms.map((p) => {
                const on = !allPlatforms && f.platforms.includes(p.id);
                return <button key={p.id} type="button" className={chip(on)}
                  onClick={() => set({ platforms: on ? f.platforms.filter((x) => x !== p.id) : [...f.platforms, p.id] })}>{p.label}</button>;
              })}
            </div>
          </div>
        </div>

        <div className="grid gap-4 sm:grid-cols-2">
          <label className="block"><span className="font-medium text-ink">Wait before replying</span>
            <select value={f.delay_minutes} onChange={(e) => set({ delay_minutes: Number(e.target.value) })}
              className="mt-1 h-9 w-full rounded-lg border border-line bg-surface px-2 text-sm">
              {[0, 2, 5, 10, 15, 30, 60, 120].map((m) => <option key={m} value={m}>{m === 0 ? "No wait" : m < 60 ? `${m} minutes` : `${m / 60} hour${m > 60 ? "s" : ""}`}</option>)}
            </select>
            <span className="mt-1 block text-xs text-muted">A short wait looks more natural than an instant answer.</span>
          </label>
          <label className="block"><span className="font-medium text-ink">At most per day</span>
            <input type="number" min={1} max={500} value={f.max_per_day} onChange={(e) => set({ max_per_day: Math.max(1, Number(e.target.value) || 1) })}
              className="mt-1 h-9 w-full rounded-lg border border-line bg-surface px-2 text-sm" />
            <span className="mt-1 block text-xs text-muted">Automatic replies stop for the day after this many.</span>
          </label>
        </div>

        <label className="block"><span className="font-medium text-ink">Extra instructions <span className="font-normal text-muted">(optional)</span></span>
          <Textarea rows={2} maxLength={1000} value={f.instructions} onChange={(e) => set({ instructions: e.target.value })}
            placeholder="For example: invite people to book a demo at hrpai.in/contact when they ask about pricing. Sign off with — Team HRP AI." />
        </label>

        <div className="flex items-start gap-2 rounded-lg bg-slate-50 px-3 py-2.5 text-xs text-muted">
          <ShieldCheck size={15} className="mt-0.5 shrink-0 text-emerald-600" />
          <span>Replies use your brand voice and company facts only (no invented prices or promises), answer in the commenter's language,
            only cover comments that arrive after you switch this on, and reply to the same person at most twice per thread.
            Every AI reply is marked in the inbox and the Activity log.</span>
        </div>

        <div className="flex justify-end gap-2 border-t border-line pt-3">
          <Button variant="secondary" onClick={onClose}>Cancel</Button>
          <Button disabled={save.isPending} onClick={() => save.mutate({
            enabled: f.enabled, mode: f.mode, emoji: f.emoji, sentiments: f.sentiments, platforms: f.platforms,
            delay_minutes: f.delay_minutes, max_per_day: f.max_per_day, instructions: f.instructions,
          })}><Bot size={15} /> {save.isPending ? "Saving…" : "Save"}</Button>
        </div>
      </div>
    </Modal>
  );
}
