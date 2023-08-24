FROM fedora:38

USER root

# we need python interpreter working together with binary *gpg*
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
    python3 -m pip install --user --upgrade pip && \
    python3 -m pip install --user --upgrade setuptools wheel && \
    dnf --assumeyes clean all && rm -rf /var/cache/dnf/*

RUN rm -rf /build
COPY --chown=root:root . /build
WORKDIR /build
RUN python3 -m pip install --user $(pwd) && \
    python3 -m unittest discover -v && \
    python3 setup.py bdist_wheel

ENTRYPOINT ["python3", "-m", "oc_ftp_upload_worker"]
