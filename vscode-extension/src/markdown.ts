// The agent replies in Markdown. The chat panel shows it formatted (paragraphs, lists,
// headings, bold, italics, code, tables) instead of raw ** and ` characters. It is built
// with createElement and text nodes only, never HTML strings, so a reply can't inject markup.
// Both functions are self-contained: the panel embeds their source (see personalAgentHtml.ts).

export type Span = { kind: "text" | "strong" | "em" | "code"; text: string };
export type Block =
  | { kind: "p" | "h"; spans: Span[] }
  | { kind: "ul" | "ol"; items: Span[][] }
  | { kind: "pre"; text: string }
  | { kind: "table"; head: Span[][]; rows: Span[][][] };

export function parseMarkdown(text: string): Block[] {
  const spans = (line: string): Span[] => {
    const out: Span[] = [];
    const pattern = /\*\*([^*]+)\*\*|`([^`]+)`|\*([^*\s][^*]*?)\*/g;
    let last = 0;
    let match: RegExpExecArray | null;
    while ((match = pattern.exec(line))) {
      if (match.index > last) out.push({ kind: "text", text: line.slice(last, match.index) });
      if (match[1] !== undefined) out.push({ kind: "strong", text: match[1] });
      else if (match[2] !== undefined) out.push({ kind: "code", text: match[2] });
      else out.push({ kind: "em", text: match[3] });
      last = match.index + match[0].length;
    }
    if (last < line.length) out.push({ kind: "text", text: line.slice(last) });
    return out;
  };
  const cells = (line: string): string[] => line.trim().replace(/^\|/, "").replace(/\|$/, "").split("|").map((c) => c.trim());
  const listItem = /^\s*(?:[-*+•]|\d+[.)])\s+/;
  const isTableRule = (line: string) => /^\s*\|?\s*:?-{2,}:?\s*(\|\s*:?-{2,}:?\s*)*\|?\s*$/.test(line);
  const lines = String(text).replace(/\r\n?/g, "\n").split("\n");
  const blocks: Block[] = [];
  let i = 0;
  while (i < lines.length) {
    const line = lines[i];
    if (/^\s*```/.test(line)) {
      const code: string[] = [];
      i++;
      while (i < lines.length && !/^\s*```/.test(lines[i])) code.push(lines[i++]);
      i++;
      blocks.push({ kind: "pre", text: code.join("\n") });
    } else if (!line.trim() || /^\s*(-{3,}|\*{3,}|_{3,})\s*$/.test(line)) {
      i++;
    } else if (/^\s*#{1,6}\s+/.test(line)) {
      blocks.push({ kind: "h", spans: spans(line.replace(/^\s*#{1,6}\s+/, "").replace(/\s+#+\s*$/, "")) });
      i++;
    } else if (line.trim().startsWith("|") && i + 1 < lines.length && isTableRule(lines[i + 1])) {
      const head = cells(line).map(spans);
      i += 2;
      const rows: Span[][][] = [];
      while (i < lines.length && lines[i].trim().startsWith("|")) rows.push(cells(lines[i++]).map(spans));
      blocks.push({ kind: "table", head, rows });
    } else if (listItem.test(line)) {
      const ordered = /^\s*\d/.test(line);
      const items: Span[][] = [];
      while (i < lines.length && listItem.test(lines[i])) {
        let item = lines[i++].replace(listItem, "");
        while (i < lines.length && lines[i].trim() && /^\s{2,}\S/.test(lines[i]) && !listItem.test(lines[i])) item += " " + lines[i++].trim();
        items.push(spans(item));
      }
      blocks.push({ kind: ordered ? "ol" : "ul", items });
    } else {
      const paragraph: string[] = [];
      while (i < lines.length && lines[i].trim() && !/^\s*(```|#{1,6}\s)/.test(lines[i]) && !listItem.test(lines[i])
        && !(lines[i].trim().startsWith("|") && i + 1 < lines.length && isTableRule(lines[i + 1]))) paragraph.push(lines[i++].trim());
      blocks.push({ kind: "p", spans: spans(paragraph.join(" ")) });
    }
  }
  return blocks;
}

// doc is the page's document; el the element to fill.
// eslint-disable-next-line @typescript-eslint/no-explicit-any
export function renderMarkdown(el: any, text: string, doc: any, parse: (t: string) => Block[]): void {
  const fill = (target: any, spans: Span[]) => {
    for (const span of spans) {
      if (span.kind === "text") target.append(doc.createTextNode(span.text));
      else {
        const node = doc.createElement(span.kind);
        node.textContent = span.text;
        target.append(node);
      }
    }
  };
  el.replaceChildren();
  for (const block of parse(text)) {
    if (block.kind === "p" || block.kind === "h") {
      const node = doc.createElement(block.kind === "h" ? "h4" : "p");
      fill(node, block.spans);
      el.append(node);
    } else if (block.kind === "ul" || block.kind === "ol") {
      const list = doc.createElement(block.kind);
      for (const item of block.items) {
        const li = doc.createElement("li");
        fill(li, item);
        list.append(li);
      }
      el.append(list);
    } else if (block.kind === "pre") {
      const pre = doc.createElement("pre");
      const code = doc.createElement("code");
      code.textContent = block.text;
      pre.append(code);
      el.append(pre);
    } else if (block.kind === "table") {
      const table = doc.createElement("table");
      const head = doc.createElement("tr");
      for (const cell of block.head) {
        const th = doc.createElement("th");
        fill(th, cell);
        head.append(th);
      }
      table.append(head);
      for (const row of block.rows) {
        const tr = doc.createElement("tr");
        for (const cell of row) {
          const td = doc.createElement("td");
          fill(td, cell);
          tr.append(td);
        }
        table.append(tr);
      }
      el.append(table);
    }
  }
}
