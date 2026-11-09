import { type ReactNode, useState } from "react";

/**
 * The first few items of a long list, with "Show all N" underneath (and "Show fewer" once open). Lists at or under
 * the limit show as they are, with no button. `render` gets the visible items so it can wrap them (a table body, a grid).
 */
export default function ShowMore<T>({ items, limit = 8, render, noun }: {
  items: T[]; limit?: number; render: (shown: T[]) => ReactNode;
  /** what the items are, for the button: "Show all 24 events" */
  noun?: string;
}) {
  const [all, setAll] = useState(false);
  const long = items.length > limit + 1;                     // hiding just one item isn't worth a button
  return <>
    {render(long && !all ? items.slice(0, limit) : items)}
    {long && <button type="button" className="showmore" aria-expanded={all} onClick={() => setAll(v => !v)}>
      {all ? "Show fewer" : `Show all ${items.length}${noun ? ` ${noun}` : ""}`}<span className="fold-chev" aria-hidden="true">›</span></button>}
  </>;
}
