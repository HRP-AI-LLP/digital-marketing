// Small, safe Markdown → React renderer (no HTML injection): headings, paragraphs, lists, links,
// bold/italic, inline code, code blocks, blockquotes, simple tables and images.
import type { ReactNode } from "react";

const HEADING = /^(#{1,6})\s+(.+?)\s*#*\s*$/;
const LIST = /^\s*([-*+]|\d+\.)\s+/;

function safeUrl(url: string): string | null {
  const u = url.trim();
  if (/^(https?:\/\/|mailto:|\/|#|\.\/)/i.test(u)) return u;
  return null;
}

let keySeq = 0;
const k = () => `md${keySeq++}`;

export function inline(text: string): ReactNode[] {
  const out: ReactNode[] = [];
  // tokens: `code`, ![alt](url), [label](url), **bold**, *em* / _em_
  const re = /(`[^`]+`)|(!?)\[([^\]]*)\]\(([^)\s]+)\)|\*\*(.+?)\*\*|(?<![\w*])\*(?!\s)(.+?)(?<!\s)\*(?![\w*])|(?<![\w_])_(?!\s)(.+?)(?<!\s)_(?![\w_])/g;
  let last = 0;
  let m: RegExpExecArray | null;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    if (m[1]) out.push(<code key={k()} className="rounded bg-slate-100 px-1 py-0.5 text-[0.9em]">{m[1].slice(1, -1)}</code>);
    else if (m[4] !== undefined) {
      const url = safeUrl(m[4]);
      if (m[2] === "!") {
        out.push(url ? <img key={k()} src={url} alt={m[3]} className="my-3 max-w-full rounded-lg" loading="lazy" /> : m[3]);
      } else if (url) {
        const ext = url.startsWith("http");
        out.push(<a key={k()} href={url} className="text-primary-700 underline underline-offset-2" {...(ext ? { target: "_blank", rel: "noreferrer" } : {})}>{inline(m[3])}</a>);
      } else out.push(m[3]);
    } else if (m[5]) out.push(<strong key={k()}>{inline(m[5])}</strong>);
    else if (m[6] || m[7]) out.push(<em key={k()}>{inline(m[6] ?? m[7])}</em>);
    last = re.lastIndex;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

const H_CLASS = ["", "text-2xl font-bold mt-2 mb-3", "text-xl font-semibold mt-6 mb-2", "text-lg font-semibold mt-5 mb-1.5",
  "font-semibold mt-4 mb-1", "font-semibold mt-3", "font-semibold mt-3"];

export function Markdown({ source }: { source: string }) {
  return <div className="text-[15px] leading-7 text-ink">{blocks(source)}</div>;
}

function blocks(src: string): ReactNode[] {
  const lines = (src || "").split("\n");
  const out: ReactNode[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    const st = line.trim();
    if (!st) { i++; continue; }
    if (st.startsWith("```")) {
      const buf: string[] = [];
      i++;
      while (i < lines.length && !lines[i].trim().startsWith("```")) buf.push(lines[i++]);
      i++;
      out.push(<pre key={k()} className="my-3 overflow-auto rounded-lg bg-slate-900 p-3 text-sm text-slate-100"><code>{buf.join("\n")}</code></pre>);
      continue;
    }
    const h = HEADING.exec(st);
    if (h) {
      const lvl = h[1].length;
      const Tag = `h${lvl}` as "h1";
      out.push(<Tag key={k()} className={H_CLASS[lvl]}>{inline(h[2])}</Tag>);
      i++;
      continue;
    }
    if (/^(-{3,}|\*{3,})$/.test(st)) { out.push(<hr key={k()} className="my-4 border-line" />); i++; continue; }
    if (st.startsWith(">")) {
      const buf: string[] = [];
      while (i < lines.length && lines[i].trim().startsWith(">")) buf.push(lines[i++].replace(/^\s*>\s?/, ""));
      out.push(<blockquote key={k()} className="my-3 border-l-4 border-secondary-500 bg-primary-50 px-3 py-1">{blocks(buf.join("\n"))}</blockquote>);
      continue;
    }
    if (st.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{2,}/.test(lines[i + 1])) {
      const cells = (row: string) => row.trim().replace(/^\||\|$/g, "").split("|").map((c) => c.trim());
      const head = cells(st);
      i += 2;
      const rows: string[][] = [];
      while (i < lines.length && lines[i].includes("|")) rows.push(cells(lines[i++]));
      out.push(
        <div key={k()} className="my-3 overflow-x-auto">
          <table className="w-full border-collapse text-sm">
            <thead><tr>{head.map((c) => <th key={k()} className="border border-line bg-primary-50 px-2 py-1 text-left">{inline(c)}</th>)}</tr></thead>
            <tbody>{rows.map((r) => <tr key={k()}>{r.map((c) => <td key={k()} className="border border-line px-2 py-1">{inline(c)}</td>)}</tr>)}</tbody>
          </table>
        </div>,
      );
      continue;
    }
    const lm = LIST.exec(line);
    if (lm) {
      const ordered = /\d/.test(lm[1][0]);
      const items: string[] = [];
      while (i < lines.length && LIST.test(lines[i])) items.push(lines[i++].replace(LIST, ""));
      const cls = `my-2 ml-6 space-y-1 ${ordered ? "list-decimal" : "list-disc"}`;
      const lis = items.map((x) => <li key={k()}>{inline(x)}</li>);
      out.push(ordered ? <ol key={k()} className={cls}>{lis}</ol> : <ul key={k()} className={cls}>{lis}</ul>);
      continue;
    }
    const buf: string[] = [];
    while (i < lines.length && lines[i].trim() && !HEADING.test(lines[i].trim()) && !/^\s*(```|>)/.test(lines[i]) && !LIST.test(lines[i])) {
      buf.push(lines[i++].trim());
    }
    out.push(<p key={k()} className="my-3">{inline(buf.join(" "))}</p>);
  }
  return out;
}
