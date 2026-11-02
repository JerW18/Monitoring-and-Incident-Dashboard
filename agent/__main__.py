import asyncio
import logging
import sys

from agent.simulator import build_client, parse_args, run_hosts


async def run(args: list[str]) -> None:
    config = parse_args(args)
    async with build_client(config) as client:
        await run_hosts(config, client)


def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
    logging.getLogger("httpx").setLevel(logging.WARNING)  # it logs every request at INFO
    try:
        asyncio.run(run(sys.argv[1:]))
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
