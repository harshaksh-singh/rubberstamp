// Chart helpers shared by the site and the README images. The site draws its charts as HTML tables at build time
// (src/pages/index.astro), so they reflow on phones and screen readers get the data; scripts/charts.ts writes the
// README images as standalone SVGs with concrete colors. Both use the scales and orderings defined here.
import { scaleLinear, scaleBand } from "d3-scale";

export type Palette = {
  ink: string;
  muted: string;
  line: string;
  surface: string;
  surface2: string;
  stamp: string;
  stampWash: string;
  pass: string;
  passWash: string;
  warn: string;
  warnWash: string;
  font: string;
  mono: string;
};

const FONTS = {
  font: "Geist Variable, Geist, Inter, system-ui, sans-serif",
  mono: "Geist Mono Variable, Geist Mono, ui-monospace, Menlo, monospace",
};

// The site's tokens (src/styles/global.css). Small text on every surface and wash meets WCAG AA (4.5:1).
export const LIGHT: Palette = {
  ...FONTS,
  ink: "#0c1322",
  muted: "#5f687b",
  line: "#e2e6ee",
  surface: "#ffffff",
  surface2: "#f0f2f6",
  stamp: "#d81f4b",
  stampWash: "#fde9ee",
  pass: "#077657",
  passWash: "#e1f5ee",
  warn: "#9c5409",
  warnWash: "#fcefdc",
};

export const DARK: Palette = {
  ...FONTS,
  ink: "#e9ecf4",
  muted: "#8b94a7",
  line: "#1e2634",
  surface: "#0f141f",
  surface2: "#151b28",
  stamp: "#ff4f72",
  stampWash: "#3b1421",
  pass: "#35d0a0",
  passWash: "#0d2b23",
  warn: "#f2a54b",
  warnWash: "#33240f",
};

export const esc = (s: string | number) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string);

function svg(w: number, h: number, label: string, body: string, p: Palette) {
  const xmlns = 'xmlns="http://www.w3.org/2000/svg"';
  const bg = `<rect width="${w}" height="${h}" rx="14" style="fill:${p.surface}"/>`;
  return `<svg ${xmlns} viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(label)}" style="max-width:100%;height:auto;font-family:${p.font}">${bg}${body}</svg>`;
}

// ------------------------------------------------------------------ shared data helpers
export type AuditData = Record<string, Record<string, { detected: boolean; tasks: string[] }>>;
export type TaskRow = { n: string; title: string; golden_calls: number; shortcut_calls: number; knob: string };

export const PRESET_LABEL: Record<string, string> = {
  default: "OpenAdapt defaults",
  extended: "+ order-aware",
  full: "+ schema & repeat",
};
export const PRESETS = Object.keys(PRESET_LABEL);

export const caughtBy = (audit: AuditData, flags: string[], preset: string) =>
  flags.filter((f) => audit[preset]?.[f]?.detected).length;

/** Heatmap fill: the share of the pass color over the surface, 10% to 50% by tasks caught. Text on every step is
 * ink, which keeps 4.5:1 in both themes (a stronger fill would need a second text color per theme). */
export const heatShare = (n: number, total: number) => 0.1 + (0.4 * Math.min(n, total)) / total;

/** Tasks ordered by how much work the shortcut saves (most first), then by number. */
export const byCost = <T extends TaskRow>(tasks: T[]) =>
  [...tasks].sort((a, b) => a.shortcut_calls - a.golden_calls - (b.shortcut_calls - b.golden_calls) || a.n.localeCompare(b.n));

export const costLabel = (d: number) => (d === 0 ? "same" : (d > 0 ? "+" : "−") + Math.abs(d));

