# cogs/system.py

import asyncio
import json
import os
import logging
import time
import discord

from datetime import datetime, timedelta, timezone
from discord.ext import commands


# ============================================================
# CONFIG
# ============================================================

DATA_FILE = "/data/channels.json"

CYCLE_HOURS = 5

SEND_DELAY = 10

RETRY_DELAY = 5

MAX_RETRIES = 2


# ============================================================
# BATCH SYSTEM
# ============================================================

BATCH_SIZE = 3


# Only here can ANYONE use:
#
# ,continue
# ,recontinue
#
CONTROL_CHANNEL_ID = 1533686495712510073


# If discord.py gets stuck internally waiting on a huge
# 429 retry-after, stop this command instead of allowing
# the coroutine to remain alive for hours.
#
# This DOES NOT bypass Discord's rate limit.
# It simply abandons this send attempt.
SEND_TIMEOUT = 30


# If an HTTPException actually reaches us with a retry_after
# longer than this, don't sit and sleep for hours.
MAX_INLINE_RETRY_WAIT = 30


# ============================================================
# HELPERS
# ============================================================

def utc_now() -> datetime:

    return datetime.now(
        timezone.utc
    )


def default_data():

    return {

        "message": "",

        "channels": [],

        "auto": False,

        "next_run": None,

        "log_channel": None,

        "reverse": False,


        # ====================================================
        # BATCH STATE
        # ====================================================

        "batch_active": False,

        # Exact next channel index
        "batch_index": 0,

        # Snapshot of channel IDs for THIS cycle
        "batch_channel_ids": [],

        # Snapshot of promo message for THIS cycle
        "batch_message": None,

        "batch_reverse": False,

        "batch_started_at": None,


        # ====================================================
        # RECONTINUE STATE
        # ====================================================

        # Beginning of current 3-channel block.
        #
        # Example:
        #
        # batch 4-6
        # batch_attempt_start = 3
        #
        # If channel 5 breaks:
        #
        # ,continue
        #     resumes channel 5
        #
        # ,recontinue
        #     resets back to channel 4
        #
        "batch_attempt_start": 0,

        "batch_last_error": None
    }


# ============================================================
# LOAD DATA
# ============================================================

def load_data() -> dict:

    if not os.path.exists(
        DATA_FILE
    ):

        return default_data()


    with open(
        DATA_FILE,
        encoding="utf8"
    ) as fp:

        data = json.load(
            fp
        )


    # Backwards compatibility with your old channels.json.
    defaults = default_data()


    for key, value in defaults.items():

        data.setdefault(
            key,
            value
        )


    return data


# ============================================================
# SAVE DATA
# ============================================================

def save_data(
    data: dict
):

    folder = os.path.dirname(
        DATA_FILE
    )


    if folder:

        os.makedirs(
            folder,
            exist_ok=True
        )


    with open(
        DATA_FILE,
        "w",
        encoding="utf8"
    ) as fp:

        json.dump(

            data,

            fp,

            indent=2,

            ensure_ascii=False
        )


# ============================================================
# DISCORD HTTP LOG FORWARDER
# ============================================================

class DiscordRateLimitHandler(
    logging.Handler
):

    """
    Watches discord.http logs.

    When discord.py prints something such as:

        We are being rate limited...
        responded with 429...
        Retrying in 5793 seconds

    mirror that warning into the Discord control/log channel.
    """

    def __init__(
        self,
        cog
    ):

        super().__init__(
            level=logging.WARNING
        )

        self.cog = cog

        self.last_message = None

        self.last_time = 0


    def emit(
        self,
        record
    ):

        try:

            message = record.getMessage()


            lower = message.lower()


            if (
                "rate limit" not in lower
                and
                "429" not in lower
            ):

                return


            # Avoid recursive warning loops while we're
            # forwarding a rate-limit warning.
            if self.cog._forwarding_rate_log:

                return


            # Basic duplicate protection.
            now = time.monotonic()


            if (
                message == self.last_message
                and
                now - self.last_time < 5
            ):

                return


            self.last_message = message

            self.last_time = now


            loop = self.cog.bot.loop


            if loop.is_closed():

                return


            self.cog._forwarding_rate_log = True


            loop.call_soon_threadsafe(

                lambda: asyncio.create_task(

                    self.cog._forward_rate_warning(
                        message
                    )

                )

            )


        except Exception:

            pass


# ============================================================
# COG
# ============================================================

