# For Copilot — why does REFERENCE MODE summarize only 1 chunk per section?

Reference mode returns a list of sections, each with a `short_summary`, a
`summary`, a `chunk_count`, and `citations` — no generated chat answer. Right now
each section comes back with **`chunk_count: 1`** (only one chunk summarized),
but it used to bundle 4–5 chunks per section. Find out why. **Read-only, just
report — file + function + the actual values.**

1. Which function builds the **reference-mode** response (the sections list with
   `short_summary` / `summary` / `chunk_count` / `citations`)? Not the chat-answer
   path.
2. How are the retrieved chunks **grouped into sections** — by which field
   (`section_path`, `title`, `topic_id`, `parent_id`, …)?
3. How many chunks per section does it actually summarize? Is there a **cap or
   slice that limits it to 1** (e.g. `[:1]`, `chunks[0]`, a max-per-section
   setting)? Where is that, and what is the value? This is the key question —
   explain why `chunk_count` is 1.
4. Does the section `summary` use **all** chunks in that section, or only the top
   one? What text is fed to the summarizer?
5. Does it **fetch more chunks from the same section** (parent/topic expansion,
   e.g. by `topic_id` / `section_path`), or does it only use what the top-K
   retrieval returned (so if only 1 of the 7 retrieved chunks fell in a section,
   that section shows 1)?
6. What changed vs. the older 4–5-chunk behavior — is it a lowered cap, a
   grouping change, or just that retrieval now returns fewer chunks per section?

## Report
For each: file + function + the real value. End with a one-line cause: is
`chunk_count: 1` from a hard per-section limit, from the grouping, or from
retrieval returning only one chunk per section — and what to change to get 2–3+
chunks summarized per section again.
