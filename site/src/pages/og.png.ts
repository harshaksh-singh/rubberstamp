// The social preview card (1200x630), rendered at build time with satori (layout) and resvg (PNG).
// docs/assets/social-preview.png is a copy of the built card (dist/og.png), uploaded as the repository's preview.
import type { APIRoute } from "astro";
import { readFile } from "node:fs/promises";
import { createRequire } from "node:module";
import satori from "satori";
import { Resvg } from "@resvg/resvg-js";
import data from "../data/site.json";

const require = createRequire(import.meta.url);
const font = (file: string) => readFile(require.resolve(`@fontsource/geist/files/${file}`));

type Node = { type: string; props: Record<string, unknown> & { children?: unknown } };
const h = (type: string, style: Record<string, unknown>, children?: unknown): Node => ({ type, props: { style, children } });
const el = (type: string, props: Record<string, unknown>): Node => ({ type, props });

export const GET: APIRoute = async () => {
  const flags: string[] = data.flags;
  const caught = flags.filter((f) => (data.audit as any).full[f]?.detected).length;
  const stat = (n: string, l: string) =>
    h("div", { display: "flex", flexDirection: "column", gap: 4 }, [
      h("div", { fontSize: 46, fontWeight: 600, letterSpacing: -1.5, color: "#e9ecf4", lineHeight: 1.1 }, n),
      h("div", { fontSize: 20, color: "#a3abbd" }, l),
    ]);
  // The site's brand mark: the Geist files have no check-mark glyph, so it is drawn as a path.
  const mark = el("svg", {
    width: 44, height: 44, viewBox: "0 0 26 26",
    children: [
      el("rect", { x: 1.2, y: 1.2, width: 23.6, height: 23.6, rx: 7, fill: "none", stroke: "#e9ecf4", strokeWidth: 1.6 }),
      el("rect", { x: 5.6, y: 5.6, width: 14.8, height: 14.8, rx: 4, fill: "#ff4f72" }),
      el("path", { d: "M9.3 13.3l2.6 2.6 4.9-5.3", fill: "none", stroke: "#fff", strokeWidth: 1.9, strokeLinecap: "round", strokeLinejoin: "round" }),
    ],
  });
  const tree = h(
    "div",
    {
      // border-box: satori sizes the root as content-box otherwise, and the padding pushes the card past 1200x630
      boxSizing: "border-box", width: 1200, height: 630, display: "flex", flexDirection: "column", justifyContent: "space-between", padding: "60px 72px 56px",
      background: "linear-gradient(135deg, #080b12 0%, #111827 60%, #2a0f19 100%)", fontFamily: "Geist", color: "#e9ecf4",
    },
    [
      h("div", { display: "flex", alignItems: "center", justifyContent: "space-between" }, [
        h("div", { display: "flex", alignItems: "center", gap: 16 }, [
          mark,
          h("div", { fontSize: 30, fontWeight: 600, letterSpacing: -0.5 }, "RubberStamp"),
        ]),
        h("div", { fontSize: 22, fontWeight: 600, letterSpacing: 5, color: "#ff4f72", border: "3px solid #ff4f72", borderRadius: 10, padding: "6px 10px 6px 16px", transform: "rotate(-8deg)" }, "APPROVED?"),
      ]),
      h("div", { display: "flex", flexDirection: "column", gap: 20 }, [
        h("div", { display: "flex", flexDirection: "column", fontSize: 62, fontWeight: 600, letterSpacing: -2.4, lineHeight: 1.06 }, [
          h("div", {}, "Which grader flaws will RL exploit?"),
          h("div", { color: "#ff4f72" }, "Audit them before you train."),
        ]),
        h("div", { fontSize: 26, color: "#bcc3d2", maxWidth: 1000, lineHeight: 1.35 }, "A helpdesk RL environment with planted grader blind spots and a pre-training audit. The GRPO runs that test the audit come next."),
      ]),
      h("div", { display: "flex", gap: 64, borderTop: "1px solid #2c3547", paddingTop: 26 }, [
        stat(`${caught}/${flags.length}`, "blind spots caught, in-sample"),
        stat(String(data.tasks.length), "tasks"),
        stat(String(data.tools), "MCP tools"),
        stat(String(data.tasks.length * data.training.world_seeds), "seeded instances"),
      ]),
    ],
  );
  const svg = await satori(tree as any, {
    width: 1200,
    height: 630,
    fonts: [
      { name: "Geist", data: await font("geist-latin-400-normal.woff"), weight: 400, style: "normal" },
      { name: "Geist", data: await font("geist-latin-600-normal.woff"), weight: 600, style: "normal" },
    ],
  });
  const png = new Resvg(svg, { fitTo: { mode: "width", value: 1200 } }).render().asPng();
  return new Response(new Uint8Array(png), { headers: { "Content-Type": "image/png" } });
};
