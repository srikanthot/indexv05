# Changelog

## 3.2.0 — Full multi-page citation support

### Code
- **`physical_pdf_pages: Collection(Edm.Int32)`** on text and table records —
  the complete sorted list of physical PDF pages the chunk covers.
  Production citation / page-navigation / highlight UIs can now resolve
  every page a chunk grounds, not just the start + end.
- `compute_page_span` returns `(start, end, pages_covered)`. Start/end
  remain backward compatible; the pages list is new.
- Trailing DI page markers are stripped from the chunk before offset
  math, so a chunk whose visible content ends on page N but whose tail
  carries a `<!-- PageBreak -->` marker is no longer mis-attributed to
  page N+1.
- Unit tests extended to 79/79: three-page span, trailing-marker
  regression, pages-list parity across single / cross-page / fully-
  internal chunks.

### Search schema
- `search/index.json`: new field `physical_pdf_pages` (filterable,
  facetable, retrievable).
- `search/skillset.json`: new output + projection for both the text
  (`/markdownDocument/*/pages/*`) and table (`/enriched_tables/*`)
  selectors.

### Validation
- `scripts/smoke_test.py` now asserts `physical_pdf_pages` is populated
  and that the list covers the declared start + end pages.

## 3.1.0 — Mandatory release gates + cloud validation

### Release gates (mandatory)
- `.github/workflows/deploy.yml` now has a `gate` job that re-runs unit
  tests, e2e simulator, ruff lint, and Bicep build on the exact SHA
  being deployed. Deploy job `needs: gate`.
- New `scripts/smoke_test.py`: post-deploy validation that runs the
  indexer, waits for success, and asserts record counts + field
  population per `record_type`. Wired as `scripts/deploy.sh --smoke`.
- New `.github/CODEOWNERS` and `.github/pull_request_template.md`
  enforce review + checklist coverage.
- New [`docs/branch-protection.md`](docs/branch-protection.md) and
  [`docs/release-gates.md`](docs/release-gates.md) document the required
  branch-protection configuration.

### Parameterization
- New Bicep params: `searchArtifactsPrefix`, `skillVersion`,
  `aoaiChatCapacity`, `aoaiEmbedCapacity`. Artifact names are emitted as
  outputs so multiple stacks can share a search service.
- `search/*.json` now templates the resource names themselves
  (`<DATASOURCE_NAME>`, `<INDEX_NAME>`, `<SKILLSET_NAME>`,
  `<INDEXER_NAME>`) alongside endpoints and keys.

### Page-span hardening
- `printed_page_label_end` now slices the chunk at the last DI page
  marker (`PageNumber` / `PageBreak`) before label extraction, instead
  of scanning the second half. Multi-page chunks now produce accurate
  end labels on manuals where labels only appear at the top of a page.

### Docs aligned with code
- `docs/validation.md` rewritten around the automated smoke test.
- `local.settings.json.example` calls out SAS as break-glass only.
- README surfaces the release-gate flow.

## 3.0.0 — Production readiness

### Security
- Managed identity is now the default auth mode (`AUTH_MODE=mi`). All four
  outbound channels — Azure OpenAI, Document Intelligence, Blob Storage,
  Azure AI Search — prefer AAD bearer tokens.
- API keys remain supported as a fallback (`AUTH_MODE=key`) for local dev.
- Azure AI Search artifacts now use identity-based auth:
  - Datasource: `ResourceId=...` connection string
  - Embedding skills + vectorizer: `apiKey` removed (search service MI)
  - `cognitiveServices` block switched to `AIServicesByIdentity`

### Infrastructure
- New `infra/main.bicep` (subscription-scoped) creates every resource the
  pipeline needs plus all RBAC role assignments.
- Parameter files per environment: `infra/parameters/{dev,prod}.bicepparam`.
- `scripts/deploy.sh` / `deploy.ps1`: one-shot deploy (infra → function code
  → search artifacts).
- `scripts/deploy_search.py` renders the four search JSONs from Bicep
  outputs and PUTs them with AAD auth; fails loud on unrendered
  placeholders.

### Observability
- Function App now wires `APPLICATIONINSIGHTS_CONNECTION_STRING` via
  Bicep; `azure-monitor-opentelemetry` added to requirements.
- Log Analytics workspace provisioned and linked to the App Insights
  component.

### CI / Release
- `.github/workflows/ci.yml`: unit tests, e2e simulator, Bicep build,
  ruff lint on every PR.
- `.github/workflows/deploy.yml`: manual-dispatch deploy with per-env
  gating (GitHub Environments).

### Bug fixes (carried from v2.3 review)
- `tables.py`: dedup repeated header row on continuation pages; per-split
  `row_count` now reflects the actual split chunk.
- `sections.py`: `extract_surrounding_text` uses `anchor.strip()`
  consistently, eliminating offset drift on whitespace.
- `pdf_crop.py`: bbox reports the post-clip rendered region; raises
  `ValueError` on inverted / out-of-page rectangles.
- `page_label.py`: `end_label` is only re-scanned when the chunk actually
  spans multiple physical pages.
- `di_client.py`: DI poll timeout lowered to 210 s (under skill timeout).
- `summary.py`: empty titles render cleanly; content cap raised to 60k
  chars to use gpt-4.1's context.
- `diagram.py`: body quotes sanitized before interpolation (prompt
  injection hardening).

### Breaking changes
- `AOAI_API_VERSION` default bumped to `2024-12-01-preview` (required for
  gpt-4.1 deployments).
- `search/datasource.json`: connection string placeholder changed from
  `<STORAGE_CONNECTION_STRING>` to `ResourceId=<STORAGE_RESOURCE_ID>;`.
- `search/skillset.json` + `search/index.json`: `<AOAI_API_KEY>` and
  `<AI_SERVICES_KEY>` are no longer substituted. Use identity.

## 2.2.0
- `chunk_id` collision fix, `table_caption` first-class, OData injection
  hardening, `ConfigError` + per-record error envelope, local e2e
  simulator.

## 2.1.0
- Multi-page text span parsing, OCR path removed, dead fields cleaned up.

## 2.0.0
- Per-figure vision, per-table markdown with multi-page merge, hash
  cache, Azure OpenAI vectorizer.

## 1.0.0
- Initial release: one vision call per PDF page; no section linking.
