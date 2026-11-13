import { describe, expect, it } from "vitest";
import { diffSummary, lineDiff, splitLines } from "./lineDiff.js";

const V1 = "# Charter\n\n## Article 1\n\nHumans stay in charge.\n\n## Article 2\n\nReport incidents.\n";
const V2 = "# Charter\n\n## Article 1\n\nHumans stay in charge, always.\n\n## Article 2\n\nReport incidents.\n\n"
  + "## Article 3\n\nShare what you learn.\n";

const ops = (rows) => rows.map((r) => r.op + r.text);

describe("lineDiff (#345)", () => {
  it("marks added and removed lines with + and − in the text", () => {
    const rows = lineDiff(V1, V2);
    const changed = rows.filter((r) => r.op !== " ");
    expect(ops(changed)).toEqual([
      "−Humans stay in charge.",
      "+Humans stay in charge, always.",
      "+",
      "+## Article 3",
      "+",
      "+Share what you learn.",
    ]);
    expect(rows.find((r) => r.text === "Report incidents.")).toMatchObject({ op: " ", oldNo: 9, newNo: 9 });
  });

  it("rebuilds both texts from the rows", () => {
    const rows = lineDiff(V1, V2);
    expect(rows.filter((r) => r.op !== "+").map((r) => r.text)).toEqual(splitLines(V1));
    expect(rows.filter((r) => r.op !== "−").map((r) => r.text)).toEqual(splitLines(V2));
  });

  it("handles empty, identical and wholly different texts", () => {
    expect(lineDiff("", "")).toEqual([]);
    expect(lineDiff("a\nb\n", "a\nb\n").every((r) => r.op === " ")).toBe(true);
    expect(ops(lineDiff("a\n", "b\n"))).toEqual(["−a", "+b"]);
    expect(ops(lineDiff("", "x\n"))).toEqual(["+x"]);
  });

  it("finds a moved block as a removal and an addition", () => {
    expect(ops(lineDiff("a\nb\nc\n", "b\nc\na\n"))).toEqual(["−a", " b", " c", "+a"]);
  });
});

describe("diffSummary", () => {
  it("counts lines and names the articles changed", () => {
    expect(diffSummary(V1, V2).text).toBe("5 lines added, 1 removed, in Articles 1, 3.");
    expect(diffSummary(V2, V1)).toMatchObject({ added: 1, removed: 5, articles: ["1", "3"] });
  });

  it("is singular for one line in one article, and says when nothing changed", () => {
    expect(diffSummary("## Article 4\n\na\n", "## Article 4\n\nb\n").text).toBe("1 line added, 1 removed, in Article 4.");
    expect(diffSummary(V1, V1).text).toBe("No changes.");
    expect(diffSummary("a\n", "a\nb\n").text).toBe("1 line added, 0 removed.");
  });

  it("orders article numbers numerically", () => {
    const old = "## Article 2\nx\n## Article 10\ny\n";
    expect(diffSummary(old, "## Article 2\nX\n## Article 10\nY\n").articles).toEqual(["2", "10"]);
  });
});
