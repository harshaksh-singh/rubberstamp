// Build-time SVG charts. Pure functions: data in, SVG string out. The site inlines them (colors are CSS variables,
// so one chart serves both themes) and scripts/charts.ts writes them as files for the README (concrete colors).
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

export const CSS_PALETTE: Palette = {
  ink: "var(--ink)",
  muted: "var(--muted)",
  line: "var(--line)",
  surface: "var(--surface)",
  surface2: "var(--surface-2)",
  stamp: "var(--stamp)",
  stampWash: "var(--stamp-wash)",
  pass: "var(--pass)",
  passWash: "var(--pass-wash)",
  warn: "var(--warn)",
  warnWash: "var(--warn-wash)",
  font: "Geist Variable, Geist, Inter, system-ui, sans-serif",
  mono: "Geist Mono Variable, Geist Mono, ui-monospace, Menlo, monospace",
};

export const LIGHT: Palette = {
  ...CSS_PALETTE,
  ink: "#0c1322",
  muted: "#687185",
  line: "#e2e6ee",
  surface: "#ffffff",
  surface2: "#f0f2f6",
  stamp: "#df2450",
  stampWash: "#fde9ee",
  pass: "#0b8f6c",
  passWash: "#e1f5ee",
  warn: "#b8650b",
  warnWash: "#fcefdc",
};

export const DARK: Palette = {
  ...CSS_PALETTE,
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

const esc = (s: string | number) =>
  String(s).replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c] as string);

function svg(w: number, h: number, label: string, body: string, p: Palette, standalone: boolean) {
  const bg = standalone ? `<rect width="${w}" height="${h}" rx="14" style="fill:${p.surface}"/>` : "";
  const xmlns = 'xmlns="http://www.w3.org/2000/svg"';
  return `<svg ${xmlns} viewBox="0 0 ${w} ${h}" width="${w}" height="${h}" role="img" aria-label="${esc(label)}" class="chart" style="max-width:100%;height:auto;font-family:${p.font}">${bg}${body}</svg>`;
}

// ------------------------------------------------------------------ coverage heatmap
export type AuditData = Record<string, Record<string, { detected: boolean; tasks: string[] }>>;
export const PRESET_LABEL: Record<string, string> = {
  default: "OpenAdapt defaults",
  extended: "+ order-aware",
  full: "+ schema & repeat",
};

export function coverageHeatmap(audit: AuditData, flags: string[], p: Palette, standalone = false, colW = 150) {
  const presets = Object.keys(PRESET_LABEL);
  const labelW = 196, rowH = 34, top = standalone ? 112 : 64, left = standalone ? 24 : 0, pad = standalone ? 24 : 0;
  const w = left + labelW + colW * presets.length + pad, h = top + rowH * flags.length + (standalone ? 64 : 8);
  const max = 20;
  let body = "";
  if (standalone) {
    body += `<text x="${left}" y="34" style="fill:${p.ink};font-size:17px;font-weight:600">Where the audit catches each planted blind spot</text>`;
  }
  presets.forEach((pr, j) => {
    const x = left + labelW + j * colW + colW / 2;
    const caught = flags.filter((f) => audit[pr]?.[f]?.detected).length;
    body += `<text x="${x}" y="${top - 26}" text-anchor="middle" style="fill:${p.ink};font-size:12.5px;font-weight:600">${esc(PRESET_LABEL[pr])}</text>`;
    body += `<text x="${x}" y="${top - 10}" text-anchor="middle" style="fill:${p.muted};font-size:11.5px;font-family:${p.mono}">${caught}/10 caught</text>`;
  });
  flags.forEach((f, i) => {
    const y = top + i * rowH;
    body += `<text x="${left}" y="${y + rowH / 2 + 4}" style="fill:${p.ink};font-size:12px;font-family:${p.mono}">${esc(f)}</text>`;
    presets.forEach((pr, j) => {
      const cell = audit[pr]?.[f];
      const n = cell?.detected ? cell.tasks.length : 0;
      const x = left + labelW + j * colW + 4;
      const cw = colW - 8, ch = rowH - 6;
      if (!cell?.detected) {
        body += `<rect class="hm-cell" x="${x}" y="${y + 3}" width="${cw}" height="${ch}" rx="6" style="fill:${p.warnWash};stroke:${p.warn};stroke-opacity:.45;stroke-dasharray:3 3"/>`;
        body += `<text x="${x + cw / 2}" y="${y + rowH / 2 + 4}" text-anchor="middle" style="fill:${p.warn};font-size:11.5px;font-weight:600;font-family:${p.mono}">missed</text>`;
      } else {
        const a = 0.28 + 0.72 * Math.min(n, max) / max;
        body += `<rect class="hm-cell" x="${x}" y="${y + 3}" width="${cw}" height="${ch}" rx="6" style="fill:${p.pass};fill-opacity:${a.toFixed(2)}"/>`;
        body += `<text x="${x + cw / 2}" y="${y + rowH / 2 + 4}" text-anchor="middle" style="fill:${a > 0.6 ? "#ffffff" : p.ink};font-size:11.5px;font-weight:600;font-family:${p.mono}">${n} task${n === 1 ? "" : "s"}</text>`;
      }
    });
  });
  if (standalone) {
    body += `<text x="${left}" y="${h - 30}" style="fill:${p.muted};font-size:11.5px">Cell = helpdesk tasks (of 20) where the audit flags the blind spot beyond the strict grader.</text>`;
    body += `<text x="${left}" y="${h - 13}" style="fill:${p.muted};font-size:11.5px">Source: results/audit/world-{default,extended,full}</text>`;
  }
  return svg(w, h, "Heatmap: tasks on which each blind spot is caught, per audit configuration", body, p, standalone);
}

