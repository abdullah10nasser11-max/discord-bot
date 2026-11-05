import os
import asyncio
import discord
import yt_dlp
from discord import app_commands
from discord.ext import commands

TOKEN = os.getenv("DISCORD_TOKEN")
# اختياري: ايدي روم نصي يرسل فيه البوت إشعارات دخول/خروج الصوت
LOG_CHANNEL_ID = int(os.getenv("LOG_CHANNEL_ID", "0"))

YDL_OPTS = {
    "format": "bestaudio/best",
    "noplaylist": True,
    "quiet": True,
    "default_search": "ytsearch",
    "source_address": "0.0.0.0",
}
FFMPEG_OPTS = {
    "before_options": "-reconnect 1 -reconnect_streamed 1 -reconnect_delay_max 5",
    "options": "-vn",
}

intents = discord.Intents.default()
intents.voice_states = True
bot = commands.Bot(command_prefix="!", intents=intents)

queues: dict[int, list[dict]] = {}


async def fetch_song(query: str) -> dict:
    """يجلب رابط الصوت من يوتيوب (رابط مباشر أو كلمات بحث)."""
    loop = asyncio.get_running_loop()

    def _extract():
        with yt_dlp.YoutubeDL(YDL_OPTS) as ydl:
            info = ydl.extract_info(query, download=False)
            if "entries" in info:
                info = info["entries"][0]
            return info

    info = await loop.run_in_executor(None, _extract)
    return {
        "title": info.get("title", "بدون عنوان"),
        "url": info["url"],
        "page": info.get("webpage_url", query),
    }


def play_next(guild: discord.Guild):
    vc = guild.voice_client
    queue = queues.get(guild.id, [])
    if not vc or not vc.is_connected() or not queue:
        return
    song = queue.pop(0)
    source = discord.FFmpegPCMAudio(song["url"], **FFMPEG_OPTS)
    vc.play(
        source,
        after=lambda e: bot.loop.call_soon_threadsafe(play_next, guild),
    )


async def ensure_voice(interaction: discord.Interaction):
    """يدخل البوت لنفس روم المستخدم (أو ينتقل له)."""
    if not interaction.user.voice or not interaction.user.voice.channel:
        await interaction.followup.send("ادخل روم صوتي أول.")
        return None
    channel = interaction.user.voice.channel
    vc = interaction.guild.voice_client
    if vc is None:
        vc = await channel.connect()
    elif vc.channel != channel:
        await vc.move_to(channel)
    return vc


@bot.event
async def setup_hook():
    await bot.tree.sync()


BOT_NAME = "بوت الملتقى"


@bot.event
async def on_ready():
    await bot.change_presence(
        activity=discord.Activity(type=discord.ActivityType.listening, name="/play | " + BOT_NAME)
    )
    print(f"{BOT_NAME} شغال باسم: {bot.user}")


# ---------- مراقبة الغرف الصوتية ----------
@bot.event
async def on_voice_state_update(member, before, after):
    if member.bot:
        return

    log = bot.get_channel(LOG_CHANNEL_ID) if LOG_CHANNEL_ID else None

    if after.channel and before.channel != after.channel:
        text = f"🎧 {member.display_name} دخل {after.channel.name}"
        print(text)
        if log:
            await log.send(text)
    elif before.channel and not after.channel:
        text = f"👋 {member.display_name} طلع من {before.channel.name}"
        print(text)
        if log:
            await log.send(text)

    # لو البوت صار لحاله في الروم يطلع تلقائياً
    vc = member.guild.voice_client
    if vc and vc.channel and len([m for m in vc.channel.members if not m.bot]) == 0:
        await asyncio.sleep(30)
        if vc.is_connected() and not [m for m in vc.channel.members if not m.bot]:
            queues.pop(member.guild.id, None)
            await vc.disconnect()


# ---------- الأوامر ----------
@bot.tree.command(name="join", description="يدخل البوت رومك الصوتي")
async def join(interaction: discord.Interaction):
    await interaction.response.defer()
    if await ensure_voice(interaction):
        await interaction.followup.send("دخلت ✅")


@bot.tree.command(name="play", description="تشغيل من يوتيوب (رابط أو اسم)")
@app_commands.describe(query="رابط يوتيوب أو كلمات بحث")
async def play(interaction: discord.Interaction, query: str):
    await interaction.response.defer()
    vc = await ensure_voice(interaction)
    if not vc:
        return
    try:
        song = await fetch_song(query)
    except Exception as e:
        await interaction.followup.send(f"ما قدرت أجيب الصوت: {e}")
        return

    queues.setdefault(interaction.guild.id, []).append(song)
    if vc.is_playing() or vc.is_paused():
        await interaction.followup.send(f"أضفته للطابور: **{song['title']}**")
    else:
        play_next(interaction.guild)
        await interaction.followup.send(f"🎶 يشتغل الحين: **{song['title']}**")


@bot.tree.command(name="pause", description="إيقاف مؤقت")
async def pause(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_playing():
        vc.pause()
        await interaction.response.send_message("⏸️ تم الإيقاف المؤقت")
    else:
        await interaction.response.send_message("ما في شي يشتغل.")


@bot.tree.command(name="resume", description="متابعة التشغيل")
async def resume(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and vc.is_paused():
        vc.resume()
        await interaction.response.send_message("▶️ تمت المتابعة")
    else:
        await interaction.response.send_message("ما في شي موقوف.")


@bot.tree.command(name="skip", description="تخطي الأغنية")
async def skip(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()  # يشغّل التالية تلقائياً عبر after
        await interaction.response.send_message("⏭️ تم التخطي")
    else:
        await interaction.response.send_message("ما في شي يشتغل.")


@bot.tree.command(name="queue", description="عرض الطابور")
async def show_queue(interaction: discord.Interaction):
    q = queues.get(interaction.guild.id, [])
    if not q:
        await interaction.response.send_message("الطابور فاضي.")
        return
    lines = [f"{i + 1}. {s['title']}" for i, s in enumerate(q[:10])]
    await interaction.response.send_message("\n".join(lines))


@bot.tree.command(name="stop", description="إيقاف ومسح الطابور")
async def stop(interaction: discord.Interaction):
    queues.pop(interaction.guild.id, None)
    vc = interaction.guild.voice_client
    if vc and (vc.is_playing() or vc.is_paused()):
        vc.stop()
    await interaction.response.send_message("⏹️ تم الإيقاف")


@bot.tree.command(name="leave", description="يطلع البوت من الروم")
async def leave(interaction: discord.Interaction):
    vc = interaction.guild.voice_client
    queues.pop(interaction.guild.id, None)
    if vc:
        await vc.disconnect()
        await interaction.response.send_message("طلعت 👋")
    else:
        await interaction.response.send_message("أنا مو في روم.")


if __name__ == "__main__":
    if not TOKEN:
        raise SystemExit("حط التوكن في المتغير DISCORD_TOKEN")
    bot.run(TOKEN)
