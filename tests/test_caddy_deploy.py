from __future__ import annotations

import os
from pathlib import Path
import subprocess
import tempfile
from unittest import TestCase


PROJECT_ROOT = Path(__file__).resolve().parents[1]
COMMON = PROJECT_ROOT / "deploy" / "lib" / "common.sh"
CADDY = PROJECT_ROOT / "deploy" / "lib" / "caddy.sh"


class CaddyDeployTests(TestCase):
    def test_public_url_helpers_validate_and_apply_ports(self) -> None:
        command = (
            f'source "{COMMON}"; source "{CADDY}"; '
            'validate_public_server_url "https://monitor.example.com"; '
            'printf "%s\n" "$(server_url_port "https://monitor.example.com")"; '
            'printf "%s\n" "$(server_url_with_port "https://monitor.example.com" 8443)"; '
            '! validate_public_server_url "https://monitor.example.com/api"; '
            '! validate_public_server_url "http://example.com:70000"'
        )
        result = subprocess.run(
            ["bash", "-c", command],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(
            result.stdout.splitlines(),
            ["443", "https://monitor.example.com:8443"],
        )

    def test_install_and_remove_proxy_preserves_existing_caddyfile(self) -> None:
        with tempfile.TemporaryDirectory(dir="/tmp") as temp_dir:
            temp = Path(temp_dir)
            caddy_dir = temp / "etc" / "caddy"
            env_dir = temp / "etc" / "proxypulse"
            caddy_dir.mkdir(parents=True)
            caddyfile = caddy_dir / "Caddyfile"
            original_site = "existing.example.com {\n\trespond \"existing\"\n}\n"
            caddyfile.write_text(original_site, encoding="utf-8")

            fake_bin = temp / "bin"
            fake_bin.mkdir()
            sudo = fake_bin / "sudo"
            sudo.write_text(
                "#!/usr/bin/env bash\n"
                "if [[ \"${1:-}\" == \"-v\" ]]; then exit 0; fi\n"
                "exec \"$@\"\n",
                encoding="utf-8",
            )
            sudo.chmod(0o755)
            caddy = fake_bin / "caddy"
            caddy.write_text("#!/usr/bin/env bash\nexit 0\n", encoding="utf-8")
            caddy.chmod(0o755)
            systemctl = fake_bin / "systemctl"
            systemctl.write_text(
                "#!/usr/bin/env bash\n"
                "printf '%s\\n' \"$*\" >> \"${SYSTEMCTL_LOG}\"\n",
                encoding="utf-8",
            )
            systemctl.chmod(0o755)

            env = {
                **os.environ,
                "PATH": f"{fake_bin}:{os.environ.get('PATH', '')}",
                "SYSTEMCTL_LOG": str(temp / "systemctl.log"),
                "PROXYPULSE_ENV_DIR": str(env_dir),
                "PROXYPULSE_CADDY_CONFIG_DIR": str(caddy_dir),
            }
            install = (
                f'source "{COMMON}"; source "{CADDY}"; '
                'install_caddy_proxy "https://monitor.example.com:8443" 8080'
            )
            installed = subprocess.run(
                ["bash", "-c", install],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(installed.returncode, 0, installed.stderr)
            self.assertIn(original_site.strip(), caddyfile.read_text(encoding="utf-8"))
            self.assertIn(
                "import sites-enabled/*.caddy",
                caddyfile.read_text(encoding="utf-8"),
            )
            proxy_file = caddy_dir / "sites-enabled" / "proxypulse.caddy"
            self.assertEqual(
                proxy_file.read_text(encoding="utf-8"),
                "https://monitor.example.com:8443 {\n"
                "\treverse_proxy 127.0.0.1:8080\n"
                "}\n",
            )

            remove = f'source "{COMMON}"; source "{CADDY}"; remove_caddy_proxy'
            removed = subprocess.run(
                ["bash", "-c", remove],
                text=True,
                capture_output=True,
                env=env,
                check=False,
            )

            self.assertEqual(removed.returncode, 0, removed.stderr)
            remaining = caddyfile.read_text(encoding="utf-8")
            self.assertIn(original_site.strip(), remaining)
            self.assertNotIn("PROXYPULSE MANAGED IMPORT", remaining)
            self.assertFalse(proxy_file.exists())
            systemctl_log = (temp / "systemctl.log").read_text(encoding="utf-8")
            self.assertIn("enable --now caddy.service", systemctl_log)
            self.assertIn("reload caddy.service", systemctl_log)

    def test_local_and_public_ports_must_differ(self) -> None:
        command = (
            f'source "{COMMON}"; source "{CADDY}"; '
            'install_caddy_proxy "http://monitor.example.com:8080" 8080'
        )
        result = subprocess.run(
            ["bash", "-c", command],
            text=True,
            capture_output=True,
            check=False,
        )

        self.assertNotEqual(result.returncode, 0)
        self.assertIn("不能与 Caddy 公网端口相同", result.stderr)
