"""
agentcookie-windows
===================
Windows port of agentcookie - sync your Chrome sessions and API keys
to your agent machine automatically.

Original agentcookie by mvanhorn (Mac only):
https://github.com/mvanhorn/agentcookie

This port brings the same concept to Windows:
- Watches your Chrome cookies and API keys on your main machine
- Ships changes to your agent machine over Tailscale or SSH
- Your agent (Hermes, Claude Code, OpenClaw) wakes up authenticated

How it works:
  1. Run this on your MAIN Windows machine (your daily driver)
  2. It watches Chrome cookies + your .env API key files
  3. When anything changes, it syncs to your agent machine
  4. Your agent machine reads the synced session - no re-auth needed

Built by Lachezar Atanasov - lachezaratanasov.com
Windows port of: github.com/mvanhorn/agentcookie
"""

import os
import sys
import time
import shutil
import sqlite3
import hashlib
import logging
import subprocess
from pathlib import Path
from datetime import datetime
from dotenv import load_dotenv

load_dotenv()

# ── Logging ───────────────────────────────────────────────────────────────────

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s - %(levelname)s - %(message)s",
    handlers=[
        logging.StreamHandler(),
        logging.FileHandler("agentcookie.log")
    ]
)
log = logging.getLogger("agentcookie-windows")


# ── Configuration ─────────────────────────────────────────────────────────────

# Your agent machine - set these in .env or pass as environment variables
AGENT_HOST = os.getenv("AGENT_HOST", "")          # e.g. 100.x.x.x (Tailscale IP) or hostname
AGENT_USER = os.getenv("AGENT_USER", "")          # SSH username on agent machine
AGENT_SSH_KEY = os.getenv("AGENT_SSH_KEY", "")    # Path to SSH private key
AGENT_COOKIE_DIR = os.getenv("AGENT_COOKIE_DIR", "~/.agentcookie")  # Where to sync on agent

# What to watch on your main machine
WATCH_INTERVAL = int(os.getenv("WATCH_INTERVAL", "30"))  # seconds between checks

# Chrome cookie paths on Windows
CHROME_COOKIE_PATHS = [
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Default" / "Cookies",
    Path(os.environ.get("LOCALAPPDATA", "")) / "Google" / "Chrome" / "User Data" / "Profile 1" / "Cookies",
    # Edge (Chromium-based) - bonus
    Path(os.environ.get("LOCALAPPDATA", "")) / "Microsoft" / "Edge" / "User Data" / "Default" / "Cookies",
]

# API key files to sync (add your own .env files here)
API_KEY_FILES = [
    Path.home() / ".env",
    Path.home() / "agentcookie" / "secrets.env",
]


# ── Cookie Reader ─────────────────────────────────────────────────────────────

