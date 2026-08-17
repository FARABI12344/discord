# cogs/findnew.py

import os
import json
import re
import difflib
import unicodedata
import discord

from discord.ext import commands


# ============================================================
# CONFIG
# ============================================================

DATA_FILE = "/data/channels.json"

PREFIX = ","

# Strong patterns found repeatedly in your collab/promo channels.
#
# Exact/normalized matching is tried first.
# difflib fuzzy matching is used afterward for similar spellings.
CHANNEL_PATTERNS = [
    "yours",
    "your",
    "urs",
    "collabs",
    "collab",
    "clbs",
    "clb",
    "promo",
    "sponsor",
    "sponsorship",
]

# 0.82 = reasonably strict.
#
# Higher = fewer false positives.
# Lower = finds more weird spellings, but may find random channels.
FUZZY_THRESHOLD = 0.82

# Don't let generated commands get too close to Discord's
# 2000-character message limit.
MAX_COMMAND_LENGTH = 1850


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

        with open(
            DATA_FILE,
            "r",
            encoding="utf8"
        ) as fp:

            data = json.load(fp)


        # Make sure expected fields exist
        data.setdefault("message", "")
        data.setdefault("channels", [])
        data.setdefault("auto", False)
        data.setdefault("next_run", None)
        data.setdefault("log_channel", None)
        data.setdefault("reverse", False)

        return data


    except Exception as e:

        print(
            "[findnew] Failed loading channels.json:",
            type(e).__name__,
            e
        )

        return default_data()


# ============================================================
# NORMALIZE CHANNEL NAMES
# ============================================================

def normalize_name(name: str) -> str:
    """
    Converts decorative Discord channel names into simpler text.

    Examples:

        "✿ㆍㆍclbs"
            -> "clbs"

        "﹒your﹒collabs"
            -> "your collabs"

        "♡⃕ㆍ𝕦r𝕤ㆍ"
            -> "urs"

    This makes pattern/fuzzy matching much more reliable.
    """

    if not name:
        return ""

    try:

        # Normalize Unicode characters
        text = unicodedata.normalize(
            "NFKC",
            str(name)
        ).casefold()

        # Replace punctuation/decorative symbols with spaces.
        #
        # Keep letters and numbers.
        text = re.sub(
            r"[^\w]+",
            " ",
            text,
            flags=re.UNICODE
        )

        # Remove underscores too
        text = text.replace("_", " ")

        # Collapse repeated whitespace
        text = re.sub(
            r"\s+",
            " ",
            text
        ).strip()

        return text

    except Exception:

        return str(name).lower().strip()


# ============================================================
# PATTERN MATCHING
# ============================================================

def channel_matches(name: str):
    """
    Returns:

        (True, reason)

    or:

        (False, None)

    Matching methods:

    1. Exact token
    2. Direct substring
    3. difflib fuzzy token matching
    """

    normalized = normalize_name(name)

    if not normalized:
        return False, None


    # ========================================================
    # TOKENIZE
    # ========================================================

    tokens = [
        token
        for token in normalized.split()
        if len(token) >= 2
    ]


    # ========================================================
    # 1. EXACT TOKEN MATCH
    # ========================================================

    for pattern in CHANNEL_PATTERNS:

        if pattern in tokens:

            return (
                True,
                f"exact:{pattern}"
            )


    # ========================================================
    # 2. DIRECT SUBSTRING MATCH
    #
    # Useful for names such as:
    #
    # "your-collabs"
    # "collabs-only"
    # "myclbs"
    # ========================================================

    compact_name = normalized.replace(" ", "")

    for pattern in CHANNEL_PATTERNS:

        if pattern in compact_name:

            return (
                True,
                f"contains:{pattern}"
            )


    # ========================================================
    # 3. DIFFLIB FUZZY MATCH
    #
    # Example:
    #
    # "cllbs" -> "clbs"
    # "collb" -> "collab"
    # "sponsers" -> "sponsors"
    # ========================================================

    for token in tokens:

        # Don't compare giant text fragments
        if len(token) > 25:
            continue


        for pattern in CHANNEL_PATTERNS:

            # Very different lengths usually aren't useful.
            if abs(
                len(token) - len(pattern)
            ) > 4:

                continue


            similarity = difflib.SequenceMatcher(
                None,
                token,
                pattern
            ).ratio()


            if similarity >= FUZZY_THRESHOLD:

                return (
                    True,
                    (
                        f"fuzzy:{token}"
                        f"->{pattern}"
                        f":{similarity:.2f}"
                    )
                )


    return False, None


# ============================================================
# CHECK WHETHER CHANNEL CAN BE CONSIDERED
# ============================================================

