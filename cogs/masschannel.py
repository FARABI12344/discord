import os
import re
import json
import discord

from discord.ext import commands


# ============================================================
# CONFIG
# ============================================================

DATA_FILE = "/data/channels.json"

PREFIX = ","


# ============================================================
# DATA
# ============================================================

def default_data():
    return {
        "message": "",
        "channels": [],
        "auto": False,
        "next_run": None,
        "log_channel": None,
        "reverse": False
    }


def load_data():
    if not os.path.exists(DATA_FILE):
        return default_data()

    try:
        with open(DATA_FILE, "r", encoding="utf8") as fp:
            data = json.load(fp)

        data.setdefault("message", "")
        data.setdefault("channels", [])
        data.setdefault("auto", False)
        data.setdefault("next_run", None)
        data.setdefault("log_channel", None)
        data.setdefault("reverse", False)

        return data

    except Exception:
        return default_data()


def save_data(data):
    folder = os.path.dirname(DATA_FILE)

    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(DATA_FILE, "w", encoding="utf8") as fp:
        json.dump(
            data,
            fp,
            indent=2,
            ensure_ascii=False
        )


# ============================================================
# CHANNEL ID PARSER
# ============================================================

def extract_channel_id(value: str):
    """
    Supports:

    ,setc 123456789012345678

    ,setc https://discord.com/channels/GUILD_ID/CHANNEL_ID

    ,setc https://canary.discord.com/channels/GUILD_ID/CHANNEL_ID

    ,setc https://ptb.discord.com/channels/GUILD_ID/CHANNEL_ID
    """

    value = value.strip().strip("<>")

    # Raw channel ID
    if value.isdigit():
        return int(value)

    # Discord channel URL
    match = re.fullmatch(
        r"https?://(?:canary\.|ptb\.)?"
        r"discord(?:app)?\.com/channels/"
        r"(?:@me|\d+)/(\d+)/?",
        value,
        flags=re.IGNORECASE
    )

    if match:
        return int(match.group(1))

    return None


# ============================================================
# COG
# ============================================================

class MassChannels(commands.Cog):

    def __init__(self, bot):
        self.bot = bot


    # ========================================================
    # ,findchannels
    # ========================================================

    @commands.command(name="findchannels")
    async def findchannels(self, ctx):

        await ctx.send(
            "🔎 Searching this channel for your old `,setc` messages..."
        )

        found = []
        seen_ids = set()

        try:

            async for message in ctx.channel.history(
                limit=None,
                oldest_first=True
            ):

                # Only messages sent by YOU
                if not self.bot.user:
                    continue

                if message.author.id != self.bot.user.id:
                    continue

                content = message.content.strip()

                # Exact format:
                # ,setc SOMETHING
                match = re.fullmatch(
                    rf"{re.escape(PREFIX)}setc\s+(\S+)",
                    content,
                    flags=re.IGNORECASE
                )

                if not match:
                    continue

                value = match.group(1).strip()

                channel_id = extract_channel_id(value)

                if not channel_id:
                    continue

                # Prevent duplicates
                if channel_id in seen_ids:
                    continue

                seen_ids.add(channel_id)

                # Keep original ID/link exactly as you typed it
                found.append(value)

        except Exception as e:

            return await ctx.send(
                f"⚠️ Search failed: "
                f"`{type(e).__name__}: {e}`"
            )


        if not found:

            return await ctx.send(
                "⚠️ No valid `,setc` messages from you "
                "were found in this channel."
            )


        # ====================================================
        # BUILD ,mass-setc COMMANDS
        # ====================================================

        commands_to_send = []

        current = f"{PREFIX}mass-setc"

        for value in found:

            addition = f" {value}"

            # Keep under Discord message limit
            if len(current) + len(addition) > 1850:

                commands_to_send.append(current)

                current = (
                    f"{PREFIX}mass-setc {value}"
                )

            else:

                current += addition


        if current != f"{PREFIX}mass-setc":
            commands_to_send.append(current)


        await ctx.send(
            f"✅ Found **{len(found)}** unique channel(s).\n"
            f"Generated **{len(commands_to_send)}** "
            f"`mass-setc` command(s):"
        )


        for command in commands_to_send:

            await ctx.send(
                f"```text\n{command}\n```"
            )


    # ========================================================
    # ,mass-setc
    # ========================================================

    @commands.command(
        name="mass-setc",
        aliases=["masssetc"]
    )
    async def mass_setc(
        self,
        ctx,
        *values
    ):

        if not values:

            return await ctx.send(
                "⚠️ Usage:\n"
                "`"
                ",mass-setc CHANNEL_ID CHANNEL_ID CHANNEL_LINK"
                "`"
            )


        data = load_data()

        existing_ids = {
            int(entry["id"])
            for entry in data.get("channels", [])
            if "id" in entry
        }


        added = []

        duplicate = []

        invalid = []

        inaccessible = []


        # ====================================================
        # PROCESS EVERY CHANNEL
        # ====================================================

        for value in values:

            channel_id = extract_channel_id(value)

            if not channel_id:

                invalid.append(value)

                continue


            if channel_id in existing_ids:

                duplicate.append(str(channel_id))

                continue


            # Try cache first
            channel = self.bot.get_channel(channel_id)


            # If not cached, try Discord
            if channel is None:

                try:

                    channel = await self.bot.fetch_channel(
                        channel_id
                    )

                except Exception:

                    inaccessible.append(
                        str(channel_id)
                    )

                    continue


            # Make sure it can actually send messages
            if not hasattr(channel, "send"):

                invalid.append(
                    str(channel_id)
                )

                continue


            guild = getattr(
                channel,
                "guild",
                None
            )


            guild_name = (
                guild.name
                if guild
                else "Unknown Server"
            )


            channel_name = getattr(
                channel,
                "name",
                str(channel_id)
            )


            # =================================================
            # ADD USING SAME FORMAT SYSTEM.PY EXPECTS
            # =================================================

            entry = {

                "id": channel_id,

                "guild_name": guild_name,

                "channel_name": channel_name,

                "last_sent": None

            }


            data["channels"].append(entry)

            existing_ids.add(channel_id)

            added.append(
                f"{guild_name}/#{channel_name}"
            )


        # ====================================================
        # SAVE
        # ====================================================

        save_data(data)


        # ====================================================
        # RESULT
        # ====================================================

        lines = [

            "✅ **Mass SetC finished**",

            "",

            f"Added: **{len(added)}**",

            f"Already saved: **{len(duplicate)}**",

            f"Invalid: **{len(invalid)}**",

            f"Couldn't access: **{len(inaccessible)}**",

            "",

            f"Total saved channels: "
            f"**{len(data['channels'])}**"

        ]


        if added:

            lines.append("")

            lines.append("**Added:**")

            for name in added[:20]:

                lines.append(
                    f"• {name}"
                )


            if len(added) > 20:

                lines.append(
                    f"• +{len(added) - 20} more"
                )


        await ctx.send(
            "\n".join(lines)
        )


# ============================================================
# SETUP
# ============================================================

async def setup(bot):

    await bot.add_cog(
        MassChannels(bot)
    )
