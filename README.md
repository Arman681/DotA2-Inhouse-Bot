# DotA2-Inhouse-Bot

A Discord bot built with Python that manages DotA2 inhouse lobbies — showing players, their MMR, and lobby info using slash commands. Designed to run 24/7 on Heroku.

---

## 🚀 Features

- 📋 `!lobby` command to display inhouse player list
- 🔐 Token-based integration with the Stratz.com API
- 🌐 Hosted on Heroku for 24/7 uptime
- ⚙️ Uses `discord.py` with async support

---

## ⚙️ Requirements

- Python 3.8+
- Discord Bot Token
- STRATZ API Token

---

## 🛠️ Local Setup

1. **Clone the repo**

```bash
git clone https://github.com/Arman681/DotA2-Inhouse-Bot.git
cd DotA2-Inhouse-Bot
```

2. **Create a `.env` file** in the root directory:

```
DISCORD_TOKEN=your_discord_token_here
STRATZ_TOKEN=your_stratz_token_here
```

3. **Install dependencies**

```bash
pip install -r requirements.txt
```

4. **Run the bot**

```bash
python FeederBot.py
```

---

## ☁️ Heroku Deployment

1. Push your code to GitHub
2. Create a Heroku app
3. Add these Config Vars in **Heroku → Settings → Reveal Config Vars**:
   - `DISCORD_TOKEN`
   - `STRATZ_TOKEN`
4. Ensure you have:
   - A `Procfile` containing: `worker: python FeederBot.py`
   - A complete `requirements.txt`
5. Enable the `worker` dyno in the **Resources** tab
6. The bot will start automatically and run 24/7

---

## 📂 File Structure

```
DotA2-Inhouse-Bot/
├── FeederBot.py
├── Procfile
├── requirements.txt
├── .gitignore
├── .env (not tracked by Git)
└── README.md
```

---

## 🧾 License

MIT License — free to use and modify.
