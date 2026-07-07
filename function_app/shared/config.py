"""
Centralized environment-variable access with explicit configuration errors.
 
Helpers in this module never raise KeyError mid-request. They raise a
typed ConfigError that the skill envelope (skill_io.handle_skill_request)
will translate into a per-record error with a clear message instead of a
500.
"""
 
import os
 
 
class ConfigError(RuntimeError):
    """Raised when a required environment variable is missing or empty."""
 
 
def required_env(name: str) -> str:
    val = os.environ.get(name, "").strip()
    if not val:
        raise ConfigError(
            f"missing required environment variable: {name}. "
            f"Set this on the Function App application settings."
        )
    return val
 
 
def optional_env(name: str, default: str = "") -> str:
    return (os.environ.get(name) or default).strip()
 
 
def feature_enabled(*names: str) -> bool:
    """True if every named env var is present and non-empty. Used to
    gate optional features (e.g. image-hash cache lookup) so they
    silently no-op when not configured."""
    for n in names:
        if not (os.environ.get(n) or "").strip():
            return False
    return True
 
 
def index_run_id() -> str:
    """Current indexing run ID. Set by the pipeline orchestrator as an
    app setting before triggering the indexer. Allows filtering/grouping
    records by run for promotion/rollback.
 
    Fallback: if not explicitly set, generates a timestamp-based ID so
    records are always attributable to a run. Operators should prefer
    setting INDEX_RUN_ID explicitly (e.g., jenkins-main-42) for clarity.
    """
    val = optional_env("INDEX_RUN_ID", "")
    if val:
        return val
    # Auto-generate from UTC timestamp so the field is never empty.
    import datetime as _dt
    return f"auto-{_dt.datetime.utcnow().strftime('%Y%m%dT%H%M%S')}"


def model_gen_kwargs(default_max_tokens: int) -> dict:
    """Generation kwargs that are SAFE for GPT-5.1 / reasoning models.

    Why this exists: GPT-5.x (and the o-series) are reasoning models. They
    REJECT any `temperature` other than the default (1) with HTTP 400
    ("Unsupported value: 'temperature' ... only the default (1) is
    supported"). The previous code hardcoded `temperature=0.0`/`0.1`, which
    made every vision/summary call 400 after the Foundry/GPT-5.1 migration.

    Policy:
      * temperature is OMITTED by default (works on reasoning models). An
        operator running a CLASSIC (non-reasoning) deployment can restore
        determinism by setting AOAI_TEMPERATURE (e.g. "0").
      * max_completion_tokens defaults generously because on reasoning
        models the hidden reasoning tokens are drawn from THIS budget --
        too small a value leaves no room for the visible JSON answer
        (empty content -> parse failure). Override via
        AOAI_MAX_COMPLETION_TOKENS.
      * reasoning_effort is sent only if AOAI_REASONING_EFFORT is set
        (e.g. "low"/"minimal"). Strongly recommended for extraction to
        cut latency/cost -- but left opt-in so an api-version/model that
        doesn't accept it can't 400. Do NOT default it blindly.

    Returns a dict to splat into client.chat.completions.create(**kwargs)
    or into a raw REST body.
    """
    kwargs: dict = {}
    raw_max = optional_env("AOAI_MAX_COMPLETION_TOKENS", "")
    try:
        kwargs["max_completion_tokens"] = int(raw_max) if raw_max else default_max_tokens
    except ValueError:
        kwargs["max_completion_tokens"] = default_max_tokens
    temp = optional_env("AOAI_TEMPERATURE", "")
    if temp != "":
        try:
            kwargs["temperature"] = float(temp)
        except ValueError:
            pass
    effort = optional_env("AOAI_REASONING_EFFORT", "")
    if effort:
        kwargs["reasoning_effort"] = effort
    return kwargs
 