class ChromeCookieReader:
    """
    Reads Chrome's cookie database on Windows.

    Chrome locks the Cookies SQLite file while running.
    We make a temporary copy first, then read from the copy.
    This is safe - read only, never modifies the original.
    """

    def __init__(self, cookie_path: Path):
        self.cookie_path = cookie_path
        self.temp_path = Path(os.environ.get("TEMP", "/tmp")) / "agentcookie_temp.db"

    def get_cookies(self, domain_filter: str = None) -> list:
        """
        Read cookies from Chrome's database.

        Args:
            domain_filter: Optional domain to filter (e.g. ".github.com")

        Returns:
            List of cookie dicts
        """
        if not self.cookie_path.exists():
            log.warning(f"Cookie file not found: {self.cookie_path}")
            return []

        # Make a temp copy (Chrome locks the original)
        try:
            shutil.copy2(self.cookie_path, self.temp_path)
        except PermissionError:
            log.warning("Chrome is running and has locked the cookie file. Close Chrome to sync cookies.")
            return []

        try:
            conn = sqlite3.connect(str(self.temp_path))
            cursor = conn.cursor()

            if domain_filter:
                cursor.execute(
                    "SELECT host_key, name, value, path, expires_utc, is_secure "
                    "FROM cookies WHERE host_key LIKE ?",
                    (f"%{domain_filter}%",)
                )
            else:
                cursor.execute(
                    "SELECT host_key, name, value, path, expires_utc, is_secure "
                    "FROM cookies"
                )

            cookies = []
            for row in cursor.fetchall():
                cookies.append({
                    "domain": row[0],
                    "name": row[1],
                    "value": row[2],
                    "path": row[3],
                    "expires": row[4],
                    "secure": bool(row[5])
                })

            conn.close()
            return cookies

        except sqlite3.Error as e:
            log.error(f"Error reading cookies: {e}")
            return []
        finally:
            if self.temp_path.exists():
                self.temp_path.unlink()

    def export_netscape_format(self, output_path: Path, domain_filter: str = None):
        """
        Export cookies in Netscape format (curl/wget compatible).
        This format is readable by most CLI tools and agent runtimes.
        """
        cookies = self.get_cookies(domain_filter)
        if not cookies:
            return False

        output_path.parent.mkdir(parents=True, exist_ok=True)

        with open(output_path, "w") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# Synced by agentcookie-windows\n")
            f.write(f"# Generated: {datetime.now().isoformat()}\n\n")

            for cookie in cookies:
                # Netscape format: domain, include_subdomains, path, secure, expiry, name, value
                include_subdomains = "TRUE" if cookie["domain"].startswith(".") else "FALSE"
                secure = "TRUE" if cookie["secure"] else "FALSE"
                expires = cookie["expires"] or 0

                f.write(
                    f"{cookie['domain']}\t{include_subdomains}\t{cookie['path']}\t"
                    f"{secure}\t{expires}\t{cookie['name']}\t{cookie['value']}\n"
                )

        log.info(f"Exported {len(cookies)} cookies to {output_path}")
        return True


# ── API Key Syncer ────────────────────────────────────────────────────────────

class ApiKeySyncer:
    """
    Syncs API key files (.env files) to the agent machine.
    Watches for changes and only syncs when something actually changed.
    """

    def __init__(self, key_files: list):
        self.key_files = [Path(f) for f in key_files if Path(f).exists()]
        self._file_hashes = {}

    def get_file_hash(self, path: Path) -> str:
        """Get MD5 hash of a file to detect changes."""
        try:
            with open(path, "rb") as f:
                return hashlib.md5(f.read()).hexdigest()
        except Exception:
            return ""

    def has_changed(self, path: Path) -> bool:
        """Check if a file has changed since last check."""
        current_hash = self.get_file_hash(path)
        previous_hash = self._file_hashes.get(str(path), "")
        if current_hash != previous_hash:
            self._file_hashes[str(path)] = current_hash
            return True
        return False

    def get_changed_files(self) -> list:
        """Return list of API key files that have changed."""
        return [f for f in self.key_files if self.has_changed(f)]


# ── SSH Syncer ────────────────────────────────────────────────────────────────

