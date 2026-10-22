"""Exercise the generated composite on GitHub without signing credentials."""
import json
import os
from pathlib import Path
import subprocess
from test_release_binaries import BASH, script

names = ['cache', 'engine', 'worker']
env = dict(os.environ, TARGET='x86_64-unknown-linux-musl', BINARY_EXT='',
           BINARIES_JSON=json.dumps(names))
subprocess.run([BASH, '-e', '-o', 'pipefail', '-c', script('Prepare binary signing')],
               env=env, check=True)
manifest = Path('.release-binary-signing/action.yml')
action = json.loads(manifest.read_text())
for step in action['runs']['steps']:
    path = step['with']['target-path']
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text('unsigned fixture')
    step['uses'] = './.github/actions/signing-fixture'
    step['with'] = {'target-path': path}
manifest.write_text(json.dumps(action))
