import asyncio

from config import ConfigError, load_settings


async def main() -> None:
    try:
        settings = load_settings()
    except ConfigError as error:
        raise SystemExit(str(error)) from error
    print(f"processing settings loaded: {settings}")


if __name__ == "__main__":
    asyncio.run(main())