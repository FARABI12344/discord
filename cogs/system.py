# cogs/system.py

import asyncio, json, os, discord
from datetime import datetime, timedelta, timezone
from discord.ext import commands


DATA_FILE = "/data/channels.json"

CYCLE_HOURS = 5
SEND_DELAY = 10
RETRY_DELAY = 5
MAX_RETRIES = 2

# NEW:
BATCH_SIZE = 3


# ─────────── helpers ───────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def default_data():
    return {
        "message": "",
        "channels": [],
        "auto": False,
        "next_run": None,
        "log_channel": None,
        "reverse": False,

        # batch state
        "batch_active": False,
        "batch_index": 0,
        "batch_channel_ids": [],
        "batch_message": None,
        "batch_reverse": False,
        "batch_started_at": None
    }


def load_data() -> dict:

    if not os.path.exists(DATA_FILE):
        return default_data()

    with open(DATA_FILE, encoding="utf8") as fp:
        data = json.load(fp)

    # preserve compatibility with existing channels.json
    defaults = default_data()

    for key, value in defaults.items():
        data.setdefault(key, value)

    return data


def save_data(d: dict):

    folder = os.path.dirname(DATA_FILE)

    if folder:
        os.makedirs(folder, exist_ok=True)

    with open(DATA_FILE, "w", encoding="utf8") as fp:
        json.dump(
            d,
            fp,
            indent=2,
            ensure_ascii=False
        )


# ─────────── cog ───────────

