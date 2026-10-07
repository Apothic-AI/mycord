"""Local IRC protocol integration tests using the real pydle client."""

import asyncio
from collections.abc import AsyncIterator

import pytest
import pytest_asyncio

from mycord_irc_repl.config import IRCConfig
from mycord_irc_repl.session import ReplSession


@pytest_asyncio.fixture
async def local_irc_server() -> AsyncIterator[tuple[str, int]]:
    """Provide a tiny loopback IRC server; no outside network is contacted."""
    async def handle_client(reader: asyncio.StreamReader, writer: asyncio.StreamWriter) -> None:
        nickname = "test-agent"
        try:
            while line := await reader.readline():
                command = line.decode("utf-8").strip()
                if command.startswith("NICK "):
                    nickname = command.split(" ", maxsplit=1)[1]
                elif command.startswith("CAP LS"):
                    writer.write(b":local CAP * LS :\r\n")
                    await writer.drain()
                elif command.startswith("USER "):
                    writer.write(f":local 001 {nickname} :Welcome\r\n".encode())
                    await writer.drain()
                elif command.startswith("WHOIS "):
                    target = command.split(" ", maxsplit=1)[1]
                    writer.write(
                        f":local 311 {nickname} {target} user localhost * :Test User\r\n".encode()
                    )
                    writer.write(
                        f":local 318 {nickname} {target} :End of WHOIS\r\n".encode()
                    )
                    await writer.drain()
                elif command.startswith("JOIN "):
                    channel = command.split(" ", maxsplit=1)[1]
                    writer.write(f":{nickname}!user@localhost JOIN :{channel}\r\n".encode())
                    writer.write(
                        f":alice!alice@localhost PRIVMSG {channel} :"
                        "hello from local IRC\r\n".encode()
                    )
                    await writer.drain()
                elif command.startswith("QUIT"):
                    break
        finally:
            writer.close()
            await writer.wait_closed()

    server = await asyncio.start_server(handle_client, host="127.0.0.1", port=0)
    address = server.sockets[0].getsockname()
    async with server:
        yield "127.0.0.1", int(address[1])
    server.close()
    await server.wait_closed()


@pytest.mark.asyncio
async def test_connect_join_and_buffer_live_message(
    local_irc_server: tuple[str, int],
) -> None:
    host, port = local_irc_server
    session = ReplSession(
        IRCConfig(
            server=host,
            port=port,
            nickname="test-agent",
            tls=False,
        )
    )

    try:
        assert await session.start(wait=2.0) is True
        cursor = session.client.latest_event_sequence
        await session.client.join("#test")
        event = await session.wait_event("channel_message", timeout=2.0, after=cursor)

        assert any(
            item["type"] == "connected" for item in session.client.recent_events
        )
        assert event["channel"] == "#test"
        assert event["nick"] == "alice"
        assert event["text"] == "hello from local IRC"
        assert session.connected is True
    finally:
        await session.close()
