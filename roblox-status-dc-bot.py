import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
import os
import sqlite3
import requests
import json
from datetime import datetime
from apscheduler.schedulers.asyncio import AsyncIOScheduler
import pytz
import asyncio

load_dotenv()

ADMIN_ID = 1066881053219881050  # Replace with your Discord ID

intents = discord.Intents.default()
intents.messages = True
intents.message_content = True

bot = commands.Bot(command_prefix='!', intents=intents)
scheduler = AsyncIOScheduler(timezone=pytz.UTC)

STATUS_TYPES = {
    1: 'Online',
    2: 'InGame',
    3: 'InStudio',
    4: 'Invisible'
}
CACHE_FILE = 'status_cache.json'

if not os.path.exists(CACHE_FILE):
    with open(CACHE_FILE, 'w') as f:
        json.dump({}, f)

def save_to_cache(roblox_id, data):
    with open(CACHE_FILE, 'r+') as f:
        cache = json.load(f)
        cache[str(roblox_id)] = data
        f.seek(0)
        json.dump(cache, f)
        f.truncate()

def get_from_cache(roblox_id):
    with open(CACHE_FILE, 'r') as f:
        cache = json.load(f)
        return cache.get(str(roblox_id))

conn = sqlite3.connect('users.db')
c = conn.cursor()
c.execute('''CREATE TABLE IF NOT EXISTS users
             (discord_id INTEGER, roblox_id INTEGER, is_main BOOLEAN, 
              last_status INTEGER, username TEXT)''')
conn.commit()

def get_roblox_info(user_id):
    try:
        response = requests.get(f"https://users.roblox.com/v1/users/{user_id}", timeout=5)
        data = response.json()
        return {
            'name': data.get('name', 'Unknown'),
            'thumbnail': f"https://thumbnails.roblox.com/v1/users/avatar-headshot?userIds={user_id}&size=720x720&format=Png&isCircular=false"
        }
    except:
        return {
            'name': 'Unknown',
            'thumbnail': 'https://www.roblox.com/Thumbs/Asset.ashx?width=420&height=420&assetId=0'
        }

def get_presence_info(roblox_id):
    cached_data = get_from_cache(roblox_id) or {}
    
    try:
        response = requests.post(
            "https://presence.roblox.com/v1/presence/users",
            json={"userIds": [int(roblox_id)]},
            headers={"Content-Type": "application/json"},
            timeout=5
        )
        if response.status_code == 200:
            presence = response.json()['userPresences'][0]
            status_code = presence['userPresenceType']
            
            data = {
                'status': STATUS_TYPES.get(status_code, 'Unknown'),
                'status_code': status_code,
                'game_id': presence.get('rootPlaceId'),
                'timestamp': str(datetime.now())
            }
            save_to_cache(roblox_id, data)
            return data
    except:
        pass
    
    if cached_data:
        cached_data['status'] += '*' if not cached_data['status'].endswith('*') else ''
        return cached_data
    return {'status': 'Unknown*', 'status_code': None, 'game_id': None}

def get_game_info(place_id):
    if not place_id:
        return None
    try:
        response = requests.get(f"https://games.roblox.com/v1/games/multiget-place-details?placeIds={place_id}", timeout=5)
        game_data = response.json()[0]
        return {
            'name': game_data.get('name', 'Unknown Game'),
            'url': f"https://www.roblox.com/games/{place_id}"
        }
    except:
        return None

