// Minimal markdown renderer for the setup guides: headings, paragraphs, bullet and numbered
// lists (one nesting level), fenced code, inline code, bold and links. Builds React elements
// (no raw HTML), so guide text can't inject markup.
import { Fragment, type ReactNode } from "react";

function inline(text: string, keyBase: string): ReactNode[] {
  const out: ReactNode[] = [];
  const re = /(`[^`]+`)|(\*\*[^*]+\*\*)|(\[[^\]]+\]\([^)\s]+\))/g;
  let last = 0;
  let m: RegExpExecArray | null;
  let i = 0;
  while ((m = re.exec(text))) {
    if (m.index > last) out.push(text.slice(last, m.index));
    const tok = m[0];
    const key = `${keyBase}-${i++}`;
    if (tok.startsWith("`")) {
      out.push(<code key={key} className="rounded bg-ink/5 px-1 py-0.5 font-mono text-[0.85em]">{tok.slice(1, -1)}</code>);
    } else if (tok.startsWith("**")) {
      out.push(<strong key={key}>{tok.slice(2, -2)}</strong>);
    } else {
      const lm = /^\[([^\]]+)\]\(([^)\s]+)\)$/.exec(tok)!;
      const href = /^(https?:|\/)/.test(lm[2]) ? lm[2] : "#";
      out.push(<a key={key} href={href} target="_blank" rel="noreferrer" className="text-primary-700 underline">{lm[1]}</a>);
    }
    last = m.index + tok.length;
  }
  if (last < text.length) out.push(text.slice(last));
  return out;
}

type Item = { text: string; children: { ordered: boolean; items: string[] } | null };
type Block =
  | { kind: "h"; level: number; text: string }
  | { kind: "p"; text: string }
  | { kind: "code"; text: string }
  | { kind: "list"; ordered: boolean; start: number; items: Item[] };

const bullet = /^(\s*)(?:[-*]|(\d+)\.)\s+(.*)$/;

function parse(md: string): Block[] {
  const lines = md.replace(/\r\n/g, "\n").split("\n");
  const blocks: Block[] = [];
  let para: string[] = [];
  const flush = () => { if (para.length) { blocks.push({ kind: "p", text: para.join(" ") }); para = []; } };
  for (let i = 0; i < lines.length; i++) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      flush();
      const code: string[] = [];
      for (i++; i < lines.length && !/^\s*```/.test(lines[i]); i++) code.push(lines[i].replace(/^ {0,4}/, ""));
      blocks.push({ kind: "code", text: code.join("\n") });
      continue;
    }
    if (/^<!--.*-->$/.test(line.trim())) continue;
    const h = /^(#{1,4})\s+(.*)$/.exec(line);
    if (h) { flush(); blocks.push({ kind: "h", level: h[1].length, text: h[2] }); continue; }
    const b = bullet.exec(line);
    if (b) {
      flush();
      const indent = b[1].length;
      const ordered = !!b[2];
      const prev = blocks[blocks.length - 1];
      if (indent >= 2 && prev?.kind === "list" && prev.items.length) {
        const parent = prev.items[prev.items.length - 1];
        parent.children ??= { ordered, items: [] };
        parent.children.items.push(b[3]);
      } else if (prev?.kind === "list" && prev.ordered === ordered && indent < 2) {
        prev.items.push({ text: b[3], children: null });
      } else {
        blocks.push({ kind: "list", ordered, start: ordered ? Number(b[2]) : 1, items: [{ text: b[3], children: null }] });
      }
      continue;
    }
    if (!line.trim()) { flush(); continue; }
    const prev = blocks[blocks.length - 1];
    if (!para.length && /^\s{2,}\S/.test(line) && prev?.kind === "list") {
      // continuation of the last list item
      const it = prev.items[prev.items.length - 1];
      if (it.children) it.children.items[it.children.items.length - 1] += " " + line.trim();
      else it.text += " " + line.trim();
      continue;
    }
    para.push(line.trim());
  }
  flush();
  return blocks;
}

export function Markdown({ text, skipFirstHeading = false }: { text: string; skipFirstHeading?: boolean }) {
  let blocks = parse(text);
  if (skipFirstHeading && blocks[0]?.kind === "h") blocks = blocks.slice(1);
  return (
    <div className="space-y-3 text-sm leading-relaxed text-ink">
      {blocks.map((b, i) => {
        const k = `b${i}`;
        if (b.kind === "h") {
          const cls = b.level <= 2 ? "text-base font-semibold" : "text-sm font-semibold";
          return <p key={k} className={`${cls} pt-1`}>{inline(b.text, k)}</p>;
        }
        if (b.kind === "code") {
          return <pre key={k} className="overflow-x-auto rounded-lg bg-slate-900 px-3 py-2 font-mono text-xs text-slate-100">{b.text}</pre>;
        }
        if (b.kind === "list") {
          const Tag = b.ordered ? "ol" : "ul";
          return (
            <Tag key={k} start={b.ordered && b.start !== 1 ? b.start : undefined} className={`space-y-1.5 pl-5 ${b.ordered ? "list-decimal" : "list-disc"}`}>
              {b.items.map((it, j) => (
                <li key={j}>
                  {inline(it.text, `${k}-${j}`)}
                  {it.children && (
                    <Fragment>
                      {(() => {
                        const Sub = it.children.ordered ? "ol" : "ul";
                        return (
                          <Sub className={`mt-1 space-y-1 pl-5 ${it.children.ordered ? "list-decimal" : "list-disc"}`}>
                            {it.children.items.map((c, n) => <li key={n}>{inline(c, `${k}-${j}-${n}`)}</li>)}
                          </Sub>
                        );
                      })()}
                    </Fragment>
                  )}
                </li>
              ))}
            </Tag>
          );
        }
        return <p key={k}>{inline(b.text, k)}</p>;
      })}
    </div>
  );
}
