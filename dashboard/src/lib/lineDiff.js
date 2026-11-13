// Line diff for charter versions (#345): an LCS diff, built here so there is no
// diff dependency to pin. Charters are small (≤ 256 KB); the common head and tail
// are trimmed first, so the table only covers the changed middle.

const MAX_CELLS = 4_000_000; // beyond this, fall back to "everything in the middle changed"

/** Lines of `text`, without a final empty line from a trailing newline. */
export function splitLines(text) {
  const lines = (text || "").split(/\r?\n/);
  if (lines.length && lines[lines.length - 1] === "") lines.pop();
  return lines;
}

/**
 * The diff of two texts as rows `{ op, text, oldNo, newNo }`, where `op` is
 * `" "` (unchanged), `"+"` (added) or `"−"` (removed; U+2212). Line numbers are 1-based.
 */
export function lineDiff(oldText, newText) {
  const a = splitLines(oldText);
  const b = splitLines(newText);
  let start = 0;
  while (start < a.length && start < b.length && a[start] === b[start]) start += 1;
  let endA = a.length;
  let endB = b.length;
  while (endA > start && endB > start && a[endA - 1] === b[endB - 1]) { endA -= 1; endB -= 1; }

  const rows = [];
  for (let i = 0; i < start; i += 1) rows.push({ op: " ", text: a[i], oldNo: i + 1, newNo: i + 1 });

  const midA = a.slice(start, endA);
  const midB = b.slice(start, endB);
  const n = midA.length;
  const m = midB.length;
  if (n * m > MAX_CELLS) {
    midA.forEach((t, i) => rows.push({ op: "−", text: t, oldNo: start + i + 1, newNo: null }));
    midB.forEach((t, j) => rows.push({ op: "+", text: t, oldNo: null, newNo: start + j + 1 }));
  } else {
    // lcs[i][j]: longest common subsequence of midA[i..] and midB[j..], in one flat array.
    const w = m + 1;
    const lcs = new Uint32Array((n + 1) * w);
    for (let i = n - 1; i >= 0; i -= 1) {
      for (let j = m - 1; j >= 0; j -= 1) {
        lcs[i * w + j] = midA[i] === midB[j] ? lcs[(i + 1) * w + j + 1] + 1
          : Math.max(lcs[(i + 1) * w + j], lcs[i * w + j + 1]);
      }
    }
    let i = 0;
    let j = 0;
    while (i < n || j < m) {
      if (i < n && j < m && midA[i] === midB[j]) {
        rows.push({ op: " ", text: midA[i], oldNo: start + i + 1, newNo: start + j + 1 });
        i += 1; j += 1;
      } else if (i < n && (j >= m || lcs[(i + 1) * w + j] >= lcs[i * w + j + 1])) {
        // Ties go to the removal, so a changed line reads "− old" then "+ new".
        rows.push({ op: "−", text: midA[i], oldNo: start + i + 1, newNo: null });
        i += 1;
      } else {
        rows.push({ op: "+", text: midB[j], oldNo: null, newNo: start + j + 1 });
        j += 1;
      }
    }
  }

  for (let k = 0; k < a.length - endA; k += 1) {
    rows.push({ op: " ", text: a[endA + k], oldNo: endA + k + 1, newNo: endB + k + 1 });
  }
  return rows;
}

const ARTICLE = /^#{1,6}\s*Article\s+([0-9]+[A-Za-z]?(?:\.[0-9]+)*)/i;

/** The article each line is under, from the nearest "## Article N" heading above it. */
function articlesOf(lines) {
  let current = null;
  return lines.map((line) => {
    const m = ARTICLE.exec(line.trim());
    if (m) current = m[1];
    return current;
  });
}

/** `{ added, removed, articles, text }`, e.g. "12 lines added, 3 removed, in Articles 3, 4, 6". */
export function diffSummary(oldText, newText, rows = lineDiff(oldText, newText)) {
  const oldArticles = articlesOf(splitLines(oldText));
  const newArticles = articlesOf(splitLines(newText));
  let added = 0;
  let removed = 0;
  const touched = new Set();
  for (const r of rows) {
    // Blank lines count, but don't name an article: a new article's leading blank line isn't a change to the last one.
    const blank = !r.text.trim();
    if (r.op === "+") { added += 1; if (!blank && newArticles[r.newNo - 1]) touched.add(newArticles[r.newNo - 1]); }
    if (r.op === "−") { removed += 1; if (!blank && oldArticles[r.oldNo - 1]) touched.add(oldArticles[r.oldNo - 1]); }
  }
  const articles = [...touched].sort((x, y) => x.localeCompare(y, undefined, { numeric: true }));
  let text;
  if (!added && !removed) text = "No changes.";
  else {
    text = `${added} ${added === 1 ? "line" : "lines"} added, ${removed} removed`;
    if (articles.length) text += `, in ${articles.length === 1 ? "Article" : "Articles"} ${articles.join(", ")}`;
    text += ".";
  }
  return { added, removed, articles, text };
}
