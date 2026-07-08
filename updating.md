Hey Copilot — I fixed the ROOT CAUSE of the function-deploy failure in
scripts/deploy_function.ps1 (the script the super command calls). The old version ran
`& func ... 2>&1` under `$ErrorActionPreference='Stop'`, which in PowerShell 5.1 turned a
benign func stderr line into the terminating "No process is associated with this object"
error and aborted a publish that was fine. The fixed version runs func through cmd.exe,
retries transient upload failures, prints the Oryx build log if it truly fails, and writes
Foundry-correct app settings.

The user has re-copied the fixed scripts/deploy_function.ps1. Now just run the ONE super
command and let it go line by line. Run in PowerShell. Do NOT set
`$ErrorActionPreference='Stop'` around it. Paste me the tail of the output (especially the
"Publishing function code" section and the final coverage), and the STEP-VALIDATE result.

# The super command -- RBAC + function code deploy + preanalyze + search artifacts + reindex + heal
python scripts/deploy.py --config deploy.config.json --auto-fix

# After it finishes, the quality gates:
python scripts/validate_index_quality.py --config deploy.config.json

# If the function publish still fails, the script now prints the Oryx BUILD LOG automatically --
# paste me that build log and I'll fix the exact error it shows.
