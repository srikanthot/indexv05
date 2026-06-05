
Field names: operationalarea, functionalarea, doctype, filetype (one word, lowercase, no underscores).

Values are free-text strings set as blob user-metadata — not a fixed enum. To see what's currently in the index, run a facet query on these fields.

Common convention: functionalarea = utility line (Gas, Electric), operationalarea = sub-domain (Gas Distribution, Gas Transmission, Electric Distribution, etc.). But the split is up to your team — pick one and stay consistent.
