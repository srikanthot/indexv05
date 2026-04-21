# Figure out where the app is extracted
ps aux | grep -E "python|gunicorn" | head -3
ls -la /tmp/ | head
ls -la /tmp/8de95d1f455c0fb/ 2>/dev/null | head -20
find /tmp -maxdepth 3 -name ".env" 2>/dev/null
find /home -name ".env" 2>/dev/null

env | grep -E "^DEBUG_MODE|^APPSETTING_DEBUG_MODE|^ENTRA|^JWT_AUDIENCE|^DEFAULT_LOCAL|^WEBSITE_AUTH" | sort



cd /tmp/8de95d1f455c0fb 2>/dev/null || cd $(ps -eo cmd | grep -o '/tmp/[a-f0-9]*' | head -1)
python3 -c "
import os
from app.config.settings import DEBUG_MODE, ENTRA_TENANT_ID, JWT_AUDIENCE, DEFAULT_LOCAL_USER_ID
print('DEBUG_MODE =', DEBUG_MODE)
print('ENTRA_TENANT_ID =', ENTRA_TENANT_ID)
print('JWT_AUDIENCE =', JWT_AUDIENCE)
print('DEFAULT_LOCAL_USER_ID =', repr(DEFAULT_LOCAL_USER_ID))
print('os.getenv DEBUG_MODE =', repr(os.getenv('DEBUG_MODE')))
print('os.getenv APPSETTING_DEBUG_MODE =', repr(os.getenv('APPSETTING_DEBUG_MODE')))
"
