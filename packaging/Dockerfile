FROM python:3.12-slim-bookworm
ENV PYTHONPATH=/opt/listening
COPY --from=sdk /listening_plugin/ /opt/listening/listening_plugin/
COPY --from=packaging /listening-engine /usr/local/bin/listening-engine
COPY *.py manifest.json /opt/listening/plugin/
RUN chmod -R a+rX /opt/listening && chmod 755 /usr/local/bin/listening-engine && listening-engine --validate
