#!/bin/sh
# Start Ember & Tide as the unprivileged user (uid/gid 1000) whenever that is safe, so a first start just works:
#  - started as root (the default) with a NEW, empty data folder (for example the ./data folder Docker creates for a
#    bind mount, owned by root): hand the folder to uid 1000 and drop to it;
#  - started as root with a data folder already owned by uid 1000: drop to it;
#  - started as root with existing data owned by someone else (an install from before the image dropped root): keep
#    running as root, as before, and say how to switch;
#  - started as any other user (Compose `user:`): run as that user, and say clearly if the folder isn't writable.
set -e
DATA="${ET_DATA_DIR:-/data}"
RUN_UID=1000
RUN_GID=1000

if [ "$(id -u)" = "0" ]; then
    mkdir -p "$DATA"
    owner="$(stat -c %u "$DATA")"
    if [ "$owner" != "$RUN_UID" ] && [ -z "$(ls -A "$DATA" 2>/dev/null)" ]; then
        chown "$RUN_UID:$RUN_GID" "$DATA"
        owner="$RUN_UID"
    fi
    if [ "$owner" = "$RUN_UID" ]; then
        exec setpriv --reuid="$RUN_UID" --regid="$RUN_GID" --clear-groups -- "$@"
    fi
    echo "Ember & Tide: $DATA already holds data owned by uid $owner, so it keeps running as root." >&2
    echo "  To run unprivileged: stop it, run 'chown -R $RUN_UID:$RUN_GID <your data folder>' on the host, start again." >&2
    exec "$@"
fi

if ! [ -w "$DATA" ]; then
    echo "Ember & Tide can't write to $DATA as uid $(id -u)." >&2
    echo "  Fix: on the host run 'chown -R $(id -u):$(id -g) <your data folder>', or remove the 'user:' line" >&2
    echo "  from your Compose file so the image sets the folder up itself." >&2
    exit 1
fi
exec "$@"
