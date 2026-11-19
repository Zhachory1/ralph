# Put the two pinned upstream runtimes on the same execution host for the offline canary.
FROM nousresearch/hermes-agent@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919 AS hermes
FROM ghcr.io/paperclipai/paperclip:2026.1117.0@sha256:bdf38d10544dc6c831e16dc027f23ad586353ef3be1a1b2c6979e63b956aceb1
COPY --from=hermes /opt/hermes /opt/hermes
RUN ln -s /opt/hermes/tools/python-*/bin/python3 /usr/local/bin/python3
ENV HERMES_PYTHON=/opt/hermes/.venv/bin/python
