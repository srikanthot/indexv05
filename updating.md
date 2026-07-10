TO: the chatbot frontend/backend Copilot.
GOAL: close the bugs we CAN right now using data that is ALREADY in the index — no reindex needed.
(A separate procedure-detection reindex is queued on the indexing side; two bugs wait for that. The
rest you can close today.) Make the code changes below, then run the self-check at the bottom and
confirm each item is actually implemented.

============================================================================
INDEX CONNECTION (already live)
============================================================================
  index          : psegtmv01-index
  endpoint       : https://srch02-pseg-tman-dev01.search.azure.us   (US Gov, note .us)
  query api      : 2024-07-01
  semantic config: mm-semantic-config
  vector field   : text_vector (1536-dim, text-embedding-ada-002) with an INTEGRATED vectorizer
                   -> you do NOT have to embed the query; send raw text in vectorQueries "kind":"text".
  auth           : Entra token for https://search.azure.us (Search Index Data Reader) or a query key.

ALWAYS put these two gates in the filter (both are populated and safe now):
  filter: retrieval_eligible eq true and is_current_revision eq true

============================================================================
WHAT IS POPULATED RIGHT NOW (measured on the live index — use these with confidence)
============================================================================
  Tables      table_row_key, table_columns, table_row_cells, table_row_semantic_value,
              table_cluster_id, table_row_index        ~100%
  Figures     figure_number 81% · figure_callouts 86% · figure_title 56% · diagram_description present
  Scoping     applies_to_domain 68% · applies_to_equipment 66% · applies_to_system 100%
  Safety      criticality 100% · hazard_class 42% (text)/47% (diagram) · governing_callouts 44% ·
              prohibitions (present where they exist, e.g. "Do not operate the bypass valve")
  Highlight   text_bbox, line_bboxes, physical_pdf_page, printed_page_label, page_width_in/height_in
  Hygiene     retrieval_eligible 100% · is_current_revision 100% (fixed)
  Procedures  procedure_id only ~9% right now (DI-escaped steps fix is pending reindex). Where a chunk
              HAS procedure_id: procedure_step_id/title 100%, step_order/step_count populated.

============================================================================
HOW TO USE EACH THING (implement exactly this)
============================================================================

1) CITATIONS / HIGHLIGHT  (the experience: one continuous bright box over the whole cited passage)
   READ THIS — current highlight is patchy because the UI is rendering the per-LINE boxes.
   - RENDER `text_bbox` AS THE PRIMARY HIGHLIGHT, NOT `line_bboxes`.
       * `text_bbox` = ONE continuous rectangle PER PAGE that hugs the chunk start-to-end. This is the
         "solid box" experience. Parse it (JSON string): [{page,x_in,y_in,w_in,h_in}, ...].
       * `line_bboxes` = per-line boxes; PRECISE but has GAPS (any line with a hyphenated word, a table
         row, or that crosses the chunk edge fails to match and gets no box -> the flicker you saw).
         Use it ONLY as an optional fine overlay, never as the sole highlight.
   - Coordinates are INCHES, origin TOP-LEFT; scale by page_width_in / page_height_in.
   - WHOLE-REGION HIGHLIGHT covering TEXT + TABLE + DIAGRAM (fixes "box stops at Note, skips the table"):
       one citation = ONE record, and text / tables / diagrams are SEPARATE records, each with its own
       box. To highlight the entire region start-to-end irrespective of content type, UNION the boxes of
       ALL records in that region:
         1. From the cited record take: source_file, page range (physical_pdf_page .. physical_pdf_page_end),
            and section key (procedure_id, else header_1/2/3 path).
         2. Fetch every record in that region (do NOT restrict record_type):
              filter: source_file eq '<f>' and physical_pdf_page ge <p0> and physical_pdf_page le <p1>
                      [and procedure_id eq '<id>'  OR same header path, when available]
            $select: record_type, physical_pdf_page, text_bbox, table_bbox, figure_bbox
         3. Union text_bbox (text) + table_bbox (table/table_row) + figure_bbox (diagram), grouped by page.
         Render that union -> one continuous highlight that now INCLUDES the middle table and any diagram,
         from the first line to the last, across every page the section spans.
   - Answer text = the verbatim `chunk` (quote it; do NOT paraphrase). The LLM writes only framing
     like "Per <source_file>, p.<printed_page_label>:". Show `printed_page_label` ("p. 1-3").
   - KNOWN (index-side, pending the next reindex, do not block on it): TABLES and some hyphenated lines
     are not yet included in the boxes, so a table region may be unhighlighted. The reindex will add
     table + hyphenation-tolerant geometry. For now, text_bbox already gives a continuous box over the
     prose; that is the correct "start-to-end" experience to ship today.
   $select: chunk, source_file, physical_pdf_page, physical_pdf_page_end, printed_page_label,
            text_bbox, chunk_bboxes, line_bboxes, page_width_in, page_height_in, procedure_id,
            header_1, header_2, header_3

