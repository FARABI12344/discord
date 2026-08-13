import discord
from discord.ext import commands
import asyncio
import json
import os
from datetime import datetime, timezone

DATA_FILE = "/data/channels.json"


def load_data():
    if not os.path.isfile(DATA_FILE):
        return {"message": "", "channels": []}

    with open(DATA_FILE, "r", encoding="utf8") as fp:
        return json.load(fp)


def save_data(data):
    with open(DATA_FILE, "w", encoding="utf8") as fp:
        json.dump(data, fp, indent=2)


class Promo(commands.Cog):
    def __init__(self, bot):
        self.bot = bot
        self.data = load_data()

    # helper ──────────────────────────────────────────────────────────
    @staticmethod
    def extract_channel_id(raw: str) -> int | None:
        raw = raw.strip().strip("<>#")

        if "discord.com/channels" in raw:
            try:
                raw = raw.split("/")[-1]
            except IndexError:
                return None

        return int(raw) if raw.isdigit() else None

    # ─────────────────────────────────────────────────────────────────
    # ,setm <message text>
    @commands.command()
    async def setm(self, ctx, *, msg: str):
        self.data["message"] = msg.strip()
        save_data(self.data)

        await ctx.send(
            f"✅ Message saved:\n```\n{msg.strip()}\n```"
        )

    # ─────────────────────────────────────────────────────────────────
    # ,setc <channel-link OR id>
    @commands.command()
    async def setc(self, ctx, *, link: str):
        chan_id = self.extract_channel_id(link)

        if not chan_id:
            await ctx.send("⚠️ Couldn't parse channel link / id.")
            return

        channel = self.bot.get_channel(chan_id)

        if channel is None:
            await ctx.send("⚠️ I'm not in that server / channel.")
            return

        # avoid duplicates
        for entry in self.data["channels"]:
            if entry["id"] == chan_id:
                await ctx.send("ℹ️ Channel already in list.")
                return

        entry = {
            "id": chan_id,
            "guild_name": channel.guild.name,
            "channel_name": channel.name,
            "last_sent": None,
        }

        self.data["channels"].append(entry)
        save_data(self.data)

        await ctx.send(
            f"✅ Added `{channel.name}` in **{channel.guild.name}** "
            f"(total: {len(self.data['channels'])})"
        )

    # ─────────────────────────────────────────────────────────────────
    # ,start1  ─ step-through sender
    @commands.command()
    async def start1(self, ctx):
        if not self.data["message"]:
            await ctx.send("⚠️ Set a message first with `,setm`.")
            return

        if not self.data["channels"]:
            await ctx.send("⚠️ No channels saved. Use `,setc`.")
            return

        for idx, entry in enumerate(self.data["channels"], start=1):
            chan = self.bot.get_channel(entry["id"])

            if chan is None:
                prompt = (
                    f"🔍 **Missing:** Can’t find server/channel "
                    f"`{entry['guild_name']} / {entry['channel_name']}`.\n"
                    "Skip and continue? (y/n)"
                )

            else:
                last = (
                    entry["last_sent"][:19].replace("T", " ")
                    if entry["last_sent"]
                    else "never"
                )

                prompt = (
                    f"➡️ **{idx}/{len(self.data['channels'])}** — "
                    f"Send to `{chan.guild.name}` • <#{chan.id}>  \n"
                    f"Last sent: **{last}**  \n"
                    "Send now? (y/n)"
                )

            await ctx.send(prompt)

            def check(m: discord.Message):
                return (
                    m.author.id == self.bot.user.id
                    and m.channel.id == ctx.channel.id
                    and m.content.lower() in {"y", "n"}
                )

            try:
                reply = await self.bot.wait_for(
                    "message",
                    check=check,
                    timeout=90
                )

            except asyncio.TimeoutError:
                await ctx.send("⏰ Timed-out. Stopping.")
                break

            if reply.content.lower() == "n":
                await ctx.send("⏩ Skipped.")
                continue

            # user said 'y'
            if chan is None:
                await ctx.send("⚠️ Still missing. Skipped.")
                continue

            try:
                await chan.send(self.data["message"])

                entry["last_sent"] = datetime.now(
                    timezone.utc
                ).isoformat()

                save_data(self.data)

                await ctx.send("✅ Sent.")

            except Exception as e:
                await ctx.send(f"❌ Failed to send: {e}")

        await ctx.send("🏁 Finished.")

    # ─────────────────────────────────────────────────────────────────
    # optional: list channels
    @commands.command()
    async def chanlist(self, ctx):
        if not self.data["channels"]:
            await ctx.send("ℹ️ No channels saved.")
            return

        lines = [
            f"{i+1}. {c['guild_name']} • #{c['channel_name']} (<#{c['id']}>)"
            for i, c in enumerate(self.data["channels"])
        ]

        await ctx.send(
            "**Saved channels:**\n" + "\n".join(lines)
        )

    # ─────────────────────────────────────────────────────────────────
    # optional: remove channel by index
    @commands.command()
    async def chanremove(self, ctx, index: int):
        if 1 <= index <= len(self.data["channels"]):
            removed = self.data["channels"].pop(index - 1)

            save_data(self.data)

            await ctx.send(
                f"🗑️ Removed `{removed['channel_name']}` "
                f"from **{removed['guild_name']}**."
            )

        else:
            await ctx.send("⚠️ Invalid index.")


async def setup(bot):
    await bot.add_cog(Promo(bot))
