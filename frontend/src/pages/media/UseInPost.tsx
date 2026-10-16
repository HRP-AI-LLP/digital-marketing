import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { api } from "../../api/client";
import { Alert, Button, EmptyState, Input, Modal, Spinner } from "../../components/ui";

type PostRow = { id: string; title: string | null; text: string | null; platform: string; post_type: string; status: string; scheduled_at: string | null };

/** Attach library media to a draft post (replaces that post's media). */
export default function UseInPost({ assetIds, onClose }: { assetIds: string[]; onClose: () => void }) {
  const qc = useQueryClient();
  const [q, setQ] = useState("");
  const [done, setDone] = useState<string | null>(null);
  const posts = useQuery({
    queryKey: ["attach-posts", q],
    queryFn: () => api<{ items: PostRow[] }>(`/content/posts?status=draft,planned,in_review,rejected&limit=30${q ? `&q=${encodeURIComponent(q)}` : ""}`),
  });
  const attach = useMutation({
    mutationFn: (postId: string) => api<{ post_id: string }>("/media/attach", { method: "POST", json: { post_id: postId, asset_ids: assetIds } }),
    onSuccess: (r) => { setDone(r.post_id); qc.invalidateQueries({ queryKey: ["posts"] }); },
  });
  return (
    <Modal title="Use in a post" onClose={onClose}>
      <p className="mb-3 text-sm text-muted">Pick a draft or a post waiting for review. Its current media is replaced; the text stays as it is.</p>
      <Input placeholder="Search posts" value={q} onChange={(e) => setQ(e.target.value)} aria-label="Search posts" />
      <div className="mt-3 max-h-80 space-y-2 overflow-y-auto">
        {posts.isLoading ? <Spinner label="Loading posts…" /> : !(posts.data?.items.length) ? (
          <EmptyState title="No draft posts" body="Create a post in Content first, then come back to attach this media." />
        ) : posts.data.items.map((p) => (
          <div key={p.id} className="flex items-center justify-between gap-3 rounded-lg border border-line px-3 py-2">
            <div className="min-w-0">
              <p className="truncate text-sm font-medium">{p.title || (p.text ?? "").slice(0, 80) || "Untitled post"}</p>
              <p className="text-xs text-muted">{p.platform.replace(/_/g, " ")} · {p.post_type} · {p.status.replace(/_/g, " ")}</p>
            </div>
            <Button variant={done === p.id ? "secondary" : "primary"} disabled={attach.isPending || done === p.id} onClick={() => attach.mutate(p.id)}>
              {done === p.id ? "Attached" : "Attach"}
            </Button>
          </div>
        ))}
      </div>
      {attach.error && <div className="mt-3"><Alert>{(attach.error as Error).message}</Alert></div>}
    </Modal>
  );
}