// ------------------------------------------------------------------ cost dumbbell
export type TaskRow = { n: string; title: string; golden_calls: number; shortcut_calls: number; knob: string };

export function costDumbbell(tasks: TaskRow[], p: Palette, standalone = false) {
  const rows = [...tasks].sort((a, b) => a.shortcut_calls - a.golden_calls - (b.shortcut_calls - b.golden_calls) || a.n.localeCompare(b.n));
  const labelW = 250, rowH = 25, top = standalone ? 92 : 40, left = standalone ? 24 : 0, plotW = 420, right = standalone ? 64 : 48;
  const w = left + labelW + plotW + right, h = top + rowH * rows.length + (standalone ? 56 : 26);
  const max = Math.max(...rows.flatMap((t) => [t.golden_calls, t.shortcut_calls]));
  const x = scaleLinear().domain([0, max + 1]).range([left + labelW, left + labelW + plotW]);
  let body = "";
  if (standalone) {
    body += `<text x="${left}" y="34" style="fill:${p.ink};font-size:17px;font-weight:600">Tool calls: the honest path vs the shortcut</text>`;
  }
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
    body += `<line class="db-line" x1="${x(t.golden_calls)}" x2="${x(t.shortcut_calls)}" y1="${y}" y2="${y}" style="stroke:${color};stroke-width:2.5;stroke-linecap:round;stroke-opacity:.8"/>`;
    body += `<circle class="db-dot" cx="${x(t.golden_calls)}" cy="${y}" r="5" style="fill:${p.surface};stroke:${p.ink};stroke-width:2"/>`;
    body += `<circle class="db-dot" cx="${x(t.shortcut_calls)}" cy="${y}" r="5" style="fill:${d < 0 ? p.stamp : p.muted}"/>`;
    const label = d === 0 ? "same" : (d > 0 ? "+" : "−") + Math.abs(d);
    body += `<text x="${left + labelW + plotW + 10}" y="${y + 4}" style="fill:${d < 0 ? p.stamp : p.muted};font-size:11.5px;font-weight:600;font-family:${p.mono}">${label}</text>`;
  });
  if (standalone) {
    body += `<text x="${left}" y="${h - 20}" style="fill:${p.muted};font-size:11.5px">Calls in each task's golden plan and documented shortcut. Source: rs-suite plans (scripts/site_data.py)</text>`;
  }
  return svg(w, h, "Dumbbell chart: tool calls in the honest path versus the shortcut, per task", body, p, standalone);
}