def is_text_like_channel(channel):
    """
    We only want channels that behave like text channels.

    Categories/voice/stage channels should not be included.
    """

    try:

        # Guild text channels should have send()
        # and are not categories.
        if not hasattr(channel, "send"):
            return False


        # Explicitly reject common non-text channel types
        channel_type = getattr(
            channel,
            "type",
            None
        )


        blocked_types = {
            getattr(
                discord.ChannelType,
                "category",
                None
            ),

            getattr(
                discord.ChannelType,
                "voice",
                None
            ),

            getattr(
                discord.ChannelType,
                "stage_voice",
                None
            )
        }


        if channel_type in blocked_types:
            return False


        return True


    except Exception:

        return False


# ============================================================
# BUILD MASS-SETC COMMANDS
# ============================================================

def build_mass_commands(channel_ids):
    """
    Converts:

        [123, 456, 789]

    into:

        ,mass-setc 123 456 789

    Automatically creates multiple commands when necessary.
    """

    commands_to_send = []

    current = f"{PREFIX}mass-setc"


    for channel_id in channel_ids:

        addition = f" {channel_id}"


        if (
            len(current)
            + len(addition)
            > MAX_COMMAND_LENGTH
        ):

            commands_to_send.append(current)

            current = (
                f"{PREFIX}mass-setc "
                f"{channel_id}"
            )


        else:

            current += addition


    if current != f"{PREFIX}mass-setc":

        commands_to_send.append(current)


    return commands_to_send


# ============================================================
# COG
# ============================================================

