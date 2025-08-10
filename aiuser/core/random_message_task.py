import datetime
from datetime import timezone
import logging
import random

import discord
from discord.ext import tasks

from aiuser.config.constants import RANDOM_MESSAGE_TASK_RETRY_SECONDS
from aiuser.config.defaults import DEFAULT_PROMPT
from aiuser.messages_list.messages import create_messages_list
from aiuser.response.dispatcher import dispatch_response
from aiuser.types.abc import MixinMeta
from aiuser.utils.utilities import format_variables

logger = logging.getLogger("red.bz_cogs.aiuser")


class RandomMessageTask(MixinMeta):
    @tasks.loop(seconds=RANDOM_MESSAGE_TASK_RETRY_SECONDS)
    async def random_message_trigger(self):

        if not self.openai_client:
            return
        if not self.bot.is_ready():
            return

        for guild_id, channels in self.channels_whitelist.items():
            try:
                last, ctx = await self.get_discord_context(guild_id, channels)
            except Exception:
                continue

            guild = last.guild
            channel = last.channel

            if not await self.check_if_valid_for_random_message(guild, last):
                return

            # Build context from last 30 messages; pick last 10 relevant to conversation
            history_msgs = [m async for m in channel.history(limit=30)]
            # Filter out bot messages and empty content
            convo = [m for m in history_msgs if not m.author.bot and (m.content or m.attachments or m.stickers)]
            convo.sort(key=lambda m: m.created_at, reverse=True)
            convo = convo[:10]

            # Time-based greeting if a user reappears after 4 hours
            greeting = None
            try:
                now = datetime.datetime.now(datetime.timezone(datetime.timedelta(hours=2), name="Europe/Amsterdam"))
            except Exception:
                now = datetime.datetime.now(tz=timezone.utc)
            if len(history_msgs) > 1:
                last_user_msg = next((m for m in history_msgs if not m.author.bot), None)
                if last_user_msg:
                    delta = now - last_user_msg.created_at.replace(tzinfo=timezone.utc)
                    if delta.total_seconds() >= 4 * 3600:
                        hour = now.hour
                        if 5 <= hour < 12:
                            greeting = "Good morning"
                        elif 12 <= hour < 18:
                            greeting = "Good afternoon"
                        else:
                            greeting = "Good evening"

            # Always use DEFAULT_PROMPT for consistency
            prompt = await format_variables(ctx, DEFAULT_PROMPT)
            messages_list = await create_messages_list(self, ctx, prompt=prompt, history=False)
            logger.debug(
                f"Sending contextual random message to #{channel.name} at {guild.name}")
            # Inject short instruction to respond related to recent conversation
            recent_summary_instruction = "Respond briefly and naturally about the ongoing conversation based on the recent messages. Avoid introducing unrelated topics."
            if greeting:
                recent_summary_instruction += f" If someone is just arriving after a while, start with '{greeting}'."
            await messages_list.add_system(recent_summary_instruction, index=len(messages_list) + 1)

            # Add the 10-message context explicitly (as user content)
            for msg in reversed(convo):
                try:
                    await messages_list.add_msg(msg, index=len(messages_list) + 1, force=True)
                except Exception:
                    continue
            messages_list.can_reply = False

            return await dispatch_response(self, ctx, messages_list)

    async def get_discord_context(self, guild_id: int, channels: list):
        guild = self.bot.get_guild(guild_id)

        if not channels:
            raise ValueError(f"Channels are empty in guild {guild.name}")

        channel = guild.get_channel(
            channels[random.randint(0, len(channels) - 1)])

        if not channel:
            raise ValueError(f"Channel not found in guild {guild.name}")

        last_message = await channel.fetch_message(channel.last_message_id)
        ctx = await self.bot.get_context(last_message)

        return last_message, ctx

    async def check_if_valid_for_random_message(self, guild: discord.Guild, last: discord.Message):
        if await self.bot.cog_disabled_in_guild(self, guild):
            return False

        try:
            if not (await self.bot.ignored_channel_or_guild(last)):
                return False
        except Exception:
            return False

        if not await self.config.guild(guild).random_messages_enabled():
            return False
        if random.random() > await self.config.guild(guild).random_messages_percent():
            return False

        if last.author.id == guild.me.id:
            # skip spamming channel with random event messages
            return False

        last_created = last.created_at.replace(
            tzinfo=datetime.timezone.utc)

        if (abs((datetime.datetime.now(datetime.timezone.utc) - last_created).total_seconds())) < 3600:
            # only sent to channels with 1 hour since last message
            return False

        return True
