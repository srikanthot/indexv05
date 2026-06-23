Continue from your final post-fix readiness report.

I reviewed your report. It says the indexing pipeline is “production-ready with warnings,” 104/104 tests passed, and all original P0 issues are addressed. Before I accept this as final, I want to close or explicitly validate the remaining PARTIAL / NEEDS VERIFICATION items only.

Do not restart the audit.
Do not repeat the full report.
Do not make broad refactors.
Focus only on the remaining partials/warnings/verification items from your final report.

Please handle these final items one by one:

1. Rotated/landscape bbox accuracy

* You marked this as NEEDS VERIFICATION.
* Please identify how to validate this with real sample PDFs.
* If there is already a diagnostic command, provide it.
* If a small safe test/script improvement is needed, add it.
* Final output should say whether rotated/landscape bbox is PASS / PARTIAL / FAIL / NEEDS SAMPLE.

2. INDEX_RUN_ID propagation

* You said INDEX_RUN_ID must be set by operator.
* Please verify where it is read in code.
* Please document exactly how Jenkins should set it.
* If safe, add a fallback auto-generated run id when env var is missing.
* Confirm whether missing INDEX_RUN_ID is still a risk after fallback.

3. Source hash edge case

* You marked source_hash comparison during reconcile for same-size/different-content edge case as NEEDS VERIFICATION.
* Please add or run a test proving that two PDFs with same filename/size but different content are detected by hash, not timestamp/size only.
* If the code currently depends on blob modified time first, verify full hash re-comparison behavior.
* Final output should say PASS / PARTIAL / FAIL.

4. Pagination beyond 1000 records

* You marked pagination >1000 as PARTIAL or future risk.
* Please inspect check_index.py, auto_heal.py, validate_index.py, reconcile.py, and any scripts reading Azure Search.
* If any still uses top=1000 without continuation/paging, either fix it or document exactly why it is safe.
* Since document count will grow, I prefer this closed now if it is a small safe change.

5. Active/staged promotion hard gate

* You marked active/staged promotion as PARTIAL.
* Coverage gate blocks pipeline, but active records are not automatically deactivated.
* Please explain whether this is acceptable for current rollout.
* If a full active/staged model is too risky now, document the safe rollout rule:

  * keep old index untouched
  * create new index for schema release
  * reindex into new index
  * validate_index --strict
  * only then switch target/alias/config
* If there is a small safe code/doc update needed, apply it.

6. Row-level bbox for table_row

* You marked table_row bbox as PARTIAL because it inherits table bbox.
* Please verify this is documented clearly.
* Confirm that table_row citation will highlight table-level area, not exact row.
* Mark this as accepted P1 if exact row bbox requires DI cell polygons.
* Make sure coverage gate does not treat missing row-level bbox as blocking unless row-level bbox is explicitly required.

7. Line-level highlight precision

* You marked line-level highlight rectangles as P1.
* Please confirm current paragraph/chunk bbox is page-constrained and avoids distant-page false highlights.
* Confirm remaining line-level precision is an accuracy improvement, not a data-loss or wrong-page issue.

8. Embedded image extraction

* You marked embedded images missed by DI as PARTIAL/counted but not extracted.
* Please confirm that manifest/coverage reports this clearly.
* Mark full secondary image extraction as P1/P2 unless critical images are currently missing from sample docs.

9. Table image restructuring

* You marked table_image as detected but not converted into structured table rows.
* Please confirm table_image detection is reported.
* Mark structured extraction as P1 unless required by current production documents.

10. Final go/no-go update
    After checking the above, update only the final readiness section.

Please provide:

A. Items closed now
B. Items still partial but accepted as P1/P2
C. Items that still block production, if any
D. Commands I should run now
E. Jenkins/env changes needed
F. Whether index rebuild is required
G. Whether full reindex is required
H. Final go/no-go verdict

Do not run destructive production Azure actions.
If any validation requires real PDFs or Jenkins access, give me the exact command/step to run manually.
