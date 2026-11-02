"""Fake hosts that report metrics to the API, with flags to inject faults."""

import argparse
import asyncio
import logging
import os
import random
import time
from dataclasses import dataclass

import httpx

log = logging.getLogger("agent")

REQUEST_TIMEOUT_SECS = 5.0
MAX_STEP = 3.0

# Healthy values stay clear of the default alert rule (cpu > 90), so a healthy host
# never opens an incident by chance.
HEALTHY_RANGES = {"cpu": (10.0, 70.0), "mem": (30.0, 80.0), "disk": (40.0, 60.0)}
SPIKED_CPU_RANGE = (95.0, 99.0)


@dataclass(frozen=True)
class Config:
    api_url: str
    api_key: str
    hosts: int
    interval: float
    spike_cpu: frozenset[str]
    stop: frozenset[str]
    stop_after: float

    @property
    def host_names(self) -> list[str]:
        return [f"host-{i}" for i in range(1, self.hosts + 1)]


def parse_args(args: list[str]) -> Config:
    parser = argparse.ArgumentParser(prog="python -m agent", description=__doc__)
    parser.add_argument("--api-url", default=os.environ.get("API_URL", "http://localhost:8000"))
    parser.add_argument("--api-key", default=os.environ.get("API_KEY"), help="or env API_KEY")
    parser.add_argument("--hosts", type=int, default=5, help="number of fake hosts")
    parser.add_argument("--interval", type=float, default=2.0, help="seconds between reports")
    parser.add_argument(
        "--spike-cpu", action="append", default=[], metavar="HOST", help="pin HOST's CPU high"
    )
    parser.add_argument(
        "--stop", action="append", default=[], metavar="HOST", help="HOST stops reporting"
    )
    parser.add_argument(
        "--stop-after", type=float, default=20.0, help="seconds before --stop takes effect"
    )
    parsed = parser.parse_args(args)

    if not parsed.api_key:
        parser.error("an API key is required: use --api-key or set API_KEY")
    if parsed.hosts < 1:
        parser.error("--hosts must be at least 1")
    if parsed.interval <= 0:
        parser.error("--interval must be positive")
    if parsed.stop_after < 0:
        parser.error("--stop-after must not be negative")

    config = Config(
        api_url=parsed.api_url,
        api_key=parsed.api_key,
        hosts=parsed.hosts,
        interval=parsed.interval,
        spike_cpu=frozenset(parsed.spike_cpu),
        stop=frozenset(parsed.stop),
        stop_after=parsed.stop_after,
    )
    # A typo would otherwise inject no fault, and the demo would just look broken.
    unknown = (config.spike_cpu | config.stop) - set(config.host_names)
    if unknown:
        parser.error(f"unknown hosts {sorted(unknown)}: valid are host-1 to host-{parsed.hosts}")
    return config


def build_client(config: Config) -> httpx.AsyncClient:
    return httpx.AsyncClient(
        base_url=config.api_url,
        headers={"X-API-Key": config.api_key},
        timeout=REQUEST_TIMEOUT_SECS,
    )


class FakeHost:
    """Holds one host's metrics, each moving as a random walk within its range."""

    def __init__(self, name: str, spiked: bool, rng: random.Random) -> None:
        self.name = name
        self.rng = rng
        self.ranges = dict(HEALTHY_RANGES)
        if spiked:
            self.ranges["cpu"] = SPIKED_CPU_RANGE
        self.values = {metric: rng.uniform(*limits) for metric, limits in self.ranges.items()}

    def samples(self) -> list[dict[str, str | float]]:
        for metric, (low, high) in self.ranges.items():
            step = self.rng.uniform(-MAX_STEP, MAX_STEP)
            self.values[metric] = min(high, max(low, self.values[metric] + step))
        # No ts: the server assigns it, so clock differences can't get samples rejected.
        return [
            {"host_id": self.name, "name": metric, "value": round(value, 2)}
            for metric, value in self.values.items()
        ]


async def send(client: httpx.AsyncClient, host: FakeHost) -> bool:
    """Report once. Failures are logged and the next tick sends fresh data, so nothing is resent."""
    try:
        response = await client.post("/v1/metrics", json={"samples": host.samples()})
        response.raise_for_status()
    except httpx.HTTPError as e:
        log.warning("%s: report failed: %r", host.name, e)
        return False
    return True


async def report_loop(
    client: httpx.AsyncClient, host: FakeHost, interval: float, stop_after: float | None
) -> None:
    started = time.monotonic()
    await asyncio.sleep(host.rng.uniform(0, interval))  # don't have every host report at once
    healthy = True
    while stop_after is None or time.monotonic() - started < stop_after:
        ok = await send(client, host)
        if ok and not healthy:
            log.info("%s: reporting again", host.name)
        healthy = ok
        await asyncio.sleep(interval)
    log.info("%s: stopped reporting (fault injected)", host.name)


async def run_hosts(config: Config, client: httpx.AsyncClient) -> None:
    rng = random.Random()
    log.info(
        "reporting %d hosts every %ss to %s (spike cpu: %s, stop after %ss: %s)",
        config.hosts,
        config.interval,
        config.api_url,
        sorted(config.spike_cpu) or "none",
        config.stop_after,
        sorted(config.stop) or "none",
    )
    async with asyncio.TaskGroup() as tasks:
        for name in config.host_names:
            host = FakeHost(name, name in config.spike_cpu, rng)
            stop_after = config.stop_after if name in config.stop else None
            tasks.create_task(report_loop(client, host, config.interval, stop_after))
