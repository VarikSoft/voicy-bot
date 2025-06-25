<p align="center">
  <img src="https://img.shields.io/badge/version-1.0.0-blue?style=flat-square" />
  <img src="https://img.shields.io/badge/status-stable-brightgreen?style=flat-square" />
  <img src="https://img.shields.io/badge/license-MIT-green?style=flat-square" />
  <img src="https://img.shields.io/badge/python-3.10+-blue?style=flat-square&logo=python" />
  <img src="https://img.shields.io/badge/discord.py-2.x-blueviolet?style=flat-square&logo=discord" />
</p>

# 🔊 Voicy — Discord Private Voice Channel Manager

Voicy is a Discord bot that lets users create **temporary personal voice channels** with saved preferences, full access control, and an interactive panel to manage everything directly inside Discord.

Each user can have their own private VC with:
- Saved name, user limit, visibility, and permissions
- Invite, kick, assign deputies and control access
- Automatic cleanup after inactivity
- Settings persist even if the bot restarts

## 🚀 Getting Started

```bash
# 1. Clone the repository
git clone https://github.com/VarikSoft/voicy-bot.git
cd voicy-bot

# 2. Install dependencies
pip install -r requirements.txt

# 3. Create a .env file in the project root
# Example:
# TOKEN=your_bot_token_here
# BOT_LANG=en

# 4. Run the bot
python bot.py
```

To make the bot work properly, you need to define two required Discord channel IDs in your code:

```python
CREATE_VC_CHANNEL_ID = 1386893005578834020  # Channel users join to trigger VC creation
VC_CATEGORY_ID       = 1386453793012453417  # Category where private VCs will be created
```
These values determine:
- CREATE_VC_CHANNEL_ID: A voice channel that acts as a trigger for creating a private voice channel.
- VC_CATEGORY_ID: The category where all user-created voice channels will be placed.

💡 If you prefer, you can implement your own `slash` commands to dynamically set these values — I personally didn’t need that 😄

## 🛠️ Features
- 🎙️ Temporary private voice channel creation.
- 🔒 Invite, kick, lock/unlock, visible/invisible toggles.
- 👑 Assign deputies with manage rights.
- 💾 Persistent user templates in JSON format.
- 🧵 Management threads created automatically.
- ✨ Slash command support for all channel actions.
- 🌍 Multi-language via lang/ru.json, lang/en.json, etc.

## 📦 Technologies Used
- 🐍 Python 3.10+
- 🤖 discord.py 2.x
- 🗂️ python-dotenv
- 📁 JSON (no database needed!)
- 📦 Local file system

## 📁 Project Structure
```
voicy-bot/
├── lang/
│   ├── en.json
│   └── ru.json
├── requirements.txt
└── bot.py
```

## 🔐 .env File Example
Create a file named .env in the project root:

```
TOKEN=your_bot_token_here
BOT_LANG=en
```
⚠️ Make sure .env is in .gitignore to avoid leaking your token.

## 💬 Supported Slash Commands
Command	Description
- `/rename`	Rename your voice channel
- `/limit`	Set a user limit
- `/invite`	Invite a user
- `/kick`	Kick/block a user from VC
- `/assign`	Give someone manage access
- `/unassign`	Remove deputy rights
- `/delete`	Manually delete your VC

## 🧹 Auto-Cleanup
Voicy automatically deletes the user's voice channel after it's empty for a set number of minutes (`timeout`). Threads are cleaned up too.

## 🧪 Example Template Format (templates.json)
```json
{
  "owner_id": {
    "name": "My VC",
    "user_limit": 10,
    "invited": [123, 456],
    "kicked": [],
    "visible": true,
    "locked": false,
    "deputies": [789]
  }
}
```

## 🙌 Contributing
Pull requests and issues are welcome!
Feel free to customize this bot for your own server needs.
