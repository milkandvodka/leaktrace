"""Watching Telegram for leaks.

With the operator's own Telegram account and API credentials (my.telegram.org), every new video or audio post in
the watched channels and groups is downloaded, traced once, and recorded; identified and narrowed results can be
sent to a webhook. The account sees only what it has joined, like any member would.

Run: python -m leaktrace watch @channel_one https://t.me/+invite ...
Needs LEAKTRACE_TG_API_ID and LEAKTRACE_TG_API_HASH; the first run asks for the phone number and login code in
the terminal (Telethon keeps the session in the data folder afterwards).
"""
import json
import os
import urllib.request
from dataclasses import dataclass
from pathlib import Path
from typing import Callable

from .config import settings
from .store import Store

TRACEABLE = {"video", "audio", "video_document"}


@dataclass
class Message:
    chat: str
    id: int
    date: str            # ISO time it was posted: the leak's "first seen"
    kind: str            # video | audio | video_document | photo | text | other
    size: int            # bytes
    link: str
    download: Callable[[Path], Path]


class Watcher:
    def __init__(self, store: Store, folder: Path, tracer: Callable | None = None, max_mb: int = 2000,
                 webhook: str | None = None):
        from .trace import trace
        self.store, self.folder = store, Path(folder)
        self.folder.mkdir(parents=True, exist_ok=True)
        self.tracer = tracer or trace
        self.max_bytes = max_mb * 1024 * 1024
        self.webhook = webhook if webhook is not None else os.getenv("LEAKTRACE_WEBHOOK")

    def handle(self, msg: Message):
        """Trace one post, once. Returns the report, or None when skipped (seen before, not media, too big)."""
        key = f"{msg.chat}:{msg.id}"
        if self.store.seen(key):
            return None
        if msg.kind not in TRACEABLE or msg.size > self.max_bytes:
            self.store.mark_seen(key, None)
            return None
        path = msg.download(self.folder)
        report = self.tracer(path, self.store, first_seen=msg.date)
        self.store.mark_seen(key, report.id)
        if self.webhook and report.verdict in ("identified", "narrowed"):
            self._notify(msg, report)
        return report

    def _notify(self, msg: Message, report) -> None:
        body = json.dumps({"post": msg.link, "verdict": report.verdict, "title": report.title,
                           "account": report.suspects[0]["viewer"] if report.suspects else None,
                           "trace": report.id}).encode()
        req = urllib.request.Request(self.webhook, data=body, headers={"Content-Type": "application/json"})
        try:
            urllib.request.urlopen(req, timeout=10).close()
        except OSError as e:  # a webhook outage must not stop the watcher
            print(f"webhook failed: {e}")


def _kind(m) -> str:
    if getattr(m, "video", None) or getattr(m, "video_note", None):
        return "video"
    if getattr(m, "audio", None) or getattr(m, "voice", None):
        return "audio"
    doc = getattr(m, "document", None)
    if doc is not None and str(getattr(doc, "mime_type", "")).startswith(("video/", "audio/")):
        return "video_document"
    if getattr(m, "photo", None):
        return "photo"
    return "text" if getattr(m, "message", None) else "other"


def run_telegram(chats: list[str], backfill: int = 20) -> None:  # pragma: no cover - needs a real account
    """Watch the given channels/groups forever (and trace the last `backfill` posts of each on start)."""
    from telethon import TelegramClient, events

    api_id, api_hash = os.getenv("LEAKTRACE_TG_API_ID"), os.getenv("LEAKTRACE_TG_API_HASH")
    if not api_id or not api_hash:
        raise SystemExit("Set LEAKTRACE_TG_API_ID and LEAKTRACE_TG_API_HASH (from my.telegram.org).")
    data = settings().data
    store = Store(data / "leaktrace.db")
    watcher = Watcher(store, data / "telegram")
    client = TelegramClient(str(data / "telegram.session"), int(api_id), api_hash)

    async def to_message(m, chat_name: str) -> Message:
        async def fetch(folder: Path) -> Path:
            return Path(await client.download_media(m, file=str(folder / f"{chat_name}-{m.id}")))
        size = int(getattr(getattr(m, "file", None), "size", 0) or 0)
        username = getattr(m.chat, "username", None) if m.chat else None
        link = f"https://t.me/{username}/{m.id}" if username else f"{chat_name}#{m.id}"
        return Message(chat=chat_name, id=m.id, date=m.date.isoformat(), kind=_kind(m), size=size, link=link,
                       download=lambda folder: client.loop.run_until_complete(fetch(folder)))

    async def handle(m, chat_name):
        msg = await to_message(m, chat_name)
        report = await client.loop.run_in_executor(None, watcher.handle, msg)
        if report:
            print(f"{msg.link}: {report.verdict}")

    async def main():
        entities = [await client.get_entity(c) for c in chats]
        for c, e in zip(chats, entities):
            async for m in client.iter_messages(e, limit=backfill):
                await handle(m, c)

        @client.on(events.NewMessage(chats=entities))
        async def on_new(event):
            await handle(event.message, str(event.chat_id))

        print(f"Watching {len(entities)} chats. Ctrl+C to stop.")
        await client.run_until_disconnected()

    with client:
        client.loop.run_until_complete(main())
