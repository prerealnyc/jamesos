"""The price table and the usage reader — pure logic, no database.

These pin the ESTIMATE math: a wrong price here is a cap that fires at the
wrong moment for every brand at once, and a usage reader that misses one SDK's
field names is a provider that silently costs nothing."""

import pytest

from james_os import spend

pytestmark = pytest.mark.nodb


def test_token_prices_are_per_million_and_split_in_out():
    # gpt-4o-mini: $0.15 in / $0.60 out per 1M
    assert spend.estimate_tokens_usd("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
    assert spend.estimate_tokens_usd("gpt-4o-mini", 0, 1_000_000) == pytest.approx(0.60)
    assert spend.estimate_tokens_usd("gpt-4o", 1000, 1000) == pytest.approx(0.0125)


def test_longest_prefix_wins_so_mini_is_not_priced_as_4o():
    # "gpt-4o-mini" starts with "gpt-4o"; the longer key must win.
    assert spend.estimate_tokens_usd("gpt-4o-mini", 1_000_000, 0) == pytest.approx(0.15)
    # A dated Claude snapshot resolves through its family.
    assert spend.estimate_tokens_usd("claude-sonnet-4-5-20250929", 1_000_000, 0) == pytest.approx(3.0)
    assert spend.estimate_tokens_usd("claude-opus-4-7", 1_000_000, 1_000_000) == pytest.approx(30.0)


def test_unknown_model_is_unpriced_not_guessed():
    assert spend.estimate_tokens_usd("some-future-model", 1000, 1000) is None
    assert spend.estimate_tokens_usd("", 1000, 1000) is None


def test_negative_or_missing_tokens_count_as_zero():
    assert spend.estimate_tokens_usd("gpt-4o", -5, None) == 0.0


def test_image_prices_by_size_with_a_default():
    assert spend.estimate_image_usd("gpt-image-1", "1024x1024") == pytest.approx(0.167)
    assert spend.estimate_image_usd("gpt-image-1", "1024x1536") == pytest.approx(0.25)
    assert spend.estimate_image_usd("gpt-image-1", "1024x1536", n=3) == pytest.approx(0.75)
    # unknown size on a known model → that model's 'auto' figure
    assert spend.estimate_image_usd("gpt-image-1", "999x999") == pytest.approx(0.167)
    # unknown model → the default per-image figure, never zero
    assert spend.estimate_image_usd("mystery-image-model") > 0


def test_usage_reader_understands_both_sdks_and_dicts():
    class OpenAIUsage:
        prompt_tokens = 120
        completion_tokens = 30

    class AnthropicUsage:
        input_tokens = 200
        output_tokens = 50

    assert spend._usage_tokens(OpenAIUsage()) == (120, 30)
    assert spend._usage_tokens(AnthropicUsage()) == (200, 50)
    assert spend._usage_tokens({"input_tokens": 7, "output_tokens": 3}) == (7, 3)
    assert spend._usage_tokens(None) == (0, 0)
    assert spend._usage_tokens(object()) == (0, 0)


def test_job_scope_sets_and_resets():
    assert spend.current_job() is None
    with spend.job_scope("competitor_sync"):
        assert spend.current_job() == "competitor_sync"
        with spend.job_scope("inner"):
            assert spend.current_job() == "inner"
        assert spend.current_job() == "competitor_sync"
    assert spend.current_job() is None


# ------------------------------------------- 2026-10-07: caps opt-in, settable

def test_the_deployment_default_records_but_caps_nothing(monkeypatch):
    """$5 was about twenty-five image draws: active brands would have lost every
    scheduled job on deploy day under a ceiling nobody chose."""
    from james_os.config import Settings

    monkeypatch.delenv("SPEND_DAILY_CAP_USD", raising=False)
    assert Settings(_env_file=None).spend_daily_cap_usd == 0.0

