We re-indexed using the new index, but the same issue still appears in the chatbot.

Issue:
User asks: “Give me UEOC 24hr checklist as per SDR.”

Expected:
It should retrieve and cite the **UEOC and Division 24-HOUR CHECKLIST** table, not the 12-hour checklist.

Observed:
The answer/citation is still sometimes pointing to the earlier/first checklist table, such as the **UEOC and Division 12-HOUR CHECKLIST**, even though the 24-hour checklist exists later in the PDF.

Also citation highlighting is still not perfect:

* some citations highlight only part of the text
* some highlight only one/two lines
* some table/image highlights are shifted or incomplete
* yellow highlight should cover the actual cited chunk/table area clearly

Please debug the indexing side only.

Do not restart the full audit. Focus on this exact issue.

Check in the index for this source PDF:

* records containing “UEOC and Division 12-HOUR CHECKLIST”
* records containing “UEOC and Division 24-HOUR CHECKLIST”
* records containing “24-hour checklist”
* records containing “12-hour checklist”
* table records
* table_row records
* table_image/diagram records if applicable

For each matching record, report:

* chunk_id / id
* record_type
* source_file
* page_start / page_end
* printed_page_label if available
* table_cluster_id
* table_split_index
* table_split_count
* table_caption / table_title
* row_index if table_row
* chunk_for_semantic or searchable text preview
* bbox / table_bbox / figure_bbox / text_bbox
* whether bbox is page-constrained
* whether 12-hour and 24-hour tables have distinct metadata

Please verify:

1. Does the 24-hour checklist have its own searchable table/table_row records?
2. Does the 24-hour checklist record explicitly contain “24-hour” in searchable text/semantic field?
3. Does the 12-hour checklist record explicitly contain “12-hour”?
4. Are 12-hour and 24-hour records accidentally merged into the same table_cluster_id?
5. Are page numbers correct for both tables?
6. Are citations/bbox correct for the 24-hour table?
7. Are table_row records created for the 24-hour checklist rows?
8. Is the table title/header/caption included in every split/table_row record?
9. Is the bbox for the 24-hour table covering the full table area?
10. If the citation highlight is partial, is the indexed bbox too small or is the downstream renderer likely misusing it?

If indexing data is correct, say clearly:
“Indexing side is correct; backend retrieval/ranking should be fixed.”

If indexing data is wrong, fix only the indexing-side issue and report:

* files changed
* what changed
* whether reindex is required
* exact validation command to prove 24-hour records exist separately from 12-hour records



We re-indexed with the new index, but the chatbot still gives the wrong citation/source for this query.

Issue:
User asks: “Give me UEOC 24hr checklist as per SDR.”

Expected:
The chatbot should retrieve and answer from the **UEOC and Division 24-HOUR CHECKLIST** table.

Observed:
It still sometimes retrieves/cites the **UEOC and Division 12-HOUR CHECKLIST** or the first similar checklist table instead of the 24-hour table.

This looks like a retrieval/ranking/citation-selection issue, not only an indexing issue.

Please debug the backend retrieval/citation flow for this exact query.

Do not do a broad refactor. Focus on the query and citation mismatch.

Please trace:

1. Raw user query
2. Rewritten query, if any
3. Search query sent to Azure AI Search
4. Vector query, if any
5. Semantic query, if any
6. Filters used
7. top/k values
8. Search results returned before rerank
9. Search results after semantic rerank/reranker
10. Final chunks selected for LLM context
11. Final citations sent to UI

For each retrieved result, log/report:

* rank
* score
* reranker_score if available
* chunk_id
* record_type
* source_file
* page_start/page_end
* table_cluster_id
* table_title/caption
* searchable text preview
* whether it contains “24-hour”
* whether it contains “12-hour”

Please verify:

1. Is the backend treating “24hr”, “24-hour”, “24 hour”, and “24 hours” as the same exact constraint?
2. Is it protecting the exact duration “24” before semantic/vector search?
3. Is it demoting records containing “12-hour” when the query asks “24-hour”?
4. Is it boosting exact matches where table title/caption contains “24-HOUR CHECKLIST”?
5. Is it fetching table_row + parent table + table_cluster records correctly?
6. Are citations built only from selected final evidence chunks?
7. Is the UI showing old/stale citations from previous answer?
8. Is the answer using one set of chunks while citations display another set?
9. Is the backend returning bbox/page metadata exactly as indexed?
10. Does the citation payload include page_start/page_end, bbox, record_type, table_cluster_id, and source_file?

Expected fix direction:

* Add exact constraint handling for duration terms:

  * 24hr
  * 24-hour
  * 24 hour
  * 24 hours
* For this query, require or strongly boost records containing “24-hour” / “24 hour”.
* Demote or exclude “12-hour” records unless the user asks for comparison.
* Prefer table/table_row records with matching title/caption over semantically similar earlier checklist.
* Ensure final citations match the actual chunks used in answer generation.

Also check citation highlight issue:
If backend payload bbox is correct but frontend highlight is off, say:
“Backend payload is correct; frontend/PDF viewer coordinate rendering needs fix.”

If backend payload is wrong or incomplete, fix citation payload construction.

Final output:

* root cause: indexing / backend retrieval / citation payload / frontend rendering
* files changed
* exact debug logs added
* exact test query result before/after
* expected citation should point to 24-hour checklist page, not 12-hour checklist
