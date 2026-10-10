# Build Dockerfile.upstream first; see deploy/media-services/README.md.
ARG WISE_BASE=inspiration-wise-upstream:fcfa443f-cpu
FROM ${WISE_BASE}

USER root
COPY --chown=${MAMBA_USER}:${MAMBA_USER} . /opt/bridge
RUN mkdir -p /data /cache && chown ${MAMBA_USER}:${MAMBA_USER} /data /cache /opt/bridge
USER ${MAMBA_USER}
RUN micromamba run -n wise-env python -c "import sys; assert sys.version_info >= (3,11), 'WISE bridge needs Python >= 3.11'" \
    && micromamba run -n wise-env pip install -e /opt/bridge

ENV HOME=/cache \
    PATH=/opt/conda/envs/wise-env/bin:$PATH \
    LD_LIBRARY_PATH=/opt/conda/envs/wise-env/lib \
    MAGIC=/opt/conda/envs/wise-env/share/misc/magic \
    HF_HOME=/cache/huggingface \
    TORCH_HOME=/cache/torch \
    XDG_CACHE_HOME=/cache \
    PYTHONPYCACHEPREFIX=/cache/pycache \
    MPLCONFIGDIR=/cache/matplotlib \
    WISE_BRIDGE_DATA=/data \
    WISE_SOURCE_DIR=/opt/wise \
    WISE_PYTHON=/opt/conda/envs/wise-env/bin/python \
    OMP_NUM_THREADS=2
WORKDIR /opt/bridge
EXPOSE 9671
ENTRYPOINT ["/opt/conda/envs/wise-env/bin/python", "-m", "uvicorn"]
CMD ["wise_bridge.api:create_app", "--factory", "--host", "0.0.0.0", "--port", "9671", "--workers", "1"]
