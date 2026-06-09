# agentcookie-windows

Windows port of [agentcookie](https://github.com/mvanhorn/agentcookie) - sync your Chrome sessions and API keys to your agent machine automatically.

**The original agentcookie is Mac only. This brings the same concept to Windows.**

Built by [Lachezar Atanasov](https://lachezaratanasov.com) - AI Product Consultant

---

## The problem this solves

Your AI agent (Hermes, Claude Code, OpenClaw) runs on a second machine. That machine isn't logged into anything.

Every time you set it up you log into every site again. Every tool. Every API key. By hand.

agentcookie-windows fixes that. It watches your main Windows machine and ships your Chrome sessions and API keys to your agent machine automatically - over your private Tailscale network. One direction only.

Your agent wakes up already logged in. To everything.

---

## Quick start

```bash
git clone https://github.com/ibmlachezar/agentcookie-windows
cd agentcookie-windows
pip install -r requirements.txt
copy .env.example .env
```

Edit `.env` with your agent machine details. Then:

```bash
# Test locally first (no second machine needed)
python agentcookie_windows.py --local-only

# Watch and sync to your agent machine
python agentcookie_windows.py
```

---

## What it syncs

**Chrome cookies** - exported in Netscape format, readable by curl, wget, and any agent runtime

**Edge cookies** - same format, auto-detected

**API key files** - your .env files with API keys for OpenAI, Anthropic, etc.

---

## Security model (same as original agentcookie)

- One direction only: your machine to agent machine, never back
- Everything moves over your private Tailscale network
- SSH key authentication - no passwords
- Never modifies your original cookie files - reads a temp copy

---

## Full autonomous agent setup guide

This tool is part of a complete Windows autonomous agent stack. Here's the full setup:

### Step 1 - Install Tailscale

Download from [tailscale.com](https://tailscale.com) on both machines.

On your agent machine:
```bash
tailscale up
tailscale ip -4  # note this IP - it goes in your .env as AGENT_HOST
```

### Step 2 - Set up SSH

On your main Windows machine:
```bash
# Generate SSH key if you don't have one
ssh-keygen -t ed25519

# Copy public key to agent machine
type %USERPROFILE%\.ssh\id_ed25519.pub
# Paste this into ~/.ssh/authorized_keys on your agent machine
```

### Step 3 - Run agentcookie-windows

```bash
python agentcookie_windows.py
```

Your sessions start syncing.

### Step 4 - Install Hermes Agent on your agent machine

```bash
pip install hermes-agent
hermes start
```

Hermes reads the synced cookies automatically from `~/.agentcookie/`.

### Step 5 - Add the AI Stack Advisor skill

```bash
# Clone the AI Stack Advisor
git clone https://github.com/ibmlachezar/ai-stack-advisor

# Copy the skill to Hermes
cp ai-stack-advisor/ai_stack_advisor.py ~/.hermes/skills/
cp ai-stack-advisor/SOUL.md ~/.hermes/skills/
```

Now ask Hermes: "What AI tools should my startup use?"

---

## Options

```bash
# Watch and sync continuously (default)
python agentcookie_windows.py

# Test locally - sync to ~/.agentcookie/sync without a second machine
python agentcookie_windows.py --local-only

# One-time sync instead of watching
python agentcookie_windows.py --once

# See what would be synced without doing it
python agentcookie_windows.py --dry-run
```

---

## Troubleshooting

**Chrome cookie file locked**
Chrome locks the cookie file while running. Close Chrome, run a one-time sync, then reopen Chrome.

```bash
python agentcookie_windows.py --once
```

**SSH connection refused**
Make sure Tailscale is running on both machines and your SSH key is in `~/.ssh/authorized_keys` on the agent machine.

**Cookies not being read by agent**
Hermes and most agent runtimes look for cookies in Netscape format at `~/.agentcookie/`. The synced files are placed there automatically.

---

## The full stack

| Tool | What it does | Link |
|---|---|---|
| agentcookie-windows | Syncs your sessions to agent machine | This repo |
| Hermes Agent | Always-on agent runtime | [github.com/mvanhorn/agentcookie](https://github.com/mvanhorn/agentcookie) |
| AI Stack Advisor | Recommends AI tools for your situation | [github.com/ibmlachezar/ai-stack-advisor](https://github.com/ibmlachezar/ai-stack-advisor) |
| Tailscale | Private network between your machines | [tailscale.com](https://tailscale.com) |

---

## Credit

This is a Windows port of [agentcookie](https://github.com/mvanhorn/agentcookie) by mvanhorn. The original concept and security model are his. This port brings it to Windows users.

---

## Built by

**Lachezar Atanasov** - Head of AI Product, AI startup founder, advisor to multiple AI companies.

- [lachezaratanasov.com](https://lachezaratanasov.com)
- [LinkedIn](https://www.linkedin.com/in/lachezar-atanasov198/)
- [GitHub](https://github.com/ibmlachezar)

## License

MIT
