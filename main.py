import os
import asyncio

import discord
from discord.ext import commands


# ============================================================
# CONFIG
# ============================================================

TOKEN = os.getenv("DISCORD_TOKEN")

COGS_FOLDER = "./cogs"
COMMAND_PREFIX = ","


# ============================================================
# CHECK RAILWAY TOKEN
# ============================================================

if not TOKEN:
    raise RuntimeError(
        "DISCORD_TOKEN environment variable is missing. "
        "Add it in Railway → Variables."
    )


# ============================================================
# BOT SETUP
# ============================================================

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
    global cogs_loaded

    print(f"✅ Logged in as {bot.user} ({bot.user.id})")

    # on_ready can run multiple times after reconnecting.
    # Only automatically load the cogs once.
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


# ============================================================
# START
# ============================================================

def main():
    print("🚀 Starting Discord client...")

    try:
        bot.run(TOKEN)

    except discord.LoginFailure:
        print(
            "❌ Login failed. Check DISCORD_TOKEN "
            "in Railway Variables."
        )

    except KeyboardInterrupt:
        print("🛑 Stopped.")

    except Exception as e:
        print(
            f"❌ Fatal: {type(e).__name__}: {e}"
        )


if __name__ == "__main__":
    main()
