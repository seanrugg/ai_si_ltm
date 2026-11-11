// Renders lib/markdown.js trees as React elements: text is always escaped by
// React, and there is no dangerouslySetInnerHTML anywhere (#256).
import { parseMarkdown } from "../lib/markdown.js";

function Inline({ nodes }) {
  return nodes.map((n, i) => {
    if (n.t === "text") return n.v;
    if (n.t === "code") return <code key={i} className="rounded bg-ink-raised px-1 font-mono text-sm">{n.v}</code>;
    if (n.t === "strong") return <strong key={i}><Inline nodes={n.c} /></strong>;
    if (n.t === "em") return <em key={i}><Inline nodes={n.c} /></em>;
    if (n.t === "link") {
      const external = /^https?:/i.test(n.href);
      return (
        <a key={i} href={n.href} className="underline hover:text-paper"
           {...(external ? { target: "_blank", rel: "noopener noreferrer nofollow" } : {})}>
          <Inline nodes={n.c} />
        </a>
      );
    }
    return null;
  });
}

const HEADING = { 1: "text-2xl", 2: "text-xl", 3: "text-lg", 4: "text-base", 5: "text-sm", 6: "text-sm" };

function Blocks({ blocks, base }) {
  return blocks.map((b, i) => {
    const key = `${base}-${i}`;
    switch (b.t) {
      case "h": {
        const Tag = `h${Math.min(6, b.level + 1)}`; // the page has its own h1
        return <Tag key={key} className={`mt-4 mb-2 font-bold ${HEADING[b.level]}`}><Inline nodes={b.c} /></Tag>;
      }
      case "p": return <p key={key} className="my-2"><Inline nodes={b.c} /></p>;
      case "ul": return <ul key={key} className="my-2 ml-6 list-disc">{b.items.map((c, j) => <li key={j}><Inline nodes={c} /></li>)}</ul>;
      case "ol": return <ol key={key} className="my-2 ml-6 list-decimal">{b.items.map((c, j) => <li key={j}><Inline nodes={c} /></li>)}</ol>;
      case "quote": return <blockquote key={key} className="my-2 border-l-2 border-ink-line pl-3 text-paper-muted"><Blocks blocks={b.c} base={key} /></blockquote>;
      case "code": return <pre key={key} className="my-2 overflow-x-auto rounded bg-ink-raised p-3 font-mono text-sm"><code>{b.v}</code></pre>;
      case "hr": return <hr key={key} className="my-4 border-ink-line" />;
      case "table":
        return (
          <table key={key} className="my-3 text-sm">
            <thead><tr>{b.head.map((c, j) => <th key={j} className="border-b border-ink-line px-2 text-left"><Inline nodes={c} /></th>)}</tr></thead>
            <tbody>{b.rows.map((r, j) => <tr key={j}>{r.map((c, k) => <td key={k} className="px-2"><Inline nodes={c} /></td>)}</tr>)}</tbody>
          </table>
        );
      default: return null;
    }
  });
}

export default function Markdown({ source }) {
  return <div className="markdown"><Blocks blocks={parseMarkdown(source)} base="md" /></div>;
}
