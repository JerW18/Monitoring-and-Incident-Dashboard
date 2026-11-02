"""The simulator: flags, value ranges, fault injection and retry."""

import asyncio
import logging
import random

import asyncpg
import httpx
import pytest
from redis.asyncio import Redis

from agent.simulator import (
    HEALTHY_RANGES,
    SPIKED_CPU_RANGE,
    Config,
    FakeHost,
    build_client,
    parse_args,
    report_loop,
    run_hosts,
)
from api.config import Settings
from api.routes.metrics import Sample
from tests.conftest import API_KEY, ClientFactory

# --- flags ---


@pytest.mark.parametrize(
    "args",
    [
        pytest.param(["--hosts", "5", "--spike-cpu", "host-9"], id="unknown-spike-host"),
        pytest.param(["--hosts", "5", "--stop", "host-6"], id="unknown-stop-host"),
        pytest.param(["--hosts", "2", "--stop", "web-1"], id="misnamed-host"),
        pytest.param(["--hosts", "0"], id="no-hosts"),
        pytest.param(["--interval", "0"], id="zero-interval"),
        pytest.param(["--stop-after", "-1"], id="negative-stop-after"),
    ],
)
def test_bad_flags_exit_with_an_error(args: list[str]) -> None:
    with pytest.raises(SystemExit) as exit_info:
        parse_args(["--api-key", "k", *args])
    assert exit_info.value.code == 2


def test_api_key_is_required(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.delenv("API_KEY", raising=False)
    with pytest.raises(SystemExit):
        parse_args([])


def test_api_key_and_url_come_from_the_environment(monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setenv("API_KEY", "from-env")
    monkeypatch.setenv("API_URL", "http://api:8000")
    config = parse_args([])
    assert (config.api_key, config.api_url) == ("from-env", "http://api:8000")


def test_faults_can_be_repeated() -> None:
    config = parse_args(
        ["--api-key", "k", "--spike-cpu", "host-1", "--spike-cpu", "host-2", "--stop", "host-3"]
    )
    assert config.spike_cpu == {"host-1", "host-2"}
    assert config.stop == {"host-3"}


# --- value ranges ---


def test_values_stay_in_range_over_a_long_walk() -> None:
    rng = random.Random(1)
    healthy = FakeHost("host-1", spiked=False, rng=rng)
    spiked = FakeHost("host-2", spiked=True, rng=rng)
    for _ in range(20_000):
        for sample in healthy.samples():
            low, high = HEALTHY_RANGES[str(sample["name"])]
            assert low <= float(sample["value"]) <= high
        cpu = next(s for s in spiked.samples() if s["name"] == "cpu")
        assert SPIKED_CPU_RANGE[0] <= float(cpu["value"]) <= SPIKED_CPU_RANGE[1]


def test_values_actually_move() -> None:
    host = FakeHost("host-1", spiked=False, rng=random.Random(1))
    assert len({str(host.samples()[0]["value"]) for _ in range(50)}) > 10


def test_samples_pass_the_api_validation() -> None:
    for sample in FakeHost("host-1", spiked=True, rng=random.Random(1)).samples():
        Sample.model_validate(sample)


# --- against the real API, Postgres and Redis ---


async def run_for(seconds: float, config: Config, client: httpx.AsyncClient) -> None:
    """The hosts report forever, so stop them from the outside."""
    with pytest.raises(TimeoutError):
        async with asyncio.timeout(seconds):
            await run_hosts(config, client)


async def test_faults_show_up_in_the_stored_metrics(
    settings: Settings, make_client: ClientFactory, db: asyncpg.Pool, redis_client: Redis
) -> None:
    config = parse_args(
        [
            "--api-key",
            API_KEY,
            "--hosts",
            "3",
            "--interval",
            "0.05",
            "--spike-cpu",
            "host-3",
            "--stop",
            "host-2",
            "--stop-after",
            "0.3",
        ]
    )
    async with make_client(settings) as client:
        client.headers["X-API-Key"] = API_KEY
        await run_for(1.2, config, client)

    rows = await db.fetch(
        """
        SELECT host_id, count(*) AS samples, max(ts) AS last_ts, array_agg(DISTINCT name) AS names,
               min(value) FILTER (WHERE name = 'cpu') AS min_cpu,
               max(value) FILTER (WHERE name = 'cpu') AS max_cpu
        FROM metrics GROUP BY host_id
        """
    )
    hosts = {r["host_id"]: r for r in rows}
    assert set(hosts) == {"host-1", "host-2", "host-3"}
    assert all(set(r["names"]) == {"cpu", "mem", "disk"} for r in rows)

    # Spiked host high, healthy hosts below the alert threshold.
    assert hosts["host-3"]["min_cpu"] >= SPIKED_CPU_RANGE[0]
    assert hosts["host-1"]["max_cpu"] <= HEALTHY_RANGES["cpu"][1]

    # The stopped host ended early and the others kept going.
    assert hosts["host-2"]["samples"] < hosts["host-1"]["samples"]
    assert (hosts["host-1"]["last_ts"] - hosts["host-2"]["last_ts"]).total_seconds() > 0.3


async def test_keeps_reporting_after_failures(caplog: pytest.LogCaptureFixture) -> None:
    caplog.set_level(logging.INFO, logger="agent")
    calls = 0

    def flaky_server(request: httpx.Request) -> httpx.Response:
        nonlocal calls
        calls += 1
        if calls == 1:
            raise httpx.ConnectError("api is down")
        if calls <= 3:
            return httpx.Response(503)
        return httpx.Response(202, json={"accepted": 3})

    transport = httpx.MockTransport(flaky_server)
    async with httpx.AsyncClient(transport=transport, base_url="http://test") as client:
        host = FakeHost("host-1", spiked=False, rng=random.Random(1))
        with pytest.raises(TimeoutError):
            async with asyncio.timeout(0.5):
                await report_loop(client, host, interval=0.02, stop_after=None)
    assert calls > 3
    # Both the connection error and the rejected requests are reported, then the recovery.
    messages = [record.getMessage() for record in caplog.records]
    assert sum("report failed" in m for m in messages) == 3
    assert sum("reporting again" in m for m in messages) == 1


def test_client_sends_the_api_key() -> None:
    config = parse_args(["--api-key", "secret", "--api-url", "http://api:8000"])
    client = build_client(config)
    assert client.headers["X-API-Key"] == "secret"
    assert str(client.base_url).startswith("http://api:8000")
