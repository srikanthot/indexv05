# Investigation request for Copilot — how is highlighting / page numbers implemented?

Symptom (raised in a manager demo): the citation **page number** and the
**highlight box** sometimes don't match / land in the wrong place. We need to
know whether the INDEX data is correct or the FRONTEND/BACKEND is consuming it
wrongly. You have both the index (Azure) and the frontend/backend code on this
machine; the indexing agent does not. Please do BOTH parts and report back.
**Read-only investigation — do not change any code.**

------------------------------------------------------------------
## PART A — ground truth from the index (run this)
Shows exactly what the index provides per record (render page vs display label,
page size, and the bbox arrays in inches):

    python scripts/inspect_highlight_contract.py --config deploy.config.json --top 5

Then re-run it on the EXACT document + page from the demo that looked wrong:

    python scripts/inspect_highlight_contract.py --config deploy.config.json --source-file "<the PDF>" --page <physical page>

Paste that output into your report.

------------------------------------------------------------------
## PART B — read the frontend + backend code and report HOW it uses the fields
Find, in the chatbot/frontend and the API/backend repos, the code that renders
citations + draws the highlight box. Report file + function for each answer:

1. **Page number shown to the user** — which index field does the UI display as
   the citation page? `printed_page_label` (correct) or `physical_pdf_page`
   (wrong)? Does it ever handle `printed_page_label_is_synthetic` /
   `printed_page_label_end`?

2. **Which page it renders the PDF on** — does it open/scroll to
   `physical_pdf_page`? Or does it try to use the printed label to pick the page
   (wrong — the printed label is not the file position)?

3. **Which bbox field it draws** — `chunk_span_bboxes`, `chunk_bboxes`,
   `line_bboxes`, or `text_bbox`? Does it read the per-entry `page` and draw on
   that physical page (chunks can span pages)?

4. **Coordinate conversion (most likely bug)** — how does it turn the box's
   inch values (`x_in/y_in/w_in/h_in`, origin TOP-LEFT) into screen pixels?
   - Does it scale by the record's real `page_width_in` / `page_height_in`?
   - Or does it assume a fixed size (8.5x11 Letter) or a fixed DPI? (Our docs
     include non-Letter/A4 pages, so a hardcoded size makes boxes drift.)
   - Does it use the correct origin (top-left) and axis direction?

5. **JSON parsing** — the four bbox fields are JSON *strings*. Does the code
   `JSON.parse` them, and handle empty string / empty array without erroring?

6. **Errors** — quote any errors the team mentioned (parse failures, missing
   fields, null handling) with the file/line and what field triggered them.

------------------------------------------------------------------
## Report back
For each of B1-B6: the file + function, what it currently does, and whether it
matches the contract printed by PART A. If a value from PART A (e.g. printed
label "3-7", physical page 42, page size 8.26x11.69) differs from what the UI
shows, say which side is wrong. That tells us if it's an index fix or a
frontend/backend fix.
