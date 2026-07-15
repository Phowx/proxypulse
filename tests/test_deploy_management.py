from __future__ import annotations

import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from unittest import TestCase


PROJECT_ROOT = Path(__file__).resolve().parents[1]


class DeployManagementTests(TestCase):
    def test_manage_menu_exposes_separate_actions_and_exit(self) -> None:
        result = subprocess.run(
            ["bash", str(PROJECT_ROOT / "deploy" / "manage.sh")],
            input="0\n",
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn("安装或更新 Server", result.stdout)
        self.assertIn("重新配置 Agent 采集范围", result.stdout)
        self.assertIn("完全卸载 Server 与 Agent", result.stdout)

    def test_env_merge_preserves_existing_values_and_creates_backup(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            temp = Path(temp_dir)
            fake_bin = self._fake_commands(temp)
            target = temp / "etc" / "agent.env"
            target.parent.mkdir()
            target.write_text(
                "PROXYPULSE_SERVER_URL=https://old.example\n"
                "PROXYPULSE_CUSTOM_VALUE=keep-me\n",
                encoding="utf-8",
            )
            command = (
                f'source "{PROJECT_ROOT / "deploy" / "lib" / "env.sh"}"; '
                f'install_merged_env "{PROJECT_ROOT / "deploy" / "env" / "agent.env.example"}" '
                f'"{target}" PROXYPULSE_SERVER_URL "https://new.example" '
                'PROXYPULSE_AGENT_ENROLLMENT_TOKEN "hidden-token"'
            )
            result = subprocess.run(
                ["bash", "-c", command],
                text=True,
                capture_output=True,
                env={**os.environ, "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}"},
                check=False,
            )

            content = target.read_text(encoding="utf-8")
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("PROXYPULSE_SERVER_URL=https://new.example", content)
            self.assertIn("PROXYPULSE_CUSTOM_VALUE=keep-me", content)
            self.assertIn("PROXYPULSE_COLLECTIONS=identity,cpu,memory,disk,network,uptime", content)
            self.assertNotIn("hidden-token", result.stdout + result.stderr)
            self.assertTrue(list(target.parent.glob("agent.env.bak.*")))
            self.assertEqual(stat.S_IMODE(target.stat().st_mode), 0o600)

    def test_server_only_uninstall_preserves_agent_and_shared_files(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            repo, env, paths = self._scoped_installation(temp_dir)
            result = subprocess.run(
                ["bash", str(repo / "deploy" / "uninstall.sh"), "--server", "--yes"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((paths["systemd"] / "proxypulse-api.service").exists())
            self.assertFalse((paths["systemd"] / "proxypulse-bot.service").exists())
            self.assertFalse((paths["env"] / "server.env").exists())
            self.assertTrue((paths["systemd"] / "proxypulse-agent.service").exists())
            self.assertTrue((paths["env"] / "agent.env").exists())
            self.assertTrue((paths["state"] / "agent-state.json").exists())
            self.assertTrue((repo / ".venv").exists())
            self.assertTrue((repo / "proxypulse.db").exists())
            self.assertIn("另一个角色仍在使用", result.stdout)

    def test_agent_only_uninstall_preserves_server_and_database(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            repo, env, paths = self._scoped_installation(temp_dir)
            result = subprocess.run(
                ["bash", str(repo / "deploy" / "uninstall.sh"), "--agent", "--yes"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertFalse((paths["systemd"] / "proxypulse-agent.service").exists())
            self.assertFalse((paths["env"] / "agent.env").exists())
            self.assertFalse((paths["state"] / "agent-state.json").exists())
            self.assertTrue((paths["systemd"] / "proxypulse-api.service").exists())
            self.assertTrue((paths["env"] / "server.env").exists())
            self.assertTrue((repo / ".venv").exists())
            self.assertTrue((repo / "proxypulse.db").exists())

    def _scoped_installation(self, temp_dir: str):
        temp = Path(temp_dir)
        repo = temp / "Proxy Pulse"
        (repo / "deploy" / "lib").mkdir(parents=True)
        shutil.copy2(PROJECT_ROOT / "deploy" / "uninstall.sh", repo / "deploy" / "uninstall.sh")
        shutil.copy2(PROJECT_ROOT / "deploy" / "lib" / "common.sh", repo / "deploy" / "lib" / "common.sh")
        shutil.copy2(PROJECT_ROOT / "deploy" / "lib" / "caddy.sh", repo / "deploy" / "lib" / "caddy.sh")

        env_dir = temp / "etc" / "proxypulse"
        state_dir = temp / "var" / "lib" / "proxypulse"
        systemd_dir = temp / "systemd"
        env_dir.mkdir(parents=True)
        state_dir.mkdir(parents=True)
        systemd_dir.mkdir()
        for name in ("proxypulse-api.service", "proxypulse-bot.service", "proxypulse-agent.service"):
            (systemd_dir / name).write_text("unit", encoding="utf-8")
        (env_dir / "server.env").write_text("server", encoding="utf-8")
        (env_dir / "agent.env").write_text("agent", encoding="utf-8")
        (state_dir / "agent-state.json").write_text("{}", encoding="utf-8")
        (repo / ".venv").mkdir()
        (repo / "proxypulse.db").write_text("database", encoding="utf-8")

        fake_bin = self._fake_commands(temp)
        env = {
            **os.environ,
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
            "PROXYPULSE_ENV_DIR": str(env_dir),
            "PROXYPULSE_STATE_DIR": str(state_dir),
            "PROXYPULSE_SYSTEMD_DIR": str(systemd_dir),
            "PROXYPULSE_CADDY_CONFIG_DIR": str(temp / "caddy"),
        }
        return repo, env, {"env": env_dir, "state": state_dir, "systemd": systemd_dir}

    def _fake_commands(self, temp: Path) -> Path:
        fake_bin = temp / "bin"
        fake_bin.mkdir(exist_ok=True)
        sudo = fake_bin / "sudo"
        sudo.write_text(
            "#!/usr/bin/env bash\n"
            "if [[ \"${1:-}\" == \"-v\" ]]; then exit 0; fi\n"
            "exec \"$@\"\n",
            encoding="utf-8",
        )
        sudo.chmod(0o755)
        systemctl = fake_bin / "systemctl"
        systemctl.write_text(
            "#!/usr/bin/env bash\n"
            "case \"${1:-}\" in is-active|is-enabled) exit 1;; *) exit 0;; esac\n",
            encoding="utf-8",
        )
        systemctl.chmod(0o755)
        return fake_bin
