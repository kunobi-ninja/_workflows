"""Exercise the release workflow's actual archive and build shell steps."""
import os
import json
from pathlib import Path
import subprocess
import shutil
import tarfile
import tempfile
import textwrap
import unittest
import zipfile

WORKFLOW = Path(__file__).resolve().parents[1] / '.github/workflows/_release-rust.yml'
BASH = shutil.which('bash')


def script(name):
    block = WORKFLOW.read_text().split(f'      - name: {name}\n', 1)[1]
    block = block.split('\n      - ', 1)[0]
    body = block.split('        run: |\n', 1)[1]
    # A following workflow comment is outside the shell block.
    lines = []
    for line in body.splitlines():
        if line.strip() and not line.startswith('          '):
            break
        lines.append(line)
    return textwrap.dedent('\n'.join(lines))


class ReleaseBinaryTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.target = 'x86_64-unknown-linux-musl'
        self.env = dict(os.environ, TARGET=self.target, BINARY_NAME='cache',
                        COMPANION_BINARY='', COMPANION_BINARIES='[]', BINARY_EXT='', ARCHIVE_EXT='tar.gz',
                        UPLOAD_WINDOWS_EXE='false', GITHUB_OUTPUT=str(self.root / 'outputs'),
                        RUNNER_OS='Windows' if os.name == 'nt' else 'Linux', COPYFILE_DISABLE='1')

    def files(self, names):
        release = self.root / 'target' / self.env['TARGET'] / 'release'
        release.mkdir(parents=True)
        for name in names:
            (release / name).write_bytes(name.encode())

    def run_step(self, name):
        if name in ('Build', 'Strip binaries', 'Package', 'Prepare binary signing'):
            result = self.run_step('Resolve release binaries')
            if result.returncode:
                return result
            output = (self.root / 'outputs').read_text().splitlines()[-1]
            self.env['BINARIES_JSON'] = output.removeprefix('names=')
        body = script(name).replace('${{ matrix.use_cross }}', 'false').replace(
            '${{ matrix.use_xwin }}', 'false')
        return subprocess.run([BASH, '-e', '-o', 'pipefail', '-c', body],
                              env=self.env, cwd=self.root, capture_output=True, text=True)

    def test_single_binary_archive_stays_compatible(self):
        self.files(['cache'])
        result = self.run_step('Package')
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(self.root / f'cache-{self.target}.tar.gz') as archive:
            self.assertEqual(archive.getnames(), ['cache'])
            self.assertEqual(archive.extractfile('cache').read(), b'cache')

    def test_companion_tar_includes_each_binary_and_signature(self):
        self.env['COMPANION_BINARY'] = 'cache-engine'
        names = ['cache', 'cache-engine', 'cache.asc', 'cache-engine.asc']
        self.files(names)
        result = self.run_step('Package')
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(self.root / f'cache-{self.target}.tar.gz') as archive:
            self.assertCountEqual(archive.getnames(), names)
            for name in names:
                self.assertEqual(archive.extractfile(name).read(), name.encode())

    def test_windows_zip_includes_both_executables(self):
        self.env.update(TARGET='x86_64-pc-windows-msvc', ARCHIVE_EXT='zip',
                        BINARY_EXT='.exe', COMPANION_BINARY='cache-engine')
        names = ['cache.exe', 'cache-engine.exe']
        self.files(names)
        result = self.run_step('Package')
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.root / 'cache-x86_64-pc-windows-msvc.zip') as archive:
            self.assertCountEqual(archive.namelist(), names)
            for name in names:
                self.assertEqual(archive.read(name), name.encode())

    def test_missing_companion_never_produces_an_archive(self):
        self.env['COMPANION_BINARY'] = 'cache-engine'
        self.files(['cache'])
        self.assertNotEqual(self.run_step('Package').returncode, 0)
        self.assertFalse(list(self.root.glob('*.tar.gz')))

    def cargo_stub(self):
        tools = self.root / 'tools'
        tools.mkdir()
        cargo = tools / 'cargo'
        cargo.write_text('#!/bin/sh\nprintf "%s\\n" "$@" > cargo-args\n')
        cargo.chmod(0o755)
        self.env.update(PATH=f'{tools}{os.pathsep}{self.env["PATH"]}', BUILD_ARGS='--locked',
                        UPLOAD_WINDOWS_EXE='false')

    def test_multiple_companions_include_all_binaries_and_signatures(self):
        self.env['COMPANION_BINARIES'] = '["cache-engine", "cache-worker"]'
        names = ['cache', 'cache-engine', 'cache-worker',
                 'cache.asc', 'cache-engine.asc', 'cache-worker.asc']
        self.files(names)
        result = self.run_step('Package')
        self.assertEqual(result.returncode, 0, result.stderr)
        with tarfile.open(self.root / f'cache-{self.target}.tar.gz') as archive:
            self.assertCountEqual(archive.getnames(), names)

    def test_multiple_companions_windows_archive(self):
        self.env.update(TARGET='x86_64-pc-windows-msvc', ARCHIVE_EXT='zip',
                        BINARY_EXT='.exe', COMPANION_BINARIES='["engine", "worker"]')
        names = ['cache.exe', 'engine.exe', 'worker.exe']
        self.files(names)
        result = self.run_step('Package')
        self.assertEqual(result.returncode, 0, result.stderr)
        with zipfile.ZipFile(self.root / 'cache-x86_64-pc-windows-msvc.zip') as archive:
            self.assertCountEqual(archive.namelist(), names)

    def test_invalid_lists_are_rejected_before_build(self):
        for value in ['{}', 'null', '"engine"', '[null]', '[1]',
                      '["../engine"]', '["engine;false"]', '["cache"]',
                      '["engine", "engine"]', '["worker\\nengine"]']:
            with self.subTest(value=value):
                self.env['COMPANION_BINARIES'] = value
                self.assertNotEqual(self.run_step('Resolve release binaries').returncode, 0)

    def test_legacy_and_multiple_companions_combine(self):
        self.env.update(COMPANION_BINARY='engine', COMPANION_BINARIES='["worker"]')
        result = self.run_step('Resolve release binaries')
        self.assertEqual(result.returncode, 0, result.stderr)
        names = (self.root / 'outputs').read_text().splitlines()[-1]
        self.assertEqual(json.loads(names.removeprefix('names=')), ['cache', 'engine', 'worker'])
        self.env['COMPANION_BINARIES'] = '["engine"]'
        self.assertNotEqual(self.run_step('Resolve release binaries').returncode, 0)

    def test_bare_exe_rejects_multiple_companions(self):
        self.env.update(COMPANION_BINARIES='["engine", "worker"]', UPLOAD_WINDOWS_EXE='true')
        self.assertNotEqual(self.run_step('Resolve release binaries').returncode, 0)

    def test_signing_selects_every_binary_on_each_platform(self):
        self.env['COMPANION_BINARIES'] = '["engine", "worker"]'
        for target, platform, extension in [
                ('aarch64-apple-darwin', 'macos', ''),
                ('x86_64-pc-windows-msvc', 'windows', '.exe'),
                (self.target, 'linux', '')]:
            with self.subTest(target=target):
                self.env.update(TARGET=target, BINARY_EXT=extension)
                result = self.run_step('Prepare binary signing')
                self.assertEqual(result.returncode, 0, result.stderr)
                action = json.loads((self.root / '.release-binary-signing/action.yml').read_text())
                steps = action['runs']['steps']
                self.assertEqual(len(steps), 3)
                for step, binary in zip(steps, ['cache', 'engine', 'worker']):
                    self.assertEqual(step['uses'], f'zondax/actions/sign-{platform}-binary@v1')
                    key = 'target-path' if platform == 'linux' else 'binary-path'
                    self.assertEqual(step['with'][key], f'target/{target}/release/{binary}{extension}')
                    for name, value in step['with'].items():
                        if name != key:
                            self.assertTrue(value.startswith('${{ inputs.'), value)
                if platform == 'macos':
                    self.assertEqual(steps[1]['with']['notarize'], '${{ inputs.notarize_macos }}')
                elif platform == 'linux':
                    self.assertEqual(steps[1]['with']['signer-token'], '${{ inputs.pgp_signer_token }}')

    @unittest.skipIf(os.name == 'nt', 'Strip shell stub uses POSIX PATH')
    def test_strip_visits_every_companion(self):
        tools = self.root / 'tools'
        tools.mkdir()
        strip = tools / 'strip'
        strip.write_text('#!/bin/sh\nprintf "%s\\n" "$@" >> strip-args\n')
        strip.chmod(0o755)
        self.env.update(PATH=f'{tools}{os.pathsep}{self.env["PATH"]}',
                        COMPANION_BINARIES='["engine", "worker"]')
        result = self.run_step('Strip binaries')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'strip-args').read_text().splitlines(),
                         [f'target/{self.target}/release/{name}'
                          for name in ['cache', 'engine', 'worker']])

    @unittest.skipIf(os.name == 'nt', 'Cargo shell stub uses POSIX PATH')
    def test_build_selects_all_companions(self):
        self.cargo_stub()
        self.env['COMPANION_BINARIES'] = '["engine", "worker"]'
        result = self.run_step('Build')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'cargo-args').read_text().splitlines(),
                         ['build', '--release', '--target', self.target, '--locked',
                          '--bin', 'cache', '--bin', 'engine', '--bin', 'worker'])

    @unittest.skipIf(os.name == 'nt', 'Cargo shell stub uses POSIX PATH')
    def test_build_explicitly_selects_primary_and_companion(self):
        self.cargo_stub()
        self.env['COMPANION_BINARY'] = 'cache-engine'
        result = self.run_step('Build')
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual((self.root / 'cargo-args').read_text().splitlines(),
                         ['build', '--release', '--target', self.target, '--locked',
                          '--bin', 'cache', '--bin', 'cache-engine'])

    @unittest.skipIf(os.name == 'nt', 'Cargo shell stub uses POSIX PATH')
    def test_invalid_companion_and_bare_exe_conflict_stop_before_build(self):
        self.cargo_stub()
        for companion, bare in [('cache', 'false'), ('../engine', 'false'),
                                ('engine;false', 'false'), ('engine', 'true')]:
            with self.subTest(companion=companion, bare=bare):
                self.env.update(COMPANION_BINARY=companion, UPLOAD_WINDOWS_EXE=bare)
                self.assertNotEqual(self.run_step('Build').returncode, 0)
                self.assertFalse((self.root / 'cargo-args').exists())


if __name__ == '__main__':
    unittest.main()
