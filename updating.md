# Page numbers — explanation for the call

## The one thing to say first
Every chunk **already has a physical page number** (1, 2, 3 … up to the
document's page count). That part of the architect's design is done and
working — nothing is missing. What was seen on screen is NOT "some chunks have
a page number and some don't." We store **two** numbers per chunk, and the
display was switching between them.

## The two numbers (per chunk)
- **Physical page** — the sequential page in the PDF file (1…N). Always
  present, always correct. This is what we use to open the document and place
  the highlight. It is the backbone.
- **Printed page** — the number actually printed on the page (e.g. "3-7",
  "iv"). This is what a human reader recognizes.

## Why you sometimes see one, sometimes the other
When the document prints a page number, we display that printed number because
that's what the reader expects. When a page has **no** printed number — covers,
figures, front matter — we fall back to showing the physical page so the
citation is never blank. Both numbers exist on every chunk; only which one we
*display* changed. That is the entire reason for the "inconsistency."

## On the architect's plan
The plan — a reliable physical page number on everything to drive the
highlighting — is already implemented and working. The physical page is the
backbone; the printed label just sits on top for display. It is assigned on
every single chunk. We did not miss it.

## The option (if the mix is confusing)
We can make the display uniform — e.g. always show the printed number, and
where there isn't one, show it as "PDF page N" so it's obvious which is which.
Small display change, no re-processing of documents needed.

## If asked "then why did the highlight look wrong?"
That's a separate, smaller item on the application side — not the page numbers.
The index already produces a precise highlight box; the app is currently
drawing a coarser one and not using the precise field. Quick change in the
frontend/backend, no re-indexing, and we've already found exactly where.

---

### Bullet version (keep on screen)
- Every chunk HAS a physical page number (1…N) — already assigned, working.
- Two numbers per chunk: physical page (file position, always there) + printed
  page (what's printed on the page, e.g. "3-7").
- We show the printed number when it exists; fall back to the physical page
  when the page prints none (covers/figures/front matter). Both always stored.
- The highlight is anchored to the physical page — always reliable.
- Highlight looking off = separate app-side display fix, no re-indexing.
