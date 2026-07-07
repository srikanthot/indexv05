# placeholder — paste real content from office laptop
from __future__ import annotations
 
import json
from collections import Counter, defaultdict
from datetime import UTC, datetime
from pathlib import Path
 
import httpx
from azure.identity import DefaultAzureCredential
 
API_VERSION = "2024-05-01-preview"
SCOPE = "https://search.azure.us/.default"
 
 
def _token() -> str:
    return DefaultAzureCredential().get_token(SCOPE).token
 
 
def main() -> int:
    root = Path('.')
    cfg = json.loads((root / 'deploy.config.json').read_text(encoding='utf-8'))
    endpoint = cfg['search']['endpoint'].rstrip('/')
    index_name = f"{cfg['search'].get('artifactPrefix') or 'mm-manuals'}-index"
 
    search_url = f"{endpoint}/indexes/{index_name}/docs/search?api-version={API_VERSION}"
    index_url = f"{endpoint}/indexes/{index_name}?api-version={API_VERSION}"
 
    token = _token()
    headers = {
        'Content-Type': 'application/json',
        'Authorization': f'Bearer {token}',
    }
 
    # Discover retrievable fields so select cannot 400.
    idx = httpx.get(index_url, headers=headers, timeout=60.0)
    idx.raise_for_status()
    fields = idx.json().get('fields', [])
    retrievable = {f.get('name') for f in fields if f.get('retrievable') is True}
 
    select_candidates = [
        'chunk_id', 'record_type', 'source_file', 'chunk', 'processing_status',
        'physical_pdf_page', 'physical_pdf_page_end', 'physical_pdf_pages',
        'page_resolution_method', 'retrieval_eligible',
    ]
    select_fields = [f for f in select_candidates if f in retrievable]
 
    # Get true total + source partitions. This avoids Azure skip<=100000 limit.
    meta_body = {
        'search': '*',
        'top': 0,
        'count': True,
        'facets': ['source_file,count:200'],
    }
    m = httpx.post(search_url, json=meta_body, headers=headers, timeout=120.0)
    m.raise_for_status()
    meta = m.json()
    service_total = int(meta.get('@odata.count') or 0)
    source_files = [
        x.get('value') for x in (meta.get('@search.facets', {}).get('source_file') or [])
        if x.get('value')
    ]
 
    records = 0
    counts_by_type = Counter()
    status_counts = Counter()
    page_list_len = Counter()
    page_span_len = Counter()
 
    anomaly_counts = Counter()
    anomaly_by_type: dict[str, Counter] = defaultdict(Counter)
    examples: dict[str, list] = defaultdict(list)
 
    def add_example(key: str, obj: dict, limit: int = 10) -> None:
        if len(examples[key]) < limit:
            examples[key].append(obj)
 
    for sf in source_files:
        safe_sf = str(sf).replace("'", "''")
        skip = 0
        top = 1000
 
        while True:
            body = {
                'search': '*',
                'filter': f"source_file eq '{safe_sf}'",
                'select': ','.join(select_fields),
                'top': top,
                'skip': skip,
            }
            r = httpx.post(search_url, json=body, headers=headers, timeout=120.0)
            r.raise_for_status()
            vals = r.json().get('value', [])
            if not vals:
                break
 
            for rec in vals:
                records += 1
                rt = rec.get('record_type') or 'NULL'
                counts_by_type[rt] += 1
                status_counts[rec.get('processing_status') or 'NULL'] += 1
 
                p = rec.get('physical_pdf_page')
                pe = rec.get('physical_pdf_page_end')
                plist = rec.get('physical_pdf_pages')
                plist = plist if isinstance(plist, list) else []
 
                if plist:
                    page_list_len[len(plist)] += 1
                if isinstance(p, int) and isinstance(pe, int):
                    span = pe - p + 1
                    if span > 0:
                        page_span_len[span] += 1
 
                # Consistency checks.
                if plist:
                    pmin = min(plist)
                    pmax = max(plist)
                    uniq = sorted(set(plist))
                    contiguous = uniq == list(range(pmin, pmax + 1))
 
                    if isinstance(p, int) and p != pmin:
                        anomaly_counts['physical_page_not_min_of_list'] += 1
                        anomaly_by_type[rt]['physical_page_not_min_of_list'] += 1
                        add_example('physical_page_not_min_of_list', {
                            'chunk_id': rec.get('chunk_id'),
                            'record_type': rt,
                            'physical_pdf_page': p,
                            'physical_pdf_pages': plist[:10],
                        })
 
                    if isinstance(pe, int) and pe != pmax:
                        anomaly_counts['physical_page_end_not_max_of_list'] += 1
                        anomaly_by_type[rt]['physical_page_end_not_max_of_list'] += 1
                        add_example('physical_page_end_not_max_of_list', {
                            'chunk_id': rec.get('chunk_id'),
                            'record_type': rt,
                            'physical_pdf_page_end': pe,
                            'physical_pdf_pages': plist[:10],
                        })
 
                    if not contiguous:
                        anomaly_counts['physical_pdf_pages_non_contiguous'] += 1
                        anomaly_by_type[rt]['physical_pdf_pages_non_contiguous'] += 1
                        add_example('physical_pdf_pages_non_contiguous', {
                            'chunk_id': rec.get('chunk_id'),
                            'record_type': rt,
                            'physical_pdf_pages': plist[:10],
                        })
 
                # Suspicious page span checks.
                list_len = len(plist)
                if list_len >= 4:
                    anomaly_counts['pages_list_len_ge_4'] += 1
                    anomaly_by_type[rt]['pages_list_len_ge_4'] += 1
                    add_example('pages_list_len_ge_4', {
                        'chunk_id': rec.get('chunk_id'),
                        'record_type': rt,
                        'source_file': rec.get('source_file'),
                        'pages': plist[:10],
                        'chunk_preview': (rec.get('chunk') or '')[:180],
                    })
                if list_len >= 5:
                    anomaly_counts['pages_list_len_ge_5'] += 1
                    anomaly_by_type[rt]['pages_list_len_ge_5'] += 1
 
                if isinstance(p, int) and isinstance(pe, int):
                    span = pe - p + 1
                    if span >= 4:
                        anomaly_counts['page_span_ge_4'] += 1
                        anomaly_by_type[rt]['page_span_ge_4'] += 1
                    if span >= 5:
                        anomaly_counts['page_span_ge_5'] += 1
                        anomaly_by_type[rt]['page_span_ge_5'] += 1
                    if span <= 0:
                        anomaly_counts['invalid_page_span_nonpositive'] += 1
                        anomaly_by_type[rt]['invalid_page_span_nonpositive'] += 1
                        add_example('invalid_page_span_nonpositive', {
                            'chunk_id': rec.get('chunk_id'),
                            'record_type': rt,
                            'physical_pdf_page': p,
                            'physical_pdf_page_end': pe,
                        })
 
                # Required page mapping for core chunk types.
                if rt in {'text', 'diagram', 'table', 'table_row'}:
                    if not isinstance(p, int):
                        anomaly_counts['missing_physical_pdf_page_required_type'] += 1
                        anomaly_by_type[rt]['missing_physical_pdf_page_required_type'] += 1
                        add_example('missing_physical_pdf_page_required_type', {
                            'chunk_id': rec.get('chunk_id'),
                            'record_type': rt,
                            'physical_pdf_page': p,
                            'physical_pdf_pages': plist[:10],
                            'page_resolution_method': rec.get('page_resolution_method'),
                        })
                    if not plist:
                        anomaly_counts['missing_physical_pdf_pages_required_type'] += 1
                        anomaly_by_type[rt]['missing_physical_pdf_pages_required_type'] += 1
                        add_example('missing_physical_pdf_pages_required_type', {
                            'chunk_id': rec.get('chunk_id'),
                            'record_type': rt,
                            'physical_pdf_page': p,
                            'page_resolution_method': rec.get('page_resolution_method'),
                        })
 
            if len(vals) < top:
                break
            skip += top
 
    summary = {
        'generated_at': datetime.now(UTC).strftime('%Y-%m-%dT%H:%M:%SZ'),
        'index_name': index_name,
        'total_records_scanned': records,
        'total_records_reported_by_service': service_total,
        'full_coverage': records == service_total,
        'counts_by_record_type': dict(counts_by_type),
        'processing_status_counts': dict(status_counts),
        'page_list_length_distribution': dict(sorted(page_list_len.items())),
        'page_span_length_distribution': dict(sorted(page_span_len.items())),
        'anomaly_counts': dict(anomaly_counts),
        'anomaly_counts_by_record_type': {k: dict(v) for k, v in anomaly_by_type.items()},
        'examples': dict(examples),
    }
 
    out_json = root / 'reports' / 'page_coordinate_audit.json'
    out_md = root / 'reports' / 'page_coordinate_audit.md'
    out_json.write_text(json.dumps(summary, indent=2, ensure_ascii=False), encoding='utf-8')
 
    lines: list[str] = []
    lines.append('# Page Coordinate Audit (Full Corpus)')
    lines.append('')
    lines.append(f"Generated at: {summary['generated_at']}")
    lines.append(f"Index: {index_name}")
    lines.append(f"Total scanned: {records}")
    lines.append(f"Service total: {service_total}")
    lines.append(f"Full coverage: {records == service_total}")
    lines.append('')
    lines.append('## Anomaly counts')
    if summary['anomaly_counts']:
        for k, v in summary['anomaly_counts'].items():
            lines.append(f"- {k}: {v}")
    else:
        lines.append('- {}')
    lines.append('')
    lines.append('## Page list length distribution')
    for k, v in summary['page_list_length_distribution'].items():
        lines.append(f"- len={k}: {v}")
    lines.append('')
    lines.append('## Page span length distribution')
    for k, v in summary['page_span_length_distribution'].items():
        lines.append(f"- span={k}: {v}")
 
    out_md.write_text('\n'.join(lines), encoding='utf-8')
 
    print(f'Index: {index_name}')
    print(f'Total scanned: {records}')
    print(f'Service total: {service_total}')
    print(f'Full coverage: {records == service_total}')
    print('Wrote reports/page_coordinate_audit.json')
    print('Wrote reports/page_coordinate_audit.md')
    return 0
 
 
if __name__ == '__main__':
    raise SystemExit(main())
 
 