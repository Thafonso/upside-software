"""The Meta gateway against recorded Graph API answers."""

from __future__ import annotations

import json
from datetime import date
from decimal import Decimal

import httpx
import pytest
import respx

from app.ads.adapters.meta_graph import (
    DEFAULT_BACKOFF_SECONDS,
    MetaGraphGateway,
    suggested_wait,
    worst_usage,
)
from app.ads.ports import AdsGatewayError, RateLimited

VERSION = "v27.0"
BASE = "https://graph.facebook.com"
ACCOUNT = "act_123456789"
INSIGHTS_URL = f"{BASE}/{VERSION}/{ACCOUNT}/insights"
CAMPAIGNS_URL = f"{BASE}/{VERSION}/{ACCOUNT}/campaigns"

INSIGHT_ROW = {
    "campaign_id": "23851",
    "campaign_name": "Leads - Batel",
    "spend": "150.37",
    "impressions": "12000",
    "reach": "9000",
    "inline_link_clicks": "310",
    "actions": [
        {"action_type": "lead", "value": "7"},
        {"action_type": "link_click", "value": "310"},
    ],
    "action_values": [{"action_type": "purchase", "value": "1990.50"}],
    "date_start": "2026-11-20",
    "date_stop": "2026-11-20",
}


@pytest.fixture
async def gateway():
    async with httpx.AsyncClient(base_url=BASE) as client:
        yield MetaGraphGateway(client, token="EAAB-token", version=VERSION)


def _usage_header(call_count: int, *, minutes: int = 0) -> str:
    return json.dumps(
        {
            "123": [
                {
                    "type": "ads_insights",
                    "call_count": call_count,
                    "total_cputime": 10,
                    "total_time": 12,
                    "estimated_time_to_regain_access": minutes,
                }
            ]
        }
    )


# ── Cabeçalho de uso ──────────────────────────────────────────


def test_worst_usage_takes_the_highest_number() -> None:
    header = json.dumps({"123": [{"call_count": 10, "total_cputime": 88, "total_time": 30}]})
    assert worst_usage(header) == 88


def test_worst_usage_survives_a_missing_or_broken_header() -> None:
    assert worst_usage(None) == 0
    assert worst_usage("") == 0
    assert worst_usage("nao e json") == 0
    assert worst_usage("[]") == 0


def test_suggested_wait_reads_minutes_and_falls_back() -> None:
    assert suggested_wait(_usage_header(90, minutes=4)) == 240
    assert suggested_wait(_usage_header(90, minutes=0)) == DEFAULT_BACKOFF_SECONDS
    assert suggested_wait(None) == DEFAULT_BACKOFF_SECONDS


# ── Insights ──────────────────────────────────────────────────


@respx.mock
async def test_daily_insights_send_the_documented_request(
    gateway: MetaGraphGateway,
) -> None:
    route = respx.get(INSIGHTS_URL).mock(
        return_value=httpx.Response(200, json={"data": [INSIGHT_ROW]})
    )

    insights = await gateway.daily_insights(ACCOUNT, date(2026, 11, 15), date(2026, 11, 21))

    request = route.calls.last.request
    params = dict(request.url.params)
    assert params["level"] == "campaign"
    assert params["time_increment"] == "1"
    assert json.loads(params["time_range"]) == {
        "since": "2026-11-15",
        "until": "2026-11-21",
    }
    assert "inline_link_clicks" in params["fields"]
    assert "action_values" in params["fields"]
    assert params["access_token"] == "EAAB-token"

    assert len(insights) == 1
    insight = insights[0]
    assert insight.day == date(2026, 11, 20)
    assert insight.campaign_id == "23851"
    assert insight.spend == Decimal("150.37")
    assert insight.impressions == 12000
    assert insight.link_clicks == 310
    assert insight.actions == {"lead": 7, "link_click": 310}
    assert insight.action_values == {"purchase": Decimal("1990.50")}


@respx.mock
async def test_insights_follow_the_paging_chain(gateway: MetaGraphGateway) -> None:
    second_page = f"{BASE}/{VERSION}/{ACCOUNT}/insights?after=cursor2"
    respx.get(INSIGHTS_URL, params={"after": "cursor2"}).mock(
        return_value=httpx.Response(
            200,
            json={"data": [{**INSIGHT_ROW, "campaign_id": "99999"}], "paging": {}},
        )
    )
    respx.get(INSIGHTS_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [INSIGHT_ROW], "paging": {"next": second_page}}
        )
    )

    insights = await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))
    assert [insight.campaign_id for insight in insights] == ["23851", "99999"]


