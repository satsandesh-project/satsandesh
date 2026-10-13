# The satsang media server: nginx with the RTMP module (Debian's libnginx-mod-rtmp). Measured
# against nginx 1.26.3 / libnginx-mod-rtmp 1.2.2; apt does not pin the version here, so a
# rebuild can pick up a newer one -- re-run the stream check from infra/satsang/README.md after one.
FROM debian:trixie-slim
RUN apt-get update \
    && apt-get install -y --no-install-recommends nginx libnginx-mod-rtmp \
    && rm -rf /var/lib/apt/lists/* \
    && mkdir -p /var/hls
COPY nginx.conf.template /etc/nginx/satsang.conf.template
COPY entrypoint.sh /entrypoint.sh
ENTRYPOINT ["/bin/sh", "/entrypoint.sh"]
