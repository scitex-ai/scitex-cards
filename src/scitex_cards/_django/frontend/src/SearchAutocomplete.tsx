/** SearchAutocomplete — GitHub-style inline autocomplete dropdown for the
 * board search input. Mirrors the vanilla `searchSuggest.js` engine consumed
 * by board_v3.html so both surfaces stay in lock-step.
 *
 * Operator pain (TG 12318, lead a2a `e09e0c886eb94e509f8daa87c23dca2a`,
 * 2026-06-12): "want GitHub-style autocomplete on the qualifier search".
 * Builds on PR #102 (qualifier syntax + hint pills) by adding a dropdown
 * that completes the qualifier name (`pro` + Tab -> `project:`) and then
 * the value (`project:pap` + Tab -> `project:paper-scitex-clew`).
 *
 * Keyboard:
 *   ↓ / ↑      move the active item (wraps)
 *   Tab/Enter  commit the active suggestion (Tab is the operator's photographed
 *              flow; Enter is the standard combobox commit)
 *   Esc        close the dropdown without committing
 *
 * Mouse: click a row to commit + refocus the input.
 *
 * Empty suggestions → nothing renders (the input behaves as if no autocomplete
 * is wired). The dropdown is bounded to a max of 8 visible rows.
 *
 * ARIA: role="combobox" + aria-autocomplete="list" on the wrapped input,
 * role="listbox" on the dropdown, role="option" + aria-selected on each row,
 * aria-activedescendant on the input pointing at the highlighted row id.
 */

import {
  useCallback,
  useEffect,
  useMemo,
  useRef,
  useState,
  type ReactElement,
} from "react";
import {
  applySuggestion,
  computeSuggestions,
  formatSuggestion,
  type Suggestion,
  type SuggestNode,
} from "./searchSuggest";

interface SearchAutocompleteProps {
  query: string;
  setQuery: (q: string) => void;
  nodes: SuggestNode[];
  /** The wrapped input — usually rendered as the only child. */
  children: ReactElement<{
    value?: string;
    onChange?: (e: React.ChangeEvent<HTMLInputElement>) => void;
    onKeyDown?: (e: React.KeyboardEvent<HTMLInputElement>) => void;
    onSelect?: (e: React.SyntheticEvent<HTMLInputElement>) => void;
    onBlur?: (e: React.FocusEvent<HTMLInputElement>) => void;
    onFocus?: (e: React.FocusEvent<HTMLInputElement>) => void;
    ref?: React.Ref<HTMLInputElement>;
    role?: string;
    "aria-autocomplete"?: string;
    "aria-controls"?: string;
    "aria-expanded"?: boolean;
    "aria-activedescendant"?: string;
  }>;
}

const LIST_ID = "stx-cards-search-suggest";
const ROW_ID = (i: number) => `${LIST_ID}-row-${i}`;

