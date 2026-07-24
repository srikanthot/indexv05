# For Copilot — how does retrieval work when a user asks a question?

We're not getting the right chunks back in the chatbot. Go through the
chatbot/backend code and tell me EXACTLY what happens when a question is asked,
so we can see whether retrieval is set up well. **Read-only — just report, with
the file + function for each answer.**

Please answer these, in order:

1. **The flow.** When a user asks a question, trace it: does the code send the
   question straight to an LLM, or does it first query **Azure AI Search** and
   then give those results to the LLM? Show the function that does the search.

2. **What kind of search.** For that Azure AI Search call, report which of these
   it actually uses (quote the request it builds):
   - keyword / full-text (`searchText`)?
   - vector / semantic embedding (`vectorQueries` with an embedded query)?
   - **hybrid** = both together?
   - Is the **L2 semantic ranker** on (`queryType: "semantic"` + a
     `semanticConfiguration`)? Which configuration name?
   - Any `queryRewrites` / query rewriting?

3. **How many results.** What `top` (and vector `k`) does it request? And of
   those, how many chunks are actually passed to the LLM as context?

4. **Re-ranking / ordering.** After results come back, is there any re-ranking
   or re-ordering (semantic reranker score, a custom sort, RRF, dedup, pick
   top-N)? Describe how the final list handed to the LLM is chosen and ordered.

5. **Filters.** Does it apply any `$filter` (e.g. record_type, retrieval_eligible,
   is_current_revision, is_locator_artifact)? List them.

6. **Fields.** Which index fields does it search over, and which fields does it
   send to the LLM as the answer context (content, source_file, page, etc.)?

7. **Score.** Azure returns `@search.score` (and `@search.rerankerScore` when the
   semantic ranker is on). Does the code READ either of these? Does it re-sort /
   threshold by them, or does it just take Azure's order? Does the query pass a
   `scoringProfile` (e.g. `safety-boost`) and its `scoringParameters`
   (`safetytags`)? (Note: the index has scoring profiles but NO
   defaultScoringProfile, so they apply ONLY if the query names one.)

## Report format
For each item: the file + function, the ACTUAL value/setting in the code, and a
one-line verdict (e.g. "hybrid: NO — vector only", "semantic ranker: OFF",
"top: 5"). At the end, a short summary: is it hybrid + semantic + filtered, or
is something missing that would explain poor chunk retrieval?
