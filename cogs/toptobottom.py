# cogs/toptobottom.py

import os
import json
import copy
import hashlib
import asyncio
import tempfile
import discord

from discord.ext import commands, tasks


# ============================================================
# CONFIG
# ============================================================

DATA_FILE = "/data/channels.json"

# Stores ONE previous complete channels.json snapshot.
PREVIOUS_FILE = "/data/channels.previous.json"

RESET_PASSWORD = "708099"

WATCH_INTERVAL = 0.5


# ============================================================
# DEFAULT DATA
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


# ============================================================
# BASIC HELPERS
# ============================================================

def ensure_folder(path: str):
    folder = os.path.dirname(path)

    if folder:
        os.makedirs(
            folder,
            exist_ok=True
        )


def file_hash(raw: bytes) -> str:
    return hashlib.sha256(raw).hexdigest()


# ============================================================
# VALIDATION
# ============================================================

def validate_data(data):
    """
    Validate the complete channels.json structure before
    allowing it to replace the live file.

    Returns:
        (True, None)

    or:
        (False, reason)
    """

    if not isinstance(data, dict):
        return False, "Root JSON must be an object/dictionary."


    if "channels" not in data:
        return False, "Missing `channels` field."


    channels = data["channels"]

    if not isinstance(channels, list):
        return False, "`channels` must be a list."


    seen_ids = set()


    for index, entry in enumerate(
        channels,
        start=1
    ):

        if not isinstance(entry, dict):

            return (
                False,
                f"Channel #{index} is not a dictionary."
            )


        if "id" not in entry:

            return (
                False,
                f"Channel #{index} has no `id`."
            )


        try:
            channel_id = int(
                entry["id"]
            )

        except (
            TypeError,
            ValueError
        ):

            return (
                False,
                f"Channel #{index} has an invalid ID."
            )


        if channel_id <= 0:

            return (
                False,
                f"Channel #{index} has an invalid ID."
            )


        if channel_id in seen_ids:

            return (
                False,
                f"Duplicate channel ID detected: "
                f"{channel_id}"
            )


        seen_ids.add(
            channel_id
        )


    return True, None


# ============================================================
# READ JSON
# ============================================================

def read_raw_file(path=DATA_FILE):

    if not os.path.exists(path):
        return None


    with open(
        path,
        "rb"
    ) as fp:

        return fp.read()


def load_data(path=DATA_FILE):

    if not os.path.exists(path):

        return default_data()


    with open(
        path,
        "r",
        encoding="utf8"
    ) as fp:

        data = json.load(fp)


    # Maintain compatibility with your other cogs
    data.setdefault(
        "message",
        ""
    )

    data.setdefault(
        "channels",
        []
    )

    data.setdefault(
        "auto",
        False
    )

    data.setdefault(
        "next_run",
        None
    )

    data.setdefault(
        "log_channel",
        None
    )

    data.setdefault(
        "reverse",
        False
    )


    valid, reason = validate_data(
        data
    )


    if not valid:

        raise ValueError(
            f"Invalid channels.json: {reason}"
        )


    return data


# ============================================================
# ATOMIC WRITE
# ============================================================

def atomic_write_json(
    path,
    data
):
    """
    Safely writes JSON using:

        temporary file
             ↓
        flush + fsync
             ↓
        os.replace()

    channels.json is therefore never intentionally left
    half-written.
    """

    valid, reason = validate_data(
        data
    )


    if not valid:

        raise ValueError(
            f"Refusing unsafe write: {reason}"
        )


    ensure_folder(
        path
    )


    folder = (
        os.path.dirname(path)
        or "."
    )


    fd, temp_path = tempfile.mkstemp(
        prefix=".channels_tmp_",
        suffix=".json",
        dir=folder
    )


    try:

        with os.fdopen(
            fd,
            "w",
            encoding="utf8"
        ) as fp:

            json.dump(
                data,
                fp,
                indent=2,
                ensure_ascii=False
            )

            fp.flush()

            os.fsync(
                fp.fileno()
            )


        # Verify TEMP file before replacing live JSON
        with open(
            temp_path,
            "r",
            encoding="utf8"
        ) as fp:

            verify_data = json.load(
                fp
            )


        valid, reason = validate_data(
            verify_data
        )


        if not valid:

            raise ValueError(
                f"Temporary JSON failed verification: "
                f"{reason}"
            )


        # Atomic replacement
        os.replace(
            temp_path,
            path
        )


    finally:

        if os.path.exists(
            temp_path
        ):

            try:
                os.remove(
                    temp_path
                )

            except Exception:
                pass