class AutoPromo(commands.Cog):

    """
    Auto promo system.

    ,start
        forward cycle

    ,startb
        reverse cycle

    Each command sends only 3 channels.

    ,continue
        sends the next 3.

    When the entire cycle finishes, the next automatic cycle
    is scheduled for CYCLE_HOURS later.
    """

    def __init__(self, bot: commands.Bot):

        self.bot = bot

        self.data = load_data()

        self.loop_task: asyncio.Task | None = None

        self.batch_lock = asyncio.Lock()

        # -----------------------------------------------------
        # Restart recovery
        # -----------------------------------------------------
        #
        # If we're halfway through a batch-cycle,
        # DO NOT automatically continue sending.
        #
        # Wait for ,continue.
        #
        # If there is no active batch but auto is enabled,
        # resume the normal 5-hour scheduler.
        # -----------------------------------------------------

        if (
            self.data.get("auto")
            and
            not self.data.get("batch_active")
            and
            self.data.get("next_run")
        ):

            self._schedule_loop()


    # ========================================================
    # LOG CHANNEL
    # ========================================================

    def _get_log_channel(self):

        log_ch = self.bot.get_channel(
            self.data.get(
                "log_channel",
                0
            )
        )

        if log_ch is not None:
            return log_ch


        # fallback
        for guild in self.bot.guilds:

            if guild.text_channels:

                return guild.text_channels[0]


        return None


    async def _log(self, message):

        log_ch = self._get_log_channel()

        if log_ch:

            try:
                await log_ch.send(message)

            except Exception as e:

                print(
                    "AutoPromo log error:",
                    type(e).__name__,
                    e
                )


    # ========================================================
    # CREATE NEW CYCLE
    # ========================================================

    def _prepare_new_cycle(
        self,
        reverse: bool
    ):

        """
        Snapshot the channel IDs + promo message.

        This is important.

        If channels.json changes while you're halfway through
        the batch process, this cycle still knows exactly what
        channels were originally supposed to be processed.
        """

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
                    int(entry["id"])
                )

            except Exception:

                continue


        self.data["batch_active"] = True

        self.data["batch_index"] = 0

        self.data["batch_channel_ids"] = (
            channel_ids
        )

        self.data["batch_message"] = (
            self.data.get("message", "")
        )

        self.data["batch_reverse"] = reverse

        self.data["batch_started_at"] = (
            utc_now().isoformat()
        )

        # While we're waiting for ,continue,
        # there is no next automatic run yet.
        self.data["next_run"] = None

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


        if not self.data.get("message"):

            return await ctx.send(
                "⚠️ Set a message first with `,setm …`."
            )


        if not self.data.get("channels"):

            return await ctx.send(
                "⚠️ Add promo channels with `,setc …` first."
            )


        if self.data.get("batch_active"):

            current = self.data.get(
                "batch_index",
                0
            )

            total = len(
                self.data.get(
                    "batch_channel_ids",
                    []
                )
            )

            return await ctx.send(
                "ℹ️ A promo cycle is already waiting "
                "for continuation.\n"
                f"Progress: **{current}/{total}**\n"
                "Use `,continue`."
            )


        if self.data.get("auto"):

            return await ctx.send(
                "ℹ️ Auto-cycle is already running."
            )


        self.data["auto"] = True

        self.data["log_channel"] = (
            ctx.channel.id
        )

        self.data["reverse"] = reverse

        save_data(
            self.data
        )


        # Prepare exact cycle snapshot
        self._prepare_new_cycle(
            reverse
        )


        await ctx.send(
            f"✅ Auto-cycle started "
            f"({'reverse' if reverse else 'forward'}) "
            f"— sending first **{BATCH_SIZE}** now."
        )


        # Send only first batch.
        #
        # After this function completes, the command is DONE.
        await self._run_next_batch()


    # ========================================================
    # ,start
    # ========================================================

    @commands.command()
    async def start(self, ctx):

        """Process channels first→last."""

        await self._common_start(
            ctx,
            reverse=False
        )


    # ========================================================
    # ,startb
    # ========================================================

    @commands.command()
    async def startb(self, ctx):

        """Process channels last→first."""

        await self._common_start(
            ctx,
            reverse=True
        )


    # ========================================================
    # ,continue
    # ========================================================

    @commands.command(name="continue")
    async def continue_cycle(
        self,
        ctx
    ):

        """
        Completely separate command.

        Reloads saved batch position and sends next 3.
        """

        if self.batch_lock.locked():

            return await ctx.send(
                "⚠️ A batch is already being processed."
            )


        self.data = load_data()


        if not self.data.get("auto"):

            return await ctx.send(
                "ℹ️ Auto-cycle isn't running."
            )


        if not self.data.get("batch_active"):

            return await ctx.send(
                "ℹ️ There is no cycle waiting "
                "for `,continue`."
            )


        # Update log location to wherever continue
        # was invoked.
        self.data["log_channel"] = (
            ctx.channel.id
        )

        save_data(
            self.data
        )


        await ctx.send(
            f"▶️ Continuing — sending next "
            f"**{BATCH_SIZE}** channel(s)."
        )


        # Completely new invocation.
        await self._run_next_batch()


    # ========================================================
    # ,stop
    # ========================================================

    @commands.command()
    async def stop(self, ctx):

        # IMPORTANT:
        # always reload latest disk data
        self.data = load_data()


        if not self.data.get("auto"):

            return await ctx.send(
                "ℹ️ Auto-cycle isn’t running."
            )


        self.data["auto"] = False

        self.data["next_run"] = None


        # Cancel current batch state too
        self.data["batch_active"] = False

        self.data["batch_index"] = 0

        self.data["batch_channel_ids"] = []

        self.data["batch_message"] = None

        self.data["batch_started_at"] = None


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

    def _schedule_loop(self):

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


    async def _loop(self):

        try:

            # -------------------------------------------------
            # Important:
            #
            # This task handles only ONE scheduled wake-up.
            #
            # It does NOT sit around waiting for ,continue.
            # -------------------------------------------------

            self.data = load_data()


            if not self.data.get("auto"):

                return


            if self.data.get("batch_active"):

                # Waiting for manual ,continue
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
                    - utc_now()
                ).total_seconds()
            )


            await asyncio.sleep(
                delay
            )


            self.data = load_data()


            if not self.data.get("auto"):

                return


            if self.data.get("batch_active"):

                return


            # ================================================
            # NEW 5-HOUR CYCLE
            # ================================================

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
                f"— first {BATCH_SIZE} channels."
            )


            # Send first 3 only.
            await self._run_next_batch()


            # Then this loop task ENDS.
            #
            # If more remain, ,continue is required.
            # If the entire cycle finished, _run_next_batch()
            # schedules the next 5-hour wakeup.


        except asyncio.CancelledError:

            pass


        except Exception as e:

            print(
                "AutoPromo loop error:",
                type(e).__name__,
                e
            )


    # ========================================================
    # GET ENTRY FROM LIVE CHANNEL DATA
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
    # RUN NEXT BATCH
    # ========================================================

    async def _run_next_batch(self):

        """
        Sends AT MOST BATCH_SIZE channels.

        Then:

        • saves exact next index
        • prints ,continue message
        • RETURNS COMPLETELY

        No wait_for().
        No waiting coroutine.
        """

        async with self.batch_lock:

            # Always reload latest data
            self.data = load_data()


            if not self.data.get("auto"):

                return


            if not self.data.get("batch_active"):

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


            # ================================================
            # CORRUPT / EMPTY STATE
            # ================================================

            if total == 0:

                self.data["batch_active"] = False

                self.data["batch_index"] = 0

                self.data["batch_channel_ids"] = []

                save_data(
                    self.data
                )


                await self._log(
                    "⚠️ Batch contained no channels."
                )

                return


            # ================================================
            # BATCH RANGE
            # ================================================

            start_index = index

            end_index = min(
                index + BATCH_SIZE,
                total
            )


            await self._log(
                f"▶️ Batch started "
                f"({'reverse' if reverse else 'forward'}) "
                f"— channels "
                f"**{start_index + 1}-{end_index}** "
                f"of **{total}**"
            )


            # ================================================
            # SEND THIS BATCH ONLY
            # ================================================

            for position in range(
                start_index,
                end_index
            ):

                channel_id = (
                    channel_ids[position]
                )


                # Refresh current JSON before each send
                # so we don't intentionally overwrite newer
                # changes from another cog.
                latest = load_data()


                # Keep our current batch fields
                self.data = latest


                chan = self.bot.get_channel(
                    channel_id
                )


                entry = self._find_channel_entry(
                    channel_id
                )


                if chan is None:

                    channel_name = (
                        entry.get(
                            "channel_name",
                            str(channel_id)
                        )
                        if entry
                        else str(channel_id)
                    )


                    await self._log(
                        f"⏩ `{channel_name}` missing"
                    )


                    # This position counts as processed.
                    self.data["batch_index"] = (
                        position + 1
                    )

                    save_data(
                        self.data
                    )

                    continue


                # ============================================
                # SEND
                # ============================================

                for attempt in range(
                    1,
                    MAX_RETRIES + 2
                ):

                    try:

                        await chan.send(
                            promo
                        )


                        # Reload newest file BEFORE storing
                        # last_sent.
                        self.data = load_data()


                        entry = self._find_channel_entry(
                            channel_id
                        )


                        if entry is not None:

                            entry["last_sent"] = (
                                utc_now().isoformat()
                            )


                        # Record exact next position
                        self.data["batch_index"] = (
                            position + 1
                        )


                        save_data(
                            self.data
                        )


                        await self._log(
                            f"✅ [{attempt}] "
                            f"{chan.guild.name}/"
                            f"#{chan.name}"
                        )


                        break


                    except discord.HTTPException as e:

                        if attempt <= MAX_RETRIES:

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

                            await self._log(
                                f"❌ Give-up "
                                f"{chan.guild.name}/"
                                f"#{chan.name}"
                            )


                            # Count it as processed so
                            # ,continue does not loop forever
                            # on this exact channel.
                            self.data = load_data()

                            self.data[
                                "batch_index"
                            ] = position + 1

                            save_data(
                                self.data
                            )


                    except Exception as e:

                        await self._log(
                            f"❌ Error "
                            f"{chan.guild.name}/"
                            f"#{chan.name}: {e}"
                        )


                        # Count failed channel as processed.
                        self.data = load_data()

                        self.data[
                            "batch_index"
                        ] = position + 1

                        save_data(
                            self.data
                        )


                        break


                # Only delay BETWEEN channels in this batch
                if position < end_index - 1:

                    await asyncio.sleep(
                        SEND_DELAY
                    )


            # ================================================
            # RELOAD FINAL POSITION
            # ================================================

            self.data = load_data()


            current = int(
                self.data.get(
                    "batch_index",
                    0
                )
            )


            # ================================================
            # FULL CYCLE FINISHED
            # ================================================

            if current >= total:

                self.data["batch_active"] = False

                self.data["batch_index"] = 0

                self.data["batch_channel_ids"] = []

                self.data["batch_message"] = None

                self.data["batch_started_at"] = None


                # Schedule NEXT full cycle
                self.data["next_run"] = (
                    utc_now()
                    + timedelta(
                        hours=CYCLE_HOURS
                    )
                ).isoformat()


                save_data(
                    self.data
                )


                await self._log(
                    "🏁 **Cycle finished**\n"
                    f"Processed: **{total}/{total}**\n"
                    f"Next automatic cycle in "
                    f"**{CYCLE_HOURS} hours**."
                )


                # Start ONE sleeping scheduler task
                # for the next cycle.
                self._schedule_loop()


                return


            # ================================================
            # MORE CHANNELS REMAIN
            # ================================================

            remaining = (
                total - current
            )


            next_end = min(
                current + BATCH_SIZE,
                total
            )


            await self._log(
                "⏸️ **Batch finished**\n\n"
                f"Processed: **{current}/{total}**\n"
                f"Remaining: **{remaining}**\n\n"
                f"Next batch: channels "
                f"**{current + 1}-{next_end}**\n\n"
                "Use `,continue` to send the next batch."
            )


            # IMPORTANT:
            #
            # FUNCTION ENDS HERE.
            #
            # Nothing is waiting for ,continue.
            # No wait_for().
            # No infinite pause.
            #
            # A future ,continue command creates an entirely
            # new command invocation.


    # ========================================================
    # UNLOAD
    # ========================================================

    def cog_unload(self):

        if (
            self.loop_task
            and
            not self.loop_task.done()
        ):

            self.loop_task.cancel()


# ============================================================
# SETUP
# ============================================================

async def setup(bot):

    await bot.add_cog(
        AutoPromo(bot)
    )