class FindNew(commands.Cog):

    def __init__(self, bot):

        self.bot = bot


    # ========================================================
    # ,findnew
    # ========================================================

    @commands.command(
        name="findnew"
    )
    async def findnew(
        self,
        ctx
    ):

        # ----------------------------------------------------
        # START MESSAGE
        # ----------------------------------------------------

        status_message = None

        try:

            status_message = await ctx.send(
                "🔎 **FindNew started**\n"
                "Scanning every server for possible "
                "collab/promo channels..."
            )

        except Exception:

            pass


        # ----------------------------------------------------
        # LOAD FRESH JSON DATA
        # ----------------------------------------------------

        try:

            data = load_data()

        except Exception as e:

            return await ctx.send(
                "❌ Failed to load channel database:\n"
                f"`{type(e).__name__}: {e}`"
            )


        # ----------------------------------------------------
        # EXISTING IDS
        # ----------------------------------------------------

        existing_ids = set()


        for entry in data.get(
            "channels",
            []
        ):

            try:

                channel_id = int(
                    entry.get("id")
                )

                existing_ids.add(
                    channel_id
                )

            except (
                TypeError,
                ValueError,
                AttributeError
            ):

                continue


        # ----------------------------------------------------
        # RESULTS
        # ----------------------------------------------------

        found_ids = []

        found_set = set()

        found_details = []


        # ----------------------------------------------------
        # STATS
        # ----------------------------------------------------

        servers_scanned = 0

        channels_scanned = 0

        already_saved_matches = 0

        new_matches = 0

        server_errors = 0

        channel_errors = 0


        # ----------------------------------------------------
        # SNAPSHOT SERVER LIST
        # ----------------------------------------------------

        try:

            guilds = list(
                self.bot.guilds
            )

        except Exception as e:

            return await ctx.send(
                "❌ Couldn't read server list:\n"
                f"`{type(e).__name__}: {e}`"
            )


        if not guilds:

            return await ctx.send(
                "⚠️ No servers were found."
            )


        # ====================================================
        # SCAN EVERY SERVER
        # ====================================================

        for guild in guilds:

            try:

                servers_scanned += 1


                # --------------------------------------------
                # Prefer text_channels
                # --------------------------------------------

                try:

                    channels = list(
                        guild.text_channels
                    )

                except Exception:

                    # Fallback
                    channels = list(
                        getattr(
                            guild,
                            "channels",
                            []
                        )
                    )


                # ============================================
                # SCAN CHANNELS
                # ============================================

                for channel in channels:

                    try:

                        if not is_text_like_channel(
                            channel
                        ):

                            continue


                        channels_scanned += 1


                        channel_name = getattr(
                            channel,
                            "name",
                            ""
                        )


                        if not channel_name:

                            continue


                        # ------------------------------------
                        # PATTERN TEST
                        # ------------------------------------

                        matched, reason = channel_matches(
                            channel_name
                        )


                        if not matched:

                            continue


                        # ------------------------------------
                        # GET ID
                        # ------------------------------------

                        try:

                            channel_id = int(
                                channel.id
                            )

                        except (
                            TypeError,
                            ValueError,
                            AttributeError
                        ):

                            continue


                        # ------------------------------------
                        # ALREADY SAVED?
                        # ------------------------------------

                        if channel_id in existing_ids:

                            already_saved_matches += 1

                            continue


                        # ------------------------------------
                        # ALREADY FOUND DURING THIS SCAN?
                        # ------------------------------------

                        if channel_id in found_set:

                            continue


                        # ------------------------------------
                        # NEW!
                        # ------------------------------------

                        found_set.add(
                            channel_id
                        )

                        found_ids.append(
                            channel_id
                        )

                        new_matches += 1


                        found_details.append({

                            "id": channel_id,

                            "guild": getattr(
                                guild,
                                "name",
                                "Unknown Server"
                            ),

                            "channel": channel_name,

                            "reason": reason

                        })


                    except Exception as e:

                        channel_errors += 1

                        print(
                            "[findnew] Channel error:",
                            getattr(
                                guild,
                                "name",
                                "Unknown"
                            ),
                            getattr(
                                channel,
                                "name",
                                "Unknown"
                            ),
                            type(e).__name__,
                            e
                        )

                        continue


            except Exception as e:

                server_errors += 1

                print(
                    "[findnew] Server error:",
                    getattr(
                        guild,
                        "name",
                        "Unknown"
                    ),
                    type(e).__name__,
                    e
                )

                continue


        # ====================================================
        # NOTHING FOUND
        # ====================================================

        if not found_ids:

            result = (

                "✅ **FindNew finished**\n\n"

                f"Servers scanned: **{servers_scanned}**\n"

                f"Channels scanned: **{channels_scanned}**\n"

                f"Matching channels already saved: "
                f"**{already_saved_matches}**\n"

                "New matching channels: **0**\n\n"

                "🎉 No new matching collab/promo "
                "channels were found."

            )


            if server_errors:

                result += (
                    f"\nServer errors: "
                    f"**{server_errors}**"
                )


            if channel_errors:

                result += (
                    f"\nChannel errors: "
                    f"**{channel_errors}**"
                )


            return await ctx.send(
                result
            )


        # ====================================================
        # BUILD MASS-SETC
        # ====================================================

        try:

            commands_to_send = build_mass_commands(
                found_ids
            )

        except Exception as e:

            return await ctx.send(
                "❌ Found channels, but failed to build "
                "`mass-setc` command:\n"
                f"`{type(e).__name__}: {e}`"
            )


        # ====================================================
        # FINISHED SUMMARY
        # ====================================================

        summary = [

            "✅ **FindNew finished**",

            "",

            f"Servers scanned: "
            f"**{servers_scanned}**",

            f"Channels scanned: "
            f"**{channels_scanned}**",

            f"Matching channels already saved: "
            f"**{already_saved_matches}**",

            f"New matching channels: "
            f"**{new_matches}**",

            f"`mass-setc` commands generated: "
            f"**{len(commands_to_send)}**",

        ]


        if server_errors:

            summary.append(
                f"Server errors: "
                f"**{server_errors}**"
            )


        if channel_errors:

            summary.append(
                f"Channel errors: "
                f"**{channel_errors}**"
            )


        await ctx.send(
            "\n".join(summary)
        )


        # ====================================================
        # OPTIONAL MATCH PREVIEW
        # ====================================================

        preview_lines = []


        for item in found_details[:20]:

            preview_lines.append(

                f"• **{item['guild']}** / "
                f"`#{item['channel']}`\n"
                f"  `{item['id']}` "
                f"({item['reason']})"

            )


        if preview_lines:

            preview_text = (
                "🔍 **New matches preview:**\n"
                + "\n".join(
                    preview_lines
                )
            )


            # Safety in case fancy Discord names
            # make the message too long.
            if len(preview_text) <= 1900:

                await ctx.send(
                    preview_text
                )


        if len(found_details) > 20:

            await ctx.send(
                f"ℹ️ Showing first **20** matches above. "
                f"There are **{len(found_details)}** total."
            )


        # ====================================================
        # SEND READY-TO-PASTE COMMANDS
        # ====================================================

        await ctx.send(
            "📋 **Ready to paste:**"
        )


        for command_text in commands_to_send:

            try:

                await ctx.send(
                    f"```text\n"
                    f"{command_text}\n"
                    f"```"
                )

            except Exception as e:

                await ctx.send(
                    "⚠️ Failed sending one generated "
                    "command:\n"
                    f"`{type(e).__name__}: {e}`"
                )


# ============================================================
# SETUP
# ============================================================

async def setup(bot):

    await bot.add_cog(
        FindNew(bot)
    )
