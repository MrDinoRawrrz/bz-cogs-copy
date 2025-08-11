import asyncio
import hashlib
import io
import json
import logging
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import List, Optional, Tuple

import aiohttp
import re
from discord import Message
from qdrant_client import QdrantClient
from qdrant_client.http.models import Filter, FieldCondition, MatchValue
from qdrant_client.http import models as rest
from redbot.core import Config, commands
from sentence_transformers import SentenceTransformer
from trafilatura import extract

logger = logging.getLogger("red.bz_cogs.aiuser")


EMBED_MODEL = "sentence-transformers/all-MiniLM-L6-v2"


def _now_iso() -> str:
    return datetime.now(tz=timezone.utc).isoformat()


def _sha256(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _normalize(text: str) -> str:
    return " ".join(text.split())


def _chunk(text: str, max_chars: int = 1200, overlap: int = 120) -> List[str]:
    text = _normalize(text)
    if len(text) <= max_chars:
        return [text]
    chunks: List[str] = []
    start = 0
    while start < len(text):
        end = min(len(text), start + max_chars)
        chunks.append(text[start:end])
        if end == len(text):
            break
        start = max(0, end - overlap)
    return chunks


_CUSTOM_EMOJI = re.compile(r"<a?:[A-Za-z0-9_~]+:[0-9]+>")


def _is_emote_only(text: str) -> bool:
    if not text:
        return True
    t = _CUSTOM_EMOJI.sub("", text)
    t = re.sub(r"\s+", "", t)
    # remove common punctuation
    t = re.sub(r"[\W_]+", "", t, flags=re.UNICODE)
    return len(t) == 0


@dataclass
class RetrievalResult:
    context_block: str
    citations: List[str]


class RAG:
    def __init__(self, cfg: Config, client: QdrantClient, embedder: SentenceTransformer, base_url: str):
        self.config = cfg
        self.client = client
        self.embedder = embedder
        self.base_url = base_url.rstrip("/")

    @classmethod
    async def create(cls, cfg: Config) -> Optional["RAG"]:
        url = await cfg.rag_qdrant_url()
        enabled = await cfg.rag_enabled()
        if not enabled or not url:
            return None
        try:
            client = QdrantClient(url=url)
            embedder = SentenceTransformer(EMBED_MODEL)
            rag = cls(cfg, client, embedder, url)
            await rag._ensure_collection()
            return rag
        except Exception:
            logger.exception("Failed to initialize Qdrant RAG client")
            return None

    async def is_enabled(self) -> bool:
        return bool(await self.config.rag_enabled())

    async def _ensure_collection(self):
        collection = await self.config.rag_collection()
        dim = self.embedder.get_sentence_embedding_dimension()
        try:
            exists = self.client.get_collection(collection)
            if exists:
                return
        except Exception:
            pass
        self.client.recreate_collection(
            collection_name=collection,
            vectors_config=rest.VectorParams(size=dim, distance=rest.Distance.COSINE),
            optimizers_config=rest.OptimizersConfigDiff(memmap_threshold=20000),
        )
        # payload indexes
        try:
            self.client.create_payload_index(collection, field_name="guild_id", field_schema=rest.PayloadSchemaType.INTEGER)
            self.client.create_payload_index(collection, field_name="channel_id", field_schema=rest.PayloadSchemaType.INTEGER)
            self.client.create_payload_index(collection, field_name="author_id", field_schema=rest.PayloadSchemaType.INTEGER)
            self.client.create_payload_index(collection, field_name="content_hash", field_schema=rest.PayloadSchemaType.KEYWORD)
        except Exception:
            pass

    async def health(self) -> Tuple[bool, Optional[str]]:
        try:
            res = self.client.get_locks_option()
            # best-effort version fetch
            version = getattr(self.client, "_client_wrapper", None)
            return True, None
        except Exception:
            return False, None

    async def _embed(self, texts: List[str]):
        return self.embedder.encode(texts, convert_to_numpy=True, normalize_embeddings=True)

    async def ingest_messages(self, messages: List[Message], source: str = "discord") -> int:
        if not messages:
            return 0
        collection = await self.config.rag_collection()
        upserts = []
        vectors = []
        texts = []
        payloads = []
        for msg in messages:
            if msg.author.bot:
                continue
            base_meta = {
                "guild_id": msg.guild.id,
                "channel_id": msg.channel.id,
                "author": msg.author.display_name,
                "author_id": msg.author.id,
                "message_id": msg.id,
                "created_at": msg.created_at.replace(tzinfo=timezone.utc).isoformat(),
                "created_at_ts": int(msg.created_at.replace(tzinfo=timezone.utc).timestamp()),
                "source": source,
            }
            content = msg.content or ""
            if not content.strip() or _is_emote_only(content):
                continue
            for chunk in _chunk(content):
                text = chunk
                content_hash = _sha256(_normalize(text))
                texts.append(text)
                payload = dict(base_meta)
                payload.update({
                    "text": text,
                    "content_hash": content_hash,
                    "first_seen": _now_iso(),
                    "last_seen": _now_iso(),
                    "sources": [source],
                })
                payloads.append(payload)
        if not texts:
            return 0
        vectors = await asyncio.get_running_loop().run_in_executor(None, lambda: self.embedder.encode(texts, convert_to_numpy=True))
        # Deduplicate by content_hash (merge sources, update last_seen)
        unique: dict[str, dict] = {}
        for pld in payloads:
            h = pld["content_hash"]
            if h in unique:
                unique[h]["sources"] = list(set(unique[h]["sources"] + pld["sources"]))
                unique[h]["last_seen"] = _now_iso()
            else:
                unique[h] = pld
        vectors_map = {pld["content_hash"]: v.tolist() for v, pld in zip(vectors, payloads)}
        points = [rest.PointStruct(id=h, vector=vectors_map[h], payload=pld) for h, pld in unique.items()]
        self.client.upsert(collection_name=collection, points=points)
        return len(points)

    async def ingest_url(self, ctx: commands.Context, url: str) -> int:
        text = await self._fetch_url_text(url)
        if not text:
            return 0
        fake_message = type("_Fake", (), {
            "author": ctx.author,
            "guild": ctx.guild,
            "channel": ctx.channel,
            "id": 0,
            "created_at": datetime.now(tz=timezone.utc),
            "content": text,
        })
        return await self.ingest_messages([fake_message], source=url)

    async def _fetch_url_text(self, url: str) -> Optional[str]:
        try:
            headers = {
                "Cache-Control": "no-cache",
                "Referer": "https://www.google.com/",
                "User-Agent": "Mozilla/5.0",
            }
            async with aiohttp.ClientSession(headers=headers) as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    html = await resp.text()
            text = extract(html) or ""
            return text[:20000]
        except Exception:
            logger.exception("Failed to fetch URL for RAG")
            return None

    async def ingest_bytes(self, ctx: commands.Context, data: bytes, filename: str) -> int:
        text = await self._extract_text_from_bytes(data, filename)
        if not text:
            return 0
        fake_message = type("_Fake", (), {
            "author": ctx.author,
            "guild": ctx.guild,
            "channel": ctx.channel,
            "id": 0,
            "created_at": datetime.now(tz=timezone.utc),
            "content": text,
        })
        return await self.ingest_messages([fake_message], source=filename)

    async def _extract_text_from_bytes(self, data: bytes, filename: str) -> Optional[str]:
        try:
            name = filename.lower()
            if name.endswith(".txt") or name.endswith(".md"):
                return data.decode("utf-8", errors="ignore")
            if name.endswith(".pdf"):
                from pypdf import PdfReader
                reader = PdfReader(io.BytesIO(data))
                pages = [p.extract_text() or "" for p in reader.pages]
                return "\n".join(pages)
            if name.endswith(".docx"):
                from docx import Document
                doc = Document(io.BytesIO(data))
                return "\n".join([p.text for p in doc.paragraphs])
        except Exception:
            logger.exception("Failed extracting text from uploaded file")
        return None

    async def retrieve_context(self, ctx: commands.Context, query: str) -> Tuple[Optional[str], Optional[List[str]]]:
        collection = await self.config.rag_collection()
        top_k = await self._get_top_k(ctx)
        min_score = await self._get_min_score(ctx)
        vector = await asyncio.get_running_loop().run_in_executor(None, lambda: self.embedder.encode([query], convert_to_numpy=True)[0])
        flt = Filter(must=[
            FieldCondition(key="guild_id", match=MatchValue(int(ctx.guild.id))),
        ])
        res = self.client.search(
            collection_name=collection,
            query_vector=vector.tolist(),
            limit=top_k,
            score_threshold=float(min_score),
            query_filter=flt,
        )
        if not res:
            return None, None
        blocks = []
        citations = []
        for i, p in enumerate(res):
            payload = p.payload or {}
            text = payload.get("text", "")
            author = payload.get("author", "?")
            created_at = payload.get("created_at", "")
            source = payload.get("source", "")
            blocks.append(f"[{i+1}] {text}")
            cite = f"{source or 'discord'} — {author} {created_at}"
            citations.append(cite)
        context_chars = await self.config.rag_max_context_chars()
        context = "\n\n".join(blocks)
        if len(context) > context_chars:
            context = context[:context_chars]
        return context, citations

    async def _get_top_k(self, ctx: commands.Context) -> int:
        guild_topk = await self.config.guild(ctx.guild).rag_top_k()
        return guild_topk or await self.config.rag_top_k()

    async def _get_min_score(self, ctx: commands.Context) -> float:
        guild_min = await self.config.guild(ctx.guild).rag_min_score()
        return guild_min if guild_min is not None else await self.config.rag_min_score()

    async def delete_user(self, user_id: int):
        collection = await self.config.rag_collection()
        try:
            self.client.delete(
                collection_name=collection,
                points_selector=rest.FilterSelector(
                    filter=Filter(must=[FieldCondition(key="author_id", match=MatchValue(int(user_id)))])
                ),
            )
        except Exception:
            logger.exception("Failed deleting user data from RAG")

    async def stats(self) -> dict:
        collection = await self.config.rag_collection()
        try:
            info = self.client.get_collection(collection)
            return {"points": info.vectors_count, "segments": info.segments_count}
        except Exception:
            return {"points": 0}

    # New operations for maintenance and privacy granularity
    async def delete_messages_by_ids(self, message_ids: List[int], author_id: Optional[int] = None):
        collection = await self.config.rag_collection()
        if not message_ids:
            return
        try:
            should = [FieldCondition(key="message_id", match=MatchValue(int(mid))) for mid in message_ids]
            must = []
            if author_id is not None:
                must.append(FieldCondition(key="author_id", match=MatchValue(int(author_id))))
            flt = Filter(must=must, should=should) if must else Filter(should=should)
            self.client.delete(collection_name=collection, points_selector=rest.FilterSelector(filter=flt))
        except Exception:
            logger.exception("Failed deleting messages by IDs from RAG")

    async def delete_older_than(self, days: int, guild_id: Optional[int] = None):
        if not days:
            return
        cutoff = int(datetime.now(tz=timezone.utc).timestamp()) - (days * 86400)
        collection = await self.config.rag_collection()
        try:
            must = [FieldCondition(key="created_at_ts", range=rest.Range(lte=cutoff))]
            if guild_id:
                must.append(FieldCondition(key="guild_id", match=MatchValue(int(guild_id))))
            self.client.delete(collection_name=collection, points_selector=rest.FilterSelector(filter=Filter(must=must)))
        except Exception:
            logger.exception("Failed retention delete in RAG")

    async def create_snapshot(self, directory: Optional[str] = None) -> Optional[dict]:
        # Trigger snapshot on Qdrant side; if a directory is provided and Qdrant is configured, you may later download
        collection = await self.config.rag_collection()
        url = f"{self.base_url}/collections/{collection}/snapshots"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.post(url) as resp:
                    resp.raise_for_status()
                    return await resp.json()
        except Exception:
            logger.exception("Failed to create snapshot in Qdrant")
            return None

    async def list_snapshots(self) -> List[dict]:
        collection = await self.config.rag_collection()
        url = f"{self.base_url}/collections/{collection}/snapshots"
        try:
            async with aiohttp.ClientSession() as session:
                async with session.get(url) as resp:
                    resp.raise_for_status()
                    data = await resp.json()
                    return data.get("result", [])
        except Exception:
            return []

    async def delete_filtered(self, *, guild_id: Optional[int] = None, user_id: Optional[int] = None, channel_id: Optional[int] = None, before_ts: Optional[int] = None, after_ts: Optional[int] = None):
        collection = await self.config.rag_collection()
        must = []
        if guild_id is not None:
            must.append(FieldCondition(key="guild_id", match=MatchValue(int(guild_id))))
        if user_id is not None:
            must.append(FieldCondition(key="author_id", match=MatchValue(int(user_id))))
        if channel_id is not None:
            must.append(FieldCondition(key="channel_id", match=MatchValue(int(channel_id))))
        if before_ts is not None or after_ts is not None:
            rng = rest.Range()
            if after_ts is not None:
                rng.gte = int(after_ts)
            if before_ts is not None:
                rng.lte = int(before_ts)
            must.append(FieldCondition(key="created_at_ts", range=rng))
        flt = Filter(must=must) if must else None
        try:
            self.client.delete(collection_name=collection, points_selector=rest.FilterSelector(filter=flt))
        except Exception:
            logger.exception("Failed filtered delete in RAG")

    async def export_user(self, guild_id: int, author_id: int) -> List[dict]:
        # Scroll all points by filter
        flt = Filter(must=[
            FieldCondition(key="guild_id", match=MatchValue(int(guild_id))),
            FieldCondition(key="author_id", match=MatchValue(int(author_id))),
        ])
        collection = await self.config.rag_collection()
        points: List[dict] = []
        next_page = None
        try:
            while True:
                res, next_page = self.client.scroll(collection_name=collection, scroll_filter=flt, with_payload=True, with_vectors=False, offset=next_page, limit=256)
                for p in res:
                    points.append(p.payload)
                if next_page is None:
                    break
        except Exception:
            logger.exception("Failed exporting user data")
        return points

    async def export_all(self, *, guild_id: Optional[int] = None, user_id: Optional[int] = None, channel_id: Optional[int] = None) -> List[dict]:
        must = []
        if guild_id is not None:
            must.append(FieldCondition(key="guild_id", match=MatchValue(int(guild_id))))
        if user_id is not None:
            must.append(FieldCondition(key="author_id", match=MatchValue(int(user_id))))
        if channel_id is not None:
            must.append(FieldCondition(key="channel_id", match=MatchValue(int(channel_id))))
        flt = Filter(must=must) if must else None
        collection = await self.config.rag_collection()
        points: List[dict] = []
        next_page = None
        try:
            while True:
                res, next_page = self.client.scroll(collection_name=collection, scroll_filter=flt, with_payload=True, with_vectors=False, offset=next_page, limit=512)
                for p in res:
                    points.append(p.payload)
                if next_page is None:
                    break
        except Exception:
            logger.exception("Failed exporting RAG data")
        return points


