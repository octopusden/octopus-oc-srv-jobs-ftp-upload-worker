FROM fedora:38

USER root

# we need python interpreter working together with binary *gpg*
# venv uses system site packages to see *python3-pysvn* installed by dnf
# setuptools<81 still ships *pkg_resources* which is used by the worker
RUN dnf --assumeyes makecache && \
    dnf --assumeyes install \
        python3-pip \
        python3-devel \
        python3-pysvn \
        libpq-devel \
        gpg \
        glibc-devel \
        gcc \
        && \
    dnf --assumeyes clean all && rm -rf /var/cache/dnf/* && \
    python3 -m venv /local/venv --system-site-packages

ENV PATH="/local/venv/bin:$PATH"

RUN python3 -m pip install --no-cache-dir --upgrade pip && \
    python3 -m pip install --no-cache-dir --upgrade "setuptools<81" wheel

RUN rm -rf /build
COPY --chown=root:root . /build
WORKDIR /build
RUN python3 -m pip install --no-cache-dir $(pwd) && \
    python3 -m unittest discover -v && \
    python3 setup.py bdist_wheel && \
    rm -rf /tmp/*

ENV HOME=/local
WORKDIR /local
RUN chgrp -R 0 /local && \
    chmod -R g=u /local

USER 1001

ENTRYPOINT ["python3", "-m", "oc_ftp_upload_worker"]
