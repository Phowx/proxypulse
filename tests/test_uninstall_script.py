from __future__ import annotations

import os
from pathlib import Path
import shutil
import subprocess
import tempfile
from unittest import TestCase


PROJECT_ROOT = Path(__file__).resolve().parents[1]
SCRIPT = PROJECT_ROOT / "deploy" / "uninstall.sh"


class UninstallScriptTests(TestCase):
    def test_declining_confirmation_makes_no_changes(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir, command_log, env = self._prepare_installation(temp_dir)
            result = subprocess.run(
                ["bash", str(repo_dir / "deploy" / "uninstall.sh")],
                input="n\n",
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn("Uninstall cancelled.", result.stdout)
            self.assertFalse(command_log.exists())
            self.assertTrue((repo_dir / ".venv").exists())
            self.assertTrue((repo_dir / "proxypulse.db").exists())

    def _prepare_installation(
        self, temp_dir: str
    ) -> tuple[Path, Path, dict[str, str]]:
        temp_path = Path(temp_dir)
        repo_dir = temp_path / "Proxy Pulse"
        deploy_dir = repo_dir / "deploy"
        deploy_dir.mkdir(parents=True)
        shutil.copy2(SCRIPT, deploy_dir / "uninstall.sh")

        (repo_dir / ".venv").mkdir()
        (repo_dir / "proxypulse.db").write_text("database", encoding="utf-8")

        command_log = temp_path / "commands.log"
        fake_bin = temp_path / "bin"
        fake_bin.mkdir()
        fake_sudo = fake_bin / "sudo"
        fake_sudo.write_text(
            "#!/usr/bin/env bash\n"
            "printf '%s\\n' \"$*\" >> \"${COMMAND_LOG}\"\n",
            encoding="utf-8",
        )
        fake_sudo.chmod(0o755)
        env = {
            **os.environ,
            "COMMAND_LOG": str(command_log),
            "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
        }
        return repo_dir, command_log, env

    def test_yes_removes_server_agent_configuration_and_data(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir, command_log, env = self._prepare_installation(temp_dir)
            result = subprocess.run(
                ["bash", str(repo_dir / "deploy" / "uninstall.sh"), "--yes"],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            commands = (
                command_log.read_text(encoding="utf-8")
                if command_log.exists()
                else ""
            )
            escaped_repo_dir = str(repo_dir).replace(" ", chr(92) + " ")

            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertIn(
                "systemctl disable --now proxypulse-api.service "
                "proxypulse-bot.service proxypulse-agent.service",
                commands,
            )
            self.assertIn(
                "rm -f -- /etc/systemd/system/proxypulse-api.service "
                "/etc/systemd/system/proxypulse-bot.service "
                "/etc/systemd/system/proxypulse-agent.service",
                commands,
            )
            self.assertIn("systemctl daemon-reload", commands)
            self.assertIn(
                "systemctl reset-failed proxypulse-api.service "
                "proxypulse-bot.service proxypulse-agent.service",
                commands,
            )
            self.assertIn(
                "rm -rf -- /etc/proxypulse /var/lib/proxypulse",
                commands,
            )
            self.assertFalse((repo_dir / ".venv").exists())
            self.assertFalse((repo_dir / "proxypulse.db").exists())
            self.assertIn(
                f"sudo rm -rf -- {escaped_repo_dir}",
                result.stdout,
            )

    def test_uninstall_is_idempotent_when_artifacts_are_absent(self) -> None:
        with tempfile.TemporaryDirectory() as temp_dir:
            repo_dir, _command_log, env = self._prepare_installation(temp_dir)
            (repo_dir / ".venv").rmdir()
            (repo_dir / "proxypulse.db").unlink()
            command = [
                "bash",
                str(repo_dir / "deploy" / "uninstall.sh"),
                "--yes",
            ]

            first = subprocess.run(
                command,
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )
            second = subprocess.run(
                command,
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

        self.assertEqual(first.returncode, 0, first.stderr)
        self.assertEqual(second.returncode, 0, second.stderr)
        self.assertIn("completely uninstalled", first.stdout)
        self.assertIn("completely uninstalled", second.stdout)

    def test_help_describes_noninteractive_option_without_cleanup(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--help"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertIn(
            "Usage: bash deploy/uninstall.sh [--yes|-y]",
            result.stdout,
        )

    def test_unknown_argument_is_rejected_without_cleanup(self) -> None:
        result = subprocess.run(
            ["bash", str(SCRIPT), "--unknown"],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 2)
        self.assertIn("Unknown argument: --unknown", result.stderr)