export function SearchAutocomplete({
  query,
  setQuery,
  nodes,
  children,
}: SearchAutocompleteProps) {
  const inputRef = useRef<HTMLInputElement | null>(null);
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const [cursor, setCursor] = useState<number>(query.length);

  // Reset the active index whenever the suggestion list rebuilds (typing).
  const dataSource = useMemo(() => ({ nodes }), [nodes]);
  const suggestions = useMemo<Suggestion[]>(() => {
    if (!open) return [];
    return computeSuggestions(query, cursor, dataSource).slice(0, 8);
  }, [open, query, cursor, dataSource]);

  useEffect(() => {
    if (active >= suggestions.length) setActive(0);
  }, [suggestions.length, active]);

  const commit = useCallback(
    (sug: Suggestion | null) => {
      if (!sug) return;
      const { newQuery, newCursorPos } = applySuggestion(query, cursor, sug);
      setQuery(newQuery);
      // Re-focus + place caret at the inserted-text boundary; defer to
      // next tick so React commits the value first.
      const el = inputRef.current;
      if (el) {
        setTimeout(() => {
          try {
            el.focus();
            el.setSelectionRange(newCursorPos, newCursorPos);
          } catch {
            /* selection ranges are best-effort */
          }
        }, 0);
      }
      setCursor(newCursorPos);
      // Keep the dropdown open so the operator can chain qualifier->value
      // without re-typing: after `project:` the next list is values.
    },
    [query, cursor, setQuery],
  );

  const trackCursor = useCallback((el: HTMLInputElement | null) => {
    if (!el) return;
    const pos = el.selectionStart;
    if (pos != null) setCursor(pos);
  }, []);

  // Merge the user's input event handlers with our own — preserve whatever
  // CardsBoard already wired (e.g. value/onChange).
  const child = children;
  const userValue = child.props.value;
  const userOnChange = child.props.onChange;

  const onChange = useCallback(
    (e: React.ChangeEvent<HTMLInputElement>) => {
      if (userOnChange) userOnChange(e);
      else setQuery(e.target.value);
      trackCursor(e.currentTarget);
      setOpen(true);
    },
    [userOnChange, setQuery, trackCursor],
  );

  const onKeyDown = useCallback(
    (e: React.KeyboardEvent<HTMLInputElement>) => {
      if (!open) {
        // Open on ↓ / Tab so a focused empty input reveals the keys.
        if (e.key === "ArrowDown" || e.key === "Tab") {
          setOpen(true);
          trackCursor(e.currentTarget);
        }
        return;
      }
      if (e.key === "ArrowDown") {
        e.preventDefault();
        setActive((i) =>
          suggestions.length ? (i + 1) % suggestions.length : 0,
        );
      } else if (e.key === "ArrowUp") {
        e.preventDefault();
        setActive((i) =>
          suggestions.length
            ? (i - 1 + suggestions.length) % suggestions.length
            : 0,
        );
      } else if (e.key === "Tab" || e.key === "Enter") {
        // Only swallow the key if we have a real suggestion to commit —
        // otherwise let Tab / Enter behave normally (form submit / blur).
        if (suggestions.length > 0) {
          e.preventDefault();
          commit(suggestions[active] || null);
        }
      } else if (e.key === "Escape") {
        e.preventDefault();
        setOpen(false);
      }
    },
    [open, suggestions, active, commit, trackCursor],
  );

  const onSelect = useCallback(
    (e: React.SyntheticEvent<HTMLInputElement>) => trackCursor(e.currentTarget),
    [trackCursor],
  );
  const onFocus = useCallback(
    (e: React.FocusEvent<HTMLInputElement>) => {
      setOpen(true);
      trackCursor(e.currentTarget);
    },
    [trackCursor],
  );
  const onBlur = useCallback(() => {
    // Defer close so a click on a row commits before the dropdown vanishes.
    setTimeout(() => setOpen(false), 150);
  }, []);

  const showList = open && suggestions.length > 0;

  // Clone the child input with our extra props merged in. We keep the
  // existing className / placeholder / value / aria-label intact.
  const setRefs = (el: HTMLInputElement | null) => {
    inputRef.current = el;
    const childRef = (child as { ref?: React.Ref<HTMLInputElement> }).ref;
    if (typeof childRef === "function") childRef(el);
    else if (childRef && typeof childRef === "object")
      (childRef as React.MutableRefObject<HTMLInputElement | null>).current =
        el;
  };

  // Build a NEW element with the merged props (React.cloneElement-equivalent
  // but typed) — we add role/aria + handlers + ref.
  const Input = child.type as React.ElementType;
  const inputEl = (
    <Input
      {...child.props}
      ref={setRefs}
      value={userValue ?? query}
      onChange={onChange}
      onKeyDown={onKeyDown}
      onSelect={onSelect}
      onFocus={onFocus}
      onBlur={onBlur}
      role="combobox"
      aria-autocomplete="list"
      aria-controls={showList ? LIST_ID : undefined}
      aria-expanded={showList}
      aria-activedescendant={showList ? ROW_ID(active) : undefined}
      autoComplete="off"
    />
  );

  return (
    <span className="stx-cards-search-suggest__wrap">
      {inputEl}
      {showList && (
        <ul
          id={LIST_ID}
          role="listbox"
          className="stx-cards-search-suggest"
          aria-label="Search suggestions"
        >
          {suggestions.map((s, i) => {
            const { label, hint } = formatSuggestion(s);
            const cls = [
              "stx-cards-search-suggest__row",
              i === active ? "stx-cards-search-suggest__row--active" : "",
              `stx-cards-search-suggest__row--${s.kind}`,
            ]
              .filter(Boolean)
              .join(" ");
            return (
              <li
                key={`${s.kind}:${s.label}:${i}`}
                id={ROW_ID(i)}
                role="option"
                aria-selected={i === active}
                className={cls}
                onMouseDown={(e) => {
                  // Use mousedown so the input doesn't lose focus first
                  // (which would close the dropdown via onBlur).
                  e.preventDefault();
                  setActive(i);
                  commit(s);
                }}
                onMouseEnter={() => setActive(i)}
              >
                <span className="stx-cards-search-suggest__label">{label}</span>
                {s.count != null && (
                  <span className="stx-cards-search-suggest__badge">
                    {s.count}
                  </span>
                )}
                {hint && (
                  <span className="stx-cards-search-suggest__hint">{hint}</span>
                )}
              </li>
            );
          })}
        </ul>
      )}
    </span>
  );
}
