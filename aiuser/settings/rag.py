import discord
from redbot.core import checks, commands

from aiuser.types.abc import MixinMeta


class RagSettings(MixinMeta):
    @commands.group(name="aiuserowner")
    @checks.is_owner()
    async def aiuser_owner_group(self, _):
        """AIUser owner/admin commands"""
        pass

    @aiuser_owner_group.group(name="rag")
    async def rag(self, _):
        """RAG (Qdrant) configuration and operations"""
        """RAG configuration commands"""
        pass

    @rag.command(name="enable")
    @checks.is_owner()
    async def rag_enable(self, ctx: commands.Context):
        val = not await self.config.rag_enabled()
        await self.config.rag_enabled.set(val)
        await ctx.send(f"RAG enabled: `{val}`")

    @rag.command(name="qdrant")
    @checks.is_owner()
    async def rag_set_qdrant(self, ctx: commands.Context, url: str, collection: str = None):
        await self.config.rag_qdrant_url.set(url)
        if collection:
            await self.config.rag_collection.set(collection)
        await ctx.send(f"Set Qdrant URL to `{url}`" + (f", collection `{collection}`" if collection else ""))

    @rag.command(name="minscore")
    async def rag_threshold(self, ctx: commands.Context, min_score: float):
        await self.config.guild(ctx.guild).rag_min_score.set(min_score)
        await ctx.send(f"Set RAG min score to `{min_score}` for this server")

    @rag.command(name="topk")
    async def rag_topk(self, ctx: commands.Context, k: int):
        await self.config.guild(ctx.guild).rag_top_k.set(k)
        await ctx.send(f"Set RAG top-k to `{k}` for this server")

    @rag.command(name="autoingest")
    @checks.is_owner()
    async def rag_auto_ingest(self, ctx: commands.Context, state: str):
        val = state.lower() in ["on", "true", "yes", "1", "enable", "enabled"]
        await self.config.rag_auto_ingest.set(val)
        await ctx.send(f"RAG auto-ingest: `{val}`")

    @rag.command(name="scope")
    async def rag_scope(self, ctx: commands.Context, scope: str):
        scope = scope.lower()
        if scope not in ["guild", "channel", "author", "mixed"]:
            return await ctx.send(":warning: invalid scope; use guild|channel|author|mixed")
        await self.config.rag_scope.set(scope)
        await ctx.send(f"RAG retrieval scope set to `{scope}`")

    @rag.command(name="health")
    async def rag_health(self, ctx: commands.Context):
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            ok, version = await rag.health()
            await ctx.send(f"Qdrant: {'OK' if ok else 'DOWN'} (v{version or 'unknown'})")
        except Exception:
            await ctx.send("Qdrant: error")

    @rag.command(name="stats")
    async def rag_stats(self, ctx: commands.Context):
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            s = await rag.stats()
            await ctx.send(f"RAG stats: `{s}`")
        except Exception:
            await ctx.send("Failed to fetch stats")

    @rag.command(name="addhere")
    async def rag_add_here(self, ctx: commands.Context, limit: int = 200):
        """Ingest recent messages in this channel into RAG"""
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            msgs = [
                m async for m in ctx.channel.history(limit=limit)
                if not m.author.bot and m.content and m.content.strip()
            ]
            count = await rag.ingest_messages(msgs)
            await ctx.send(f"Indexed `{count}` chunks from {len(msgs)} messages")
        except Exception:
            await ctx.send("Failed to ingest messages")

    @rag.command(name="addurl")
    async def rag_add_url(self, ctx: commands.Context, url: str):
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            cnt = await rag.ingest_url(ctx, url)
            await ctx.send(f"Indexed `{cnt}` chunks from URL")
        except Exception:
            await ctx.send("Failed to ingest URL")

    @rag.command(name="addfile")
    async def rag_add_file(self, ctx: commands.Context):
        if not ctx.message.attachments:
            return await ctx.send(":warning: Attach a .txt/.md/.pdf/.docx file")
        data = await ctx.message.attachments[0].read()
        filename = ctx.message.attachments[0].filename
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            cnt = await rag.ingest_bytes(ctx, data, filename)
            await ctx.send(f"Indexed `{cnt}` chunks from file `{filename}`")
        except Exception:
            await ctx.send("Failed to ingest file")

    @rag.command(name="search")
    async def rag_search(self, ctx: commands.Context, *, query: str):
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            context, cites = await rag.retrieve_context(ctx, query)
            if not context:
                return await ctx.send("No hits")
            preview = context[:1500]
            if len(context) > 1500:
                preview += "..."
            cite_text = "\n".join([f"[{i+1}] {c}" for i, c in enumerate(cites or [])])
            await ctx.send(f"```{preview}```\n{cite_text}")
        except Exception:
            await ctx.send("Search failed")

    @commands.group(name="aiuser")
    async def aiuser_group(self, _):
        """AIUser user commands"""
        pass

    @aiuser_group.group(name="privacy")
    async def privacy(self, _):
        """User privacy commands"""
        pass

    @privacy.command(name="delete-mine")
    async def privacy_delete_mine(self, ctx: commands.Context, *, ids_or_flags: str = ""):
        """Delete your indexed messages from RAG. Options: --reply | --ids <id1,id2> | --since <minutes> | --all"""
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            # Parse flags
            ids: list[int] = []
            if "--reply" in ids_or_flags and ctx.message.reference:
                ids.append(ctx.message.reference.message_id)
            if "--ids" in ids_or_flags:
                try:
                    part = ids_or_flags.split("--ids", 1)[1].strip()
                    if part.startswith(" "):
                        part = part.strip()
                    csv = part.split()[0]
                    ids.extend(int(x.strip()) for x in csv.split(",") if x.strip())
                except Exception:
                    pass
            if "--all" in ids_or_flags:
                # Use author_id filter (implemented via delete_user)
                await rag.delete_user(ctx.author.id)
                return await ctx.send("Deleted all your indexed data.")
            if ids:
                await rag.delete_messages_by_ids(ids, author_id=ctx.author.id)
                return await ctx.send(f"Deleted {len(ids)} message(s) from RAG.")
            return await ctx.send(":warning: Provide --reply or --ids or --all")
        except Exception:
            await ctx.send("Deletion failed")

    @privacy.command(name="export-mine")
    async def privacy_export_mine(self, ctx: commands.Context):
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            data = await rag.export_user(ctx.guild.id, ctx.author.id)
            import json
            from io import BytesIO
            buf = BytesIO(json.dumps(data, indent=2).encode("utf-8"))
            await ctx.author.send(file=discord.File(buf, filename=f"rag_export_{ctx.guild.id}_{ctx.author.id}.json"))
            await ctx.send("DM'd your export.")
        except Exception:
            await ctx.send("Export failed")

    @rag.command(name="clear")
    async def rag_clear(self, ctx: commands.Context, *, flags: str = ""):
        """Clear RAG data with filters: --user @x --channel #y --before ISO --after ISO | no flags clears guild."""
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            user = None
            channel = None
            before_ts = None
            after_ts = None
            if "--user" in flags and ctx.message.mentions:
                user = ctx.message.mentions[0].id
            if "--channel" in flags and ctx.message.channel_mentions:
                channel = ctx.message.channel_mentions[0].id
            import dateutil.parser as dp  # if unavailable, we can require ISO epoch in future
            for token in flags.split():
                if token.startswith("--before="):
                    before_ts = int(dp.parse(token.split("=", 1)[1]).timestamp())
                if token.startswith("--after="):
                    after_ts = int(dp.parse(token.split("=", 1)[1]).timestamp())
            await rag.delete_filtered(guild_id=ctx.guild.id, user_id=user, channel_id=channel, before_ts=before_ts, after_ts=after_ts)
            await ctx.send("Cleared.")
        except Exception:
            await ctx.send("Clear failed")

    @rag.command(name="export")
    async def rag_export(self, ctx: commands.Context, *, flags: str = ""):
        """Export RAG payloads to a JSON file. Optional filters: --user @x --channel #y"""
        try:
            from aiuser.rag.client import RAG
            rag = await RAG.create(self.config)
            if not rag:
                return await ctx.send("RAG disabled or misconfigured")
            user = None
            channel = None
            if "--user" in flags and ctx.message.mentions:
                user = ctx.message.mentions[0].id
            if "--channel" in flags and ctx.message.channel_mentions:
                channel = ctx.message.channel_mentions[0].id
            data = await rag.export_all(guild_id=ctx.guild.id, user_id=user, channel_id=channel)
            if not data:
                return await ctx.send("No data found for export")
            import json
            from io import BytesIO
            buf = BytesIO(json.dumps(data, indent=2).encode("utf-8"))
            name = f"rag_export_g{ctx.guild.id}{'_u'+str(user) if user else ''}{'_c'+str(channel) if channel else ''}.json"
            await ctx.send(file=discord.File(buf, filename=name))
        except Exception:
            await ctx.send("Export failed")