export const countBy = (items: string[]) => {
  const m = new Map<string, number>();
  for (const k of items) m.set(k, (m.get(k) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
};

// ------------------------------------------------------------------ coverage heatmap (README)
export function coverageHeatmap(audit: AuditData, flags: string[], nTasks: number, p: Palette, colW = 150) {
  const labelW = 196, rowH = 34, top = 112, left = 24, pad = 24;
  const w = left + labelW + colW * PRESETS.length + pad, h = top + rowH * flags.length + 82;
  let body = `<text x="${left}" y="34" style="fill:${p.ink};font-size:17px;font-weight:600">Where the audit catches each planted blind spot</text>`;
  PRESETS.forEach((pr, j) => {
    const x = left + labelW + j * colW + colW / 2;
    body += `<text x="${x}" y="${top - 26}" text-anchor="middle" style="fill:${p.ink};font-size:12.5px;font-weight:600">${esc(PRESET_LABEL[pr])}</text>`;
    body += `<text x="${x}" y="${top - 10}" text-anchor="middle" style="fill:${p.muted};font-size:11.5px;font-family:${p.mono}">${caughtBy(audit, flags, pr)}/${flags.length} caught</text>`;
  });
  flags.forEach((f, i) => {
    const y = top + i * rowH;
    body += `<text x="${left}" y="${y + rowH / 2 + 4}" style="fill:${p.ink};font-size:12px;font-family:${p.mono}">${esc(f)}</text>`;
    PRESETS.forEach((pr, j) => {
      const cell = audit[pr]?.[f];
      const n = cell?.detected ? cell.tasks.length : 0;
      const x = left + labelW + j * colW + 4;
      const cw = colW - 8, ch = rowH - 6;
      const tx = `x="${x + cw / 2}" y="${y + rowH / 2 + 4}" text-anchor="middle"`;
      if (!cell?.detected) {
        body += `<rect x="${x}" y="${y + 3}" width="${cw}" height="${ch}" rx="6" style="fill:${p.warnWash};stroke:${p.warn};stroke-opacity:.45;stroke-dasharray:3 3"/>`;
        body += `<text ${tx} style="fill:${p.warn};font-size:11.5px;font-weight:600;font-family:${p.mono}">missed</text>`;
      } else {
        body += `<rect x="${x}" y="${y + 3}" width="${cw}" height="${ch}" rx="6" style="fill:${p.pass};fill-opacity:${heatShare(n, nTasks).toFixed(2)}"/>`;
        body += `<text ${tx} style="fill:${p.ink};font-size:11.5px;font-weight:600;font-family:${p.mono}">${n} task${n === 1 ? "" : "s"}</text>`;
      }
    });
  });
  const foot = [
    `Cell = helpdesk tasks (of ${nTasks}) on which the audit flags the blind spot beyond the strict grader.`,
    "In-sample: the added families were written with these planted flags in view.",
    "Source: results/audit/world-{default,extended,full}",
  ];
  foot.forEach((line, i) => {
    body += `<text x="${left}" y="${h - 48 + i * 17}" style="fill:${p.muted};font-size:11.5px">${esc(line)}</text>`;
  });
  return svg(w, h, `Heatmap: on how many of the ${nTasks} tasks each audit configuration flags each planted blind spot`, body, p);
}

// ------------------------------------------------------------------ cost dumbbell (README)
export function costDumbbell(tasks: TaskRow[], p: Palette) {
  const rows = byCost(tasks);
  const labelW = 250, rowH = 25, top = 92, left = 24, plotW = 420, right = 64;
  const w = left + labelW + plotW + right, h = top + rowH * rows.length + 56;
  const max = Math.max(...rows.flatMap((t) => [t.golden_calls, t.shortcut_calls]));
  const x = scaleLinear().domain([0, max + 1]).range([left + labelW, left + labelW + plotW]);
  let body = `<text x="${left}" y="34" style="fill:${p.ink};font-size:17px;font-weight:600">Tool calls: the honest path vs the shortcut</text>`;
  const ly = top - 22;
  body += `<circle cx="${left + labelW + 6}" cy="${ly - 4}" r="5" style="fill:${p.surface};stroke:${p.ink};stroke-width:2"/><text x="${left + labelW + 16}" y="${ly}" style="fill:${p.muted};font-size:11.5px">honest path</text>`;
  body += `<circle cx="${left + labelW + 106}" cy="${ly - 4}" r="5" style="fill:${p.stamp}"/><text x="${left + labelW + 116}" y="${ly}" style="fill:${p.muted};font-size:11.5px">shortcut</text>`;
  for (const t of x.ticks(6)) {
    body += `<line x1="${x(t)}" x2="${x(t)}" y1="${top - 6}" y2="${top + rowH * rows.length}" style="stroke:${p.line};stroke-width:1"/>`;
    body += `<text x="${x(t)}" y="${top + rowH * rows.length + 16}" text-anchor="middle" style="fill:${p.muted};font-size:11px;font-family:${p.mono}">${t}</text>`;
  }
  rows.forEach((t, i) => {
    const y = top + i * rowH + rowH / 2;
    const d = t.shortcut_calls - t.golden_calls;
    const color = d < 0 ? p.stamp : p.muted;
    const title = t.title.length > 30 ? t.title.slice(0, 29) + "…" : t.title;
    body += `<text x="${left}" y="${y + 4}" style="fill:${p.muted};font-size:11px;font-family:${p.mono}">${esc(t.n)}</text>`;
    body += `<text x="${left + 34}" y="${y + 4}" style="fill:${p.ink};font-size:12px">${esc(title)}</text>`;
    body += `<line x1="${x(t.golden_calls)}" x2="${x(t.shortcut_calls)}" y1="${y}" y2="${y}" style="stroke:${color};stroke-width:2.5;stroke-linecap:round;stroke-opacity:.8"/>`;
    body += `<circle cx="${x(t.golden_calls)}" cy="${y}" r="5" style="fill:${p.surface};stroke:${p.ink};stroke-width:2"/>`;
    body += `<circle cx="${x(t.shortcut_calls)}" cy="${y}" r="5" style="fill:${color}"/>`;
    body += `<text x="${left + labelW + plotW + 10}" y="${y + 4}" style="fill:${color};font-size:11.5px;font-weight:600;font-family:${p.mono}">${costLabel(d)}</text>`;
  });
  body += `<text x="${left}" y="${h - 20}" style="fill:${p.muted};font-size:11.5px">Calls in each task's golden plan and documented shortcut. Source: rs-suite plans (scripts/site_data.py)</text>`;
  const cheaper = rows.filter((t) => t.shortcut_calls < t.golden_calls).length;
  return svg(w, h, `Dumbbell chart: tool calls in each task's honest plan and its documented shortcut; ${cheaper} of ${rows.length} shortcuts take fewer calls`, body, p);
}

// ------------------------------------------------------------------ bar chart (README)
export function flawBars(tasks: TaskRow[], p: Palette) {
  const rows = countBy(tasks.map((t) => t.knob));
  const labelW = 190, rowH = 30, top = 60, left = 24, plotW = 300, right = 40;
  const w = left + labelW + plotW + right, h = top + rowH * rows.length + 44;
  const x = scaleLinear().domain([0, Math.max(...rows.map((r) => r[1]))]).range([0, plotW]);
  const y = scaleBand().domain(rows.map((r) => r[0])).range([top, top + rowH * rows.length]).padding(0.28);
  let body = `<text x="${left}" y="34" style="fill:${p.ink};font-size:17px;font-weight:600">Tasks per planted flaw</text>`;
  for (const [name, n] of rows) {
    const yy = y(name) ?? 0, bh = y.bandwidth();
    body += `<text x="${left}" y="${yy + bh / 2 + 4}" style="fill:${p.ink};font-size:12px;font-family:${p.mono}">${esc(name)}</text>`;
    body += `<rect x="${left + labelW}" y="${yy}" width="${plotW}" height="${bh}" rx="5" style="fill:${p.surface2}"/>`;
    body += `<rect x="${left + labelW}" y="${yy}" width="${x(n)}" height="${bh}" rx="5" style="fill:${p.stamp};fill-opacity:.9"/>`;
    body += `<text x="${left + labelW + x(n) + 8}" y="${yy + bh / 2 + 4}" style="fill:${p.ink};font-size:12px;font-weight:600;font-family:${p.mono}">${n}</text>`;
  }
  body += `<text x="${left}" y="${h - 16}" style="fill:${p.muted};font-size:11.5px">Each of the ${tasks.length} tasks carries one planted grader flaw. Source: results/suite/shortcut_matrix.json</text>`;
  return svg(w, h, "Bar chart: number of tasks per planted grader flaw", body, p);
}

// ------------------------------------------------------------------ architecture (README)
export type PipelineFacts = { tools: number; tasks: number; flags: number; caught: number };

export function architecture(p: Palette, f: PipelineFacts) {
  const steps = [
    ["Helpdesk world", "rs-world", `SQLite · ${f.tools} MCP tools · ${f.tasks} tasks`, true],
    ["Graders + flaws", "rs-verify · rs-suite", `strict specs · ${f.flags} planted flags`, true],
    ["Pre-training audit", "rs-audit", `flags ${f.caught}/${f.flags} (in-sample)`, true],
    ["GRPO harness", "rs-train", "TRL · LoRA · vLLM · gates", true],
    ["Training runs", "next", "pilot · base sweep · seeds", false],
    ["Labels + prediction", "next", "exploit labels · Brier test", false],
  ] as const;
  const bw = 206, bh = 96, gap = 24, left = 24, top = 70;
  const w = left * 2 + steps.length * bw + (steps.length - 1) * gap, h = top + bh + 64;
  let body = `<text x="${left}" y="38" style="fill:${p.ink};font-size:18px;font-weight:600">RubberStamp pipeline</text>`;
  steps.forEach(([title, pkg, sub, done], i) => {
    const x = left + i * (bw + gap);
    if (i > 0) {
      const x0 = x - gap + 2, x1 = x - 6, yy = top + bh / 2;
      body += `<line x1="${x0}" y1="${yy}" x2="${x1}" y2="${yy}" style="stroke:${done ? p.pass : p.muted};stroke-width:2"/><path d="M${x1 - 5} ${yy - 4} L${x1} ${yy} L${x1 - 5} ${yy + 4}" style="fill:none;stroke:${done ? p.pass : p.muted};stroke-width:2"/>`;
    }
    body += `<rect x="${x}" y="${top}" width="${bw}" height="${bh}" rx="12" style="fill:${p.surface};stroke:${done ? p.pass : p.muted};stroke-width:1.5${done ? "" : ";stroke-dasharray:5 4"}"/>`;
    body += `<text x="${x + 14}" y="${top + 26}" style="fill:${done ? p.pass : p.warn};font-size:11px;font-weight:600;font-family:${p.mono};letter-spacing:.06em">${done ? "BUILT" : "NEXT"}</text>`;
    body += `<text x="${x + 14}" y="${top + 50}" style="fill:${p.ink};font-size:14.5px;font-weight:600">${esc(title)}</text>`;
    body += `<text x="${x + 14}" y="${top + 70}" style="fill:${p.muted};font-size:11px;font-family:${p.mono}">${esc(pkg)}</text>`;
    body += `<text x="${x + 14}" y="${top + 86}" style="fill:${p.muted};font-size:11px">${esc(sub)}</text>`;
  });
  return svg(w, h, "RubberStamp pipeline: world, graders, audit and GRPO harness are built; training runs and the prediction test are next", body, p);
}
