"""Exercise the release workflow's actual archive and build shell steps."""
import os
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
                        COMPANION_BINARY='', BINARY_EXT='', ARCHIVE_EXT='tar.gz',
                        RUNNER_OS='Windows' if os.name == 'nt' else 'Linux', COPYFILE_DISABLE='1')

    def files(self, names):
        release = self.root / 'target' / self.env['TARGET'] / 'release'
        release.mkdir(parents=True)
        for name in names:
            (release / name).write_bytes(name.encode())

    def run_step(self, name):
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
        self.env.update(PATH=f'{tools}:/usr/bin:/bin', BUILD_ARGS='--locked',
                        UPLOAD_WINDOWS_EXE='false')

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
