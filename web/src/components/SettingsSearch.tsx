import { type RefObject, useLayoutEffect, useState } from "react";

/**
 * Search on a long settings page. It works on what is on screen, so every setting is found without a list to keep up
 * to date: top-level blocks that don't match are hidden, and inside the ones that do, so are the rows that don't (unless
 * the section's own name matched, then the whole section shows). Group headings stay only above something left. Folds
 * show their contents while searching (FoldSearch in Fold.tsx). Matches the visible words plus aria-labels, titles and
 * `data-keywords` (other words people might type, e.g. "alarm" on a wake setting).
 */
const norm = (t: string) => t.toLowerCase().normalize("NFD").replace(/[̀-ͯ]/g, "").replace(/[’']/g, "");

function words(el: Element): string {
  const extra = [el, ...el.querySelectorAll("[aria-label],[title],[data-keywords]")]
    .map(x => [x.getAttribute("aria-label"), x.getAttribute("title"), x.getAttribute("data-keywords")].filter(Boolean).join(" "));
  return norm(`${el.textContent ?? ""} ${extra.join(" ")}`);
}

const ROWS = ".row, .item, .rowbtn";

export function useSettingsFilter(root: RefObject<HTMLElement | null>, query: string): number | null {
  const [count, setCount] = useState<number | null>(null);
  useLayoutEffect(() => {
    const el = root.current;
    if (!el) return;
    el.querySelectorAll("[data-miss]").forEach(n => n.removeAttribute("data-miss"));
    const want = norm(query).trim().split(/\s+/).filter(Boolean);
    if (!want.length) { if (count !== null) setCount(null); return; }
    const hit = (n: Element) => { const t = words(n); return want.every(w => t.includes(w)); };
    let shown = 0;
    const kids = [...el.children];
    for (const k of kids) {
      if (k.matches(".settings-group")) continue;
      if (!hit(k)) { k.setAttribute("data-miss", ""); continue; }
      shown++;
      const head = k.querySelector(":scope > .fold-head");
      if (head && hit(head)) continue;                     // the section's name matched: show all of it
      for (const r of k.querySelectorAll(ROWS)) {
        if (!hit(r) && !r.closest("[data-miss]")) r.setAttribute("data-miss", "");
      }
    }
    let heading: Element | null = null, any = false;
    const close = () => { if (heading && !any) heading.setAttribute("data-miss", ""); };
    for (const k of kids) {
      if (k.matches(".settings-group")) { close(); heading = k; any = false; }
      else if (!k.hasAttribute("data-miss")) any = true;
    }
    close();
    if (shown !== count) setCount(shown);
  });
  return count;
}
