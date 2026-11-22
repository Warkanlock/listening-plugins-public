FROM python:3.12-slim-bookworm AS build
ARG TARGETARCH
COPY build/sockseek-pin.json build/provision.py /tmp/
RUN test "$TARGETARCH" = amd64 && python3 /tmp/provision.py

FROM python:3.12-slim-bookworm
RUN apt-get update && apt-get install -y --no-install-recommends libicu72 libssl3 \
    && rm -rf /var/lib/apt/lists/*
COPY --from=build /opt/sockseek /opt/sockseek
COPY --chmod=644 build/licenses/Sockseek-AGPL-3.0.txt /usr/share/licenses/sockseek/LICENSE
LABEL org.opencontainers.image.source="https://github.com/Warkanlock/listening-plugins-public" \
      music.listening.sockseek.source="https://github.com/fiso64/sockseek/archive/refs/tags/v3.0.6.tar.gz"
ENV PYTHONPATH=/opt/listening
COPY --from=sdk /listening_plugin/ /opt/listening/listening_plugin/
COPY --from=packaging /listening-engine /usr/local/bin/listening-engine
COPY *.py manifest.json /opt/listening/plugin/
RUN chmod -R a+rX /opt/listening && chmod 755 /usr/local/bin/listening-engine && listening-engine --validate