# ============================================================
# PREVIOUS SNAPSHOT
# ============================================================

def save_previous_raw(raw: bytes):

    if not raw:
        return


    ensure_folder(
        PREVIOUS_FILE
    )


    # Make sure snapshot itself is valid JSON
    try:

        decoded = raw.decode(
            "utf8"
        )

        data = json.loads(
            decoded
        )


        valid, reason = validate_data(
            data
        )


        if not valid:
            return


    except Exception:
        return


    folder = (
        os.path.dirname(PREVIOUS_FILE)
        or "."
    )


    fd, temp_path = tempfile.mkstemp(
        prefix=".previous_tmp_",
        suffix=".json",
        dir=folder
    )


    try:

        with os.fdopen(
            fd,
            "wb"
        ) as fp:

            fp.write(
                raw
            )

            fp.flush()

            os.fsync(
                fp.fileno()
            )


        os.replace(
            temp_path,
            PREVIOUS_FILE
        )


    finally:

        if os.path.exists(
            temp_path
        ):

            try:
                os.remove(
                    temp_path
                )

            except Exception:
                pass


# ============================================================
# MEMBER COUNT
# ============================================================

def get_guild_member_count(guild):
    """
    Best available server member count.

    Prefer guild.member_count because it does not require
    every member to be locally cached.
    """

    if guild is None:
        return None


    try:

        count = getattr(
            guild,
            "member_count",
            None
        )


        if count is not None:

            count = int(
                count
            )


            if count >= 0:

                return count


    except Exception:
        pass


    # Fallback
    try:

        members = getattr(
            guild,
            "members",
            None
        )


        if members is not None:

            return len(
                members
            )


    except Exception:
        pass


    return None


# ============================================================
# COG
# ============================================================

