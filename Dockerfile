FROM docker.io/library/alpine:latest
RUN apk add --no-cache python3 py3-pip py3-ruamel.yaml
RUN pip install python-slugify requests loguru
VOLUME /srv
COPY hb_downloader.py /tmp/hb_downloader.py
COPY example_config.yaml /tmp/example_config.yaml
COPY entrypoint.sh /usr/local/bin/entrypoint.sh
WORKDIR /srv
ENTRYPOINT ["entrypoint.sh"]
