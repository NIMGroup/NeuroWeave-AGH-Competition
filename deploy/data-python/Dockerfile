ARG BASE=public.ecr.aws/docker/library/python@sha256:0dd364ba7e10242f07755449e3a3d0e35f9efd987952737b90def6709ab0c5ce
FROM ${BASE}
ARG PROFILE
ARG LOCK_SHA256
LABEL neuroweave.data.profile=${PROFILE} neuroweave.data.lock-sha256=${LOCK_SHA256}
COPY wheels /wheels
COPY requirements.lock /requirements.lock
RUN python -m pip install --no-index --find-links=/wheels --require-hashes --only-binary=:all: -r /requirements.lock \
    && python -m pip check && rm -rf /wheels /requirements.lock
USER 65534:65534
WORKDIR /output
