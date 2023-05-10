hooks = [
  {
    "name": "remove_orphaned_pycs",
    "pattern": ".",
    "action": [
      "python3", "-u", "build/hook-scripts/remove_orphaned_pycs.py",
    ],
  },
  {
    "name": "vpython_sync",
    "pattern": ".",
    "action": [
      "vpython3",
      "-vpython-spec", "build/.vpython3",
      "-vpython-tool", "install",
    ],
  },
]