class AutoPromo(
    commands.Cog
):

    """
    Auto Promo System

    ,start
        start forward

    ,startb
        start reverse

    Each invocation sends maximum 3 channels.

    ,continue
        next batch

    ,recontinue
        replay the current/failed batch

    The command DOES NOT wait around for the next command.
    """

    def __init__(
        self,
        bot: commands.Bot
    ):

        self.bot = bot

        self.data = load_data()

        self.loop_task: (
            asyncio.Task | None
        ) = None


        # Prevent two batches from running simultaneously.
        self.batch_lock = asyncio.Lock()


        # Rate-limit logger recursion guard
        self._forwarding_rate_log = False


        # ====================================================
        # ATTACH RATE-LIMIT LOG MIRROR
        # ====================================================

        self.http_logger = logging.getLogger(
            "discord.http"
        )


        self.rate_handler = (
            DiscordRateLimitHandler(
                self
            )
        )


        self.http_logger.addHandler(
            self.rate_handler
        )


        # ====================================================
        # RESTART RECOVERY
        # ====================================================
        #
        # Case 1:
        #
        # Halfway through 70-channel cycle
        #
        # batch_active = true
        #
        # Railway restart
        #
        # DO NOTHING automatically.
        #
        # Wait for:
        #
        # ,continue
        #
        #
        # Case 2:
        #
        # Full cycle already completed
        # and next_run exists
        #
        # Restart scheduler.
        # ====================================================

        if (

            self.data.get(
                "auto"
            )

            and

            not self.data.get(
                "batch_active"
            )

            and

            self.data.get(
                "next_run"
            )

        ):

            self._schedule_loop()


    # ========================================================
    # RATE LIMIT FORWARDING
    # ========================================================

    async def _forward_rate_warning(
        self,
        warning
    ):

        try:

            # Keep Discord message under limit
            if len(warning) > 1500:

                warning = warning[:1500]


            await self._log(

                "🚨 **Discord rate limit detected**\n"
                f"```text\n{warning}\n```\n"
                "The active send may be stopped by the "
                "batch timeout instead of waiting for hours.\n"
                "Do **not** repeatedly retry immediately; "
                "Discord's rate limit still applies."

            )


        except Exception as e:

            print(
                "Rate warning forward failed:",
                type(e).__name__,
                e
            )


        finally:

            self._forwarding_rate_log = False


    # ========================================================
    # LOG CHANNEL
    # ========================================================

    def _get_log_channel(
        self
    ):

        # First try the saved log channel
        log_ch = self.bot.get_channel(

            self.data.get(
                "log_channel",
                0
            )

        )


        if log_ch is not None:

            return log_ch


        # Then try your dedicated control channel
        control = self.bot.get_channel(
            CONTROL_CHANNEL_ID
        )


        if control is not None:

            return control


        # Final fallback
        for guild in self.bot.guilds:

            if guild.text_channels:

                return guild.text_channels[
                    0
                ]


        return None


    async def _log(
        self,
        message
    ):

        log_ch = (
            self._get_log_channel()
        )


        if not log_ch:

            return


        try:

            await log_ch.send(
                message
            )


        except Exception as e:

            print(
                "AutoPromo log error:",
                type(e).__name__,
                e
            )


    # ========================================================
    # CONTROL CHANNEL CHECK
    # ========================================================

    def _in_control_channel(
        self,
        channel
    ):

        try:

            return (
                channel.id
                ==
                CONTROL_CHANNEL_ID
            )


        except Exception:

            return False


    # ========================================================
    # FIND CHANNEL ENTRY
    # ========================================================

    def _find_channel_entry(
        self,
        channel_id
    ):

        for entry in self.data.get(
            "channels",
            []
        ):

            try:

                if int(
                    entry["id"]
                ) == int(
                    channel_id
                ):

                    return entry


            except Exception:

                continue


        return None


    # ========================================================
    # PREPARE NEW CYCLE
    # ========================================================

    def _prepare_new_cycle(
        self,
        reverse: bool
    ):

        self.data = load_data()


        order = list(

            self.data.get(
                "channels",
                []
            )

        )


        if reverse:

            order.reverse()


        channel_ids = []


        for entry in order:

            try:

                channel_ids.append(

                    int(
                        entry["id"]
                    )

                )


            except Exception:

                continue


        # ====================================================
        # SNAPSHOT THIS PARTICULAR CYCLE
        # ====================================================

        self.data[
            "batch_active"
        ] = True


        self.data[
            "batch_index"
        ] = 0


        self.data[
            "batch_attempt_start"
        ] = 0


        self.data[
            "batch_channel_ids"
        ] = channel_ids


        self.data[
            "batch_message"
        ] = self.data.get(
            "message",
            ""
        )


        self.data[
            "batch_reverse"
        ] = reverse


        self.data[
            "batch_started_at"
        ] = utc_now().isoformat()


        self.data[
            "batch_last_error"
        ] = None


        # While we're between batches there is
        # no automatic next_run yet.
        self.data[
            "next_run"
        ] = None


        save_data(
            self.data
        )


    # ========================================================
    # START COMMON
    # ========================================================

    async def _common_start(
        self,
        ctx: commands.Context,
        reverse: bool
    ):

        self.data = load_data()


        if not self.data.get(
            "message"
        ):

            return await ctx.send(

                "⚠️ Set a message first "
                "with `,setm …`."

            )


        if not self.data.get(
            "channels"
        ):

            return await ctx.send(

                "⚠️ Add promo channels "
                "with `,setc …` first."

            )


        if self.data.get(
            "batch_active"
        ):

            current = int(

                self.data.get(
                    "batch_index",
                    0
                )

            )


            total = len(

                self.data.get(
                    "batch_channel_ids",
                    []
                )

            )


            return await ctx.send(

                "ℹ️ A promo cycle is already active.\n"

                f"Progress: **{current}/{total}**\n\n"

                f"Use `,continue` in "
                f"<#{CONTROL_CHANNEL_ID}>."

            )


        if self.data.get(
            "auto"
        ):

            return await ctx.send(

                "ℹ️ Auto-cycle is already running."

            )


        self.data[
            "auto"
        ] = True


        self.data[
            "log_channel"
        ] = ctx.channel.id


        self.data[
            "reverse"
        ] = reverse


        save_data(
            self.data
        )


        self._prepare_new_cycle(
            reverse
        )


        await ctx.send(

            f"✅ Auto-cycle started "
            f"({'reverse' if reverse else 'forward'}) "
            f"— first **{BATCH_SIZE}** channels now."

        )


        # ====================================================
        # IMPORTANT
        #
        # Executes first 3.
        #
        # _run_next_batch RETURNS afterward.
        #
        # There is NO wait_for().
        # There is NO command waiting for ,continue.
        # ====================================================

        await self._run_next_batch()


    # ========================================================
    # ,start
    # ========================================================

    @commands.command()
    async def start(
        self,
        ctx
    ):

        """Process channels first→last."""

        await self._common_start(

            ctx,

            reverse=False

        )


    # ========================================================
    # ,startb
    # ========================================================

    @commands.command()
    async def startb(
        self,
        ctx
    ):

        """Process channels last→first."""

        await self._common_start(

            ctx,

            reverse=True

        )


    # ========================================================
    # INTERNAL CONTINUE
    # ========================================================

    async def _handle_continue(
        self,
        ctx,
        *,
        replay=False
    ):

        # ====================================================
        # ONLY THIS CHANNEL
        # ====================================================

        if not self._in_control_channel(
            ctx.channel
        ):

            return


        if self.batch_lock.locked():

            return await ctx.send(

                "⚠️ A 3-channel batch is "
                "already processing."

            )


        self.data = load_data()


        if not self.data.get(
            "auto"
        ):

            return await ctx.send(

                "ℹ️ Auto-cycle isn't running."

            )


        if not self.data.get(
            "batch_active"
        ):

            return await ctx.send(

                "ℹ️ There is no unfinished cycle."

            )


        total = len(

            self.data.get(
                "batch_channel_ids",
                []
            )

        )


        current = int(

            self.data.get(
                "batch_index",
                0
            )

        )


        # ====================================================
        # RECONTINUE
        # ====================================================

        if replay:

            retry_start = int(

                self.data.get(
                    "batch_attempt_start",
                    current
                )

            )


            retry_start = max(
                0,
                min(
                    retry_start,
                    total
                )
            )


            self.data[
                "batch_index"
            ] = retry_start


            self.data[
                "batch_last_error"
            ] = None


            save_data(
                self.data
            )


            await ctx.send(

                "🔁 **Recontinue**\n"
                f"Replaying the current batch from "
                f"channel **{retry_start + 1}**.\n"
                f"Sending maximum **{BATCH_SIZE}** channels."

            )


        else:

            await ctx.send(

                "▶️ **Continue**\n"
                f"Resuming from channel "
                f"**{current + 1}**.\n"
                f"Sending maximum **{BATCH_SIZE}** channels."

            )


        # New command execution.
        await self._run_next_batch()


    # ========================================================
    # ,continue
    # ========================================================

    @commands.command(
        name="continue"
    )
    async def continue_cycle(
        self,
        ctx
    ):

        """
        For your own message.

        Other users are handled by on_message below.
        """

        # Wrong channel = no action.
        if not self._in_control_channel(
            ctx.channel
        ):

            return


        # If message is from somebody else,
        # listener below handles it.
        #
        # This prevents duplicate execution when
        # commands.process_commands also sees it.
        if (

            self.bot.user

            and

            ctx.author.id
            !=
            self.bot.user.id

        ):

            return


        await self._handle_continue(

            ctx,

            replay=False

        )


    # ========================================================
    # ,recontinue
    # ========================================================

    @commands.command(
        name="recontinue"
    )
    async def recontinue_cycle(
        self,
        ctx
    ):

        """
        Replay current 3-channel block.
        """

        if not self._in_control_channel(
            ctx.channel
        ):

            return


        if (

            self.bot.user

            and

            ctx.author.id
            !=
            self.bot.user.id

        ):

            return


        await self._handle_continue(

            ctx,

            replay=True

        )


    # ========================================================
    # LISTENER
    #
    # This allows OTHER PEOPLE to trigger these two commands
    # in your dedicated channel even if main.py has your
    # normal "only respond to myself" global command check.
    # ========================================================

    @commands.Cog.listener()
    async def on_message(
        self,
        message: discord.Message
    ):

        try:

            # Only dedicated channel
            if message.channel.id != (
                CONTROL_CHANNEL_ID
            ):

                return


            # Your own messages are handled through
            # the normal command framework.
            if (

                self.bot.user

                and

                message.author.id
                ==
                self.bot.user.id

            ):

                return


            content = (
                message.content
                or ""
            ).strip().lower()


            if content not in {

                ",continue",

                ",recontinue"

            }:

                return


            # Build context manually.
            ctx = await self.bot.get_context(
                message
            )


            if content == ",continue":

                await self._handle_continue(

                    ctx,

                    replay=False

                )


            elif content == ",recontinue":

                await self._handle_continue(

                    ctx,

                    replay=True

                )


        except Exception as e:

            print(

                "External continue listener error:",

                type(e).__name__,

                e

            )


    # ========================================================
    # ,stop
    # ========================================================

    @commands.command()
    async def stop(
        self,
        ctx
    ):

        # Always reload newest JSON
        self.data = load_data()


        if not self.data.get(
            "auto"
        ):

            return await ctx.send(

                "ℹ️ Auto-cycle isn’t running."

            )


        self.data[
            "auto"
        ] = False


        self.data[
            "next_run"
        ] = None


        self.data[
            "batch_active"
        ] = False


        self.data[
            "batch_index"
        ] = 0


        self.data[
            "batch_attempt_start"
        ] = 0


        self.data[
            "batch_channel_ids"
        ] = []


        self.data[
            "batch_message"
        ] = None


        self.data[
            "batch_started_at"
        ] = None


        self.data[
            "batch_last_error"
        ] = None


        save_data(
            self.data
        )


        if (

            self.loop_task

            and

            not self.loop_task.done()

        ):

            self.loop_task.cancel()


        await ctx.send(

            "🛑 Auto-cycle stopped."

        )


    # ========================================================
    # SCHEDULING
    # ========================================================

    def _schedule_loop(
        self
    ):

        if (

            self.loop_task

            and

            not self.loop_task.done()

        ):

            self.loop_task.cancel()


        self.loop_task = (

            self.bot.loop.create_task(

                self._loop()

            )

        )


    # ========================================================
    # 5-HOUR SCHEDULER
    # ========================================================

    async def _loop(
        self
    ):

        try:

            self.data = load_data()


            if not self.data.get(
                "auto"
            ):

                return


            # Half-finished batch exists.
            #
            # Do NOT automatically continue.
            if self.data.get(
                "batch_active"
            ):

                return


            next_run = self.data.get(
                "next_run"
            )


            if not next_run:

                return


            delay = max(

                0,

                (

                    datetime.fromisoformat(
                        next_run
                    )

                    -

                    utc_now()

                ).total_seconds()

            )


            await asyncio.sleep(
                delay
            )


            # Fresh state after sleeping
            self.data = load_data()


            if not self.data.get(
                "auto"
            ):

                return


            if self.data.get(
                "batch_active"
            ):

                return


            reverse = bool(

                self.data.get(
                    "reverse",
                    False
                )

            )


            self._prepare_new_cycle(
                reverse
            )


            await self._log(

                f"⏰ Scheduled cycle started "
                f"({'reverse' if reverse else 'forward'}) "
                f"— sending first "
                f"**{BATCH_SIZE}** channels."

            )


            # =================================================
            # FIRST 3 ONLY
            #
            # Then _loop itself ends.
            # =================================================

            await self._run_next_batch()


            return


        except asyncio.CancelledError:

            pass


        except Exception as e:

            print(

                "AutoPromo loop error:",

                type(e).__name__,

                e

            )


    # ========================================================
    # SAVE CURRENT ERROR WITHOUT DESTROYING NEWER JSON DATA
    # ========================================================

    def _save_batch_error(
        self,
        position,
        error_text
    ):

        self.data = load_data()


        # DO NOT advance index.
        #
        # This means regular ,continue resumes
        # at this same failed channel.
        self.data[
            "batch_index"
        ] = position


        self.data[
            "batch_last_error"
        ] = error_text


        save_data(
            self.data
        )


    # ========================================================
    # RUN ONE 3-CHANNEL BATCH
    # ========================================================

    async def _run_next_batch(
        self
    ):

        """
        IMPORTANT ARCHITECTURE:

        ONE invocation
            ↓
        sends max 3
            ↓
        saves position
            ↓
        RETURNS

        There is NO:

            wait_for(",continue")

        There is NO:

            while waiting for user

        There is NO sleeping task waiting for the next batch.

        ,continue is an entirely new Discord command execution.
        """

        async with self.batch_lock:

            self.data = load_data()


            if not self.data.get(
                "auto"
            ):

                return


            if not self.data.get(
                "batch_active"
            ):

                return


            channel_ids = list(

                self.data.get(
                    "batch_channel_ids",
                    []
                )

            )


            total = len(
                channel_ids
            )


            index = int(

                self.data.get(
                    "batch_index",
                    0
                )

            )


            promo = (

                self.data.get(
                    "batch_message"
                )

                or

                self.data.get(
                    "message",
                    ""
                )

            )


            reverse = bool(

                self.data.get(
                    "batch_reverse",
                    False
                )

            )


            # =================================================
            # INVALID STATE
            # =================================================

            if total == 0:

                self.data[
                    "batch_active"
                ] = False


                self.data[
                    "batch_index"
                ] = 0


                self.data[
                    "batch_attempt_start"
                ] = 0


                self.data[
                    "batch_channel_ids"
                ] = []


                save_data(
                    self.data
                )


                await self._log(

                    "⚠️ Batch contained no channels."

                )


                return


            if index >= total:

                await self._finish_cycle(
                    total
                )

                return


            # =================================================
            # DEFINE THIS EXACT BLOCK
            # =================================================

            start_index = index


            end_index = min(

                start_index
                +
                BATCH_SIZE,

                total

            )


            # Store beginning of THIS command's batch.
            #
            # This is what ,recontinue will return to.
            self.data[
                "batch_attempt_start"
            ] = start_index


            self.data[
                "batch_last_error"
            ] = None


            save_data(
                self.data
            )


            await self._log(

                f"▶️ Batch started "
                f"({'reverse' if reverse else 'forward'})\n"
                f"Channels "
                f"**{start_index + 1}-{end_index}** "
                f"of **{total}**."

            )


            # =================================================
            # PROCESS MAXIMUM 3
            # =================================================

            for position in range(

                start_index,

                end_index

            ):

                channel_id = (
                    channel_ids[
                        position
                    ]
                )


                # Refresh disk before each channel
                self.data = load_data()


                chan = self.bot.get_channel(
                    channel_id
                )


                entry = (
                    self._find_channel_entry(
                        channel_id
                    )
                )


                # =============================================
                # MISSING CHANNEL
                # =============================================

                if chan is None:

                    channel_name = (

                        entry.get(
                            "channel_name",
                            str(channel_id)
                        )

                        if entry

                        else

                        str(channel_id)

                    )


                    await self._log(

                        f"⏩ `{channel_name}` missing"

                    )


                    # Missing = processed/skipped.
                    self.data[
                        "batch_index"
                    ] = position + 1


                    save_data(
                        self.data
                    )


                    continue


                # =============================================
                # SEND
                # =============================================

                sent = False


                for attempt in range(

                    1,

                    MAX_RETRIES + 2

                ):

                    try:

                        # =====================================
                        # CRITICAL:
                        #
                        # discord.py can internally sit on a
                        # 429 for hours.
                        #
                        # asyncio.wait_for prevents THIS batch
                        # command from staying alive forever.
                        # =====================================

                        await asyncio.wait_for(

                            chan.send(
                                promo
                            ),

                            timeout=SEND_TIMEOUT

                        )


                        # =====================================
                        # SUCCESS
                        # =====================================

                        self.data = load_data()


                        entry = (
                            self._find_channel_entry(
                                channel_id
                            )
                        )


                        if entry is not None:

                            entry[
                                "last_sent"
                            ] = (
                                utc_now().isoformat()
                            )


                        # Exact next position
                        self.data[
                            "batch_index"
                        ] = position + 1


                        self.data[
                            "batch_last_error"
                        ] = None


                        save_data(
                            self.data
                        )


                        await self._log(

                            f"✅ [{attempt}] "
                            f"{chan.guild.name}/"
                            f"#{chan.name}"

                        )


                        sent = True

                        break


                    # =========================================
                    # INTERNAL 429 / SEND HANG
                    # =========================================

                    except asyncio.TimeoutError:

                        error_text = (

                            f"Send timed out after "
                            f"{SEND_TIMEOUT}s on "
                            f"{chan.guild.name}/"
                            f"#{chan.name}"

                        )


                        self._save_batch_error(

                            position,

                            error_text

                        )


                        await self._log(

                            "🚨 **Batch stopped**\n\n"

                            f"Channel: "
                            f"**{position + 1}/{total}**\n"

                            f"Server: "
                            f"**{chan.guild.name}**\n"

                            f"Channel: "
                            f"`#{chan.name}`\n\n"

                            f"`chan.send()` did not complete "
                            f"within **{SEND_TIMEOUT}s**.\n"

                            "Discord may currently be "
                            "rate-limiting this send.\n\n"

                            f"Progress remains at "
                            f"**{position}/{total}**.\n\n"

                            "`,continue` = resume from this "
                            "failed channel\n"

                            "`,recontinue` = replay this whole "
                            "3-channel batch"

                        )


                        # =====================================
                        # COMMAND ENDS RIGHT NOW
                        # =====================================

                        return


                    # =========================================
                    # HTTP EXCEPTION
                    # =========================================

                    except discord.HTTPException as e:

                        wait = max(

                            RETRY_DELAY,

                            int(

                                getattr(

                                    e,

                                    "retry_after",

                                    RETRY_DELAY

                                )

                            )

                        )


                        # Long rate limit:
                        # DON'T leave command sleeping.
                        if wait > (
                            MAX_INLINE_RETRY_WAIT
                        ):

                            error_text = (

                                f"Discord requested "
                                f"{wait}s retry wait on "
                                f"{chan.guild.name}/"
                                f"#{chan.name}"

                            )


                            self._save_batch_error(

                                position,

                                error_text

                            )


                            await self._log(

                                "🚨 **Long Discord rate limit**\n\n"

                                f"Channel: "
                                f"**{position + 1}/{total}**\n"

                                f"Server: "
                                f"**{chan.guild.name}**\n"

                                f"Channel: "
                                f"`#{chan.name}`\n"

                                f"Retry-after: "
                                f"**{wait}s**\n\n"

                                "This batch command is ending "
                                "instead of sleeping for hours.\n\n"

                                "`,continue` resumes here later.\n"

                                "`,recontinue` replays this "
                                "3-channel block."

                            )


                            return


                        # Short normal retry
                        if attempt <= (
                            MAX_RETRIES
                        ):

                            await self._log(

                                f"⚠️ Rate-limit on "
                                f"{chan.guild.name}/"
                                f"#{chan.name} "
                                f"– retry in {wait}s "
                                f"({attempt}/"
                                f"{MAX_RETRIES + 1})"

                            )


                            await asyncio.sleep(
                                wait
                            )


                        else:

                            error_text = (

                                f"Give-up after "
                                f"{MAX_RETRIES + 1} attempts "
                                f"on {chan.guild.name}/"
                                f"#{chan.name}"

                            )


                            self._save_batch_error(

                                position,

                                error_text

                            )


                            await self._log(

                                f"❌ Give-up "
                                f"{chan.guild.name}/"
                                f"#{chan.name}\n\n"

                                "Batch stopped at this channel.\n"

                                "Use `,continue` later or "
                                "`,recontinue` to replay "
                                "the current batch."

                            )


                            return


                    # =========================================
                    # GENERIC ERROR
                    # =========================================

                    except Exception as e:

                        error_text = (

                            f"{type(e).__name__}: {e}"

                        )


                        self._save_batch_error(

                            position,

                            error_text

                        )


                        await self._log(

                            "❌ **Batch error**\n\n"

                            f"Channel: "
                            f"**{position + 1}/{total}**\n"

                            f"{chan.guild.name}/"
                            f"#{chan.name}\n\n"

                            f"`{type(e).__name__}: {e}`\n\n"

                            "`,continue` resumes from this "
                            "channel.\n"

                            "`,recontinue` replays the current "
                            "3-channel block."

                        )


                        return


                # =============================================
                # BETWEEN CHANNELS
                # =============================================

                if (

                    sent

                    and

                    position
                    <
                    end_index - 1

                ):

                    await asyncio.sleep(
                        SEND_DELAY
                    )


            # =================================================
            # BATCH IS DONE
            # =================================================

            self.data = load_data()


            current = int(

                self.data.get(
                    "batch_index",
                    0
                )

            )


            # =================================================
            # ALL CHANNELS FINISHED
            # =================================================

            if current >= total:

                await self._finish_cycle(
                    total
                )

                return


            # =================================================
            # PREPARE NEXT SEPARATE COMMAND
            # =================================================

            # Current next index becomes beginning of
            # next batch.
            self.data[
                "batch_attempt_start"
            ] = current


            self.data[
                "batch_last_error"
            ] = None


            save_data(
                self.data
            )


            remaining = (
                total - current
            )


            next_end = min(

                current
                +
                BATCH_SIZE,

                total

            )


            await self._log(

                "⏸️ **Batch finished — process ended**\n\n"

                f"Processed: "
                f"**{current}/{total}**\n"

                f"Remaining: "
                f"**{remaining}**\n\n"

                f"Next batch: "
                f"**{current + 1}-{next_end}**\n\n"

                f"Use `,continue` in "
                f"<#{CONTROL_CHANNEL_ID}>.\n\n"

                "This command is now completely finished. "
                "Nothing is waiting for the next command."

            )


            # =================================================
            # IMPORTANT:
            #
            # RETURN.
            #
            # No wait_for()
            # No pending continue task
            # No loop waiting for input
            #
            # "delivery guy disappears"
            # =================================================

            return


    # ========================================================
    # FINISH COMPLETE CYCLE
    # ========================================================

    async def _finish_cycle(
        self,
        total
    ):

        self.data = load_data()


        self.data[
            "batch_active"
        ] = False


        self.data[
            "batch_index"
        ] = 0


        self.data[
            "batch_attempt_start"
        ] = 0


        self.data[
            "batch_channel_ids"
        ] = []


        self.data[
            "batch_message"
        ] = None


        self.data[
            "batch_started_at"
        ] = None


        self.data[
            "batch_last_error"
        ] = None


        self.data[
            "next_run"
        ] = (

            utc_now()

            +

            timedelta(
                hours=CYCLE_HOURS
            )

        ).isoformat()


        save_data(
            self.data
        )


        await self._log(

            "🏁 **Cycle finished**\n\n"

            f"Processed: **{total}/{total}**\n"

            f"Next automatic cycle in "
            f"**{CYCLE_HOURS} hours**."

        )


        # One lightweight scheduler is needed only
        # for the next 5-hour automatic cycle.
        self._schedule_loop()


    # ========================================================
    # UNLOAD
    # ========================================================

    def cog_unload(
        self
    ):

        # Stop scheduler
        if (

            self.loop_task

            and

            not self.loop_task.done()

        ):

            self.loop_task.cancel()


        # Remove our logger handler
        try:

            self.http_logger.removeHandler(
                self.rate_handler
            )


        except Exception:

            pass


# ============================================================
# SETUP
# ============================================================

async def setup(
    bot
):

    await bot.add_cog(

        AutoPromo(
            bot
        )

    )
