# Heroku Deploy button (see heroku.yml): the published small image, started on the port Heroku assigns.
FROM ghcr.io/scombat/s1d1-tantive:0.7.0-small-onnx-int8

# Heroku sets $PORT for the web process and ignores EXPOSE and USER (it runs the container as its own non-root user;
# the image files are world-readable). The base image has an ENTRYPOINT, so it is replaced rather than given a CMD.
ENTRYPOINT ["sh", "-c", "S1D1_PORT=${PORT:-8788} exec python -m tantive"]