class SSHSyncer:
    """
    Syncs files to the agent machine over SSH/SCP.
    Works with Tailscale (recommended) or any SSH connection.

    Security model (same as original agentcookie):
    - One direction only: your machine -> agent machine
    - Never syncs back
    - Uses SSH key authentication
    - Works over Tailscale private network
    """

    def __init__(self, host: str, user: str, ssh_key: str = None, remote_dir: str = "~/.agentcookie"):
        self.host = host
        self.user = user
        self.ssh_key = ssh_key
        self.remote_dir = remote_dir

    def is_configured(self) -> bool:
        """Check if SSH sync is configured."""
        return bool(self.host and self.user)

    def sync_file(self, local_path: Path, remote_filename: str = None) -> bool:
        """
        Sync a single file to the agent machine.

        Args:
            local_path: Local file to sync
            remote_filename: Optional rename on remote. Defaults to same name.

        Returns:
            True if successful
        """
        if not self.is_configured():
            log.warning("SSH not configured. Sync skipped. Set AGENT_HOST and AGENT_USER in .env")
            return False

        remote_filename = remote_filename or local_path.name
        remote_path = f"{self.remote_dir}/{remote_filename}"

        # Build SCP command
        cmd = ["scp"]
        if self.ssh_key:
            cmd.extend(["-i", self.ssh_key])
        cmd.extend([
            "-o", "StrictHostKeyChecking=no",
            str(local_path),
            f"{self.user}@{self.host}:{remote_path}"
        ])

        try:
            # Create remote directory first
            mkdir_cmd = ["ssh"]
            if self.ssh_key:
                mkdir_cmd.extend(["-i", self.ssh_key])
            mkdir_cmd.extend([
                "-o", "StrictHostKeyChecking=no",
                f"{self.user}@{self.host}",
                f"mkdir -p {self.remote_dir}"
            ])
            subprocess.run(mkdir_cmd, capture_output=True, timeout=10)

            # Sync the file
            result = subprocess.run(cmd, capture_output=True, timeout=30)
            if result.returncode == 0:
                log.info(f"Synced {local_path.name} to {self.host}:{remote_path}")
                return True
            else:
                log.error(f"Sync failed: {result.stderr.decode()}")
                return False

        except subprocess.TimeoutExpired:
            log.error(f"Sync timed out. Is {self.host} reachable?")
            return False
        except FileNotFoundError:
            log.error("SCP not found. Make sure OpenSSH is installed on Windows.")
            return False

    def sync_local_only(self, local_path: Path, output_dir: Path) -> bool:
        """
        Save to local output directory (for testing without a second machine).
        Useful for seeing what would be synced.
        """
        output_dir.mkdir(parents=True, exist_ok=True)
        dest = output_dir / local_path.name
        shutil.copy2(local_path, dest)
        log.info(f"Local sync: {local_path.name} -> {dest}")
        return True


# ── Main Watcher ──────────────────────────────────────────────────────────────