async def send_status_notification(user, roblox_id, is_main, presence_info, old_status):
    new_status = presence_info.get('status_code')
    user_info = get_roblox_info(roblox_id)
    game_info = get_game_info(presence_info['game_id']) if presence_info['game_id'] else None
    
    status_display = presence_info['status'].replace('*', '')
    
    if old_status == 2 and new_status != 2:
        title_part = "is now offline"
    elif new_status == 2 and old_status != 2:
        title_part = "is now InGame"
    else:
        title_part = f"is now {status_display}"
    
    embed = discord.Embed(
        title=f"{'👑' if is_main else '👤'} {user_info['name']} {title_part}",
        color=discord.Color.green() if new_status in [1, 2] else discord.Color.blue(),
        timestamp=datetime.now()
    )
    
    embed.set_thumbnail(url=user_info['thumbnail'])
    
    if new_status == 2 and game_info:
        embed.add_field(name="Playing", value=f"[{game_info['name']}]({game_info['url']})", inline=False)
    
    if '*' in presence_info['status']:
        embed.add_field(name="Note", value="* indicates cached data which may be inaccurate", inline=False)
    
    embed.set_footer(text="👑 Main account | 👤 Alt account")
    
    await user.send(embed=embed)

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user}')
    try:
        synced = await bot.tree.sync()
        print(f"Synced {len(synced)} commands")
    except Exception as e:
        print(f"Error syncing commands: {e}")
    scheduler.start()

async def check_statuses():
    c.execute("SELECT discord_id, roblox_id, is_main, last_status FROM users")
    for row in c.fetchall():
        discord_id, roblox_id, is_main, last_status = row
        presence_info = get_presence_info(roblox_id)
        current_status = presence_info.get('status_code')
        
        if current_status is not None and current_status != last_status:
            user = await bot.fetch_user(discord_id)
            await send_status_notification(user, roblox_id, is_main, presence_info, last_status)
            
            c.execute('''UPDATE users SET last_status=?
                      WHERE discord_id=? AND roblox_id=?''',
                      (current_status, discord_id, roblox_id))
            conn.commit()
        
        await asyncio.sleep(1)

scheduler.add_job(check_statuses, 'interval', minutes=1)

@bot.tree.command(name="add", description="Add a Roblox account to track")
@app_commands.describe(roblox_id="The Roblox user ID to track")
async def add(interaction: discord.Interaction, roblox_id: int):
    c.execute("SELECT COUNT(*) FROM users WHERE discord_id=?", (interaction.user.id,))
    is_main = c.fetchone()[0] == 0
    
    user_info = get_roblox_info(roblox_id)
    presence_info = get_presence_info(roblox_id)
    status_code = presence_info.get('status_code', 1)
    
    c.execute('INSERT INTO users VALUES (?, ?, ?, ?, ?)',
              (interaction.user.id, roblox_id, is_main, status_code, user_info['name']))
    conn.commit()
    
    embed = discord.Embed(
        title=f"{'👑 Main' if is_main else '👤 Alt'} Account Added",
        description=f"Now tracking {user_info['name']} ({roblox_id})",
        color=discord.Color.blue(),
        timestamp=datetime.now()
    )
    embed.set_thumbnail(url=user_info['thumbnail'])
    embed.set_footer(text="👑 Main account | 👤 Alt account")
    
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="accounts", description="List your tracked accounts")
async def accounts(interaction: discord.Interaction):
    c.execute("SELECT roblox_id, is_main, username, last_status FROM users WHERE discord_id=?", (interaction.user.id,))
    
    embed = discord.Embed(title="Your Tracked Accounts", timestamp=datetime.now())
    
    for roblox_id, is_main, username, status_code in c.fetchall():
        status = STATUS_TYPES.get(status_code, 'Unknown*')
        embed.add_field(
            name=f"{'👑' if is_main else '👤'} {username}",
            value=f"{status}\nID: {roblox_id}",
            inline=False
        )
    
    embed.set_footer(text="👑 Main account | 👤 Alt account")
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="status", description="Check an account's status")
@app_commands.describe(roblox_id="The Roblox user ID to check")
async def status(interaction: discord.Interaction, roblox_id: int):
    c.execute("SELECT is_main FROM users WHERE discord_id=? AND roblox_id=?", (interaction.user.id, roblox_id))
    account = c.fetchone()
    
    if not account:
        await interaction.response.send_message("You're not tracking this account!", ephemeral=True)
        return
    
    is_main = account[0]
    presence_info = get_presence_info(roblox_id)
    user_info = get_roblox_info(roblox_id)
    
    status_emojis = {
        1: "🟢", 2: "🎮", 3: "💻", 4: "👻"
    }
    
    status_code = presence_info.get('status_code')
    status_emoji = status_emojis.get(status_code, "❓")
    account_emoji = "👑" if is_main else "👤"
    
    embed = discord.Embed(
        title=f"{account_emoji} {status_emoji} {user_info['name']} is {presence_info['status']}",
        timestamp=datetime.now()
    )
    embed.set_thumbnail(url=user_info['thumbnail'])
    
    if status_code == 2 and presence_info.get('game_id'):
        game_info = get_game_info(presence_info['game_id'])
        if game_info:
            embed.add_field(name="Playing", value=f"[{game_info['name']}]({game_info['url']})", inline=False)
    
    if '*' in presence_info['status']:
        embed.add_field(name="Note", value="* indicates cached data (might be inaccurate)", inline=False)
    
    embed.set_footer(text="👑 Main account | 👤 Alt account")
    await interaction.response.send_message(embed=embed)