2) TABLES / NUMERIC VALUES  (deterministic lookup)
   - Filter record_type eq 'table_row'. Look up by `table_row_key`; read the value from
     `table_row_cells` ("Header: value" strings). `table_columns` gives the header order.
   - DROP NOISE: ignore rows where is_locator_artifact eq true OR table_row_quality eq 'noise'
     (the "List of Figures / Figure 11.14 ..." rows are TOC noise — never answer from them).
   - Reunite split tables by `table_cluster_id`. If table_rows_truncated eq true, fall back to the
     parent record_type eq 'table' markdown for that cluster.
   $select: table_row_key, table_columns, table_row_cells, table_cluster_id, table_row_quality,
            is_locator_artifact, physical_pdf_page, table_bbox, source_file

3) FIGURES / DIAGRAMS  (SHOW, never assert)
   - Render the figure (figure_bbox + physical_pdf_page) and say "verify against Figure X".
   - NEVER state figure_callouts / diagram_ocr_text values as fact. Use diagram_description only to
     find/route, not as the answer value.
   $select: figure_number, figure_title, diagram_description, figure_bbox, physical_pdf_page, source_file

4) PROCEDURES  (use where detected; degrade gracefully where not)
   - If the top hit HAS a procedure_id: fetch the whole procedure —
     filter: procedure_id eq '<id>' and is_current_revision eq true ; orderby procedure_step_order asc.
     Verify assembled step numbers vs procedure_step_count (if fewer -> say a step is missing).
     Quote procedure_step_text / chunk verbatim; attach governing_callouts + prohibitions.
   - If the top hit has NO procedure_id (common until the reindex): DO NOT fabricate steps. Return the
     verbatim `chunk` + page + highlight as a grounded "here is the manual passage" answer, and if the
     query is hazardous and no ordered procedure is found, REFUSE ("no specific step procedure found —
     read the page / consult supervisor"). This is still a correct, grounded, non-hallucinated answer.

5) SAFETY / REFUSAL GATE  (the live-wire behavior)
   - Trigger STRICT mode on hazardous queries (live/energized/gas) OR when hazard_class contains one.
   - Always render governing_callouts + prohibitions WITH any steps.
   - Honor is_prohibition / prohibitions: surface "Do not ..." clauses prominently.
   - If STRICT and no specific grounded procedure/answer -> REFUSE, never generalize.

6) SCOPING / CURRENCY
   - Use applies_to_domain/equipment as a re-rank BOOST + state the scope in the answer. Do NOT hard-
     filter on them (only ~65% populated -> you'd drop valid answers).
   - is_current_revision eq true is now safe to hard-filter (all 46 manuals current).

============================================================================
BUGS YOU CAN CLOSE NOW (grounded in existing data)
============================================================================
  61021  steel vs cast iron  -> FOUND & POPULATED as a diagram. Show the diagram + description +
         applies_to_equipment. CLOSE with the figure (show-not-assert).
  67009  397 aluminum ampacity -> the ampacity table IS indexed (table_row_key/columns/cells 100%).
         Query record_type eq 'table_row' for the conductor row (key like "397" / "397 AAC"); read the
         amperage cell from table_row_cells. CLOSE via table lookup.
  66009  hazardous classification fired gas equip -> FOUND as a diagram w/ description + governing_
         callouts + criticality=high. Show the figure + the callout rules. CLOSE (show-not-assert).
  60014  bypass at regulating stations -> the prohibition IS indexed
         (prohibitions=["Do not operate the bypass valve"]) + governing_callouts + applies_to_equipment.
         Answer with the verbatim prohibition + callout + page/highlight. CLOSE (grounded prohibition);
         the ordered-step version arrives after the procedure reindex.

  WAITING ON THE PROCEDURE REINDEX (don't force these now):
  61020a  mark-out 4"->6"      -> content is indexed as prose; ordered steps populate after reindex.
  61020b  regulator-pit water  -> same; if the manual has no numbered steps it's a source gap (the
          post-reindex evidence run will say which). For now, return the verbatim passage + page.

============================================================================
SELF-CHECK — after you code it, VERIFY each is actually implemented (report yes/no)
============================================================================
  [ ] Every answer quotes `chunk`/`procedure_step_text` verbatim (no paraphrased steps/values).
  [ ] Every answer renders the PDF highlight from text_bbox/line_bboxes on physical_pdf_page.
  [ ] Table answers read table_row_cells and EXCLUDE is_locator_artifact / table_row_quality='noise'.
  [ ] Figure answers SHOW the figure and never state an OCR'd value as fact.
  [ ] Hazardous query with no grounded procedure/answer -> REFUSES (no generic steps).
  [ ] governing_callouts + prohibitions are shown with any procedure answer.
  [ ] Query filter always includes retrieval_eligible eq true and is_current_revision eq true.
  [ ] Chunks without procedure_id return a grounded passage+page (not fabricated steps).
  [ ] Bugs 61021, 67009, 66009, 60014 each return a grounded, cited answer end-to-end.
