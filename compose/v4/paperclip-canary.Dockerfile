# Put the two pinned upstream runtimes on the same execution host for the offline canary.
#
# Apply the pinned server patches; build context is compose/v4.
FROM nousresearch/hermes-agent@sha256:36099926da316742423544e055d4734777105ffe6341873af5459b957d0c2919 AS hermes
FROM ghcr.io/paperclipai/paperclip:2026.1117.0@sha256:bdf38d10544dc6c831e16dc027f23ad586353ef3be1a1b2c6979e63b956aceb1 AS paperclip-base

FROM paperclip-base AS paperclip-handoff
WORKDIR /app
COPY paperclip-handoff.patch paperclip-protected-assignment.patch /tmp/
RUN git apply --verbose -p1 /tmp/paperclip-handoff.patch && \
    git apply --verbose -p1 /tmp/paperclip-protected-assignment.patch && \
    cd server && \
    PAPERCLIP_TSC_EMIT_ONLY=1 PAPERCLIP_TYPECHECK_LOW_MEMORY=1 node ../scripts/run-tsc.mjs && \
    cp -Rf ../packages/paperclip-runner/dist/. dist/vendor/paperclip-runner/ && \
    node scripts/bundle-runtime.mjs

FROM paperclip-base
COPY --from=paperclip-handoff /app/server/src /app/server/src
COPY --from=paperclip-handoff /app/server/dist /app/server/dist
COPY --from=paperclip-handoff /app/server/dist-runtime /app/server/dist-runtime
COPY --from=hermes /opt/hermes /opt/hermes
RUN ln -s /opt/hermes/tools/python-*/bin/python3 /usr/local/bin/python3
ENV HERMES_PYTHON=/opt/hermes/.venv/bin/python