class AgentCookieWindows:
    """
    Main watcher - monitors cookies and API keys, syncs when changed.
    """

    def __init__(self, local_only: bool = False):
        self.local_only = local_only
        self.output_dir = Path.home() / ".agentcookie" / "sync"

        # Initialize components
        self.cookie_readers = [
            ChromeCookieReader(p) for p in CHROME_COOKIE_PATHS if p.exists()
        ]
        self.api_syncer = ApiKeySyncer(API_KEY_FILES)
        self.ssh_syncer = SSHSyncer(
            host=AGENT_HOST,
            user=AGENT_USER,
            ssh_key=AGENT_SSH_KEY,
            remote_dir=AGENT_COOKIE_DIR
        )

        self._last_cookie_hash = ""

    def get_cookies_hash(self) -> str:
        """Get a hash representing current cookie state."""
        hashes = []
        for reader in self.cookie_readers:
            if reader.cookie_path.exists():
                try:
                    h = hashlib.md5(open(reader.cookie_path, "rb").read()).hexdigest()
                    hashes.append(h)
                except Exception:
                    pass
        return "-".join(hashes)

    def sync_cookies(self) -> bool:
        """Export and sync cookies if they've changed."""
        current_hash = self.get_cookies_hash()
        if current_hash == self._last_cookie_hash:
            return False

        self._last_cookie_hash = current_hash
        log.info("Cookies changed - syncing...")

        synced = False
        for reader in self.cookie_readers:
            browser = "edge" if "Edge" in str(reader.cookie_path) else "chrome"
            output_path = self.output_dir / f"{browser}_cookies.txt"

            if reader.export_netscape_format(output_path):
                if self.local_only:
                    synced = True
                elif self.ssh_syncer.is_configured():
                    synced = self.ssh_syncer.sync_file(output_path, f"{browser}_cookies.txt")

        return synced

    def sync_api_keys(self) -> bool:
        """Sync API key files if they've changed."""
        changed_files = self.api_syncer.get_changed_files()
        if not changed_files:
            return False

        synced = False
        for key_file in changed_files:
            log.info(f"API key file changed: {key_file.name}")

            if self.local_only:
                self.ssh_syncer.sync_local_only(key_file, self.output_dir)
                synced = True
            elif self.ssh_syncer.is_configured():
                synced = self.ssh_syncer.sync_file(key_file)

        return synced

    def status(self):
        """Print current status."""
        print(f"\n{'='*60}")
        print(f"  agentcookie-windows")
        print(f"  Windows port by Lachezar Atanasov")
        print(f"{'='*60}")
        print(f"\nWatching:")
        for reader in self.cookie_readers:
            browser = "Edge" if "Edge" in str(reader.cookie_path) else "Chrome"
            status = "FOUND" if reader.cookie_path.exists() else "NOT FOUND"
            print(f"  {browser} cookies: {status}")

        print(f"\nAPI key files:")
        for f in API_KEY_FILES:
            status = "FOUND" if Path(f).exists() else "NOT FOUND"
            print(f"  {f}: {status}")

        print(f"\nAgent machine:")
        if AGENT_HOST:
            print(f"  Host: {AGENT_HOST}")
            print(f"  User: {AGENT_USER}")
            print(f"  Remote dir: {AGENT_COOKIE_DIR}")
        else:
            print(f"  Not configured (set AGENT_HOST in .env)")
            print(f"  Running in LOCAL ONLY mode - syncing to {self.output_dir}")

        print(f"\nSync interval: every {WATCH_INTERVAL} seconds")
        print(f"\nStarting watcher... (Ctrl+C to stop)\n")

    def run(self):
        """Main watch loop."""
        self.status()

        while True:
            try:
                cookie_synced = self.sync_cookies()
                api_synced = self.sync_api_keys()

                if not cookie_synced and not api_synced:
                    log.debug(f"No changes detected. Next check in {WATCH_INTERVAL}s")

                time.sleep(WATCH_INTERVAL)

            except KeyboardInterrupt:
                print("\n\nStopping agentcookie-windows. Your agent machine keeps its last synced session.")
                sys.exit(0)
            except Exception as e:
                log.error(f"Unexpected error: {e}")
                time.sleep(WATCH_INTERVAL)


# ── Entry Point ───────────────────────────────────────────────────────────────

if __name__ == "__main__":
    import argparse

    parser = argparse.ArgumentParser(
        description="agentcookie-windows - sync your Chrome sessions to your agent machine",
        formatter_class=argparse.RawDescriptionHelpFormatter,
        epilog="""
Examples:
  # Watch and sync (requires AGENT_HOST in .env)
  python agentcookie_windows.py

  # Test mode - sync locally, no second machine needed
  python agentcookie_windows.py --local-only

  # One-time sync instead of watching
  python agentcookie_windows.py --once

  # Check what would be synced without doing it
  python agentcookie_windows.py --dry-run
        """
    )
    parser.add_argument("--local-only", action="store_true",
                       help="Sync to local ~/.agentcookie/sync instead of remote machine")
    parser.add_argument("--once", action="store_true",
                       help="Run once and exit instead of watching")
    parser.add_argument("--dry-run", action="store_true",
                       help="Show what would be synced without actually syncing")

    args = parser.parse_args()

    if args.dry_run:
        print("DRY RUN - showing what would be synced:\n")
        for path in CHROME_COOKIE_PATHS:
            if path.exists():
                print(f"WOULD SYNC: {path}")
        for path in API_KEY_FILES:
            if Path(path).exists():
                print(f"WOULD SYNC: {path}")
        sys.exit(0)

    agent = AgentCookieWindows(local_only=args.local_only or not AGENT_HOST)

    if args.once:
        agent.sync_cookies()
        agent.sync_api_keys()
        print("One-time sync complete.")
    else:
        agent.run()
