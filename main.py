import os
import asyncio

import discord
from discord.ext import commands


# ============================================================
# CONFIG
# ============================================================

COGS_FOLDER = "./cogs"
COMMAND_PREFIX = ","


# ============================================================
# LOAD TOKENS
# ============================================================

# Try to get tokens from environment variables
TOKEN_1 = os.getenv("DISCORD_TOKEN", "")
TOKEN_2 = os.getenv("DISCORD_TOKEN_2", "")

# Fallback: if env var is empty, you can hardcode them here as a backup
# TOKEN_1 = "your_first_token_here"
# TOKEN_2 = "your_second_token_here"

TOKENS = []
if TOKEN_1:
    TOKENS.append(TOKEN_1)
if TOKEN_2:
    TOKENS.append(TOKEN_2)

if not TOKENS:
    raise RuntimeError(
        "No tokens found. "
        "Set 'DISCORD_TOKEN' and/or 'DISCORD_TOKEN_2' "
        "environment variables."
    )


# ============================================================
# BOT FACTORY
# ============================================================

def create_bot_instance(token):
    """
    Creates a new Bot instance with all event handlers and commands.
    This allows us to run multiple bots with the exact same logic.
    """
    bot = commands.Bot(
        command_prefix=COMMAND_PREFIX,
        self_bot=True,
    )

    cogs_loaded = False

    # ============================================================
    # ONLY RESPOND TO YOUR OWN COMMANDS
    # ============================================================

    @bot.check
    async def only_me(ctx):
        return (
            bot.user is not None
            and ctx.author.id == bot.user.id
        )

    # ============================================================
    # COG LOADER
    # ============================================================

    async def load_all_cogs():
        results = []

        os.makedirs(COGS_FOLDER, exist_ok=True)

        cog_files = sorted(
            filename
            for filename in os.listdir(COGS_FOLDER)
            if filename.endswith(".py")
            and not filename.startswith("__")
        )

        if not cog_files:
            return ["⚠️ No cogs found."]

        for filename in cog_files:
            cog_name = f"cogs.{filename[:-3]}"

            try:
                if cog_name in bot.extensions:
                    await bot.reload_extension(cog_name)
                    results.append(f"🔁 {filename}")
                else:
                    await bot.load_extension(cog_name)
                    results.append(f"✅ {filename}")

            except Exception as e:
                results.append(
                    f"❌ {filename} | "
                    f"{type(e).__name__}: {e}"
                )

        return results

    # ============================================================
    # READY EVENT
    # ============================================================

    @bot.event
    async def on_ready():
        # We use a local variable here, not global, because each bot has its own state
        nonlocal cogs_loaded
        
        print(f"✅ Logged in as {bot.user} ({bot.user.id})")

        # Only load cogs once per bot instance
        if not cogs_loaded:
            results = await load_all_cogs()

            for result in results:
                print(result)

            cogs_loaded = True

        print("🚀 Ready")

    # ============================================================
    # REFRESH COGS
    # ============================================================

    @bot.command(name="refresh")
    async def refresh(ctx):
        msg = await ctx.send("🔄 Refreshing...")

        try:
            results = await load_all_cogs()

            failed = any(
                result.startswith("❌")
                for result in results
            )

            # Retry once if something failed
            if failed:
                await asyncio.sleep(1)

                retry = await load_all_cogs()

                results += [
                    "",
                    "──── RETRY ────",
                    *retry
                ]

            output = "\n".join(results)

            # Stay below Discord's 2000 character limit
            if len(output) > 1800:
                output = output[:1800] + "\n...shortened"

            await msg.edit(
                content=f"```text\n{output}\n```"
            )

        except Exception as e:
            error = f"{type(e).__name__}: {e}"

            print(f"Refresh error: {error}")

            try:
                await msg.edit(
                    content=f"⚠️ Refresh failed: `{error}`"
                )
            except Exception:
                pass

    # ============================================================
    # PING
    # ============================================================

    @bot.command(name="ping")
    async def ping(ctx):
        latency = round(bot.latency * 1000)

        await ctx.send(
            f"🏓 Pong! `{latency}ms`"
        )

    # ============================================================
    # COMMAND ERROR HANDLER
    # ============================================================

    @bot.event
    async def on_command_error(ctx, error):

        # Ignore commands from other users
        if isinstance(error, commands.CheckFailure):
            return

        # Ignore unknown commands
        if isinstance(error, commands.CommandNotFound):
            return

        if isinstance(error, commands.MissingRequiredArgument):
            await ctx.send(
                f"⚠️ Missing argument: `{error.param.name}`"
            )
            return

        if isinstance(error, commands.BadArgument):
            await ctx.send(
                f"⚠️ Invalid argument: `{error}`"
            )
            return

        if isinstance(error, commands.CommandOnCooldown):
            await ctx.send(
                f"⏳ Try again in `{error.retry_after:.1f}s`."
            )
            return

        original = getattr(error, "original", error)

        error_text = (
            f"{type(original).__name__}: {original}"
        )

        print(
            f"⚠️ Command error "
            f"[{getattr(ctx.command, 'name', 'unknown')}]: "
            f"{error_text}"
        )

        try:
            await ctx.send(
                f"⚠️ Error: `{error_text}`"
            )

        except Exception:
            pass

    # ============================================================
    # SMALL COMMAND DELAY
    # ============================================================

    @bot.before_invoke
    async def before_command(ctx):
        await asyncio.sleep(0.3)

    return bot


# ============================================================
# RUN BOT HELPER
# ============================================================

async def run_bot(bot, token):
    """Starts a single bot instance"""
    try:
        await bot.start(token)
    except discord.LoginFailure:
        print(f"❌ Login failed for token ending in ...{token[-5:]}")
    except KeyboardInterrupt:
        raise
    except Exception as e:
        print(f"❌ Bot error: {type(e).__name__}: {e}")


# ============================================================
# MAIN
# ============================================================

async def main():
    print("🚀 Starting Discord clients...")
    
    bots = []
    tasks = []

    for i, token in enumerate(TOKENS, 1):
        bot = create_bot_instance(token)
        bots.append(bot)
        tasks.append(asyncio.create_task(run_bot(bot, token)))
        print(f"🚀 Starting Bot {i}...")

    # Run all bots concurrently
    try:
        await asyncio.gather(*tasks)
    except KeyboardInterrupt:
        print("🛑 Stopping all bots...")
        for bot in bots:
            if bot.is_ready():
                await bot.close()

if __name__ == "__main__":
    asyncio.run(main())
