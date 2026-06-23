CONTEXT
I have a chatbot (RAG) over technical manuals — PDFs, some 3,000–4,000 pages. Backend uses
Azure Document Intelligence (DI) for layout; frontend renders the PDF (PDF.js). Under each
answer the bot shows citations. Clicking a citation should: open the PDF, jump to the EXACT
page, and highlight the ENTIRE answer chunk — every line of it, neatly — and nothing else.

You already analyzed this codebase earlier and gave me a root-cause writeup + 5 recommendations
(paragraph-level bbox vs chunk-level; build_highlight_text / highlight_text text-search via
PDF.js findController being fragile; tables/figures coarse bbox; recommendations: tracked
paragraph mapping, highlight_snippets array, row-level cell bbox, figure region + description,
raise MAX_HIGHLIGHT_LEN to 3000). I reviewed that and it's good DIRECTIONALLY. Now I want you
to pressure-test it against a STRONGER approach below, evaluate both against the ACTUAL CODE,
and give me a final recommendation. DO NOT change any code yet — analysis and plan only.

WHAT THE USER EXPERIENCES (this is the bar)
The user does not read the PDF top to bottom. They read the answer, click the citation to
VERIFY it, land on the highlight, and look ONLY at the highlight without scrolling. If the
highlight visibly contains the answer → they trust it. If it shows one line, the wrong
paragraph, half a diagram, or the whole page → trust is broken even when the answer was correct.

THE ERRORS I AM ACTUALLY SEEING (user-expected → what happens)
- Expect: full chunk highlighted.        Get: only 1 line, or only ~4 lines, answer not inside.
- Expect: just the answer.               Get: whole 2 paragraphs, or an area the answer isn't in.
- Expect: highlight on the right spot.   Get: shifted to one side.
- Expect: the diagram highlighted.       Get: left half / right half / middle of the diagram only.
- Expect: the diagram highlighted.       Get: the WHOLE page highlighted.
- Expect: the table row.                 Get: the entire table highlighted.
- Expect: the passage.                   Get: nothing highlighted (text-search missed).

MY REFINED APPROACH (please tell me if this is correct, better, or if you have a better one)
Core principle: SMART INGESTION, DUMB FRONTEND. The chunk's location is known for free at
ingestion — capture it then and never re-derive it by matching text at click time.

1. STOP using text-search / fuzzy text matching to locate highlights.
   Instead anchor by CHARACTER OFFSET / SPAN: DI gives every paragraph, line, word, table cell,
   and figure a span {offset, length} into the document content string, plus a boundingRegion
   {pageNumber, polygon}. Chunk ON the DI content string while preserving each chunk's
   (start_offset, end_offset). Then select every element whose span INTERSECTS that range.
   This is deterministic arithmetic — no head-probes, no length guards, no false positives,
   no missed mid-paragraph splits.

2. Render at LINE granularity, with WORD-LEVEL CLIPPING on the first and last line, and emit a
   LIST OF RECTANGLES (one per line) — NEVER a single min/max union box. A union box over a
   multi-paragraph chunk is what produces "highlights 2 paragraphs + the gap + a figure between."
   Line rects following the text contour are what make it look like a clean highlighter.

3. Treat the COORDINATE TRANSFORM as a SEPARATE bug. DI polygons are inches, top-left origin;
   PDF.js draws in points (1/72"), bottom-left origin, at a viewport scale, on possibly rotated
   pages. The "shifted / half-diagram" symptoms are almost certainly a transform bug, not a
   matching bug. The viewer must do: x_pt = x_in*72 ; y_pt = (page_height_in - y_in - h_in)*72
   (Y-FLIP) ; then page rotation (0/90/180/270) ; then viewport zoom ; then devicePixelRatio.
   Build a calibration test: a known box must land dead-on at 100% zoom, 150% zoom, on a
   landscape/rotated page, and on a non-Letter page size.

4. TABLES: highlight the matched ROW's CELLS (union of cells[].boundingRegions), never the whole
   table. Honor merged cells and tables continued across two pages.

5. FIGURES/DIAGRAMS: the "answer" is a VISION-GENERATED description that does NOT physically
   exist on the page, so it cannot be text-highlighted. Highlight the FIGURE REGION and show the
   description in the citation panel/tooltip — not as an on-page text highlight.

6. FALLBACK LADder — NEVER paint the whole page. If exact rects fail: draw paragraph rects
   (marked approximate); if those fail: jump to the page with NO highlight + a soft "approximate
   location" note; if nothing resolves: jump to page 1 + "could not locate, open PDF".

7. Store a per-chunk record the frontend just DRAWS (no frontend intelligence):
   - chunk_id (stable content hash), source_path, kind (text|table_row|figure|summary)
   - first_page (PHYSICAL page index to jump to), printed_label (display only, never navigation)
   - confidence (exact|approximate|none)
   - regions: a LIST of { page, page_width_in, page_height_in, rotation,
       rects_in: [ {x,y,w,h in inches, top-left origin}, ... one per line ] }
       (a second region entry if the chunk crosses a page)
   - figure_description (shown in panel, not highlighted)
   - coords_schema_version + content_hash (invalidate on PDF reprocess)

COMPARISON I WANT YOU TO MAKE
There are basically six mechanisms: (A) text-search at view time, (B) bbox chosen by fuzzy text
match [my current], (C) bbox anchored by character offset/span, (D) word-level span anchoring,
(E) server-baked highlight image (NotebookLM/Glean style), (F) embedded PDF annotations.
Tell me which is best for MY case and why, and whether C+D (my refined approach) is correct or
if E (pre-rendered highlighted page image) is worth it as a fallback/preview.

WHAT I WANT YOU TO DO (take your time, be exhaustive)
1. Enumerate a LARGE set of failure scenarios for citation highlighting — aim for 100+ distinct
   cases (think up to ~1000), grouped into families: page navigation, region selection/matching,
   coordinate transform, granularity/shape, tables, figures, multi-page/multi-column, document
   variance (scanned/rotated/skewed/mixed/non-Latin), chunking artifacts, fallback/error states,
   frontend/viewer/UX (zoom/scroll/remount/mobile), pipeline/data integrity (stale cache,
   reprocessed PDF, id mismatch), and trust/perception. For EACH scenario state: what the user
   sees → the root cause → and whether MY refined approach (C+D + the items above) SOLVES it or
   not. Flag any scenario it does NOT fully solve.
2. Map each scenario to the ACTUAL functions in my codebase (the ones you already found, e.g.
   _text_bbox_for_chunk, build_highlight_text, MAX_HIGHLIGHT_LEN, the figure/table bbox paths,
   the page-resolution logic) and say exactly what is wrong today and what would change.
3. Give a FINAL verdict: is my refined approach the best achievable, or is there a better one?
   If better, describe it concretely.
4. Give an implementation PLAN ordered by impact (what to do first for the biggest visible win),
   the exact data shape each record should store, the coordinate-transform spec, and a QA plan
   (a golden eval set + visual-regression diffing) that would let me PROVE ~99–100% accuracy.
5. Define what "100%" means as concrete acceptance criteria, including "zero confidently-wrong
   highlights" (a confident wrong highlight is worse than no highlight).

Constraints: do NOT modify any code in this pass — analysis, scenario coverage, verdict, and
plan only. Be specific to my actual code, not generic. Take your time and be thorough.
