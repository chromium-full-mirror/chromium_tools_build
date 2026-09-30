use_relative_paths = True
hooks = [
  {
    "name": "remove_orphaned_pycs",
    "pattern": ".",
    "action": [
      "python3", "-u", "hook-scripts/remove_orphaned_pycs.py",
    ],
  },
  {
    "name": "vpython_sync",
    "pattern": ".",
    "action": [
      "vpython3",
      "-vpython-spec", "vpython.toml",
      "-vpython-tool", "install",
    ],
  },
]
