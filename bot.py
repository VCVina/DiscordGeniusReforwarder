import os
import discord
from discord.ext import commands
from dotenv import load_dotenv

# ===== config =====
load_dotenv()  # load .env 文件

TOKEN = os.getenv("DISCORDAPP_TOKEN")
TARGET_CHANNEL_NAME = os.getenv("TARGET_CHANNEL_NAME", "天才俱乐部")  # target channel name
TRIGGER_EMOJI = int(os.getenv("TRIGGER_EMOJI", "1446321054057369620"))         # trigger emoji id

# ===== Intents =====
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True   # read message content
intents.members = True          # member
intents.reactions = True         # reaction

bot = commands.Bot(command_prefix="!", intents=intents)

@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    print("------")

@bot.event
async def on_raw_reaction_add(payload: discord.RawReactionActionEvent):
    # debug log
    print("=== on_raw_reaction_add fired ===")
    print("user_id:", payload.user_id)
    print("emoji.id:", payload.emoji.id)
    print("emoji.name:", payload.emoji.name)
    print("guild_id:", payload.guild_id, "channel_id:", payload.channel_id, "message_id:", payload.message_id)

    # 1. ignore bot itself
    # if payload.user_id == bot.user.id:
    #     print(" -> ignore: this is bot itself")
    #     return

    # 2. only handle your own reactions
    # if payload.user_id != MY_USER_ID:
    #     print(" -> ignore: not my reaction")
    #     return

    # 3. check if it's the target custom emoji (by ID)
    if payload.emoji.id != TRIGGER_EMOJI:
       print(f" -> ignore: emoji id {payload.emoji.id} != {TRIGGER_EMOJI}")
       return

    print(" -> passed all checks, will fetch message and forward")

    # 4. find guild by guild_id
    guild = bot.get_guild(payload.guild_id) if payload.guild_id else None
    print("guild:", guild)
    
    if guild is None:
        print(" -> no guild (maybe DM), ignore")
        return
    print("channel_id:", payload.channel_id)
    
    channel = guild.get_channel(payload.channel_id) or bot.get_channel(payload.channel_id)
    print("channel:", channel)
    target_channel = discord.utils.get(guild.text_channels, name=TARGET_CHANNEL_NAME)
    if not isinstance(channel, discord.TextChannel):
        print(" -> channel is not a TextChannel, ignore")
        return
    # 5. fetch original message (raw event has no message object)
    try:
        print(payload.message_id)
        message = await channel.fetch_message(payload.message_id)
        print(" -> fetched message:", message.content)
        emoji_reaction = None
        for r in message.reactions:
            # only handle custom emoji :emoji:, check by id
            # note: after RawReaction, reactions include "current count" (including the one you just added)
            if getattr(r.emoji, "id", None) == TRIGGER_EMOJI:
                emoji_reaction = r
                break

        if emoji_reaction is not None:
            print(f" -> current :emoji: count on this message = {emoji_reaction.count}")
            # if :emoji: count is greater than 1, it means someone else already reacted before you
            # in this case, do not forward, just return
            if emoji_reaction.count > 1:
                print(" -> :emoji: reaction already existed before, skip forwarding")
                return
        
        # 6. here starts the logic to find the target channel and send the message
        target_channel = discord.utils.get(guild.text_channels, name=TARGET_CHANNEL_NAME)
        if target_channel is None:
            print(f" -> cannot find channel {TARGET_CHANNEL_NAME}")
            return
        
        # this is the jump link to the original message
        jump_url = message.jump_url  
        content = message.content or "(原消息没有文字内容)"

        embed = discord.Embed(
            description=content,
            timestamp=message.created_at,
        )
        
        author = message.author

        # prefer display_name, fallback to regular name
        author_display_name = getattr(author, "display_name", author.name)

        # safely get avatar (both User and Member have display_avatar)
        author_avatar_url = author.display_avatar.url if author.display_avatar else None

        embed.set_author(
            name=f"{author_display_name}",
            icon_url=author_avatar_url,
        )

        forward_user = guild.get_member(payload.user_id)
        if forward_user is None:
            # if not found in guild cache, fetch globally
            try:
                forward_user = await bot.fetch_user(payload.user_id)
            except Exception as e:
                print(" -> fetch_user failed:", repr(e))
                forward_user = None
        if forward_user is not None:
            display_name = getattr(forward_user, "display_name", forward_user.name)
            avatar_url = forward_user.display_avatar.url if forward_user.display_avatar else None
            embed.set_footer(
                text=f"品鉴人 {display_name}",
                icon_url=avatar_url,
            )
        else:
            # if really cannot get the user object, use plain text as fallback (no avatar)
            embed.set_footer(
                text="由某位用户标记转发",
            )
        embed.add_field(name="原频道", value=message.channel.mention, inline=True)
        embed.add_field(name="原消息链接", value=f"[点此跳转]({jump_url})", inline=False)
        print(guild.get_member(payload.user_id))

        files = []
        if message.attachments:
            print(f" -> found {len(message.attachments)} attachment(s)")
            for att in message.attachments:
                try:
                    file = await att.to_file()
                    files.append(file)
                    print(f"    - attachment: {att.filename}")
                except Exception as e:
                    print("    - download attachment failed:", repr(e))

        try:
            # text header + embed + attachments
            embeds_to_send = [embed]
            if message.embeds:
                embeds_to_send.extend(message.embeds)
            await target_channel.send(
                "",
                embeds=embeds_to_send,
                files=files if files else None,
            )
            print(" -> forwarded successfully (embed + attachments)")
        except Exception as e:
            print(" -> ERROR when sending to target_channel:", repr(e))

    except discord.NotFound:
        print(" -> message not found")
        return
    except discord.Forbidden:
        print(" -> no permission to read message")
        return
    except Exception as e:
        print(" -> error when fetching message:", e)
        return

bot.run(TOKEN)
