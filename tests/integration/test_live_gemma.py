"""Live tests against hosted Gemma 4. Skipped unless GEMINI_API_KEY is set. Run: pytest -m live"""
import pytest

from oss_forge.ai.gemma_provider import GemmaProvider
from oss_forge.ai.schemas import Ping
from oss_forge.config import Config
from oss_forge.workflows.solve import Forge

cfg = Config.from_env()
pytestmark = [pytest.mark.live, pytest.mark.skipif(not cfg.has_api_key, reason="GEMINI_API_KEY not set")]


def test_live_inference_and_usage():
    provider = GemmaProvider(cfg.with_overrides(max_api_calls=3))
    reply = provider.generate_json("live:ping", 'Reply with {"ok": true, "message": "hello"}', Ping, max_output_tokens=200)
    usage = provider.usage()
    assert reply.ok and usage["reported_by_api"] and usage["prompt_tokens"] > 0


def test_live_solve_median_case(shopkit_repo, tmp_path):
    run_cfg = cfg.with_overrides(home=tmp_path / "home", sandbox="host", dry_run=False, max_api_calls=12)
    issue = ("Sales report shows the wrong median order value\n\nWith four orders worth 10, 20, 30 and 40 dollars "
             "`shopkit report` prints `Median order value: 30.00`, but the median is 25.00.")
    capsule = Forge(run_cfg, shopkit_repo, issue, allow_host_exec=True).solve()
    # Live model output varies: assert honesty invariants rather than a specific outcome.
    assert capsule["verification_status"] in {"VERIFIED", "PARTIALLY_VERIFIED", "FAILED", "INCONCLUSIVE"}
    if capsule["verification_status"] == "VERIFIED":
        assert capsule["verification_checks"]["regression_fails_before"]
        assert capsule["verification_checks"]["regression_passes_after"]
    assert capsule["usage"]["api_calls"] <= 12