@bot.tree.command(name="remove", description="Remove an account from tracking")
@app_commands.describe(roblox_id="The Roblox user ID to remove")
async def remove(interaction: discord.Interaction, roblox_id: int):
    c.execute("DELETE FROM users WHERE discord_id=? AND roblox_id=?", (interaction.user.id, roblox_id))
    if c.rowcount > 0:
        user_info = get_roblox_info(roblox_id)
        embed = discord.Embed(
            title="Account Removed",
            description=f"Stopped tracking {user_info['name']} ({roblox_id})",
            color=discord.Color.orange(),
            timestamp=datetime.now()
        )
        embed.set_thumbnail(url=user_info['thumbnail'])
        await interaction.response.send_message(embed=embed, ephemeral=True)
    else:
        await interaction.response.send_message("You weren't tracking this account!", ephemeral=True)

@bot.tree.command(name="say", description="Send a message to all users (Admin only)")
@app_commands.describe(message="The message to send")
async def say(interaction: discord.Interaction, message: str):
    if interaction.user.id != ADMIN_ID:
        await interaction.response.send_message("Permission denied!", ephemeral=True)
        return
    
    await interaction.response.send_message("Sending messages...", ephemeral=True)
    
    c.execute("SELECT DISTINCT discord_id FROM users")
    user_ids = [row[0] for row in c.fetchall()]
    
    success, failed = 0, 0
    for user_id in user_ids:
        try:
            user = await bot.fetch_user(user_id)
            embed = discord.Embed(title="📢 Announcement", description=message, color=0x5865F2)
            await user.send(embed=embed)
            success += 1
        except:
            failed += 1
        await asyncio.sleep(1)
    
    await interaction.followup.send(f"Success: {success}, Failed: {failed}", ephemeral=True)

@bot.tree.command(name="help", description="Show all commands and their usage")
async def help_command(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Bot Commands Help",
        description="List of available commands:",
        color=discord.Color.blue()
    )
    commands_list = [
        ("/add <roblox_id>", "Track a Roblox account"),
        ("/accounts", "List your tracked accounts"),
        ("/status <roblox_id>", "Check an account's current status"),
        ("/remove <roblox_id>", "Stop tracking an account"),
        ("/say <message>", "Admin: Broadcast a message"),
        ("/help", "Show this help menu"),
        ("/credits", "Bot developer credits")
    ]
    for cmd, desc in commands_list:
        embed.add_field(name=cmd, value=desc, inline=False)
    await interaction.response.send_message(embed=embed, ephemeral=True)

@bot.tree.command(name="credits", description="Show the bot's credits")
async def credits(interaction: discord.Interaction):
    embed = discord.Embed(
        title="Credits",
        description="Developed by [Your Name]",
        color=discord.Color.green()
    )
    embed.add_field(name="GitHub", value="[Repository Link](https://github.com/your-repo)")
    await interaction.response.send_message(embed=embed, ephemeral=True)

bot.run(os.getenv('DISCORD_TOKEN'))
