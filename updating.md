# =========================
# OS files
# =========================
.DS_Store
Thumbs.db
Desktop.ini

# =========================
# Editors / IDE
# =========================
.vscode/
.idea/
*.swp
*.swo
*~

# =========================
# Environment files
# =========================
.env
.env.*
!.env.example

# =========================
# Python
# =========================
__pycache__/
*.pyc
*.pyo
.venv/
venv/
.pytest_cache/
.mypy_cache/
.ruff_cache/
.coverage
coverage/
htmlcov/

# =========================
# Node / Next.js
# =========================
node_modules/
.next/
out/
dist/
build/
*.tsbuildinfo

# =========================
# Logs
# =========================
*.log
npm-debug.log*
yarn-debug.log*
yarn-error.log*
uvicorn*.log
*.err.log

# =========================
# Local generated / testing folders
# Do not push these to GitHub
# =========================
handoff_bundle_*/
handoff_bundle*/
indexing_handoff_bundle/
reports/
evaluation/

# =========================
# Local diagnostic / testing files
# Do not push these to GitHub
# =========================
deploy.config.json
diagnose_highlight.py
index_catalog.json

# =========================
# Local temp/cache files
# =========================
tmp/
temp/
.cache/
