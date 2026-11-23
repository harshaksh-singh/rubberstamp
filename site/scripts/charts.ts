// Writes the README images (light and dark) from the same chart code and data the site uses.
//   cd site && npm run charts
import { mkdir, readFile, writeFile } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { architecture, costDumbbell, coverageHeatmap, DARK, flawBars, LIGHT } from "../src/lib/charts.ts";

const here = (p: string) => fileURLToPath(new URL(p, import.meta.url));
const data = JSON.parse(await readFile(here("../src/data/site.json"), "utf8"));
const out = here("../../docs/assets/");
await mkdir(out, { recursive: true });

const charts = {
  "audit-coverage": (p: typeof LIGHT) => coverageHeatmap(data.audit, data.flags, p, true),
  "shortcut-cost": (p: typeof LIGHT) => costDumbbell(data.tasks, p, true),
  "flaws-per-task": (p: typeof LIGHT) => flawBars(data.tasks, p, true),
  pipeline: (p: typeof LIGHT) => architecture(p),
};
for (const [name, draw] of Object.entries(charts)) {
  for (const [theme, palette] of [["light", LIGHT], ["dark", DARK]] as const) {
    await writeFile(`${out}${name}-${theme}.svg`, draw(palette) + "\n");
  }
}
console.log(`wrote ${Object.keys(charts).length * 2} SVGs to docs/assets/`);
