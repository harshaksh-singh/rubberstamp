// The social preview card (1200x630), rendered at build time with satori (layout) and resvg (PNG).
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

export const GET: APIRoute = async () => {
  const flags: string[] = data.flags;
  const caught = flags.filter((f) => (data.audit as any).full[f]?.detected).length;
  const stat = (n: string, l: string) =>
    h("div", { display: "flex", flexDirection: "column", gap: 4 }, [
      h("div", { fontSize: 46, fontWeight: 600, letterSpacing: -1.5, color: "#e9ecf4" }, n),
      h("div", { fontSize: 20, color: "#8b94a7" }, l),
    ]);
  const tree = h(
    "div",
    {
      width: 1200, height: 630, display: "flex", flexDirection: "column", justifyContent: "space-between", padding: "64px 72px",
      background: "linear-gradient(135deg, #080b12 0%, #111827 60%, #2a0f19 100%)", fontFamily: "Geist", color: "#e9ecf4",
    },
    [
      h("div", { display: "flex", alignItems: "center", justifyContent: "space-between" }, [
        h("div", { display: "flex", alignItems: "center", gap: 16 }, [
          h("div", { width: 44, height: 44, borderRadius: 12, background: "#ff4f72", display: "flex", alignItems: "center", justifyContent: "center", fontSize: 26, fontWeight: 600, color: "#fff" }, "✓"),
          h("div", { fontSize: 30, fontWeight: 600, letterSpacing: -0.5 }, "RubberStamp"),
        ]),
        h("div", { fontSize: 22, fontWeight: 600, letterSpacing: 5, color: "#ff4f72", border: "3px solid #ff4f72", borderRadius: 10, padding: "6px 16px", transform: "rotate(-8deg)" }, "APPROVED?"),
      ]),
      h("div", { display: "flex", flexDirection: "column", gap: 18 }, [
        h("div", { fontSize: 66, fontWeight: 600, letterSpacing: -2.6, lineHeight: 1.04, maxWidth: 980 }, "Find the grader flaws RL will exploit, before you train."),
        h("div", { fontSize: 26, color: "#bcc3d2", maxWidth: 960, lineHeight: 1.35 }, "A helpdesk RL environment with planted grader blind spots, a pre-training audit, and a GRPO harness."),
      ]),
      h("div", { display: "flex", gap: 72, borderTop: "1px solid #1d2533", paddingTop: 28 }, [
        stat(`${caught}/${flags.length}`, "blind spots caught"),
        stat(String(data.tasks.length), "tasks"),
        stat(String(data.tools), "MCP tools"),
        stat("GRPO", "TRL · LoRA · vLLM"),
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
