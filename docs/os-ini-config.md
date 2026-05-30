# os.ini Configuration

`os.ini` is an optional read-only data slot loaded by the OS at boot. It sits
immediately after `os.bin`:

| Slot | Purpose |
| ---: | --- |
| 1 | `os.bin` |
| 2 | `os.ini` |
| 3 | default app ELF |

The initial OS section is:

```ini
[os]
ELF=app.elf
ARGS=--help -a -p path
```

`ELF` names the app ELF to launch. The OS resolves it by filename from the APF
data-slot table. `slot:N` is also accepted for explicit numeric slots. If the
key or file is missing, the OS falls back to `app.elf` in slot 3.

`ARGS` is tokenized and passed as `argv[1...]`; `argv[0]` is the ELF name.
Single quotes, double quotes, and backslash escaping are supported.

Apps can read any section through the SDK:

```c
#include "of.h"

char value[64];
if (of_config_get("quake", "pak", value, sizeof(value)) == 0) {
    /* use value */
}

int enabled = of_config_get_bool("video", "smooth", 1);
int scale = of_config_get_int("video", "scale", 2);
```

Parser rules:

- Section and key lookup is ASCII case-insensitive.
- Values preserve case and internal spaces.
- Blank lines and lines starting with `#` or `;` are ignored.
- Duplicate keys are allowed; the last value wins.
- The OS currently accepts up to 16 KiB and 256 key/value entries.
