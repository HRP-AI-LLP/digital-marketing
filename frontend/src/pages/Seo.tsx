import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link, Route, Routes, useNavigate, useSearchParams } from "react-router-dom";
import { Plus } from "lucide-react";
import { api } from "../api/client";
import { useAuth } from "../auth/AuthContext";
import { Alert, Button, EmptyState, PageHeader, Spinner, StatusBadge, formatDate } from "../components/ui";
import { ArticleEditor } from "./seo/Editor";
import { NewArticleModal } from "./seo/NewArticle";
import { scoreTone, type ArticleSummary } from "./seo/types";

function ArticleList() {
  const { can } = useAuth();
  const navigate = useNavigate();
  const [params, setParams] = useSearchParams();
  const prefill = params.get("keyword") ?? "";
  const [open, setOpen] = useState(!!prefill && can("seo.manage"));
  const q = useQuery({
    queryKey: ["seo-articles"],
    queryFn: () => api<ArticleSummary[]>("/seo/articles"),
    refetchInterval: (query) => (query.state.data?.some((a) => a.status === "queued" || a.status === "writing") ? 5000 : false),
  });
  const close = () => { setOpen(false); if (prefill) setParams({}, { replace: true }); };

  return (
    <>
      <PageHeader
        title="SEO articles"
        description="Long-form articles for your website, grounded in your own pages. Export as MDX or HTML, then share them as social posts."
        action={can("seo.manage") && <Button onClick={() => setOpen(true)}><Plus size={16} /> New article</Button>}
      />
      {q.error && <Alert>{(q.error as Error).message}</Alert>}
      {q.isLoading && <Spinner label="Loading articles" />}
      {q.data && q.data.length === 0 && (
        <EmptyState title="No articles yet" body="Pick a keyword and the writer drafts a full article with headings, FAQ, meta tags and schema."
          action={can("seo.manage") && <Button onClick={() => setOpen(true)}><Plus size={16} /> New article</Button>} />
      )}
      {q.data && q.data.length > 0 && (
        <div className="overflow-x-auto rounded-xl border border-line bg-surface">
          <table className="w-full text-sm">
            <thead className="border-b border-line text-left text-muted">
              <tr>
                <th className="px-4 py-2.5 font-medium">Title</th>
                <th className="px-4 py-2.5 font-medium">Keyword</th>
                <th className="px-4 py-2.5 font-medium">Status</th>
                <th className="px-4 py-2.5 text-right font-medium">Score</th>
                <th className="px-4 py-2.5 text-right font-medium">Words</th>
                <th className="px-4 py-2.5 font-medium">Updated</th>
              </tr>
            </thead>
            <tbody>
              {q.data.map((a) => (
                <tr key={a.id} className="cursor-pointer border-b border-line last:border-0 hover:bg-primary-50/50" onClick={() => navigate(`/seo/${a.id}`)}>
                  <td className="px-4 py-2.5"><Link to={`/seo/${a.id}`} className="font-medium hover:text-primary-700" onClick={(e) => e.stopPropagation()}>{a.title}</Link></td>
                  <td className="px-4 py-2.5 text-muted">{a.keyword}</td>
                  <td className="px-4 py-2.5"><StatusBadge status={a.status} /></td>
                  <td className={`px-4 py-2.5 text-right font-semibold tabular-nums ${a.word_count ? scoreTone(a.seo_score) : "text-muted"}`}>{a.word_count ? a.seo_score : "—"}</td>
                  <td className="px-4 py-2.5 text-right tabular-nums">{a.word_count ? a.word_count.toLocaleString() : "—"}</td>
                  <td className="px-4 py-2.5 text-muted">{formatDate(a.updated_at)}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}
      {open && <NewArticleModal initialKeyword={prefill} onClose={close} />}
    </>
  );
}

export default function Seo() {
  return (
    <Routes>
      <Route index element={<ArticleList />} />
      <Route path=":id" element={<ArticleEditor />} />
    </Routes>
  );
}
