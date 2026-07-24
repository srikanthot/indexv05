# For Copilot — check how the chatbot retrieves (read-only, just report)

In the chatbot backend, find the function that queries Azure AI Search and tell
me, with the file + function and the actual values:

1. Does the request set `queryType: "semantic"` (semantic ranker ON) and which
   `semanticConfiguration`? (yes / no + the value)
2. Is it **hybrid** — does it send BOTH `vectorQueries` (an embedded question)
   AND `search` text, or only one of them?
3. What `top` does it request, and how many chunks does it finally pass to the
   LLM?
4. Does it read `@search.rerankerScore` / `@search.score` and sort or threshold
   by it, or does it just take Azure's order?
5. Does it apply any `$filter` or a `scoringProfile` (e.g. `safety-boost`)?

Just report what the code does — don't change anything.