class TopToBottom(commands.Cog):

    def __init__(
        self,
        bot
    ):

        self.bot = bot

        self.operation_lock = asyncio.Lock()

        self._observed_raw = None

        self._internal_change = False


        # Initialize our observed state
        try:

            self._observed_raw = read_raw_file(
                DATA_FILE
            )

        except Exception as e:

            print(
                "[toptobottom] Initial file read failed:",
                type(e).__name__,
                e
            )


        self.watch_json.start()


    # ========================================================
    # BACKGROUND WATCHER
    # ========================================================

    @tasks.loop(
        seconds=WATCH_INTERVAL
    )
    async def watch_json(self):
        """
        Watches changes made by OTHER cogs.

        Example:

            current = 49 channels
                ↓
            ,setc adds #50
                ↓
            watcher notices channels.json changed
                ↓
            saves 49-channel file as previous

        This is what allows ,previous to undo a ,setc or
        ,mass-setc even though those commands live elsewhere.
        """

        try:

            raw = read_raw_file(
                DATA_FILE
            )


            if raw is None:
                return


            # First observation
            if self._observed_raw is None:

                self._observed_raw = raw

                return


            if file_hash(
                raw
            ) == file_hash(
                self._observed_raw
            ):

                return


            # A different cog changed channels.json.
            #
            # Save what WE last saw as the previous state.
            if not self._internal_change:

                save_previous_raw(
                    self._observed_raw
                )


            self._observed_raw = raw


        except Exception as e:

            print(
                "[toptobottom] Watcher error:",
                type(e).__name__,
                e
            )


    @watch_json.before_loop
    async def before_watch_json(self):

        await self.bot.wait_until_ready()


    # ========================================================
    # SAFETY CHECK
    # ========================================================

    async def require_stopped(
        self,
        ctx
    ):
        """
        DO NOT mutate channels.json while AutoPromo is running.

        Your system.py may later save its old in-memory copy and
        overwrite our modification.
        """

        try:

            data = load_data()


        except Exception as e:

            await ctx.send(
                "❌ Cannot safely read `channels.json`:\n"
                f"`{type(e).__name__}: {e}`"
            )

            return None


        if data.get(
            "auto"
        ):

            await ctx.send(
                "🛑 **Safety block**\n\n"
                "AutoPromo is currently running.\n"
                "Use `,stop` first and wait for it to confirm.\n\n"
                "I will **not** rearrange, restore, or reset "
                "`channels.json` while an auto-cycle is active."
            )

            return None


        return data


    # ========================================================
    # INTERNAL SAFE COMMIT
    # ========================================================

    async def safe_commit(
        self,
        old_raw,
        new_data
    ):
        """
        Backup current state and atomically write replacement.
        """

        # Ensure disk DID NOT change while we were processing.
        current_raw = read_raw_file(
            DATA_FILE
        )


        if current_raw is None:

            raise RuntimeError(
                "channels.json disappeared during operation."
            )


        if file_hash(
            current_raw
        ) != file_hash(
            old_raw
        ):

            raise RuntimeError(
                "channels.json changed while this command was "
                "working. No changes were made. Run the command "
                "again."
            )


        # Save old version before touching live JSON
        save_previous_raw(
            old_raw
        )


        self._internal_change = True


        try:

            atomic_write_json(
                DATA_FILE,
                new_data
            )


            # Read back + verify
            verify = load_data(
                DATA_FILE
            )


            expected_ids = [
                int(entry["id"])
                for entry in new_data["channels"]
            ]


            actual_ids = [
                int(entry["id"])
                for entry in verify["channels"]
            ]


            if expected_ids != actual_ids:

                raise RuntimeError(
                    "Post-write verification failed: "
                    "channel ordering differs."
                )


            self._observed_raw = read_raw_file(
                DATA_FILE
            )


        finally:

            self._internal_change = False


    # ========================================================
    # ,movetop
    # ========================================================

    @commands.command(
        name="movetop"
    )
    async def movetop(
        self,
        ctx
    ):
        """
        Rearrange entire channels list from highest-member
        server to lowest-member server.
        """

        if self.operation_lock.locked():

            return await ctx.send(
                "⚠️ Another channels.json operation is "
                "already running."
            )


        async with self.operation_lock:

            # =================================================
            # AUTO MUST BE STOPPED
            # =================================================

            data = await self.require_stopped(
                ctx
            )


            if data is None:
                return


            # =================================================
            # BACKUP SOURCE BYTES
            # =================================================

            try:

                original_raw = read_raw_file(
                    DATA_FILE
                )


                if original_raw is None:

                    return await ctx.send(
                        "❌ `channels.json` does not exist."
                    )


            except Exception as e:

                return await ctx.send(
                    "❌ Could not read `channels.json`:\n"
                    f"`{type(e).__name__}: {e}`"
                )


            channels = data.get(
                "channels",
                []
            )


            if not channels:

                return await ctx.send(
                    "ℹ️ There are no saved channels to arrange."
                )


            await ctx.send(
                "📊 **MoveTop started**\n"
                f"Checking **{len(channels)}** saved channels "
                "and ranking their servers by member count..."
            )


            # =================================================
            # BUILD RANKING
            # =================================================

            ranked = []

            missing = []

            errors = 0


            for original_index, entry in enumerate(
                channels
            ):

                try:

                    channel_id = int(
                        entry["id"]
                    )


                    # =========================================
                    # CACHE FIRST
                    # =========================================

                    channel = self.bot.get_channel(
                        channel_id
                    )


                    # =========================================
                    # FETCH IF NOT CACHED
                    # =========================================

                    if channel is None:

                        try:

                            channel = await self.bot.fetch_channel(
                                channel_id
                            )

                        except Exception:

                            channel = None


                    # =========================================
                    # MISSING
                    # =========================================

                    if channel is None:

                        missing.append(
                            {
                                "entry": copy.deepcopy(
                                    entry
                                ),
                                "index": original_index
                            }
                        )

                        continue


                    guild = getattr(
                        channel,
                        "guild",
                        None
                    )


                    if guild is None:

                        missing.append(
                            {
                                "entry": copy.deepcopy(
                                    entry
                                ),
                                "index": original_index
                            }
                        )

                        continue


                    # =========================================
                    # MEMBER COUNT
                    # =========================================

                    member_count = get_guild_member_count(
                        guild
                    )


                    if member_count is None:

                        missing.append(
                            {
                                "entry": copy.deepcopy(
                                    entry
                                ),
                                "index": original_index
                            }
                        )

                        continue


                    ranked.append(
                        {
                            # Preserve the EXACT original entry
                            # and all fields such as last_sent.
                            "entry": copy.deepcopy(
                                entry
                            ),

                            "member_count": member_count,

                            "guild_name": getattr(
                                guild,
                                "name",
                                entry.get(
                                    "guild_name",
                                    "Unknown Server"
                                )
                            ),

                            "channel_name": getattr(
                                channel,
                                "name",
                                entry.get(
                                    "channel_name",
                                    "Unknown Channel"
                                )
                            ),

                            # Used to preserve previous order
                            # when two servers have same count.
                            "original_index": original_index
                        }
                    )


                except Exception as e:

                    errors += 1

                    print(
                        "[toptobottom] Ranking error:",
                        entry,
                        type(e).__name__,
                        e
                    )


                    missing.append(
                        {
                            "entry": copy.deepcopy(
                                entry
                            ),
                            "index": original_index
                        }
                    )


            # =================================================
            # SORT
            #
            # Biggest server first.
            #
            # Python sort is stable, so channels with identical
            # member counts keep their previous relative order.
            # =================================================

            ranked.sort(
                key=lambda item: (
                    -item["member_count"],
                    item["original_index"]
                )
            )


            # Missing/unresolvable channels go LAST,
            # but ARE NOT DELETED.
            missing.sort(
                key=lambda item: item["index"]
            )


            new_channels = [
                item["entry"]
                for item in ranked
            ]


            new_channels.extend(
                item["entry"]
                for item in missing
            )


            # =================================================
            # HARD SAFETY CHECK
            #
            # Same number of channels BEFORE and AFTER.
            # Same IDs BEFORE and AFTER.
            # =================================================

            old_ids = [
                int(entry["id"])
                for entry in channels
            ]


            new_ids = [
                int(entry["id"])
                for entry in new_channels
            ]


            if len(old_ids) != len(
                new_ids
            ):

                return await ctx.send(
                    "❌ **ABORTED.**\n"
                    "Safety validation found a different number "
                    "of channels after sorting.\n"
                    "Nothing was changed."
                )


            if set(
                old_ids
            ) != set(
                new_ids
            ):

                return await ctx.send(
                    "❌ **ABORTED.**\n"
                    "Safety validation found missing/extra "
                    "channel IDs after sorting.\n"
                    "Nothing was changed."
                )


            # =================================================
            # CREATE NEW DATA
            #
            # EVERYTHING except channel order stays unchanged.
            # =================================================

            new_data = copy.deepcopy(
                data
            )


            new_data["channels"] = (
                new_channels
            )


            # =================================================
            # COMMIT
            # =================================================

            try:

                await self.safe_commit(
                    original_raw,
                    new_data
                )


            except Exception as e:

                return await ctx.send(
                    "❌ **MoveTop aborted safely.**\n"
                    f"`{type(e).__name__}: {e}`\n\n"
                    "The operation refused to continue because "
                    "the file could not be safely verified."
                )


            # =================================================
            # RESULT
            # =================================================

            await ctx.send(
                "✅ **MoveTop finished safely**\n\n"
                f"Total channels: **{len(new_channels)}**\n"
                f"Ranked successfully: **{len(ranked)}**\n"
                f"Unresolved but preserved at bottom: "
                f"**{len(missing)}**\n"
                f"Errors: **{errors}**\n\n"
                "No channels were deleted.\n"
                "A complete previous snapshot was saved."
            )


            # =================================================
            # PREVIEW TOP 20
            # =================================================

            if ranked:

                lines = [
                    "🏆 **Top servers/channels:**"
                ]


                for index, item in enumerate(
                    ranked[:20],
                    start=1
                ):

                    lines.append(
                        f"{index}. "
                        f"**{item['member_count']:,}** members — "
                        f"{item['guild_name']} / "
                        f"`#{item['channel_name']}`"
                    )


                await ctx.send(
                    "\n".join(
                        lines
                    )[:1950]
                )


    # ========================================================
    # ,previous
    # ========================================================

    @commands.command(
        name="previous"
    )
    async def previous(
        self,
        ctx
    ):
        """
        Restore the immediately previous channels.json state.

        Works for changes detected from:
            ,setc
            ,mass-setc
            ,movetop
            ,reset
            etc.
        """

        if self.operation_lock.locked():

            return await ctx.send(
                "⚠️ Another channels.json operation is "
                "already running."
            )


        async with self.operation_lock:

            current_data = await self.require_stopped(
                ctx
            )


            if current_data is None:
                return


            try:

                current_raw = read_raw_file(
                    DATA_FILE
                )


                if current_raw is None:

                    return await ctx.send(
                        "❌ Current `channels.json` is missing."
                    )


                # =================================================
                # VERY RECENT EXTERNAL CHANGE
                #
                # If ,setc changed the file faster than the
                # 0.5-second watcher noticed, our in-memory
                # observed version itself is the previous version.
                # =================================================

                if (
                    self._observed_raw is not None
                    and
                    file_hash(
                        self._observed_raw
                    )
                    !=
                    file_hash(
                        current_raw
                    )
                ):

                    previous_raw = (
                        self._observed_raw
                    )


                else:

                    if not os.path.exists(
                        PREVIOUS_FILE
                    ):

                        return await ctx.send(
                            "⚠️ No previous channels.json "
                            "snapshot exists yet."
                        )


                    previous_raw = read_raw_file(
                        PREVIOUS_FILE
                    )


                if not previous_raw:

                    return await ctx.send(
                        "⚠️ Previous snapshot is empty."
                    )


                # =================================================
                # VALIDATE PREVIOUS
                # =================================================

                previous_data = json.loads(
                    previous_raw.decode(
                        "utf8"
                    )
                )


                valid, reason = validate_data(
                    previous_data
                )


                if not valid:

                    return await ctx.send(
                        "❌ Previous backup failed validation:\n"
                        f"`{reason}`\n"
                        "Current file was NOT changed."
                    )


                current_count = len(
                    current_data.get(
                        "channels",
                        []
                    )
                )


                previous_count = len(
                    previous_data.get(
                        "channels",
                        []
                    )
                )


                # =================================================
                # SAVE CURRENT AS THE NEW PREVIOUS
                #
                # This means ,previous can also act as a safe
                # one-step toggle if used again.
                # =================================================

                save_previous_raw(
                    current_raw
                )


                self._internal_change = True


                try:

                    atomic_write_json(
                        DATA_FILE,
                        previous_data
                    )


                    # Verify
                    verify = load_data(
                        DATA_FILE
                    )


                    if len(
                        verify["channels"]
                    ) != previous_count:

                        raise RuntimeError(
                            "Restored channel count failed "
                            "verification."
                        )


                    self._observed_raw = (
                        read_raw_file(
                            DATA_FILE
                        )
                    )


                finally:

                    self._internal_change = False


                await ctx.send(
                    "↩️ **Previous version restored safely**\n\n"
                    f"Before: **{current_count}** channels\n"
                    f"Restored: **{previous_count}** channels\n\n"
                    "The complete JSON was validated before "
                    "being restored."
                )


            except Exception as e:

                await ctx.send(
                    "❌ **Restore failed safely.**\n"
                    f"`{type(e).__name__}: {e}`\n\n"
                    "Current `channels.json` was not "
                    "intentionally erased."
                )


    # ========================================================
    # ,reset
    # ========================================================

    @commands.command(
        name="reset"
    )
    async def reset(
        self,
        ctx
    ):
        """
        Dangerous reset.

        Interactive password confirmation:

            ,reset
            bot asks for password
            user sends 708099
        """

        if self.operation_lock.locked():

            return await ctx.send(
                "⚠️ Another channels.json operation is "
                "already running."
            )


        async with self.operation_lock:

            data = await self.require_stopped(
                ctx
            )


            if data is None:
                return


            current_count = len(
                data.get(
                    "channels",
                    []
                )
            )


            # =================================================
            # WARNING
            # =================================================

            await ctx.send(
                "⚠️ **DANGER — RESET CHANNEL DATABASE**\n\n"
                f"This will remove all **{current_count}** "
                "saved channels and clear the promo state.\n\n"
                "A previous backup will be created first.\n\n"
                "To confirm, send the reset password within "
                "**60 seconds**."
            )


            # =================================================
            # PASSWORD CHECK
            # =================================================

            def check(message):

                if not self.bot.user:

                    return False


                return (
                    message.author.id
                    == self.bot.user.id

                    and

                    message.channel.id
                    == ctx.channel.id
                )


            try:

                reply = await self.bot.wait_for(
                    "message",
                    check=check,
                    timeout=60
                )


            except asyncio.TimeoutError:

                return await ctx.send(
                    "⏰ Reset cancelled — confirmation timed out."
                )


            if reply.content.strip() != RESET_PASSWORD:

                return await ctx.send(
                    "❌ Incorrect password. Reset cancelled."
                )


            # =================================================
            # RECHECK AUTO AFTER PASSWORD WAIT
            #
            # Someone could have used ,start during the 60-sec
            # confirmation period.
            # =================================================

            latest_data = await self.require_stopped(
                ctx
            )


            if latest_data is None:

                return


            # =================================================
            # READ CURRENT VERSION AGAIN
            # =================================================

            try:

                current_raw = read_raw_file(
                    DATA_FILE
                )


                if current_raw is None:

                    return await ctx.send(
                        "❌ `channels.json` disappeared. "
                        "Reset cancelled."
                    )


                # =================================================
                # RESET TO VALID EMPTY STRUCTURE
                #
                # DON'T write an empty file and DON'T write {}.
                #
                # Your other cogs expect these keys.
                # =================================================

                empty_data = default_data()


                # Save full previous JSON first
                save_previous_raw(
                    current_raw
                )


                self._internal_change = True


                try:

                    atomic_write_json(
                        DATA_FILE,
                        empty_data
                    )


                    verify = load_data(
                        DATA_FILE
                    )


                    if len(
                        verify["channels"]
                    ) != 0:

                        raise RuntimeError(
                            "Reset verification says channels "
                            "are not empty."
                        )


                    self._observed_raw = (
                        read_raw_file(
                            DATA_FILE
                        )
                    )


                finally:

                    self._internal_change = False


                await ctx.send(
                    "🗑️ **Reset complete**\n\n"
                    f"Removed: **{current_count}** channels\n"
                    "Saved channels now: **0**\n\n"
                    "The previous complete JSON is still "
                    "available through `,previous`."
                )


            except Exception as e:

                await ctx.send(
                    "❌ **Reset failed safely.**\n"
                    f"`{type(e).__name__}: {e}`"
                )


    # ========================================================
    # UNLOAD
    # ========================================================

    def cog_unload(
        self
    ):

        try:

            self.watch_json.cancel()

        except Exception:

            pass


# ============================================================
# SETUP
# ============================================================

async def setup(
    bot
):

    await bot.add_cog(
        TopToBottom(bot)
    )
