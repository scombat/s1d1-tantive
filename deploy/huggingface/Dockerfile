# Hugging Face Docker Space: the published image, nothing built here (the weights are already inside it).
# Bump the tag when a new release is published; keep the variant `full`, which fits CPU Basic (16 GB).
FROM ghcr.io/scombat/s1d1-tantive:0.7.0-full-onnx-int8

# Docker Spaces run the container as uid 1000 (the image's own user is 10001; its files are world-readable and
# the service writes nothing outside /tmp)
USER 1000

# Defaults; the Space's Variables override them at run time. S1D1_API_KEY is deliberately absent: set it as a Space
# secret, never in this file.
ENV S1D1_FORMATS=systemone,decisions,classify \
    S1D1_TIERS=TRIVIAL=0.15,LOW=0.4,MEDIUM=0.7,HIGH=0.9,EXPERT=1.0 \
    S1D1_DEADLINE_MS=800

EXPOSE 8788
