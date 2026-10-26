# syntax=docker/dockerfile:1.7
# s1d1-tantive images (spec § 6): `base` carries the service, `variant` adds one ONNX int8 variant under /model.
#   docker build --target variant --build-arg VARIANT=small-onnx-int8 -t tantive:small .
# Weights come from the Hub at the commit pinned in checksums/<VARIANT>.json (WEIGHTS_SRC=hub, default) or from a
# local folder weights/<VARIANT>/ (WEIGHTS_SRC=local); both are verified file by file before and after the copy.

ARG VARIANT=full-onnx-int8
ARG WEIGHTS_SRC=hub

# The two pinned images, each in one place: python:3.12-slim (bookworm) and ghcr.io/astral-sh/uv:0.11.26, digests
# resolved 2026-10-20 with `docker buildx imagetools inspect <image>`; later stages refer to these aliases.
FROM python:3.12-slim@sha256:a0f57064cbdcd6285fa45f51237ab0b35ea8ebaed06a1f5973e89e19dfb86704 AS python
FROM ghcr.io/astral-sh/uv:0.13.4@sha256:9f130421e930bfbdc563c00740466603f8723158e90297d07957c2294640262b AS uv

# ---- weights from the Hub, at the pinned commit, verified before anything else uses them -------------------------
FROM python AS weights-hub
ARG VARIANT
RUN pip install --no-cache-dir huggingface_hub==2.2.0
COPY checksums/${VARIANT}.json /checksums.json
COPY src/tantive/verify.py /verify.py
# download exactly the listed files at the pinned commit, drop the download cache, then verify every file
RUN python -c "import json, shutil; from huggingface_hub import snapshot_download; c = json.load(open('/checksums.json')); \
    snapshot_download(c['repo'], revision=c['revision'], local_dir='/weights', allow_patterns=list(c['files'])); \
    shutil.rmtree('/weights/.cache', ignore_errors=True)" \
    && python /verify.py /weights /checksums.json

# ---- weights from a local folder (CI without egress to huggingface.co); verified in the variant stage ------------
# `scratch` has no tag to pin: it is the empty image
# hadolint ignore=DL3006
FROM scratch AS weights-local
ARG VARIANT
COPY weights/${VARIANT}/ /weights/

# hadolint ignore=DL3006
FROM weights-${WEIGHTS_SRC} AS weights

# ---- the service: dependencies from uv.lock into a venv, built with uv, shipped without it ------------------------
FROM python AS builder
COPY --from=uv /uv /bin/uv
ENV UV_LINK_MODE=copy UV_COMPILE_BYTECODE=1 UV_PYTHON_DOWNLOADS=never
WORKDIR /app
COPY pyproject.toml uv.lock README.md LICENSE ./
COPY src ./src
# huggingface-hub is a declared dependency of tokenizers but is only imported by Tokenizer.from_pretrained, which the
# service never calls: it and its transfer library hf-xet are removed so the image carries no Hub client (and no `hf`)
RUN uv sync --locked --no-dev --no-editable \
    && uv pip uninstall --python /app/.venv/bin/python huggingface-hub hf-xet

FROM python AS base
# no pip in the final image: the venv is complete and nothing installs at run time
RUN python -m pip uninstall --yes pip \
    && useradd --uid 10001 --no-create-home --shell /usr/sbin/nologin tantive
COPY --from=builder --chown=10001:10001 /app/.venv /app/.venv
ENV PATH=/app/.venv/bin:$PATH \
    S1D1_MODEL_DIR=/model \
    S1D1_HOST=0.0.0.0 \
    S1D1_PORT=8788 \
    PYTHONUNBUFFERED=1
WORKDIR /app
USER 10001
EXPOSE 8788
ENTRYPOINT ["python", "-m", "tantive"]

# ---- one variant: base plus its weights, verified again inside the image -----------------------------------------
FROM base AS variant
ARG VARIANT
ARG VERSION=dev
ARG GIT_SHA=unknown
ARG MODEL_REPO=
ARG MODEL_REVISION=
ARG MODEL_SHA256=
COPY --from=weights --chown=10001:10001 /weights /model
COPY --chown=10001:10001 checksums/${VARIANT}.json /model/checksums.json
RUN python -c "import json; open('/model/REVISION', 'w').write(json.load(open('/model/checksums.json'))['revision'] + '\n')" \
    && python -m tantive.verify /model /model/checksums.json
LABEL org.opencontainers.image.title="s1d1-tantive" \
      org.opencontainers.image.description="The s1d1 router as a decision service, with the ${VARIANT} weights" \
      org.opencontainers.image.version="${VERSION}" \
      org.opencontainers.image.revision="${GIT_SHA}" \
      org.opencontainers.image.source="https://github.com/scombat/s1d1-tantive" \
      org.opencontainers.image.licenses="BUSL-1.1" \
      io.s1d1.model.repo="${MODEL_REPO}" \
      io.s1d1.model.revision="${MODEL_REVISION}" \
      io.s1d1.model.sha256="${MODEL_SHA256}"