// ------------------------------------------------------------------ bar charts
export function bars(rows: [string, number][], p: Palette, opts: { title?: string; note?: string; label: string; mono?: boolean; color?: string }, standalone = false) {
  const labelW = 190, rowH = 30, top = standalone ? 60 : 8, left = standalone ? 24 : 0, plotW = 300, right = standalone ? 40 : 30;
  const w = left + labelW + plotW + right, h = top + rowH * rows.length + (standalone ? 44 : 6);
  const x = scaleLinear().domain([0, Math.max(...rows.map((r) => r[1]))]).range([0, plotW]);
  const y = scaleBand().domain(rows.map((r) => r[0])).range([top, top + rowH * rows.length]).padding(0.28);
  const fill = opts.color ?? p.stamp;
  let body = "";
  if (standalone && opts.title) body += `<text x="${left}" y="34" style="fill:${p.ink};font-size:17px;font-weight:600">${esc(opts.title)}</text>`;
  for (const [name, n] of rows) {
    const yy = y(name) ?? 0, bh = y.bandwidth();
    body += `<text x="${left}" y="${yy + bh / 2 + 4}" style="fill:${p.ink};font-size:12px${opts.mono === false ? "" : `;font-family:${p.mono}`}">${esc(name)}</text>`;
    body += `<rect x="${left + labelW}" y="${yy}" width="${plotW}" height="${bh}" rx="5" style="fill:${p.surface2}"/>`;
    body += `<rect class="bar" x="${left + labelW}" y="${yy}" width="${x(n)}" height="${bh}" rx="5" style="fill:${fill};fill-opacity:.9"/>`;
    body += `<text x="${left + labelW + x(n) + 8}" y="${yy + bh / 2 + 4}" style="fill:${p.ink};font-size:12px;font-weight:600;font-family:${p.mono}">${n}</text>`;
  }
  if (standalone && opts.note) body += `<text x="${left}" y="${h - 16}" style="fill:${p.muted};font-size:11.5px">${esc(opts.note)}</text>`;
  return svg(w, h, opts.label, body, p, standalone);
}

const countBy = (items: string[]) => {
  const m = new Map<string, number>();
  for (const k of items) m.set(k, (m.get(k) ?? 0) + 1);
  return [...m.entries()].sort((a, b) => b[1] - a[1] || a[0].localeCompare(b[0]));
};

export function flawBars(tasks: TaskRow[], p: Palette, standalone = false) {
  return bars(countBy(tasks.map((t) => t.knob)), p, {
    title: "Tasks per planted flaw",
    note: "Each of the 20 tasks carries one planted grader flaw. Source: results/suite/shortcut_matrix.json",
    label: "Bar chart: number of tasks per planted grader flaw",
  }, standalone);
}

export function categoryBars(tasks: (TaskRow & { category: string })[], p: Palette, standalone = false) {
  const rows = countBy(tasks.map((t) => t.category.replaceAll("_", " ")));
  return bars(rows, p, { title: "Tasks by kind", label: "Bar chart: number of tasks per task category", mono: false, color: p.ink }, standalone);
}

// ------------------------------------------------------------------ architecture (README image)
export function architecture(p: Palette) {
  const steps = [
    ["Helpdesk world", "rs-world", "SQLite · 16 MCP tools · 20 tasks", true],
    ["Graders + flaws", "rs-verify · rs-suite", "strict specs · 10 planted flags", true],
    ["Pre-training audit", "rs-audit", "mutation families · 10/10 caught", true],
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
    body += `<text x="${x + 14}" y="${top + 26}" style="fill:${done ? p.pass : p.warn};font-size:10.5px;font-weight:600;font-family:${p.mono};letter-spacing:.06em">${done ? "BUILT" : "NEXT"}</text>`;
    body += `<text x="${x + 14}" y="${top + 50}" style="fill:${p.ink};font-size:14.5px;font-weight:600">${esc(title)}</text>`;
    body += `<text x="${x + 14}" y="${top + 70}" style="fill:${p.muted};font-size:11px;font-family:${p.mono}">${esc(pkg)}</text>`;
    body += `<text x="${x + 14}" y="${top + 86}" style="fill:${p.muted};font-size:10.5px">${esc(sub)}</text>`;
  });
  return svg(w, h, "RubberStamp pipeline: world, graders, audit, harness built; training runs and analysis next", body, p, true);
}
