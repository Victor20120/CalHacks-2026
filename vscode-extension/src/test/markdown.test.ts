import { strict as assert } from "node:assert";
import { test } from "node:test";
import { parseMarkdown, renderMarkdown } from "../markdown";
import { personalAgentHtml } from "../personalAgentHtml";

// A tiny stand-in for the webview's document: enough to see what renderMarkdown builds.
type FakeNode = { tag: string; text?: string; children: FakeNode[]; textContent?: string };
const doc = {
  createElement: (tag: string) => {
    const node: FakeNode & { append(...n: FakeNode[]): void; replaceChildren(): void } = {
      tag, children: [], append(...n) { this.children.push(...n); }, replaceChildren() { this.children = []; },
    };
    return node;
  },
  createTextNode: (text: string) => ({ tag: "#text", text, children: [] }),
};
const flat = (node: FakeNode): string =>
  node.tag === "#text" ? node.text! : node.textContent ?? node.children.map(flat).join("");

const REPLY = [
  "I'm Agent 1, your personal agent.",
  "",
  "**What went wrong.** The run used `dt = 0.03`, so r = 0.30 > 0.25.",
  "",
  "**What I'd do next time:**",
  "- Choose `dt` from `dx`: dt = r*dx^2/alpha.",
  "- Put the check in the code *itself*.",
  "",
  "| dt | r |",
  "|---|---|",
  "| 0.03 | 0.30 |",
  "",
  "```",
  "python heat2d.py config.json",
  "```",
].join("\n");

test("a reply's Markdown becomes paragraphs, bold, code, lists, a table and a code block", () => {
  const blocks = parseMarkdown(REPLY);
  assert.deepEqual(blocks.map((b) => b.kind), ["p", "p", "p", "ul", "table", "pre"]);
  const second = blocks[1] as { spans: { kind: string; text: string }[] };
  assert.deepEqual(second.spans.map((s) => s.kind), ["strong", "text", "code", "text"]);
  const list = blocks[3] as { items: { kind: string; text: string }[][] };
  assert.equal(list.items.length, 2);
  assert.ok(list.items[1].some((s) => s.kind === "em" && s.text === "itself"));
  const table = blocks[4] as { head: unknown[]; rows: unknown[][] };
  assert.equal(table.head.length, 2);
  assert.equal(table.rows.length, 1);
  assert.equal((blocks[5] as { text: string }).text, "python heat2d.py config.json");
});

test("no raw Markdown symbols are left in what the researcher reads", () => {
  const root = doc.createElement("div");
  renderMarkdown(root, REPLY, doc, parseMarkdown);
  const shown = flat(root);
  assert.ok(!shown.includes("**") && !shown.includes("`") && !shown.includes("|---"));
  assert.ok(shown.includes("What went wrong.") && shown.includes("dt = 0.03"));
  assert.deepEqual(root.children.map((c) => c.tag), ["p", "p", "p", "ul", "table", "pre"]);
});

test("HTML in a reply stays text: it is never turned into markup", () => {
  const root = doc.createElement("div");
  renderMarkdown(root, "<img src=x onerror=alert(1)> and **<b>bold</b>**", doc, parseMarkdown);
  const shown = flat(root);
  assert.ok(shown.includes("<img src=x onerror=alert(1)>"));
  const strong = root.children[0].children.find((c) => c.tag === "strong")!;
  assert.equal(strong.textContent, "<b>bold</b>"); // set as text, so the browser shows the characters
});

test("snake_case and lone asterisks are left alone", () => {
  const [p] = parseMarkdown("max_T and r_crit are 2 * 3 apart") as { spans: { kind: string; text: string }[] }[];
  assert.deepEqual(p.spans, [{ kind: "text", text: "max_T and r_crit are 2 * 3 apart" }]);
});

test("the panel embeds the renderer and uses it for replies", () => {
  const html = personalAgentHtml("abc123");
  assert.ok(html.includes("const parseMarkdown=function parseMarkdown"));
  assert.ok(html.includes("renderMarkdown(a,x.reply,document,parseMarkdown)"));
  assert.equal((html.match(/<\/script>/g) || []).length, 1);
});
