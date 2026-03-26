import os
import re
import discord
from discord.ext import commands
from dotenv import load_dotenv
from openai import OpenAI
from pathlib import Path

# ===== config =====
load_dotenv()  # load .env

TOKEN = os.getenv("DISCORDAPP_TOKEN")
TARGET_CHANNEL_NAME = os.getenv("TARGET_CHANNEL_NAME", "default")  # target channel name
TRIGGER_EMOJI = int(os.getenv("TRIGGER_EMOJI", "1446321054057369620"))         # trigger emoji id
client = OpenAI()
RESTRICTED_REFORWARD_MODE = True if os.getenv("RESTRICTED_REFORWARD_MODE", "false").lower() == "true" else False
RESTRICTED_AICHAT_MODE = True if os.getenv("RESTRICTED_AICHAT_MODE", "false").lower() == "true" else False
MY_USER_ID = int(os.getenv("MY_USER_ID"))  # User ID
PROMPT_PATH = Path(__file__).with_name("aichat_prompt.txt")
SYSTEM_PROMPT = ""

CONVERSATION_HISTORY_LIMIT = int(os.getenv("CONVERSATION_HISTORY_LIMIT"))  # per channel

conversation_history = {}  # channel_id -> list[{"author": str, "content": str}]

# ===== Intents =====
intents = discord.Intents.default()
intents.guilds = True
intents.messages = True
intents.message_content = True   # read message content
intents.members = True          # member
intents.reactions = True         # reaction

bot = commands.Bot(command_prefix="!", intents=intents)
try:
    SYSTEM_PROMPT = PROMPT_PATH.read_text(encoding="utf-8")
except FileNotFoundError:
    # If the file does not exist, provide a very short persona as fallback
    SYSTEM_PROMPT = "你是一个懒洋洋又自恋的天才学者型NPC，用简体中文简短回答问题。"
    print(f"[WARN] aichat_prompt.txt not found, using fallback SYSTEM_PROMPT")
import re

# 本地规则返回：("ALLOW"|"BLOCK"|"DOWNGRADE", reason)
def local_policy_check(text: str):
    t = (text or "").strip()
    if not t:
        return ("ALLOW", "empty")

    # 规则 2：对受保护群体的整体贬损/控制倾向（示例：性别）
    # 这不是完美 NLP，只是低成本的“守门员”
    group_words = ["女人", "女性", "女生", "女的", "母人", "男人", "男性", "男的", "废物", "垃圾", "傻逼", "弱智", "畜生", "滚", "去死"]
    if any(w in t for w in group_words):
        return ("BLOCK", "group_derogatory_or_control")
    
    # 规则 3：NSFW/未成年人等你想更严格的（建议仍交给 OpenAI）
    # 本地只做极少量“硬挡”，避免误杀
    return ("ALLOW", "ok")

def build_moderation_input(text: str, image_urls: list[str] | None = None):
    items = [{"type": "text", "text": text or ""}]
    if image_urls:
        for url in image_urls[:4]:  # 可选：限制最多审 4 张，避免太慢
            items.append({"type": "image_url", "image_url": {"url": url}})
    return items

def is_flagged_by_openai(text: str, image_urls: list[str] | None = None) -> bool:
    # omni-moderation-latest 支持文本+图片输入
    resp = client.moderations.create(
        model="omni-moderation-latest",
        input=build_moderation_input(text, image_urls),
    )
    # 取第一个结果的 flagged
    return bool(resp.results[0].flagged)

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
    print("Access Mode:", RESTRICTED_REFORWARD_MODE)

    # 1. ignore bot itself
    # if payload.user_id == bot.user.id:
    #     print(" -> ignore: this is bot itself")
    #     return

    # 2. only handle your own reactions
    if payload.user_id != MY_USER_ID:
        if RESTRICTED_REFORWARD_MODE:
            print(f" -> ignore: reaction from user_id {payload.user_id} ")
            return
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

        # ===== [NEW] moderation gate before forwarding =====
        # 只审“将要转发”的内容：文字 + 附件图片（可选）
        att_image_urls = []
        for att in message.attachments:
            fn = (att.filename or "").lower()
            is_img = False
            if att.content_type and att.content_type.startswith("image/"):
                is_img = True
            elif fn.endswith((".png", ".jpg", ".jpeg", ".webp", ".gif", ".bmp")):
                is_img = True
            if is_img:
                att_image_urls.append(att.url)

        text_to_check = message.content or ""
        
        # local policy gate (before forwarding)
        action, reason = local_policy_check(message.content or "")
        if action == "BLOCK":
            print(f" -> local policy blocked forwarding: {reason}")
            return

        try:
            flagged = is_flagged_by_openai(text_to_check, att_image_urls if att_image_urls else None)
        except Exception as e:
            print(" -> moderation error, choose to block forwarding:", repr(e))
            return

        if flagged:
            print(" -> moderation flagged: block forwarding")
            # 你可以选择：不转发，或者转发一条“被拦截”的提示到 logs（可选）
            # await target_channel.send("有一条消息因内容审核未通过，已拦截。")
            return
        
    
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

