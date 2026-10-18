#!/bin/bash
# The settings script's old name. Updaters already on devices, and older
# dashboards, still call sudo-configure-picoclaw.sh after an update; this
# passes them on to configure-agent.sh. Safe to delete once every device has
# run an updater that calls the new name.
for c in /usr/local/bin/sudo-configure-agent.sh /usr/local/bin/configure-agent.sh; do
  [ -x "$c" ] && exec "$c" "$@"
done
echo "configure-agent.sh is not installed" >&2
exit 1