@respx.mock
async def test_paging_stops_when_there_is_no_next(
    gateway: MetaGraphGateway,
) -> None:
    respx.get(INSIGHTS_URL).mock(
        return_value=httpx.Response(
            200, json={"data": [INSIGHT_ROW], "paging": {"cursors": {"after": "x"}}}
        )
    )
    assert len(await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))) == 1


@respx.mock
async def test_rows_without_a_date_or_campaign_are_dropped(
    gateway: MetaGraphGateway,
) -> None:
    respx.get(INSIGHTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    INSIGHT_ROW,
                    {**INSIGHT_ROW, "date_start": "ontem"},
                    {**INSIGHT_ROW, "campaign_id": ""},
                ]
            },
        )
    )
    insights = await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))
    assert len(insights) == 1


@respx.mock
async def test_an_empty_account_is_not_an_error(gateway: MetaGraphGateway) -> None:
    respx.get(INSIGHTS_URL).mock(return_value=httpx.Response(200, json={"data": []}))
    assert await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20)) == []


# ── Campanhas ─────────────────────────────────────────────────


@respx.mock
async def test_list_campaigns(gateway: MetaGraphGateway) -> None:
    respx.get(CAMPAIGNS_URL).mock(
        return_value=httpx.Response(
            200,
            json={
                "data": [
                    {
                        "id": "23851",
                        "name": "Leads - Batel",
                        "status": "ACTIVE",
                        "objective": "OUTCOME_LEADS",
                    },
                    {"id": "23852", "name": "Remarketing", "status": "PAUSED"},
                    {"name": "sem id"},
                ]
            },
        )
    )

    campaigns = await gateway.list_campaigns(ACCOUNT)
    assert [campaign.id for campaign in campaigns] == ["23851", "23852"]
    assert campaigns[0].objective == "OUTCOME_LEADS"
    assert campaigns[0].is_running is True
    assert campaigns[1].objective is None
    assert campaigns[1].is_running is False
    assert campaigns[0].account_id == ACCOUNT


# ── Limite de uso ─────────────────────────────────────────────


@respx.mock
async def test_backs_off_before_meta_starts_refusing(
    gateway: MetaGraphGateway,
) -> None:
    """At 75% of the window we stop on our own, instead of being cut off."""
    respx.get(INSIGHTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": [INSIGHT_ROW]},
            headers={"x-business-use-case-usage": _usage_header(80, minutes=3)},
        )
    )

    with pytest.raises(RateLimited) as caught:
        await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))
    assert caught.value.retry_after == 180
    assert "80%" in str(caught.value)


@respx.mock
async def test_usage_below_the_threshold_passes_through(
    gateway: MetaGraphGateway,
) -> None:
    respx.get(INSIGHTS_URL).mock(
        return_value=httpx.Response(
            200,
            json={"data": [INSIGHT_ROW]},
            headers={"x-business-use-case-usage": _usage_header(74)},
        )
    )
    assert len(await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))) == 1


@pytest.mark.parametrize("code", [17, 80004])
@respx.mock
async def test_meta_throttling_codes_become_a_wait(gateway: MetaGraphGateway, code: int) -> None:
    respx.get(INSIGHTS_URL).mock(
        return_value=httpx.Response(
            400, json={"error": {"code": code, "message": "User request limit reached"}}
        )
    )
    with pytest.raises(RateLimited) as caught:
        await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))
    assert caught.value.retry_after == DEFAULT_BACKOFF_SECONDS


@respx.mock
async def test_http_429_is_also_a_wait(gateway: MetaGraphGateway) -> None:
    respx.get(INSIGHTS_URL).mock(return_value=httpx.Response(429, json={}))
    with pytest.raises(RateLimited):
        await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))


# ── Outros erros ──────────────────────────────────────────────


@respx.mock
async def test_a_bad_token_is_a_real_error(gateway: MetaGraphGateway) -> None:
    respx.get(INSIGHTS_URL).mock(
        return_value=httpx.Response(
            400,
            json={"error": {"code": 190, "message": "Invalid OAuth access token"}},
        )
    )
    with pytest.raises(AdsGatewayError, match="Invalid OAuth"):
        await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))


@respx.mock
async def test_a_network_failure_is_reported(gateway: MetaGraphGateway) -> None:
    respx.get(INSIGHTS_URL).mock(side_effect=httpx.ConnectError("no route"))
    with pytest.raises(AdsGatewayError, match="falar com a Meta"):
        await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))


@respx.mock
async def test_a_non_json_answer_is_reported(gateway: MetaGraphGateway) -> None:
    respx.get(INSIGHTS_URL).mock(return_value=httpx.Response(200, text="<html>"))
    with pytest.raises(AdsGatewayError, match="não é JSON"):
        await gateway.daily_insights(ACCOUNT, date(2026, 11, 20), date(2026, 11, 20))