@bot.event
async def on_message(message: discord.Message):
    # 1. ignore bot messages
    print("AIChat Access Mode:", RESTRICTED_AICHAT_MODE)
    if message.author.bot:
        return

    # Debug: check if the event is triggered
    print("[on_message] from:", message.author, "content:", message.content)

    channel_id = message.channel.id
    author_name = getattr(message.author, "display_name", None) or str(message.author)

    hist = conversation_history.setdefault(channel_id, [])
    hist.append({
        "author": author_name,
        "content": message.content or "",
    })
    if len(hist) > CONVERSATION_HISTORY_LIMIT:
        hist.pop(0)
        
    # 2. Check if the bot was mentioned
    if bot.user in message.mentions:
        print("[on_message] bot was mentioned")
        if message.author.id != MY_USER_ID:
            if RESTRICTED_AICHAT_MODE:
                print(f" -> ignore: message from user_id {message.author.id} ")
                return
        # Remove the @Bot mention part, keep only the actual question
        content = message.content.replace(bot.user.mention, "").strip()

        image_urls = []
        for att in message.attachments:
            # content_type "image/png""image/jpeg"
            if att.content_type and att.content_type.startswith("image/"):
                image_urls.append(att.url)
                print(f"[on_message] found image attachment: {att.filename} ({att.url})")

        if not content and not image_urls:
            await message.reply("你有什么问题就问，我很忙。")
            return
        user_content_parts = []
        hist = conversation_history.get(message.channel.id, [])
        hist_for_context = hist[:-1]  # exclude the current message we just appended
        context_lines = []
        for h in hist_for_context:
            txt = (h.get("content") or "").strip()
            if not txt:
                continue
            # 一行一个：用户名: 内容
            context_lines.append(f"{h.get('author','unknown')}: {txt}")

        if context_lines:
            context_text = "\n".join(context_lines)
            user_content_parts.append({
                "type": "text",
                "text": f"以下是本频道最近的聊天记录，用于理解上下文：\n{context_text}\n---",
            })
        if content:
            user_content_parts.append({
                "type": "text",
                "text": content,
            })
        else:
            user_content_parts.append({
                "type": "text",
                "text": "请说明这个图片中有什么内容。",
            })

        for url in image_urls:
            user_content_parts.append({
                "type": "image_url",
                "image_url": {
                    "url": url,
                },
            })    
        print("[on_message] user content:", content)
        print("[on_message] image count:", len(image_urls))

        # 3. Call OpenAI Chat Completions
        try:
            completion = client.chat.completions.create(
                model="gpt-5-mini",  # model
                #reasoning={"effort": "high"},
                messages=[
                    {"role": "system", "content": SYSTEM_PROMPT},
                    {"role": "user", "content": user_content_parts},
                ],
            )
            reply = completion.choices[0].message.content
            print("[on_message] openai reply:", repr(reply))
        except Exception as e:
            print("OpenAI API error:", repr(e))
            reply = "(已读)"

        # 4. reply to the message
        try:
            await message.reply(reply)
            print("[on_message] replied successfully")
        except Exception as e:
            print("[on_message] reply failed:", repr(e))
        return

    # 5. If the message is not @bot, hand it back to the command system
    await bot.process_commands(message)


bot.run(TOKEN)
