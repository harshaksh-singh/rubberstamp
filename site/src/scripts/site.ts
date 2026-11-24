import { gsap } from "gsap";
import { ScrollTrigger } from "gsap/ScrollTrigger";
import { SplitText } from "gsap/SplitText";
import { Flip } from "gsap/Flip";

gsap.registerPlugin(ScrollTrigger, SplitText, Flip);
// Smooth on slow machines: never jump more than 33 ms per frame, and ignore mobile address-bar resizes.
gsap.ticker.lagSmoothing(500, 33);
ScrollTrigger.config({ ignoreMobileResize: true });

type Flag = { detected: boolean; families: string[]; tasks: string[] };
type Task = {
  n: string; title: string; category: string; prompt: string; shortcut: string; lenient: string; knob: string;
  accepted_by: string[]; noop_accepted_by: string[];
};
const DATA = JSON.parse(document.getElementById("site-data")!.textContent!) as {
  tasks: Task[]; audit: Record<string, Record<string, Flag>>; flags: string[]; flagText: Record<string, string>;
  ownFlag: { flagged: Record<string, { all: number; later: number }> };
};
const $ = <T extends Element = HTMLElement>(s: string, root: ParentNode = document) => root.querySelector(s) as T;
const $$ = <T extends Element = HTMLElement>(s: string, root: ParentNode = document) => [...root.querySelectorAll(s)] as T[];
const esc = (s: string) => s.replace(/[&<>"]/g, (c) => ({ "&": "&amp;", "<": "&lt;", ">": "&gt;", '"': "&quot;" })[c]!);
const reduce = () => matchMedia("(prefers-reduced-motion: reduce)").matches;

/* ------------------------------------------------------------ theme: a circle grows from the toggle */
const root = document.documentElement;
// Set before paint by the inline script in Base.astro when motion is allowed; gone if this script ran too late.
const animateIn = root.classList.contains("anim");
$("#theme").addEventListener("click", (e) => {
  const next = root.dataset.theme === "dark" ? "light" : "dark";
  const apply = () => {
    root.dataset.theme = next;
    try { localStorage.setItem("rs-theme", next); } catch {}
  };
  const doc = document as Document & { startViewTransition?: (cb: () => void) => { ready: Promise<void> } };
  if (!doc.startViewTransition || reduce()) return apply();
  const { clientX: x, clientY: y } = e as MouseEvent;
  const r = Math.hypot(Math.max(x, innerWidth - x), Math.max(y, innerHeight - y));
  doc.startViewTransition(apply).ready.then(() => {
    root.animate(
      { clipPath: [`circle(0px at ${x}px ${y}px)`, `circle(${r}px at ${x}px ${y}px)`] },
      { duration: 620, easing: "cubic-bezier(.2,.8,.2,1)", pseudoElement: "::view-transition-new(root)" },
    );
  });
});

/* ------------------------------------------------------------ nav: glass on scroll, active section */
const nav = $("#nav");
ScrollTrigger.create({ start: 8, end: "max", onToggle: (self) => nav.classList.toggle("scrolled", self.isActive) });
for (const link of $$<HTMLAnchorElement>(".nav-links a")) {
  const target = $(link.getAttribute("href")!);
  if (!target) continue;
  ScrollTrigger.create({ trigger: target, start: "top 45%", end: "bottom 45%", onToggle: (s) => link.classList.toggle("active", s.isActive) });
}

/* ------------------------------------------------------------ audit explorer */
const flagsEl = $("#flags");
const scoreEl = $("#score");
const meterEl = $("#meter");
const seg = $("#presets");
const buttons = $$<HTMLButtonElement>("#presets button");
const FAMILY: Record<string, string> = {
  duplicate: "duplicate write", unauthorized_side_effect: "side effect on another record", partial_write: "partial write",
  optimistic_success: "claimed success", wrong_value: "wrong value", wrong_order: "wrong order",
  wrong_constrained_value: "constrained value", field_coverage: "field coverage", schema_collateral: "collateral via any tool",
  repeat_existing: "repeat existing", wrong_record: "wrong record", late_write: "write after claim", missing_side_effect: "missing side effect",
};
const familyNames = (fams: string[]) => [...new Set(fams.map((f) => FAMILY[f.split(":")[0]] ?? f.split(":")[0].replaceAll("_", " ")))];
let preset = "full";

// The thumb and the meter move by transform only: the thumb slides one equal-width column per step (--i), the
// meter scales its fill (--m). Nothing here reads layout, apart from Flip's own measurement.
function renderAudit(next: string, animate: boolean) {
  const before = DATA.audit[preset];
  const a = DATA.audit[next];
  const state = animate && !reduce() ? Flip.getState($$(".flag", flagsEl)) : null;
  const caught = DATA.flags.filter((f) => a[f]?.detected).length;
  const order = [...DATA.flags].sort((x, y) => Number(!!a[y]?.detected) - Number(!!a[x]?.detected));
  const cards = new Map($$(".flag", flagsEl).map((el) => [el.dataset.flag!, el]));
  for (const f of order) {
    const el = cards.get(f)!;
    const v = a[f] ?? { detected: false, families: [], tasks: [] };
    el.classList.toggle("caught", v.detected);
    el.classList.toggle("missed", !v.detected);
    $(".state", el).textContent = v.detected ? "Caught" : "Missed";
    $(".by", el).textContent = v.detected
      ? `Caught by ${familyNames(v.families).slice(0, 3).join(", ")} on ${v.tasks.length} task${v.tasks.length === 1 ? "" : "s"}.`
      : "No audit family in this configuration flags it.";
    flagsEl.appendChild(el);
  }
  if (state) {
    Flip.from(state, { duration: 0.6, ease: "power3.inOut", stagger: 0.02, absolute: false });
    for (const f of DATA.flags) {
      if (a[f]?.detected && !before[f]?.detected) {
        // a ring that grows and fades around a newly caught card (opacity and scale only)
        gsap.fromTo($(".ring", cards.get(f)!), { opacity: 1, scale: 1 }, { opacity: 0, scale: 1.08, duration: 0.9, ease: "power2.out", delay: 0.35 });
      }
    }
  }
  const from = { n: Number(scoreEl.textContent) };
  gsap.to(from, { n: caught, duration: animate && !reduce() ? 0.6 : 0, ease: "power2.out", onUpdate: () => (scoreEl.textContent = String(Math.round(from.n))) });
  meterEl.style.setProperty("--m", String(caught / DATA.flags.length));
  const own = DATA.ownFlag.flagged[next];
  $("#own-all").textContent = String(own.all);
  $("#own-later").textContent = String(own.later);
  buttons.forEach((b) => b.setAttribute("aria-pressed", String(b.dataset.p === next)));
  seg.style.setProperty("--i", String(buttons.findIndex((b) => b.dataset.p === next)));
  preset = next;
}
buttons.forEach((b) => b.addEventListener("click", () => renderAudit(b.dataset.p!, true)));
renderAudit("full", false);

/* ------------------------------------------------------------ blind-spot matrix */
const detail = $("#detail");
const rows = $$<HTMLTableRowElement>("#mx tbody tr");
function select(i: number, animate: boolean) {
  const t = DATA.tasks[i];
  rows.forEach((r) => r.setAttribute("aria-selected", String(Number(r.dataset.i) === i)));
  const chips = t.accepted_by.map((v) => `<span class="chip${v === t.knob ? " own" : ""}">${esc(v)}</span>`).join("");
  const noop = t.noop_accepted_by.length
    ? `A plan that only submits a resolution also passes ${t.noop_accepted_by.map(esc).join(", ")}, which is not this task's own flaw.`
    : "Doing nothing passes no grader.";
  detail.innerHTML = `
    <div class="d-top"><span class="tag">${esc(t.n)}</span><span class="tag">${esc(t.category.replaceAll("_", " "))}</span></div>
    <h3>${esc(t.title)}</h3>
    <div class="d-block"><span class="lab">The task</span><p class="prompt">${esc(t.prompt)}</p></div>
    <div class="d-block"><span class="lab">The shortcut</span><p>${esc(t.shortcut)}</p></div>
    <div class="d-block"><span class="lab">Planted flaw · ${esc(t.knob)}</span><p>${esc(t.lenient)}</p></div>
    <div class="d-block"><span class="lab">Graders that pay the shortcut</span><div class="chips">${chips}</div>
      <p style="font-size:.82rem;color:var(--muted)">${noop} The strict grader rejects it.</p></div>`;
  if (animate && !reduce()) gsap.from(detail.children, { opacity: 0, y: 8, duration: 0.35, stagger: 0.04, ease: "power2.out" });
}
rows.forEach((r) => {
  r.addEventListener("click", () => select(Number(r.dataset.i), true));
  r.addEventListener("keydown", (e) => {
    const go = (el: Element | null) => { if (el instanceof HTMLTableRowElement) { e.preventDefault(); el.focus(); select(Number(el.dataset.i), true); } };
    if (e.key === "Enter" || e.key === " ") { e.preventDefault(); select(Number(r.dataset.i), true); }
    if (e.key === "ArrowDown") go(r.nextElementSibling);
    if (e.key === "ArrowUp") go(r.previousElementSibling);
  });
});
select(7, false);

/* ------------------------------------------------------------ copy */
const copy = $<HTMLButtonElement>("#copy");
copy.addEventListener("click", async () => {
  const text = $("#code").textContent!.split("\n").map((l) => l.replace(/^\$ /, "").replace(/\s+#.*$/, "")).join("\n");
  try { await navigator.clipboard.writeText(text); copy.textContent = "Copied"; }
  catch {
    const range = document.createRange(); range.selectNodeContents($("#code"));
    const sel = getSelection(); sel?.removeAllRanges(); sel?.addRange(range); copy.textContent = "Selected";
  }
  setTimeout(() => (copy.textContent = "Copy"), 1600);
});

/* ------------------------------------------------------------ motion (skipped entirely for reduced motion) */
const replay = $<HTMLButtonElement>("#replay");
const mm = gsap.matchMedia();
mm.add("(prefers-reduced-motion: no-preference)", () => {
  const card = $("#verdict");
  const playCard = () => {
    const tl = gsap.timeline({ defaults: { ease: "power2.out" } });
    tl.set("#stamp", { opacity: 0 })
      .from(card, { y: 30, opacity: 0, duration: 0.8, ease: "power3.out" })
      .from(".v-step", { x: -10, opacity: 0, duration: 0.35, stagger: 0.12 }, "-=0.35")
      .from(".v-grade", { y: 10, opacity: 0, duration: 0.4, stagger: 0.25 }, "+=0.1")
      .fromTo("#stamp", { opacity: 0, scale: 2.6, rotate: -28 }, { opacity: 1, scale: 1, rotate: -10, duration: 0.42, ease: "back.out(2.4)" })
      .fromTo(card, { x: 0 }, { keyframes: { x: [0, -5, 4, -2, 0] }, duration: 0.32, ease: "none" }, "-=0.08");
    return tl;
  };
  // Replay exists only where it can do something: with JS and with motion allowed.
  let cardTl: gsap.core.Timeline | null = null;
  const onReplay = () => {
    cardTl?.progress(1).kill();
    gsap.set([card, ".v-step", ".v-grade", "#stamp"], { clearProps: "all" });
    cardTl = playCard();
  };
  replay.hidden = false;
  replay.addEventListener("click", onReplay);
  const hideReplay = () => {
    replay.hidden = true;
    replay.removeEventListener("click", onReplay);
  };
  // If the fallback in Base.astro already showed the page, do not hide and replay what the visitor has seen.
  if (!animateIn) return hideReplay;

  // hero: headline by words, then the copy, then the verdict card plays its trace and the stamp lands
  const split = SplitText.create("#headline", { type: "words", mask: "words" });
  const intro = gsap.timeline({ defaults: { ease: "power3.out" } });
  intro
    .from(split.words, { yPercent: 110, opacity: 0, duration: 0.9, stagger: 0.045 })
    .from("[data-anim='fade']", { y: 16, opacity: 0, duration: 0.7, stagger: 0.08 }, "-=0.55")
    .from(".stats .stat, .stats-note", { y: 18, opacity: 0, duration: 0.6, stagger: 0.06 }, "-=0.4");
  cardTl = playCard();
  intro.add(cardTl, 0.35);

  // count-ups
  for (const el of $$("[data-count]")) {
    const to = Number(el.dataset.count);
    const o = { n: 0 };
    el.textContent = "0";
    gsap.to(o, { n: to, duration: 1.2, ease: "power2.out", delay: 0.5, onUpdate: () => (el.textContent = String(Math.round(o.n))) });
  }

  // section reveals
  gsap.set(".reveal", { opacity: 0 });
  ScrollTrigger.batch(".reveal", {
    start: "top 88%",
    once: true,
    onEnter: (els) => gsap.fromTo(els, { y: 26, opacity: 0 }, { y: 0, opacity: 1, duration: 0.8, stagger: 0.08, ease: "power3.out", overwrite: true }),
  });

  // charts draw in: one trigger per chart, transforms and opacity only
  const once = (trigger: Element | string, start = "top 80%") => ({ trigger, start, once: true });
  gsap.from(".chart-heat .hm-cell", { opacity: 0, scale: 0.7, duration: 0.35, stagger: 0.012, ease: "power2.out", scrollTrigger: once(".chart-heat") });
  gsap.timeline({ scrollTrigger: once(".chart-cost") })
    .from(".chart-cost .db-line", { scaleX: 0, duration: 0.6, stagger: 0.02, ease: "power2.out" })
    .from(".chart-cost .db-dot", { scale: 0, duration: 0.3, stagger: 0.008, ease: "back.out(2)" }, 0.1);
  for (const chart of $$(".chart-bars")) {
    gsap.from($$(".hb-bar", chart), { scaleX: 0, duration: 0.7, stagger: 0.06, ease: "power3.out", scrollTrigger: once(chart, "top 85%") });
  }

  // matrix cells settle in, row by row
  gsap.from("#mx .cell.own, #mx .cell.also", { scale: 0, duration: 0.35, stagger: 0.012, ease: "back.out(2)", scrollTrigger: once("#mx", "top 78%") });

  // pipeline: the line draws, then each stage lands
  const vertical = matchMedia("(max-width: 1000px)").matches;
  gsap.from(".flow-line i", { [vertical ? "scaleY" : "scaleX"]: 0, duration: 1.2, ease: "power2.inOut", scrollTrigger: once("#flow", "top 78%") });
  gsap.from("#flow .stage", { y: 20, opacity: 0, duration: 0.6, stagger: 0.12, ease: "power3.out", scrollTrigger: once("#flow", "top 78%") });
  ScrollTrigger.refresh();

  return () => {
    hideReplay();
    split.revert();
  };
});
// Every start state is set, so the inline styles GSAP wrote now decide what shows.
root.classList.remove("anim");
