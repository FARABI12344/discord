# cogs/system.py


import asyncio, json, os, discord

from datetime import datetime, timedelta, timezone

from discord.ext import commands


DATA_FILE   = "channels.json"

CYCLE_HOURS = 5         # every 5 hours

SEND_DELAY  = 10        # 10 s between servers

RETRY_DELAY = 5         # 5 s between retries

MAX_RETRIES = 2         # +1 initial try


# ─────────── helpers ───────────


def utc_now() -> datetime:

    return datetime.now(timezone.utc)


def load_data() -> dict:

    if not os.path.exists(DATA_FILE):

        return {"message": "", "channels": [], "auto": False,

                "next_run": None, "log_channel": None, "reverse": False}

    with open(DATA_FILE, encoding="utf8") as fp:

        return json.load(fp)


def save_data(d: dict):

    with open(DATA_FILE, "w", encoding="utf8") as fp:

        json.dump(d, fp, indent=2)


# ─────────── cog ───────────


class AutoPromo(commands.Cog):

    """Auto-sends promo every N hours.  `,start` = forward, `,startb` = reverse."""


    def __init__(self, bot: commands.Bot):

        self.bot = bot

        self.data = load_data()

        self.loop_task: asyncio.Task | None = None

        if self.data.get("auto"):

            self._schedule_loop()


    # ── commands ──

    async def _common_start(self, ctx: commands.Context, reverse: bool):

        self.data = load_data()

        if not self.data.get("message"):

            return await ctx.send("⚠️  Set a message first with `,setm …`.")

        if not self.data.get("channels"):

            return await ctx.send("⚠️  Add promo channels with `,setc …` first.")

        if self.data.get("auto"):

            return await ctx.send("ℹ️  Auto-cycle is already running.")


        self.data.update({

            "auto": True,

            "next_run": utc_now().isoformat(),

            "log_channel": ctx.channel.id,

            "reverse": reverse

        })

        save_data(self.data)

        self._schedule_loop()

        await ctx.send(

            f"✅ Auto-cycle started ({'reverse' if reverse else 'forward'}) – first run now."

        )


    @commands.command()

    async def start(self, ctx):

        """Process channels first→last every cycle."""

        await self._common_start(ctx, reverse=False)


    @commands.command()

    async def startb(self, ctx):

        """Process channels last→first every cycle."""

        await self._common_start(ctx, reverse=True)


    @commands.command()

    async def stop(self, ctx):

        if not self.data.get("auto"):

            return await ctx.send("ℹ️  Auto-cycle isn’t running.")

        self.data["auto"] = False

        self.data["next_run"] = None

        save_data(self.data)

        if self.loop_task and not self.loop_task.done():

            self.loop_task.cancel()

        await ctx.send("🛑 Auto-cycle stopped.")


    # ── scheduling ──

    def _schedule_loop(self):

        if self.loop_task and not self.loop_task.done():

            self.loop_task.cancel()

        self.loop_task = self.bot.loop.create_task(self._loop())


    async def _loop(self):

        try:

            while self.data.get("auto"):

                delay = max(0, (

                    datetime.fromisoformat(self.data["next_run"]) - utc_now()

                ).total_seconds())

                await asyncio.sleep(delay)

                if not self.data.get("auto"):

                    break

                await self._run_cycle()

                self.data["next_run"] = (

                    utc_now() + timedelta(hours=CYCLE_HOURS)

                ).isoformat()

                save_data(self.data)

        except asyncio.CancelledError:

            pass

        except Exception as e:

            print("AutoPromo loop error:", e)


    # ── single cycle ──

    async def _run_cycle(self):

        self.data = load_data()      # fresh state

        promo = self.data["message"]

        log_ch = self.bot.get_channel(self.data.get("log_channel", 0))

        if log_ch is None:

            # lost original log channel, fall back to first guild text channel

            for g in self.bot.guilds:

                if g.text_channels:

                    log_ch = g.text_channels[0]

                    break


        order = list(self.data["channels"])

        if self.data.get("reverse"):

            order.reverse()


        async def log(msg):

            if log_ch:

                await log_ch.send(msg)


        await log(f"▶️ Cycle started ({'reverse' if self.data.get('reverse') else 'forward'}) "

                  f"— {len(order)} channels")


        for entry in order:

            chan = self.bot.get_channel(entry["id"])

            if chan is None:

                await log(f"⏩ `{entry['channel_name']}` missing")

                continue


            sent = False

            for attempt in range(1, MAX_RETRIES + 2):

                try:

                    await chan.send(promo)

                    entry["last_sent"] = utc_now().isoformat()

                    await log(f"✅ [{attempt}] {chan.guild.name}/#{chan.name}")

                    sent = True

                    break

                except discord.HTTPException as e:

                    if attempt <= MAX_RETRIES:

                        wait = max(RETRY_DELAY, int(getattr(e, "retry_after", RETRY_DELAY)))

                        await log(f"⚠️ Rate-limit on {chan.guild.name}/#{chan.name} "

                                  f"– retry in {wait}s ({attempt}/{MAX_RETRIES+1})")

                        await asyncio.sleep(wait)

                    else:

                        await log(f"❌ Give-up {chan.guild.name}/#{chan.name}")

                except Exception as e:

                    await log(f"❌ Error {chan.guild.name}/#{chan.name}: {e}")

                    break

            await asyncio.sleep(SEND_DELAY)


        save_data(self.data)

        await log("🏁 Cycle finished")


    def cog_unload(self):

        if self.loop_task and not self.loop_task.done():

            self.loop_task.cancel()


async def setup(bot):

    await bot.add_cog(AutoPromo(bot))
