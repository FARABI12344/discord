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
# How many channels to process before waiting for ,continue
BATCH_SIZE = 3


# ─────────── helpers ───────────

def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def load_data() -> dict:

    if not os.path.exists(DATA_FILE):

        return {
            "message": "",
            "channels": [],
            "auto": False,
            "next_run": None,
            "log_channel": None,
            "reverse": False,

            # ── batch state ──
            "batch_active": False,
            "batch_order": [],
            "batch_index": 0,
            "batch_message": "",
            "batch_reverse": False,
            "batch_started_at": None
        }


    with open(
        DATA_FILE,
        encoding="utf8"
    ) as fp:

        data = json.load(fp)


    # Make sure older channels.json files still work
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


    # ── NEW batch fields ──

    data.setdefault(
        "batch_active",
        False
    )

    data.setdefault(
        "batch_order",
        []
    )

    data.setdefault(
        "batch_index",
        0
    )

    data.setdefault(
        "batch_message",
        ""
    )

    data.setdefault(
        "batch_reverse",
        False
    )

    data.setdefault(
        "batch_started_at",
        None
    )


    return data


def save_data(d: dict):

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
        forward order

    ,startb
        reverse order

    Each batch processes 3 channels.

    ,continue
        processes the next 3 channels.
    """


    def __init__(
        self,
        bot: commands.Bot
    ):

        self.bot = bot

        self.data = load_data()

        self.loop_task: asyncio.Task | None = None


        # Prevent two ,continue commands from
        # processing the same batch simultaneously.
        self.batch_lock = asyncio.Lock()


        if self.data.get(
            "auto"
        ):

            self._schedule_loop()


    # ========================================================
    # LOG CHANNEL
    # ========================================================

    def get_log_channel(
        self,
        data
    ):

        log_ch = self.bot.get_channel(
            data.get(
                "log_channel",
                0
            )
        )


        if log_ch is None:

            for guild in self.bot.guilds:

                if guild.text_channels:

                    log_ch = (
                        guild.text_channels[0]
                    )

                    break


        return log_ch


    async def log(
        self,
        data,
        message
    ):

        log_ch = self.get_log_channel(
            data
        )


        if log_ch:

            try:

                await log_ch.send(
                    message
                )

            except Exception as e:

                print(
                    "AutoPromo log error:",
                    e
                )


    # ========================================================
    # START
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
                "⚠️ Set a message first with `,setm …`."
            )


        if not self.data.get(
            "channels"
        ):

            return await ctx.send(
                "⚠️ Add promo channels with `,setc …` first."
            )


        if self.data.get(
            "auto"
        ):

            return await ctx.send(
                "ℹ️ Auto-cycle is already running."
            )


        # Clear any old/incomplete batch data
        self.data.update({

            "auto": True,

            "next_run": utc_now().isoformat(),

            "log_channel": ctx.channel.id,

            "reverse": reverse,

            "batch_active": False,

            "batch_order": [],

            "batch_index": 0,

            "batch_message": "",

            "batch_reverse": reverse,

            "batch_started_at": None

        })


        save_data(
            self.data
        )


        self._schedule_loop()


        await ctx.send(

            f"✅ Auto-cycle started "
            f"({'reverse' if reverse else 'forward'}) "
            f"– first batch now."

        )


    @commands.command()
    async def start(
        self,
        ctx
    ):

        """
        Process channels first→last every cycle.
        """

        await self._common_start(
            ctx,
            reverse=False
        )


    @commands.command()
    async def startb(
        self,
        ctx
    ):

        """
        Process channels last→first every cycle.
        """

        await self._common_start(
            ctx,
            reverse=True
        )


    # ========================================================
    # STOP
    # ========================================================

    @commands.command()
    async def stop(
        self,
        ctx
    ):

        # IMPORTANT:
        # always load newest JSON before saving
        self.data = load_data()


        if not self.data.get(
            "auto"
        ):

            return await ctx.send(
                "ℹ️ Auto-cycle isn’t running."
            )


        self.data["auto"] = False

        self.data["next_run"] = None


        # Stop / erase current batch progress
        self.data["batch_active"] = False

        self.data["batch_order"] = []

        self.data["batch_index"] = 0

        self.data["batch_message"] = ""

        self.data["batch_reverse"] = False

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
        Continue the currently paused cycle
        by processing the next 3 channels.
        """

        # Prevent:
        #
        # ,continue
        # ,continue
        #
        # being executed simultaneously.

        if self.batch_lock.locked():

            return await ctx.send(
                "⚠️ A batch is already being processed."
            )


        async with self.batch_lock:

            self.data = load_data()


            # -----------------------------------------------
            # AUTO NOT RUNNING
            # -----------------------------------------------

            if not self.data.get(
                "auto"
            ):

                return await ctx.send(
                    "ℹ️ There is no active auto-cycle."
                )


            # -----------------------------------------------
            # NO PAUSED BATCH
            # -----------------------------------------------

            if not self.data.get(
                "batch_active"
            ):

                return await ctx.send(
                    "ℹ️ There is currently no batch "
                    "waiting for `,continue`."
                )


            order = self.data.get(
                "batch_order",
                []
            )


            index = int(
                self.data.get(
                    "batch_index",
                    0
                )
            )


            total = len(
                order
            )


            if index >= total:

                return await ctx.send(
                    "ℹ️ That cycle is already finished."
                )


            await ctx.send(

                f"▶️ Continuing cycle...\n"
                f"Processed: **{index}/{total}**\n"
                f"Remaining: **{total - index}**"

            )


            await self._send_next_batch()


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


    async def _loop(
        self
    ):

        try:

            while True:

                # Always read newest disk state.
                self.data = load_data()


                if not self.data.get(
                    "auto"
                ):

                    break


                # =================================================
                # IF A BATCH IS PAUSED:
                #
                # DO NOTHING.
                #
                # Wait forever for the user to send:
                #
                # ,continue
                #
                # =================================================

                if self.data.get(
                    "batch_active"
                ):

                    await asyncio.sleep(
                        2
                    )

                    continue


                next_run = self.data.get(
                    "next_run"
                )


                if not next_run:

                    await asyncio.sleep(
                        2
                    )

                    continue


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


                # Reload after sleeping.
                self.data = load_data()


                if not self.data.get(
                    "auto"
                ):

                    break


                if self.data.get(
                    "batch_active"
                ):

                    continue


                # Start a completely new cycle.
                await self._begin_batch_cycle()


        except asyncio.CancelledError:

            pass


        except Exception as e:

            print(
                "AutoPromo loop error:",
                e
            )


    # ========================================================
    # BEGIN NEW CYCLE
    # ========================================================

    async def _begin_batch_cycle(
        self
    ):

        self.data = load_data()


        if not self.data.get(
            "auto"
        ):

            return


        channels = self.data.get(
            "channels",
            []
        )


        if not channels:

            await self.log(
                self.data,
                "⚠️ No saved channels."
            )

            return


        # Snapshot exact channel order.
        #
        # New channels added halfway through the cycle
        # will be picked up NEXT cycle.
        order = [

            int(
                entry["id"]
            )

            for entry in channels

            if "id" in entry

        ]


        reverse = bool(
            self.data.get(
                "reverse"
            )
        )


        if reverse:

            order.reverse()


        # IMPORTANT:
        # Snapshot the message too.
        #
        # If ,setm changes during a paused cycle,
        # the current cycle keeps using the same message.
        promo = self.data.get(
            "message",
            ""
        )


        self.data.update({

            "batch_active": True,

            "batch_order": order,

            "batch_index": 0,

            "batch_message": promo,

            "batch_reverse": reverse,

            "batch_started_at": utc_now().isoformat()

        })


        save_data(
            self.data
        )


        await self.log(

            self.data,

            f"▶️ Cycle started "
            f"({'reverse' if reverse else 'forward'}) "
            f"— {len(order)} channels\n"
            f"📦 Batch size: **{BATCH_SIZE}**"

        )


        # Automatically send FIRST 3.
        async with self.batch_lock:

            await self._send_next_batch()


    # ========================================================
    # SEND NEXT BATCH
    # ========================================================

    async def _send_next_batch(
        self
    ):

        self.data = load_data()


        if not self.data.get(
            "auto"
        ):

            return


        if not self.data.get(
            "batch_active"
        ):

            return


        order = self.data.get(
            "batch_order",
            []
        )


        index = int(
            self.data.get(
                "batch_index",
                0
            )
        )


        promo = self.data.get(
            "batch_message",
            ""
        )


        total = len(
            order
        )


        # Determine this batch's end.
        end_index = min(
            index + BATCH_SIZE,
            total
        )


        batch_start = index


        # ====================================================
        # PROCESS UP TO 3 CHANNELS
        # ====================================================

        while index < end_index:

            # Reload newest JSON before processing
            # each channel.
            self.data = load_data()


            if not self.data.get(
                "auto"
            ):

                return


            # Batch could have been cancelled.
            if not self.data.get(
                "batch_active"
            ):

                return


            channel_id = order[
                index
            ]


            chan = self.bot.get_channel(
                channel_id
            )


            # =================================================
            # MISSING CHANNEL
            # =================================================

            if chan is None:

                await self.log(

                    self.data,

                    f"⏩ Channel `{channel_id}` missing"

                )


            else:

                # =================================================
                # SAME RETRY SYSTEM AS BEFORE
                # =================================================

                for attempt in range(
                    1,
                    MAX_RETRIES + 2
                ):

                    try:

                        await chan.send(
                            promo
                        )


                        sent_time = (
                            utc_now().isoformat()
                        )


                        # Reload newest JSON before saving
                        # last_sent.
                        latest = load_data()


                        for entry in latest.get(
                            "channels",
                            []
                        ):

                            try:

                                if int(
                                    entry.get(
                                        "id",
                                        0
                                    )
                                ) == channel_id:

                                    entry[
                                        "last_sent"
                                    ] = sent_time

                                    break

                            except Exception:

                                continue


                        # Preserve current batch progress.
                        latest[
                            "batch_active"
                        ] = True

                        latest[
                            "batch_order"
                        ] = order

                        latest[
                            "batch_message"
                        ] = promo


                        save_data(
                            latest
                        )


                        self.data = latest


                        await self.log(

                            self.data,

                            f"✅ [{attempt}] "
                            f"{chan.guild.name}/#{chan.name}"

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


                            await self.log(

                                self.data,

                                f"⚠️ Rate-limit on "
                                f"{chan.guild.name}/#{chan.name} "
                                f"– retry in {wait}s "
                                f"({attempt}/"
                                f"{MAX_RETRIES + 1})"

                            )


                            await asyncio.sleep(
                                wait
                            )


                        else:

                            await self.log(

                                self.data,

                                f"❌ Give-up "
                                f"{chan.guild.name}/#{chan.name}"

                            )


                    except Exception as e:

                        await self.log(

                            self.data,

                            f"❌ Error "
                            f"{chan.guild.name}/#{chan.name}: "
                            f"{e}"

                        )


                        break


            # =================================================
            # CHANNEL PROCESSED
            #
            # Save progress IMMEDIATELY.
            # =================================================

            index += 1


            latest = load_data()


            # If someone stopped the system while
            # channel.send() was happening, don't resurrect it.
            if not latest.get(
                "auto"
            ):

                return


            latest[
                "batch_active"
            ] = True

            latest[
                "batch_order"
            ] = order

            latest[
                "batch_index"
            ] = index

            latest[
                "batch_message"
            ] = promo


            save_data(
                latest
            )


            self.data = latest


            # Same delay as your original system.
            if index < end_index:

                await asyncio.sleep(
                    SEND_DELAY
                )


        # ====================================================
        # BATCH FINISHED
        # ====================================================

        self.data = load_data()


        current_index = int(
            self.data.get(
                "batch_index",
                index
            )
        )


        # ====================================================
        # WHOLE CYCLE COMPLETE
        # ====================================================

        if current_index >= total:

            self.data[
                "batch_active"
            ] = False

            self.data[
                "batch_order"
            ] = []

            self.data[
                "batch_index"
            ] = 0

            self.data[
                "batch_message"
            ] = ""

            self.data[
                "batch_reverse"
            ] = False

            self.data[
                "batch_started_at"
            ] = None


            # SAME AS ORIGINAL:
            #
            # next cycle = 5 hours AFTER this cycle finishes.
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


            await self.log(

                self.data,

                "🏁 Cycle finished\n"
                f"✅ Processed all **{total}** channels.\n"
                f"⏰ Next cycle in **{CYCLE_HOURS} hours**."

            )


            return


        # ====================================================
        # MORE CHANNELS LEFT
        # ====================================================

        processed_this_batch = (
            current_index
            -
            batch_start
        )


        remaining = (
            total
            -
            current_index
        )


        await self.log(

            self.data,

            f"⏸️ **Batch finished**\n\n"
            f"Processed this batch: "
            f"**{processed_this_batch}**\n"
            f"Cycle progress: "
            f"**{current_index}/{total}**\n"
            f"Remaining: "
            f"**{remaining}**\n\n"
            f"Send `,continue` to process "
            f"the next **{min(BATCH_SIZE, remaining)}** "
            f"channel(s).\n\n"
            f"⏳ No timeout — progress is saved."

        )


    # ========================================================
    # UNLOAD
    # ========================================================

    def cog_unload(
        self
    ):

        if (
            self.loop_task
            and
            not self.loop_task.done()
        ):

            self.loop_task.cancel()


# ============================================================
# SETUP
# ============================================================

async def setup(
    bot
):

    await bot.add_cog(
        AutoPromo(bot)
    )
