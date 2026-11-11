// A small, safe markdown parser for the fleet charter (#256).
// It produces a tree, never HTML: the renderer (components/Markdown.jsx) turns
// it into React elements, so any HTML in the source shows as text. Links keep
// only http(s) and relative targets; anything else (javascript:, data:, …) is
// dropped to plain text.
//
// Supported: headings, paragraphs, lists (bulleted, numbered, one level),
// block quotes, fenced code, horizontal rules, tables; inline code, bold,
// italic and links.

const SCHEME = /^[a-z][a-z0-9+.-]*:/i;

export function safeHref(href) {
  const h = (href || "").trim();
  if (!h) return null;
  if (/^https?:\/\//i.test(h)) return h;
  if (SCHEME.test(h) || h.startsWith("//")) return null; // other schemes, protocol-relative
  // Control characters can hide a scheme from naive checks ("java\nscript:").
  // eslint-disable-next-line no-control-regex
  if (/[\u0000-\u001f\u007f]/.test(h)) return null;
  return h;
}

// Inline: returns [{t:"text"|"code"|"strong"|"em"|"link", ...}]
export function parseInline(src) {
  const out = [];
  let text = "";
  const flush = () => { if (text) { out.push({ t: "text", v: text }); text = ""; } };
  let i = 0;
  while (i < src.length) {
    const rest = src.slice(i);
    let m;
    if (rest[0] === "\\" && rest.length > 1 && /[\\`*_[\]()#+\-.!|]/.test(rest[1])) {
      text += rest[1]; i += 2; continue;
    }
    if ((m = rest.match(/^`([^`]+)`/))) { flush(); out.push({ t: "code", v: m[1] }); i += m[0].length; continue; }
    if ((m = rest.match(/^\*\*([^*]+?)\*\*/)) || (m = rest.match(/^__([^_]+?)__/))) {
      flush(); out.push({ t: "strong", c: parseInline(m[1]) }); i += m[0].length; continue;
    }
    if ((m = rest.match(/^\*([^*\s][^*]*?)\*/)) || (m = rest.match(/^_([^_\s][^_]*?)_(?![A-Za-z0-9])/))) {
      flush(); out.push({ t: "em", c: parseInline(m[1]) }); i += m[0].length; continue;
    }
    if ((m = rest.match(/^\[([^\]]+)\]\(([^)\s]*)(?:\s+"[^"]*")?\)/))) {
      flush();
      const href = safeHref(m[2]);
      if (href) out.push({ t: "link", href, c: parseInline(m[1]) });
      else out.push({ t: "text", v: m[1] }); // unsafe target: keep the words, drop the link
      i += m[0].length; continue;
    }
    text += rest[0]; i += 1;
  }
  flush();
  return out;
}

const cells = (line) => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());

// Blocks: [{t:"h", level, c} | {t:"p", c} | {t:"ul"|"ol", items:[c]} | {t:"quote", c:[blocks]}
//          | {t:"code", v} | {t:"hr"} | {t:"table", head:[c], rows:[[c]]}]
export function parseMarkdown(src) {
  const lines = String(src || "").replace(/\r\n?/g, "\n").split("\n");
  const blocks = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    let m;
    if (!line.trim()) { i += 1; continue; }
    if ((m = line.match(/^\s*(```|~~~)/))) {
      const fence = m[1];
      const body = [];
      i += 1;
      while (i < lines.length && !lines[i].trim().startsWith(fence)) { body.push(lines[i]); i += 1; }
      blocks.push({ t: "code", v: body.join("\n") });
      i += 1; continue;
    }
    if ((m = line.match(/^(#{1,6})\s+(.*?)\s*#*\s*$/))) {
      blocks.push({ t: "h", level: m[1].length, c: parseInline(m[2]) }); i += 1; continue;
    }
    if (/^\s*([-*_])(\s*\1){2,}\s*$/.test(line)) { blocks.push({ t: "hr" }); i += 1; continue; }
    if (line.includes("|") && i + 1 < lines.length && /^\s*\|?\s*:?-{3,}:?\s*(\|\s*:?-{3,}:?\s*)*\|?\s*$/.test(lines[i + 1])) {
      const head = cells(line).map(parseInline);
      const rows = [];
      i += 2;
      while (i < lines.length && lines[i].includes("|") && lines[i].trim()) {
        rows.push(cells(lines[i]).map(parseInline)); i += 1;
      }
      blocks.push({ t: "table", head, rows });
      continue;
    }
    if (/^\s*>/.test(line)) {
      const body = [];
      while (i < lines.length && /^\s*>/.test(lines[i])) { body.push(lines[i].replace(/^\s*>\s?/, "")); i += 1; }
      blocks.push({ t: "quote", c: parseMarkdown(body.join("\n")) });
      continue;
    }
    if ((m = line.match(/^\s*([-*+]|\d+[.)])\s+/))) {
      const ordered = /\d/.test(m[1]);
      const items = [];
      while (i < lines.length) {
        const lm = lines[i].match(/^\s*([-*+]|\d+[.)])\s+(.*)$/);
        if (lm && /\d/.test(lm[1]) === ordered) { items.push(lm[2]); i += 1; continue; }
        if (items.length && /^\s{2,}\S/.test(lines[i])) { items[items.length - 1] += " " + lines[i].trim(); i += 1; continue; }
        break;
      }
      blocks.push({ t: ordered ? "ol" : "ul", items: items.map(parseInline) });
      continue;
    }
    const para = [];
    while (i < lines.length && lines[i].trim() && !/^(#{1,6}\s|\s*```|\s*~~~|\s*>|\s*([-*+]|\d+[.)])\s)/.test(lines[i])) {
      para.push(lines[i].trim()); i += 1;
    }
    blocks.push({ t: "p", c: parseInline(para.join(" ")) });
  }
  return blocks;
}
