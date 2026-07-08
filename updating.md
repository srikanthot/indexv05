Hey Copilot — the deploy is FIXED and all 5 docs indexed. Now we just VERIFY the new fields
landed correctly (read-only; changes nothing). These two scripts run locally against the live
index using the config. The user has copied scripts/validate_index_quality.py and
scripts/verify_new_fields.py. Run both and paste me the full output.

# 1) The quality gates: schema completeness, table alignment, figure linkage, locator
#    suppression, applicability coverage %, procedure order, durability. Prints RESULT: PASS/FAIL.
python scripts/validate_index_quality.py --config deploy.config.json

# 2) Field-by-field fill-rate + real example values, per record type, so we can eyeball accuracy.
python scripts/verify_new_fields.py --config deploy.config.json

# 3) (optional) same field check but for ONE document, to spot-check a specific PDF:
# python scripts/verify_new_fields.py --config deploy.config.json --source-file CO-CC-GEN.pdf

# Paste me: the RESULT line + coverage from (1), and the full per-record-type table from (2).
