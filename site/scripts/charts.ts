// Writes the README images (light and dark) from site.json, with the helpers the site's charts use.
//   cd site && npm run charts
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { architecture, caughtBy, costDumbbell, coverageHeatmap, DARK, flawBars, LIGHT, type Palette } from "../src/lib/charts.ts";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const data = JSON.parse(await readFile(here("../src/data/site.json"), "utf8"));
const out = here("../../docs/assets/");
await mkdir(out, { recursive: true });

const facts = { tools: data.tools, tasks: data.tasks.length, flags: data.flags.length, caught: caughtBy(data.audit, data.flags, "full") };
const charts = {
  "audit-coverage": (p: Palette) => coverageHeatmap(data.audit, data.flags, data.tasks.length, p),
  "shortcut-cost": (p: Palette) => costDumbbell(data.tasks, p),
  "flaws-per-task": (p: Palette) => flawBars(data.tasks, p),
  pipeline: (p: Palette) => architecture(p, facts),
};
for (const [name, draw] of Object.entries(charts)) {
  for (const [theme, palette] of [["light", LIGHT], ["dark", DARK]] as const) {
    await writeFile(`${out}${name}-${theme}.svg`, draw(palette) + "\n");
  }
}
console.log(`wrote ${Object.keys(charts).length * 2} SVGs to docs/assets/`);
