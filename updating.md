Copilot — the Jenkins pipeline failed with a long error. I don't need the whole log. Please give me a
SHORT triage summary I can pass on, exactly these 4 things:

1. WHICH pipeline + STAGE failed
   - which Jenkinsfile (Jenkinsfile.deploy = setup, or Jenkinsfile.run = nightly operate)
   - the failing stage name (the "[Pipeline] { (STAGE NAME)" line, e.g. "Bootstrap environment",
     "Load config", "Reconcile", "Preanalyze", "Run indexer", "Tests", "Lint")

2. THE ACTUAL ERROR — the last ~30 lines of that stage only, especially any line with:
   Traceback / ERROR / Exception / "az ..." error / AuthorizationFailed / Forbidden(403) /
   NotFound(404) / "not found" / "does not exist" / exit code N

3. THE COMMAND that was running when it failed (the "+ python scripts/...." or "+ az ...." line).

4. Which TARGET_ENV was selected (dev / prod) and whether the deploy.config.json credential
   (deploy-config-dev / deploy-config-prod) loaded OK.

Keep it to ~15-25 lines total. That's enough to diagnose — do NOT paste the full 2000-line log.
