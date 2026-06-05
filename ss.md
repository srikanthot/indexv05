
Field names: operationalarea, functionalarea, doctype, filetype (one word, lowercase, no underscores).

Values are free-text strings set as blob user-metadata — not a fixed enum. To see what's currently in the index, run a facet query on these fields.

Common convention: functionalarea = utility line (Gas, Electric), operationalarea = sub-domain (Gas Distribution, Gas Transmission, Electric Distribution, etc.). But the split is up to your team — pick one and stay consistent.

Field names in the index are operationalarea, functionalarea, doctype, filetype — all lowercase, single word, no underscores. Add them to your select clause in the search query and they'll come back on every record, so you can render them as tags next to each citation.

Values are free-text (not a fixed enum) — set as Azure Blob user-metadata on each PDF. Typical: functionalarea = Gas / Electric, operationalarea = Gas Distribution / Gas Transmission / Electric Distribution / Electric Transmission.

If they're showing as null right now, blob metadata isn't set on those PDFs yet — that's a separate cleanup, not a frontend issue.
