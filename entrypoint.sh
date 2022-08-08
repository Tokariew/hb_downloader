#!/bin/sh

cp -n /tmp/example_config.yaml /srv/config.yaml
ln -s /srv/config.yaml /tmp/config.yaml
exec python3 /tmp/hb_downloader.py "$@"
