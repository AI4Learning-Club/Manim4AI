# Title Protocol

> Migrated verbatim from the former CodeGen prompt contract.

TITLE PROTOCOL:
- Each page MUST choose exactly ONE page-title style:
  1. long top title via `self.make_page_title(...)` or
     `self.fit_to_top_band(...)`,
  2. title chip via `self.show_page_title_chip(...)`, which appears large near
     the center, then shrinks/moves to the top-right and stays there.
- Never use both page-title styles on the same page.
- If a page uses the long top title style, that title MUST be explicitly shown
  in the page's first reveal beat, then remain visible for the rest of that
  page until the page ends.
- Every page may have only one title system.
- The long page title is page-persistent: show it once at the start of that
  page, keep it visible while that page's body teaches, and clear it only when
  the page ends.
- The title chip is also page-persistent: it enters as a large center title,
  then parks at the top-right and stays there until the page ends.
- The long page title does NOT belong inside `bodyN`. Keep it in the top band,
  and fit only `bodyN` with `self.fit_body(...)`.
- The title chip does NOT belong inside `bodyN` either. It is a separate
  persistent page-title system outside the fitted body.
