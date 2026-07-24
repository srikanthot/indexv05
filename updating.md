# Citation page numbers — what they are and why the format varies

## There are two page numbers on every citation
1. **Physical PDF page** — the sequential position in the PDF file (1, 2, 3 … to the
   last page). Always present, always correct. This is what opens the document
   and places the highlight. It is our reliable internal standard.
2. **Printed page label** — the number actually printed on that page of the
   manual. This is what a person reading the paper/PDF sees, so it's what we show
   in the citation text.

## Why the printed label looks different from one citation to another
Because the **manuals themselves print their pages in different styles**, by
section and by document. We display exactly what is printed on the page — we do
not invent a number — so the citation matches what the reader will see when they
open the document. Examples of what you're seeing:

| You see | What it is | Why |
|---|---|---|
| `1-7`, `7-1` | Chapter–page format: `1-7` = Chapter 1, page 7; `7-1` = Chapter 7, page 1 | These manuals number pages *within each chapter*, so the leading number is the chapter |
| `39`, `49` | Plain sequential page number | Some sections/documents print a simple running number instead of chapter–page |
| `FORM-008` | The label on a **forms** page | The forms section is paginated as `FORM-001`, `FORM-002` … instead of numbers |
| `iv`, `ii` | Roman numerals | Front matter (table of contents, intro) is often numbered in roman |

All of these are **correct** — they mirror the source document's own pagination.
"1-7" vs "7-1" is not an inconsistency; it's just two different chapter/page
positions in the same chapter–page scheme.

## The rule we use (which one, when)
- **To display in a citation** → the **printed page label** (so it matches the
  physical manual). This is why you sometimes see `1-7`, sometimes `49`,
  sometimes `FORM-008` — whatever that page actually prints.
- **To open the PDF and draw the highlight** → the **physical page** (always
  reliable, always present).
- **If a page prints no label at all** (covers, full-page figures, some scans) →
  we fall back to showing the **physical page**, and flag it internally as
  "approximate" so we never show a blank.

## What is the "standard"
- **Internal standard = the physical page** (1…N). It is on every chunk, is never
  ambiguous, and is what drives navigation and highlighting.
- **Display standard = printed label when the page has one, physical page when it
  doesn't.**
- We intentionally do **not** force a single uniform number for display, because
  the printed label is what a user can match against the actual manual. Forcing a
  fake uniform number would make citations *not* match the document.

## The `FORM-008` case specifically
That citation points to a page in the manual's **Forms** section. That page's
printed label is literally `FORM-008`, and it is **physical page 271** of the PDF.
So it is not an error — the system correctly read the printed label from the page
and it happens to be a form number rather than a page number.

## One-line summary
Every citation has a reliable physical PDF page (our standard) plus the page
number printed on that page (what we display). The printed number varies in
format (`1-7`, `49`, `FORM-008`, roman numerals) because the manuals themselves
paginate differently by section — we show exactly what's on the page so the
citation matches the real document.
