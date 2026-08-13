import os
import asyncio

import discord
from discord.ext import commands, tasks


# ============================================================
# CONFIG
# ============================================================

TOKEN_FILE = "token.txt"
COGS_FOLDER = "./cogs"
COMMAND_PREFIX = ","


# ============================================================
# READ TOKEN
# ============================================================

if not os.path.isfile(TOKEN_FILE):
    raise FileNotFoundError(
        f"{TOKEN_FILE} was not found. Create it and put your token inside."
    )

with open(TOKEN_FILE, "r", encoding="utf-8") as f:
    TOKEN = f.read().strip()

if not TOKEN:
    raise ValueError(f"{TOKEN_FILE} is empty.")


# ============================================================
# BOT SETUP
# ============================================================

bot = commands.Bot(
    command_prefix=COMMAND_PREFIX,
    self_bot=True,
)


# ============================================================
# ONLY RESPOND TO YOUR OWN COMMANDS
# ============================================================

@bot.check
async def only_me(ctx):
    if bot.user is None:
        return False

    return ctx.author.id == bot.user.id


# ============================================================
# COG LOADER
# ============================================================

async def load_all_cogs():
    results = []

    if not os.path.isdir(COGS_FOLDER):
        os.makedirs(COGS_FOLDER, exist_ok=True)
        results.append("📁 Created missing cogs folder.")
        return results

    files = sorted(os.listdir(COGS_FOLDER))

    cog_files = [
        filename
        for filename in files
        if filename.endswith(".py")
        and not filename.startswith("__")
    ]

    if not cog_files:
        results.append("⚠️ No cogs found.")
        return results

    for filename in cog_files:
        cog_name = f"cogs.{filename[:-3]}"

        try:
            if cog_name in bot.extensions:
                await bot.reload_extension(cog_name)
                results.append(f"🔁 Reloaded: {filename}")

            else:
                await bot.load_extension(cog_name)
                results.append(f"✅ Loaded: {filename}")

        except commands.ExtensionNotLoaded:
            try:
                await bot.load_extension(cog_name)
                results.append(f"✅ Loaded: {filename}")

            except Exception as e:
                results.append(
                    f"❌ Failed: {filename} | "
                    f"{type(e).__name__}: {e}"
                )

        except Exception as e:
            results.append(
                f"❌ Failed: {filename} | "
                f"{type(e).__name__}: {e}"
            )

    return results


# ============================================================
# HEARTBEAT
# ============================================================

@tasks.loop(seconds=25)
async def heartbeat():
    print("💓 heartbeat")


@heartbeat.before_loop
async def before_heartbeat():
    await bot.wait_until_ready()


# ============================================================
# ON READY
# ============================================================

@bot.event
async def on_ready():
    print("=" * 55)
    print(f"✅ Logged in as: {bot.user}")
    print(f"🆔 User ID: {bot.user.id}")
    print("=" * 55)

    print("\n🔄 Loading cogs...\n")

    results = await load_all_cogs()

    for result in results:
        print(result)

    print("\n🚀 Client is ready.\n")

    # Prevent:
    # RuntimeError: Task is already launched and is not completed
    if not heartbeat.is_running():
        heartbeat.start()


# ============================================================
# REFRESH COMMAND
# ============================================================

@bot.command(name="refresh")
async def refresh(ctx):
    try:
        msg = await ctx.send("🔄 Refreshing cogs...")

        results = await load_all_cogs()

        failed = [
            result
            for result in results
            if result.startswith("❌")
        ]

        # Retry failed cogs once
        if failed:
            await asyncio.sleep(1)

            results.append("")
            results.append("────── RETRY ──────")

            retry_results = await load_all_cogs()
            results.extend(retry_results)

        output = "\n".join(results)

        # Discord message limit is 2000 characters.
        if len(output) > 1850:
            output = output[:1850]
            output += "\n\n...output shortened"

        await msg.edit(
            content=f"```text\n{output}\n```"
        )

    except Exception as e:
        try:
            await ctx.send(
                f"⚠️ Refresh failed: "
                f"{type(e).__name__}: {e}"
            )
        except Exception:
            print(
                f"Refresh error: "
                f"{type(e).__name__}: {e}"
            )


# ============================================================
# OPTIONAL TEST COMMAND
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

    # Ignore commands from anybody other than yourself
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
            f"⚠️ Invalid argument: {error}"
        )
        return

    if isinstance(error, commands.CommandOnCooldown):
        await ctx.send(
            f"⏳ Try again in {error.retry_after:.1f}s."
        )
        return

    # Get the real error if it's wrapped
    original = getattr(error, "original", error)

    print(
        f"⚠️ Command error in "
        f"{getattr(ctx.command, 'name', 'unknown')}: "
        f"{type(original).__name__}: {original}"
    )

    try:
        await ctx.send(
            f"⚠️ Error: "
            f"`{type(original).__name__}: {original}`"
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
# MAIN
# ============================================================

def main():
    print("🚀 Starting...")

    try:
        bot.run(TOKEN)

    except KeyboardInterrupt:
        print("\n🛑 Stopped manually.")

    except discord.LoginFailure:
        print(
            "❌ Login failed. Check the token inside token.txt."
        )

    except Exception as e:
        print(
            f"❌ Fatal error: "
            f"{type(e).__name__}: {e}"
        )


if __name__ == "__main__":
    main()
